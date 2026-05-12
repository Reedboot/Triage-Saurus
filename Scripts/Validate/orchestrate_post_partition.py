#!/usr/bin/env python3
"""Integrated orchestrator: runs after partition scans complete.

Workflow:
1. Aggregate partition summaries
2. Validate screenshots for completed experiments
3. Identify rule-candidate opportunities
4. Log all findings and recommendations

Usage:
    python3 orchestrate_post_partition.py [--wait-for-completion]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime


def check_partitions_complete(audit_root: Path = Path("Output/Audit/partition-runs")) -> bool:
    """Check if all partition validators have exited."""
    pids = {}
    for pidfile in audit_root.glob("partition*.pid"):
        try:
            pid = int(pidfile.read_text().strip())
            pids[pidfile.stem] = pid
        except:
            pass
    
    if not pids:
        return False
    
    # Check if processes still running
    running = []
    for name, pid in pids.items():
        result = subprocess.run(
            ["ps", "-p", str(pid)],
            capture_output=True,
            timeout=5
        )
        if result.returncode == 0:
            running.append(name)
    
    return len(running) == 0


def run_aggregation(output_file: Path = Path("Output/Audit/partition-aggregation.json")) -> bool:
    """Run partition aggregation."""
    print("\n📊 Running partition aggregation...")
    result = subprocess.run(
        ["python3", "Scripts/Validate/aggregate_partition_results.py", "--output", str(output_file)],
        cwd=Path(__file__).parent.parent.parent,
    )
    return result.returncode == 0


def get_completed_experiments(agg_file: Path) -> list[str]:
    """Extract experiment IDs from aggregation results."""
    try:
        with open(agg_file) as f:
            agg = json.load(f)
        return [r["experiment_id"] for r in agg["completed_repos"] if r.get("experiment_id")]
    except Exception as e:
        print(f"⚠️  Failed to extract experiments: {e}")
        return []


async def validate_experiment_diagrams(exp_id: str, base_url: str = "http://127.0.0.1:9000") -> bool:
    """Validate diagrams for a single experiment."""
    output_dir = Path("Output/Audit/diagram-validation")
    result = subprocess.run(
        [
            "python3",
            "Scripts/Validate/validate_diagrams_headless.py",
            "--experiment", exp_id,
            "--base-url", base_url,
            "--output", str(output_dir),
        ],
        cwd=Path(__file__).parent.parent.parent,
        capture_output=True,
        timeout=120,
    )
    
    if result.returncode == 0:
        print(f"  ✅ {exp_id}: diagram validation passed")
        return True
    else:
        print(f"  ⚠️  {exp_id}: diagram validation had issues")
        if result.stderr:
            print(f"     {result.stderr.decode()[:200]}")
        return False


async def validate_all_diagrams(exp_ids: list[str], concurrency: int = 3) -> dict[str, bool]:
    """Validate diagrams for all completed experiments."""
    print(f"\n📸 Validating diagrams for {len(exp_ids)} completed experiments (concurrency={concurrency})...")
    
    results = {}
    semaphore = asyncio.Semaphore(concurrency)
    
    async def validate_with_semaphore(exp_id):
        async with semaphore:
            return await validate_experiment_diagrams(exp_id)
    
    tasks = [validate_with_semaphore(exp_id) for exp_id in exp_ids]
    outcomes = await asyncio.gather(*tasks, return_exceptions=True)
    
    for exp_id, outcome in zip(exp_ids, outcomes):
        results[exp_id] = outcome if isinstance(outcome, bool) else False
    
    passed = sum(1 for v in results.values() if v)
    print(f"✅ Diagram validation: {passed}/{len(exp_ids)} passed")
    
    return results


def generate_post_scan_report(
    agg_file: Path,
    diagram_validation_dir: Path,
) -> dict:
    """Generate consolidated post-scan report."""
    report = {
        "timestamp": datetime.now().isoformat(),
        "aggregation_file": str(agg_file),
        "diagram_validation_dir": str(diagram_validation_dir),
        "status": "pending",
        "sections": {},
    }
    
    # Load aggregation
    try:
        with open(agg_file) as f:
            agg = json.load(f)
        report["sections"]["aggregation"] = {
            "total_repos": agg["global_stats"]["total_repos"],
            "completed": agg["global_stats"]["global_completed"],
            "failed": agg["global_stats"]["global_failed"],
            "partitions": len(agg["partition_runs"]),
        }
    except Exception as e:
        report["sections"]["aggregation"] = {"error": str(e)}
    
    # Summarize diagram validation
    validation_files = list(diagram_validation_dir.glob("validation_*.json"))
    if validation_files:
        issues_by_severity = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
        for vfile in validation_files:
            try:
                with open(vfile) as f:
                    v = json.load(f)
                for issue in v.get("issues", []):
                    severity = issue.get("severity", "LOW")
                    issues_by_severity[severity] = issues_by_severity.get(severity, 0) + 1
            except:
                pass
        
        report["sections"]["diagram_validation"] = {
            "files_checked": len(validation_files),
            "issues_by_severity": issues_by_severity,
        }
    
    return report


async def main():
    parser = argparse.ArgumentParser(description="Orchestrate post-partition workflow")
    parser.add_argument(
        "--wait-for-completion",
        action="store_true",
        help="Wait for partitions to complete before proceeding",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=7200,
        help="Timeout for waiting on partitions (seconds)",
    )
    args = parser.parse_args()
    
    print("🦖 Post-Partition Orchestration Starting")
    print(f"⏰ {datetime.now().isoformat()}")
    
    # Wait for completion if requested
    if args.wait_for_completion:
        print(f"\n⏳ Waiting for partitions to complete (timeout={args.timeout}s)...")
        deadline = time.time() + args.timeout
        while time.time() < deadline:
            if check_partitions_complete():
                print("✅ All partitions have completed")
                break
            print(f"   Still waiting... ({int(deadline - time.time())}s remaining)")
            await asyncio.sleep(15)
        else:
            print("❌ Timeout waiting for partitions")
            return 1
    
    # Step 1: Aggregate
    agg_file = Path("Output/Audit/partition-aggregation.json")
    if not run_aggregation(agg_file):
        print("❌ Aggregation failed")
        return 1
    
    # Step 2: Validate diagrams
    exp_ids = get_completed_experiments(agg_file)
    if not exp_ids:
        print("⚠️  No completed experiments found")
        return 0
    
    diagram_results = await validate_all_diagrams(exp_ids, concurrency=3)
    
    # Step 3: Generate report
    diagram_dir = Path("Output/Audit/diagram-validation")
    report = generate_post_scan_report(agg_file, diagram_dir)
    report_file = Path("Output/Audit/post-partition-report.json")
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(json.dumps(report, indent=2), encoding="utf-8")
    
    print(f"\n✅ Post-partition orchestration complete")
    print(f"📄 Report: {report_file}")
    print(f"📸 Diagram validation: {diagram_dir}")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
