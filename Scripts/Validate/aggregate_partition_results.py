#!/usr/bin/env python3
"""Aggregate partition validation results into a consolidated status view.

This script merges individual partition summary.json files (from
WebScanValidation_* directories) into one consolidated report for easy
review and downstream rerun planning.

Usage:
    python3 aggregate_partition_results.py \
      --output Output/Audit/partition-aggregation.json

Output:
    - Merged summary with global completed/failed counts
    - Per-partition breakdown with partition index
    - Consolidated list of all repos with their final status
    - Recommendations for failed repos requiring rerun
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def collect_partition_summaries(audit_root: Path) -> list[tuple[str, dict]]:
    """Find and parse all partition summary.json files."""
    summaries: list[tuple[str, dict]] = []
    
    for run_dir in sorted(audit_root.glob("WebScanValidation_*")):
        summary_file = run_dir / "summary.json"
        if not summary_file.exists():
            continue
        
        try:
            with open(summary_file, encoding="utf-8") as f:
                data = json.load(f)
            summaries.append((run_dir.name, data))
        except Exception as e:
            print(f"⚠️  Failed to parse {summary_file}: {e}")
    
    return summaries


def aggregate_summaries(summaries: list[tuple[str, dict]]) -> dict[str, Any]:
    """Merge partition summaries into one global view."""
    aggregated = {
        "aggregation_timestamp": datetime.now(timezone.utc).isoformat(),
        "partition_runs": [],
        "global_stats": {
            "total_repos": 0,
            "global_completed": 0,
            "global_failed": 0,
            "partition_count": 0,
        },
        "all_repos": [],
        "failed_repos": [],
        "completed_repos": [],
        "recommendations": [],
    }
    
    repos_seen = {}  # (path, name) -> status
    partition_count = 0
    
    for run_name, summary in summaries:
        partition_idx = summary.get("partition_index")
        partition_cnt = summary.get("partition_count")
        
        if partition_cnt is not None:
            partition_count = max(partition_count, partition_cnt)
        
        partition_summary = {
            "run_directory": run_name,
            "partition_index": partition_idx,
            "partition_count": partition_cnt,
            "repos_total": summary.get("repos_total", 0),
            "completed": summary.get("completed", 0),
            "failed": summary.get("failed", 0),
            "retry_attempted": summary.get("retry_attempted", 0),
            "retry_recovered": summary.get("retry_recovered", 0),
        }
        aggregated["partition_runs"].append(partition_summary)
        
        # Track individual repo results
        for result in summary.get("results", []):
            repo_path = result.get("repo_path", "unknown")
            repo_name = result.get("repo_name", "unknown")
            status = result.get("status", "unknown")
            key = (repo_path, repo_name)
            
            if key not in repos_seen:
                repos_seen[key] = status
                aggregated["all_repos"].append({
                    "repo_name": repo_name,
                    "repo_path": repo_path,
                    "status": status,
                    "experiment_id": result.get("experiment_id"),
                    "run_directory": run_name,
                })
                
                if status == "completed":
                    aggregated["completed_repos"].append({
                        "repo_name": repo_name,
                        "repo_path": repo_path,
                        "experiment_id": result.get("experiment_id"),
                        "provider_screenshots": result.get("provider_screenshots", []),
                        "orphan_issues": result.get("orphan_issues", []),
                        "connection_issues": result.get("connection_issues", []),
                        "parity_issues": result.get("parity_issues", []),
                        "rule_candidates": result.get("rule_candidates", []),
                    })
                else:
                    aggregated["failed_repos"].append({
                        "repo_name": repo_name,
                        "repo_path": repo_path,
                        "error": result.get("error", "unknown failure"),
                        "run_directory": run_name,
                    })
    
    # Compute global stats
    aggregated["global_stats"]["total_repos"] = len(repos_seen)
    aggregated["global_stats"]["global_completed"] = len(aggregated["completed_repos"])
    aggregated["global_stats"]["global_failed"] = len(aggregated["failed_repos"])
    aggregated["global_stats"]["partition_count"] = partition_count
    
    # Generate recommendations
    if aggregated["failed_repos"]:
        aggregated["recommendations"].append(
            f"⚠️  {len(aggregated['failed_repos'])} repos failed; "
            "recommend retry pass with same partitions or sequential execution for blocking issues."
        )
    
    if aggregated["global_stats"]["global_completed"] > 0:
        aggregated["recommendations"].append(
            f"✅ {aggregated['global_stats']['global_completed']} repos completed; "
            "ready for diagram validation and screenshot triage."
        )
    
    return aggregated


def main():
    parser = argparse.ArgumentParser(
        description="Aggregate partition validation results into a consolidated status view."
    )
    parser.add_argument(
        "--audit-root",
        type=Path,
        default=Path("Output/Audit"),
        help="Root directory containing WebScanValidation_* runs",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("Output/Audit/partition-aggregation.json"),
        help="Output file for aggregated results",
    )
    args = parser.parse_args()
    
    print(f"🔍 Collecting partition summaries from {args.audit_root}...")
    summaries = collect_partition_summaries(args.audit_root)
    
    if not summaries:
        print("❌ No partition summaries found")
        return 1
    
    print(f"✅ Found {len(summaries)} partition runs")
    print("📊 Aggregating results...")
    aggregated = aggregate_summaries(summaries)
    
    # Write output
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(aggregated, f, indent=2)
    
    print(f"✅ Aggregation complete. Results written to {args.output}")
    print()
    print("📈 Global Summary:")
    stats = aggregated["global_stats"]
    print(f"  Total repos: {stats['total_repos']}")
    print(f"  Completed: {stats['global_completed']}")
    print(f"  Failed: {stats['global_failed']}")
    if stats["partition_count"]:
        print(f"  Partitions: {stats['partition_count']}")
    print()
    
    if aggregated["recommendations"]:
        print("💡 Recommendations:")
        for rec in aggregated["recommendations"]:
            print(f"  {rec}")
    
    return 0 if stats["global_failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
