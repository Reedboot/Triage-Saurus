#!/usr/bin/env python3
"""
validate_rule_coverage.py

Validates opengrep rule detection coverage against the PublicAssetTest test suite.

Each .tf/.yaml/.py file in PublicAssetTest corresponds to one security rule:
  - <rule-id>.tf         → positive case: rule MUST fire
  - <rule-id>.pass.tf    → negative case: rule MUST NOT fire (false-positive check)

Usage:
    python3 Scripts/Validate/validate_rule_coverage.py --repo /path/to/PublicAssetTest
    python3 Scripts/Validate/validate_rule_coverage.py --repo /path/to/PublicAssetTest --rules /path/to/Rules

Output:
    - Updates RULE_COVERAGE.md in the repo root
    - Prints a coverage summary to stdout
    - Returns exit code 0 if coverage >= threshold, 1 otherwise
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Coverage threshold (%)
COVERAGE_THRESHOLD = 95.0

# Marker in file path to identify pass (compliant) test cases
PASS_MARKER = ".pass."


def find_opengrep() -> Optional[str]:
    """Locate the opengrep binary."""
    for candidate in ["opengrep", "semgrep"]:
        result = subprocess.run(
            ["which", candidate], capture_output=True, text=True
        )
        if result.returncode == 0:
            return result.stdout.strip()
    return None


def run_scan(opengrep_bin: str, rules_dir: str, repo_path: str) -> dict:
    """Run opengrep scan and return parsed JSON findings."""
    cmd = [
        opengrep_bin,
        "scan",
        "--config", rules_dir,
        "--json",
        "--no-error",
        repo_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode not in (0, 1):  # 1 = findings found (expected)
        print(f"[WARN] opengrep exited with code {result.returncode}", file=sys.stderr)
        if result.stderr:
            print(f"[WARN] stderr: {result.stderr[:500]}", file=sys.stderr)

    # Try to parse JSON from stdout
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        # Try to find JSON block in mixed output
        match = re.search(r'\{.*\}', result.stdout, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        print("[WARN] Could not parse opengrep JSON output", file=sys.stderr)
        return {"results": []}


def build_findings_index(scan_output: dict, repo_path: str) -> Dict[str, List[str]]:
    """
    Build a mapping: normalised_file_path -> [rule_ids that fired on it]
    """
    index: Dict[str, List[str]] = {}
    for finding in scan_output.get("results", []):
        path = finding.get("path", "")
        # Normalise to relative path within repo
        if path.startswith(repo_path):
            path = path[len(repo_path):].lstrip("/")
        rule_id = finding.get("check_id", finding.get("rule_id", ""))
        # Strip leading path prefix from rule_id (e.g. Rules.Misconfigurations.Azure.AKS.azure-aks-...)
        rule_id = rule_id.split(".")[-1] if "." in rule_id else rule_id
        index.setdefault(path, []).append(rule_id)
    return index


def extract_expected_rule_from_file(filepath: str) -> Optional[str]:
    """
    Extract the expected rule ID from the RULE: comment header in the file.
    Falls back to deriving it from the filename.
    """
    try:
        with open(filepath, encoding="utf-8", errors="replace") as f:
            for line in f:
                # Support both # (HCL/YAML/Python/shell) and // (C#/JS/Go) comment styles
                m = re.match(r"(?:#|//)\s*RULE:\s*(.+)", line.strip())
                if m:
                    # Take only the first rule ID if multiple are listed (comma-separated)
                    rule_ids = [r.strip() for r in m.group(1).split(",")]
                    return rule_ids[0]
    except OSError:
        pass

    # Derive from filename: strip extension(s)
    name = Path(filepath).name
    # Remove .pass.tf, .pass.yaml, .tf, .yaml, .json, .env, .cs etc.
    name = re.sub(r"\.pass\.(tf|yaml|yml|json|env|cs|py|js)$", "", name)
    name = re.sub(r"\.(tf|yaml|yml|json|env|cs|py|js)$", "", name)
    return name if name else None


def collect_test_cases(repo_path: str) -> List[Dict]:
    """
    Walk the repo and collect all test case files.
    Returns list of dicts: {
        'relative_path': str,
        'absolute_path': str,
        'is_pass_case': bool,
        'expected_rule': str,
    }
    """
    test_extensions = {".tf", ".yaml", ".yml", ".json", ".env", ".cs", ".py", ".js"}
    skip_dirs = {".git", ".terraform", "node_modules", "__pycache__"}
    cases = []

    for root, dirs, files in os.walk(repo_path):
        # Skip hidden/ignored directories
        dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]

        for fname in files:
            ext = Path(fname).suffix.lower()
            if ext not in test_extensions:
                continue
            if fname in {"README.md", "RULE_COVERAGE.md", "variables.tf", "outputs.tf", "main.tf", "providers.tf"}:
                continue

            abs_path = os.path.join(root, fname)
            rel_path = os.path.relpath(abs_path, repo_path)
            is_pass = PASS_MARKER in fname

            expected_rule = extract_expected_rule_from_file(abs_path)
            if not expected_rule:
                continue

            cases.append({
                "relative_path": rel_path,
                "absolute_path": abs_path,
                "is_pass_case": is_pass,
                "expected_rule": expected_rule,
            })

    return cases


def evaluate_coverage(
    cases: List[Dict],
    findings_index: Dict[str, List[str]],
) -> Tuple[List[Dict], Dict]:
    """
    For each test case, determine if the result is:
      positive case:  DETECTED (✅) or MISSED (❌)
      negative case:  NO_FINDING (🟢) or FALSE_POSITIVE (🔴)

    Returns (annotated_cases, summary_stats)
    """
    results = []
    stats = {
        "total": 0,
        "detected": 0,
        "missed": 0,
        "no_finding": 0,
        "false_positive": 0,
        "coverage_pct": 0.0,
    }

    for case in cases:
        rel_path = case["relative_path"]
        expected_rule = case["expected_rule"]
        is_pass = case["is_pass_case"]

        # Normalize path separators
        rel_path_norm = rel_path.replace("\\", "/")

        # Check if the expected rule fired on this file
        fired_rules = findings_index.get(rel_path_norm, [])
        # Also check with forward slashes
        if not fired_rules:
            fired_rules = findings_index.get(rel_path, [])

        rule_fired = any(
            r == expected_rule or r.endswith(f".{expected_rule}") or expected_rule.endswith(f".{r}")
            for r in fired_rules
        )

        if is_pass:
            status = "🔴 FALSE_POSITIVE" if rule_fired else "🟢 NO_FINDING"
            if rule_fired:
                stats["false_positive"] += 1
            else:
                stats["no_finding"] += 1
        else:
            status = "✅ DETECTED" if rule_fired else "❌ MISSED"
            if rule_fired:
                stats["detected"] += 1
            else:
                stats["missed"] += 1

        stats["total"] += 1
        results.append({**case, "status": status, "fired_rules": fired_rules})

    # Coverage = (correctly detected + correctly silent) / (total positive + total negative)
    correct = stats["detected"] + stats["no_finding"]
    total = stats["total"]
    stats["coverage_pct"] = (correct / total * 100) if total > 0 else 0.0

    return results, stats


def update_rule_coverage_md(
    repo_path: str,
    results: List[Dict],
    stats: Dict,
    scan_timestamp: str,
) -> None:
    """Update RULE_COVERAGE.md in the repo with current scan results."""
    coverage_file = os.path.join(repo_path, "RULE_COVERAGE.md")
    if not os.path.exists(coverage_file):
        print(f"[WARN] RULE_COVERAGE.md not found at {coverage_file}", file=sys.stderr)
        return

    with open(coverage_file, encoding="utf-8") as f:
        content = f.read()

    # Update header stats
    content = re.sub(
        r"\*\*Last Updated:\*\* .*",
        f"**Last Updated:** {scan_timestamp}",
        content,
    )
    content = re.sub(
        r"\*\*Detection Rate:\*\* .*",
        f"**Detection Rate:** {stats['coverage_pct']:.1f}% ({stats['detected']}/{stats['detected'] + stats['missed']} positive cases detected)",
        content,
    )
    content = re.sub(
        r"\*\*False Positive Rate:\*\* .*",
        f"**False Positive Rate:** {stats['false_positive']} false positives out of {stats['no_finding'] + stats['false_positive']} negative cases",
        content,
    )

    # Build lookup: relative_path -> status
    status_lookup: Dict[str, str] = {
        r["relative_path"].replace("\\", "/"): r["status"]
        for r in results
    }

    def replace_status(m: re.Match) -> str:
        """Replace status placeholder '-' in table cells."""
        file_col = m.group(1).strip()
        # Find matching result
        status = status_lookup.get(file_col, "-")
        row = m.group(0)
        # Replace the first '-' in the result column (4th or 5th column)
        # This is a simple approach - replace '| - |' patterns
        return row.replace("| - |", f"| {status} |", 1)

    # Update table rows with actual results
    for result in results:
        rel_path = result["relative_path"].replace("\\", "/")
        status = result["status"]
        # Match table rows containing this file path
        if PASS_MARKER in rel_path:
            # Negative test - update "Negative Result" column
            pattern = re.escape(rel_path)
            content = re.sub(
                rf"(\| {pattern} \|[^|\n]+\|[^|\n]+\|)( - )(\|)",
                rf"\g<1> {status} \g<3>",
                content,
            )
        else:
            # Positive test - update "Positive Result" column
            pattern = re.escape(rel_path)
            content = re.sub(
                rf"(\| {pattern} \|[^|\n]+\|)( - )(\|)",
                rf"\g<1> {status} \g<3>",
                content,
            )

    # Update summary table
    cloud_stats: Dict[str, Dict] = {}
    for result in results:
        cloud = result["relative_path"].split("/")[0] if "/" in result["relative_path"] else "other"
        cs = cloud_stats.setdefault(cloud, {"total": 0, "detected": 0, "missed": 0, "fp": 0, "tn": 0})
        cs["total"] += 1
        if "DETECTED" in result["status"]:
            cs["detected"] += 1
        elif "MISSED" in result["status"]:
            cs["missed"] += 1
        elif "FALSE_POSITIVE" in result["status"]:
            cs["fp"] += 1
        elif "NO_FINDING" in result["status"]:
            cs["tn"] += 1

    with open(coverage_file, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"[OK] Updated {coverage_file}")


def print_summary(stats: Dict, results: List[Dict]) -> None:
    """Print a human-readable coverage summary."""
    pct = stats["coverage_pct"]
    status_icon = "✅" if pct >= COVERAGE_THRESHOLD else "❌"

    print("\n" + "=" * 60)
    print(f"  RULE COVERAGE REPORT — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)
    print(f"  Total test cases:      {stats['total']}")
    print(f"  ✅ Detected:           {stats['detected']}")
    print(f"  ❌ Missed:             {stats['missed']}")
    print(f"  🟢 No false positives: {stats['no_finding']}")
    print(f"  🔴 False positives:    {stats['false_positive']}")
    print(f"\n  {status_icon} Coverage: {pct:.1f}% (threshold: {COVERAGE_THRESHOLD}%)")
    print("=" * 60)

    if stats["missed"] > 0:
        print("\n  ❌ MISSED DETECTIONS (rules needed):")
        for r in results:
            if "MISSED" in r["status"]:
                print(f"    - {r['relative_path']}")
                print(f"      Expected rule: {r['expected_rule']}")

    if stats["false_positive"] > 0:
        print("\n  🔴 FALSE POSITIVES (rule needs fixing):")
        for r in results:
            if "FALSE_POSITIVE" in r["status"]:
                print(f"    - {r['relative_path']}")
                print(f"      Rule that fired: {r['fired_rules']}")
                print(f"      Expected rule: {r['expected_rule']}")


def main():
    parser = argparse.ArgumentParser(
        description="Validate opengrep rule detection coverage against PublicAssetTest"
    )
    parser.add_argument(
        "--repo",
        required=True,
        help="Path to PublicAssetTest repository",
    )
    parser.add_argument(
        "--rules",
        default=None,
        help="Path to Triage-Saurus Rules directory (auto-detected if not provided)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=COVERAGE_THRESHOLD,
        help=f"Minimum coverage %% to pass (default: {COVERAGE_THRESHOLD})",
    )
    parser.add_argument(
        "--no-scan",
        action="store_true",
        help="Skip running opengrep (use existing JSON output)",
    )
    parser.add_argument(
        "--scan-output",
        default=None,
        help="Path to existing opengrep JSON output (used with --no-scan)",
    )
    args = parser.parse_args()

    repo_path = os.path.abspath(args.repo)
    if not os.path.isdir(repo_path):
        print(f"[ERROR] Repo path not found: {repo_path}", file=sys.stderr)
        sys.exit(1)

    # Auto-detect Rules directory
    if args.rules:
        rules_dir = args.rules
    else:
        # Try to find Triage-Saurus Rules relative to this script
        script_dir = Path(__file__).parent
        for candidate in [
            script_dir.parent.parent / "Rules",
            Path.home() / "code" / "Triage-Saurus" / "Rules",
        ]:
            if candidate.is_dir():
                rules_dir = str(candidate)
                break
        else:
            print("[ERROR] Could not auto-detect Rules directory. Use --rules", file=sys.stderr)
            sys.exit(1)

    print(f"[INFO] Repo: {repo_path}")
    print(f"[INFO] Rules: {rules_dir}")

    scan_timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    # Run scan or load existing output
    if args.no_scan and args.scan_output:
        print(f"[INFO] Loading existing scan output from {args.scan_output}")
        with open(args.scan_output) as f:
            scan_output = json.load(f)
    else:
        opengrep_bin = find_opengrep()
        if not opengrep_bin:
            print("[ERROR] opengrep/semgrep not found on PATH", file=sys.stderr)
            sys.exit(1)
        print(f"[INFO] Running {opengrep_bin} scan...")
        scan_output = run_scan(opengrep_bin, rules_dir, repo_path)

    # Build findings index
    findings_index = build_findings_index(scan_output, repo_path)
    print(f"[INFO] Found {len(scan_output.get('results', []))} findings across {len(findings_index)} files")

    # Collect test cases
    print("[INFO] Collecting test cases...")
    cases = collect_test_cases(repo_path)
    positive_cases = [c for c in cases if not c["is_pass_case"]]
    negative_cases = [c for c in cases if c["is_pass_case"]]
    print(f"[INFO] Found {len(positive_cases)} positive tests, {len(negative_cases)} negative tests")

    # Evaluate coverage
    results, stats = evaluate_coverage(cases, findings_index)

    # Print summary
    print_summary(stats, results)

    # Update RULE_COVERAGE.md
    update_rule_coverage_md(repo_path, results, stats, scan_timestamp)

    # Exit with appropriate code
    if stats["coverage_pct"] < args.threshold:
        print(f"\n[FAIL] Coverage {stats['coverage_pct']:.1f}% is below threshold {args.threshold}%")
        sys.exit(1)
    else:
        print(f"\n[PASS] Coverage {stats['coverage_pct']:.1f}% meets threshold {args.threshold}%")
        sys.exit(0)


if __name__ == "__main__":
    main()
