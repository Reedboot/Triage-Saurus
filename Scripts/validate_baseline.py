#!/usr/bin/env python3
"""Validate finding/summary outputs against baseline quality criteria.

Checks that generated outputs meet the expected structure and quality markers
defined in baseline_reference.json.

Usage:
    python3 Scripts/validate_baseline.py Output/Summary/Repos/fi_api.md
    python3 Scripts/validate_baseline.py Output/Findings/Cloud/*.md
    python3 Scripts/validate_baseline.py --experiment 001
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from output_paths import OUTPUT_ROOT, REPO_ROOT

BASELINE_REF = OUTPUT_ROOT / "Learning" / "baseline_reference.json"


def load_baseline() -> dict:
    """Load baseline reference."""
    if not BASELINE_REF.exists():
        print(f"ERROR: Baseline reference not found: {BASELINE_REF}")
        return {}
    return json.loads(BASELINE_REF.read_text())


def detect_output_type(path: Path) -> str:
    """Detect the type of output file."""
    path_str = str(path).lower()
    
    if "summary/repos" in path_str:
        return "repo_summary"
    elif "findings/cloud" in path_str:
        return "cloud_finding"
    elif "findings/code" in path_str:
        return "code_finding"
    elif "knowledge" in path_str:
        return "knowledge"
    else:
        return "unknown"


def extract_sections(content: str) -> list[str]:
    """Extract section headings from markdown content."""
    sections = []
    for line in content.splitlines():
        # Match ## or ### headings
        match = re.match(r'^#{1,3}\s+(.+)$', line.strip())
        if match:
            sections.append(match.group(1).strip())
    return sections


def check_quality_markers(content: str, markers: dict) -> dict[str, bool]:
    """Check which quality markers are present."""
    results = {}
    
    if markers.get("has_mermaid_diagram"):
        results["has_mermaid_diagram"] = "```mermaid" in content
    
    if markers.get("has_executive_summary_table"):
        results["has_executive_summary_table"] = "| Aspect | Value |" in content
    
    if markers.get("has_route_mappings_table"):
        results["has_route_mappings_table"] = "| Incoming Path |" in content or "| Route |" in content
    
    if markers.get("has_language_detection"):
        results["has_language_detection"] = "Languages & Frameworks" in content or "Languages/Frameworks" in content
    
    if markers.get("has_auth_detection"):
        results["has_auth_detection"] = "Authentication" in content
    
    if markers.get("has_ingress_egress_signals"):
        results["has_ingress_egress_signals"] = "Ingress" in content and "Egress" in content
    
    if markers.get("has_score"):
        results["has_score"] = re.search(r'\*\*\d+/10\*\*', content) is not None
    
    if markers.get("has_applicability"):
        results["has_applicability"] = "Applicability" in content
    
    if markers.get("has_recommendations"):
        results["has_recommendations"] = "Recommendations" in content
    
    if markers.get("has_dev_skeptic"):
        results["has_dev_skeptic"] = "Dev" in content and "Skeptic" in content
    
    if markers.get("has_platform_skeptic"):
        results["has_platform_skeptic"] = "Platform" in content and "Skeptic" in content
    
    if markers.get("has_code_evidence"):
        results["has_code_evidence"] = "Evidence" in content or "evidence:" in content.lower()
    
    if markers.get("has_exploitability"):
        results["has_exploitability"] = "Exploitability" in content
    
    return results


def validate_file(path: Path, baseline: dict) -> dict:
    """Validate a single file against baseline criteria."""
    if not path.exists():
        return {"error": f"File not found: {path}", "score": 0}
    
    content = path.read_text(encoding="utf-8", errors="replace")
    output_type = detect_output_type(path)
    
    if output_type == "unknown":
        return {"error": f"Unknown output type for: {path}", "score": 0}
    
    ref = baseline.get("references", {}).get(output_type, {})
    required_sections = ref.get("required_sections", [])
    quality_markers = ref.get("quality_markers", {})
    
    # Extract actual sections
    actual_sections = extract_sections(content)
    
    # Check required sections
    missing_sections = []
    for req in required_sections:
        found = any(req in s for s in actual_sections)
        if not found:
            missing_sections.append(req)
    
    sections_score = (len(required_sections) - len(missing_sections)) / max(len(required_sections), 1)
    
    # Check quality markers
    marker_results = check_quality_markers(content, quality_markers)
    markers_met = sum(1 for v in marker_results.values() if v)
    markers_total = max(len(marker_results), 1)
    markers_score = markers_met / markers_total
    
    # Check for incomplete TODOs
    todo_count = content.lower().count("[phase 2 todo]") + content.lower().count("[todo]")
    completeness_penalty = min(todo_count * 0.05, 0.3)  # Max 30% penalty
    
    # Calculate overall score
    overall_score = (sections_score * 0.4 + markers_score * 0.4 + (1 - completeness_penalty) * 0.2)
    
    return {
        "path": str(path),
        "type": output_type,
        "sections_found": len(actual_sections),
        "sections_required": len(required_sections),
        "sections_missing": missing_sections,
        "sections_score": sections_score,
        "quality_markers": marker_results,
        "markers_score": markers_score,
        "todo_count": todo_count,
        "completeness_penalty": completeness_penalty,
        "overall_score": overall_score,
    }


def validate_experiment(exp_id: str, baseline: dict) -> list[dict]:
    """Validate all outputs in an experiment folder."""
    exp_dirs = list((OUTPUT_ROOT / "Learning" / "experiments").glob(f"{exp_id}_*"))
    if not exp_dirs:
        return [{"error": f"Experiment {exp_id} not found"}]
    
    exp_dir = exp_dirs[0]
    results = []
    
    # Validate findings
    for finding in (exp_dir / "Findings").rglob("*.md"):
        results.append(validate_file(finding, baseline))
    
    # Validate summaries
    for summary in (exp_dir / "Summary").rglob("*.md"):
        results.append(validate_file(summary, baseline))
    
    return results


def print_validation_results(results: list[dict]) -> None:
    """Print validation results."""
    if not results:
        print("No results to display.")
        return
    
    print("== Validation Results ==")
    print()
    
    for r in results:
        if "error" in r:
            print(f"ERROR: {r['error']}")
            continue
        
        path = Path(r["path"]).name
        score = r["overall_score"]
        score_pct = f"{score:.0%}"
        
        # Score emoji
        if score >= 0.9:
            emoji = "🟢"
        elif score >= 0.7:
            emoji = "🟡"
        else:
            emoji = "🔴"
        
        print(f"{emoji} {path}: {score_pct}")
        print(f"   Type: {r['type']}")
        print(f"   Sections: {r['sections_found']}/{r['sections_required']} ({r['sections_score']:.0%})")
        
        if r.get("sections_missing"):
            print(f"   Missing: {', '.join(r['sections_missing'][:3])}")
        
        print(f"   Quality markers: {r['markers_score']:.0%}")
        
        if r.get("todo_count", 0) > 0:
            print(f"   ⚠️  TODOs remaining: {r['todo_count']}")
        
        print()
    
    # Summary
    valid_results = [r for r in results if "error" not in r]
    if valid_results:
        avg_score = sum(r["overall_score"] for r in valid_results) / len(valid_results)
        print(f"Average score: {avg_score:.0%} across {len(valid_results)} files")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate outputs against baseline quality criteria.")
    parser.add_argument("files", nargs="*", help="Files to validate")
    parser.add_argument("--experiment", "-e", help="Validate all outputs in an experiment")
    
    args = parser.parse_args()
    
    baseline = load_baseline()
    if not baseline:
        return 1
    
    results = []
    
    if args.experiment:
        results = validate_experiment(args.experiment, baseline)
    elif args.files:
        for f in args.files:
            path = Path(f)
            if path.exists():
                results.append(validate_file(path, baseline))
            else:
                results.append({"error": f"File not found: {f}"})
    else:
        # Default: validate all outputs
        for path in (OUTPUT_ROOT / "Summary").rglob("*.md"):
            results.append(validate_file(path, baseline))
        for path in (OUTPUT_ROOT / "Findings").rglob("*.md"):
            results.append(validate_file(path, baseline))
    
    print_validation_results(results)
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
