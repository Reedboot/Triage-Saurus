#!/usr/bin/env python3
"""
run_pipeline.py — Offline Phase 1-3c pipeline runner.

Runs the complete AI-free triage pipeline against a repository:
  Phase 1 — opengrep detection + targeted misconfiguration scan → findings in DB
  Phase 2 — Script-based code context discovery → metadata in DB + repo summary MD
  Phase 3a — Internet exposure analysis → exposure findings + provider diagrams
  Phase 3b — Render finding MDs from DB
  Phase 3c — Render architecture diagram from DB

No LLM or internet access required.

Usage:
    python3 Scripts/Utils/run_pipeline.py --repo /path/to/repo [--name my_scan]

Examples:
    python3 Scripts/Utils/run_pipeline.py --repo /home/user/my-k8s-repo
    python3 Scripts/Utils/run_pipeline.py --repo /home/user/my-k8s-repo --name baseline_v1
    python3 Scripts/Utils/run_pipeline.py --repo /home/user/my-k8s-repo --name recheck --experiment 005

Output:
    Output/Learning/experiments/<id>_<name>/
    ├── Summary/
    │   ├── Cloud/Architecture_AWS.md     ← layered architecture diagram
    │   └── Repos/<repo-name>.md          ← languages, frameworks, K8s context
    ├── Findings/
    │   └── <repo-name>/
    │       └── <finding-title>.md        ← one MD per finding
    └── scan_<repo-name>.json             ← raw opengrep results
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent  # Scripts/Utils → Scripts/
REPO_ROOT = SCRIPTS.parent                         # Scripts/ → repo root
sys.path.insert(0, str(SCRIPTS / "Persist"))
sys.path.insert(0, str(SCRIPTS / "Utils"))

_EXPERIMENTS   = SCRIPTS / "Experiments" / "triage_experiment.py"
_DISCOVER      = SCRIPTS / "Context"     / "discover_code_context.py"
_ANALYZE_EXPOSURE = SCRIPTS / "Analyze"  / "exposure_analyzer.py"
_RENDER_EXPOSURE = SCRIPTS / "Generate" / "render_exposure_summary.py"
_RENDER        = SCRIPTS / "Generate"    / "render_finding.py"
_GEN_DIAGRAM   = SCRIPTS / "Generate"   / "generate_diagram.py"

from db_helpers import get_db_connection


def _run(cmd: list[str], label: str) -> int:
    """Run a subprocess, stream output, return exit code.

    Forces subprocesses to include the repo Scripts/ paths on PYTHONPATH and
    ensures they run from the repo root. This avoids import errors for
    db_helpers and other internal modules when scripts are invoked as
    subprocesses.
    """
    import os

    print(f"\n{'─'*60}")
    print(f"▶  {label}")
    print(f"   {' '.join(cmd)}")
    print('─'*60)

    # Build environment with PYTHONPATH including Scripts and its subfolders
    env = os.environ.copy()
    existing = env.get('PYTHONPATH', '')
    paths = [str(SCRIPTS), str(SCRIPTS / 'Persist'), str(SCRIPTS / 'Utils')]
    if existing:
        paths.append(existing)
    env['PYTHONPATH'] = ':'.join(paths)

    result = subprocess.run(cmd, cwd=str(REPO_ROOT), env=env)
    if result.returncode != 0:
        print(f"\n[ERROR] {label} exited with code {result.returncode}", file=sys.stderr)
    return result.returncode


def _get_experiment_findings(experiment_id: str) -> list[int]:
    """Return all finding IDs for the experiment."""
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT id FROM findings WHERE experiment_id = ? ORDER BY id",
            (experiment_id,),
        ).fetchall()
    return [row[0] for row in rows]


def _resolve_experiment_dir(experiment_id: str) -> Path | None:
    experiments = REPO_ROOT / "Output" / "Learning" / "experiments"
    candidates = sorted(experiments.glob(f"{experiment_id}_*"))
    return candidates[0] if candidates else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Offline Phase 1-3 pipeline: detection → code context → render MDs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--repo",
        required=True,
        help="Absolute or relative path to the repository to scan.",
    )
    parser.add_argument(
        "--name",
        default="offline_scan",
        help="Experiment name suffix (default: offline_scan).",
    )
    parser.add_argument(
        "--experiment",
        default=None,
        help="Reuse an existing experiment ID instead of creating a new one.",
    )
    parser.add_argument(
        "--skip-phase1",
        action="store_true",
        help="Skip Phase 1 (use if findings already in DB for this experiment).",
    )
    parser.add_argument(
        "--skip-phase2",
        action="store_true",
        help="Skip Phase 2 (use if context_metadata already populated).",
    )
    parser.add_argument(
        "--no-opengrep",
        action="store_true",
        help="Skip opengrep in Phase 2 (file parsing only).",
    )
    args = parser.parse_args()

    repo_path = Path(args.repo).resolve()
    if not repo_path.is_dir():
        print(f"[ERROR] Not a directory: {repo_path}", file=sys.stderr)
        return 1

    repo_name = repo_path.name

    # ── Create or reuse experiment ────────────────────────────────────────────
    if args.experiment:
        experiment_id = args.experiment
        exp_dir = _resolve_experiment_dir(experiment_id)
        if not exp_dir:
            print(f"[ERROR] Experiment {experiment_id} not found.", file=sys.stderr)
            return 1
        print(f"\n[Pipeline] Reusing experiment {experiment_id} at {exp_dir}")
    else:
        print(f"\n[Pipeline] Creating new experiment: {args.name}")
        result = subprocess.run(
            [sys.executable, str(_EXPERIMENTS), "new", args.name,
             "--repos", str(repo_path)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )
        print(result.stdout)
        if result.returncode != 0:
            print(result.stderr, file=sys.stderr)
            return 1
        # Parse experiment ID from output ("Created experiment: 005_...")
        experiment_id = None
        for line in result.stdout.splitlines():
            if line.startswith("Created experiment:"):
                tag = line.split(":", 1)[1].strip()
                experiment_id = tag.split("_")[0]
                break
        if not experiment_id:
            print("[ERROR] Could not parse experiment ID from output.", file=sys.stderr)
            return 1
        exp_dir = _resolve_experiment_dir(experiment_id)

    print(f"\n{'='*60}")
    print(f"  Experiment : {experiment_id}")
    print(f"  Repo       : {repo_path}")
    print(f"  Output dir : {exp_dir}")
    print(f"{'='*60}")

    # ── Phase 1: Detection + targeted scan + store findings ───────────────────
    if not args.skip_phase1:
        rc = _run(
            [sys.executable, str(_EXPERIMENTS), "run", experiment_id],
            "Phase 1 — Detection scan + targeted misconfig scan",
        )
        if rc != 0:
            print("[WARN] Phase 1 exited non-zero — findings may be partial.", file=sys.stderr)
    else:
        print("\n[Pipeline] Skipping Phase 1 (--skip-phase1)")

    # ── Phase 2: Code context discovery ──────────────────────────────────────
    if not args.skip_phase2:
        phase2_cmd = [
            sys.executable, str(_DISCOVER),
            "--experiment", experiment_id,
            "--repo", repo_name,
            "--target", str(repo_path),
            "--output-dir", str(exp_dir),
        ]
        if args.no_opengrep:
            phase2_cmd.append("--no-opengrep")
        rc = _run(phase2_cmd, "Phase 2 — Script-based code context discovery")
        if rc != 0:
            print("[WARN] Phase 2 exited non-zero — metadata may be partial.", file=sys.stderr)
    else:
        print("\n[Pipeline] Skipping Phase 2 (--skip-phase2)")

    # ── Phase 3a: Internet Exposure Analysis ─────────────────────────────────────
    print(f"\n{'─'*60}")
    print(f"▶  Phase 3a — Internet Exposure Analysis")
    print('─'*60)
    
    # Run exposure analyzer
    import os
    env = os.environ.copy()
    existing = env.get('PYTHONPATH', '')
    paths = [str(SCRIPTS), str(SCRIPTS / 'Persist'), str(SCRIPTS / 'Utils'), str(SCRIPTS / 'Analyze')]
    if existing:
        paths.append(existing)
    env['PYTHONPATH'] = ':'.join(paths)
    
    result = subprocess.run(
        [sys.executable, str(_ANALYZE_EXPOSURE), "--experiment", experiment_id],
        cwd=str(REPO_ROOT),
        env=env,
    )
    if result.returncode != 0:
        print("[WARN] Phase 3a (exposure analysis) exited non-zero.", file=sys.stderr)
    
    # Render exposure summaries
    exposure_cloud_dir = exp_dir / "Summary" / "Cloud"
    exposure_cloud_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, str(_RENDER_EXPOSURE), "--experiment", experiment_id,
         "--output-dir", str(exposure_cloud_dir)],
        cwd=str(REPO_ROOT),
        env=env,
    )
    if result.returncode != 0:
        print("[WARN] Phase 3a (exposure rendering) exited non-zero.", file=sys.stderr)

    # ── Phase 3b: Render finding MDs ─────────────────────────────────────────
    finding_ids = _get_experiment_findings(experiment_id)
    if finding_ids:
        print(f"\n{'─'*60}")
        print(f"▶  Phase 3b — Render {len(finding_ids)} finding MD(s)")
        print('─'*60)
        findings_dir = exp_dir / "Findings" / repo_name
        findings_dir.mkdir(parents=True, exist_ok=True)
        ok = skipped = 0
        for fid in finding_ids:
            out_path = findings_dir / f"finding_{fid}.md"
            # Ensure subprocess has correct PYTHONPATH so internal modules import
            import os
            env = os.environ.copy()
            existing = env.get('PYTHONPATH', '')
            paths = [str(SCRIPTS), str(SCRIPTS / 'Persist'), str(SCRIPTS / 'Utils')]
            if existing:
                paths.append(existing)
            env['PYTHONPATH'] = ':'.join(paths)
            result = subprocess.run(
                [sys.executable, str(_RENDER), "--id", str(fid), "--out", str(out_path)],
                cwd=str(REPO_ROOT),
                env=env,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                print(f"  ✓ [{fid}] → {out_path.relative_to(REPO_ROOT)}")
                ok += 1
            else:
                # Print first stderr line for succinct diagnostics
                err = result.stderr.strip().splitlines()[0] if result.stderr else ''
                print(f"  ✗ [{fid}] {err[:200]}")
                skipped += 1
        print(f"\n  Rendered: {ok}  Failed: {skipped}")
    else:
        print("\n[Pipeline] No findings in DB for this experiment — skipping render.")

    # ── Phase 3c: Architecture diagram ───────────────────────────────────────
    cloud_dir = exp_dir / "Summary" / "Cloud"
    cloud_dir.mkdir(parents=True, exist_ok=True)
    # Generate per-provider architecture files (Architecture_AWS.md, Architecture_Azure.md, ...)
    _run(
        [sys.executable, str(_GEN_DIAGRAM), experiment_id,
         "--split-by-provider", "--output", str(cloud_dir)],
        "Phase 3b — Generate architecture diagrams (per-provider)",
    )

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  ✅ Pipeline complete (no LLM used)")
    print(f"{'='*60}")
    print(f"\n  Experiment ID : {experiment_id}")
    print(f"  Findings      : {len(finding_ids)} (see {exp_dir}/Findings/)")
    print(f"  Repo summary  : {exp_dir}/Summary/Repos/{repo_name}.md")
    print(f"  Architectures : {cloud_dir}/Architecture_<PROVIDER>.md (per-provider files written)")
    print(f"\n  Next steps (require LLM):")
    print(f"    python3 Scripts/Enrich/enrich_findings.py --experiment {experiment_id}")
    print(f"    python3 Scripts/run_skeptics.py --experiment {experiment_id} --reviewer all")
    print(f"    python3 Scripts/triage_experiment.py complete {experiment_id}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
