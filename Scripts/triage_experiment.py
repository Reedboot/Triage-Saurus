#!/usr/bin/env python3
"""Experiment management CLI for Triage-Saurus.

Main entry point for the experiment/learning system.

Usage:
    python3 Scripts/triage_experiment.py resume
    python3 Scripts/triage_experiment.py new <name> --repos <repo1> <repo2>
    python3 Scripts/triage_experiment.py run <id>
    python3 Scripts/triage_experiment.py list
    python3 Scripts/triage_experiment.py status
    python3 Scripts/triage_experiment.py review <id>
    python3 Scripts/triage_experiment.py compare <id1> <id2>
    python3 Scripts/triage_experiment.py learn <id>
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

from output_paths import OUTPUT_ROOT, REPO_ROOT
import learning_db as db

LEARNING_DIR = OUTPUT_ROOT / "Learning"
STATE_FILE = LEARNING_DIR / "state.json"
EXPERIMENTS_DIR = LEARNING_DIR / "experiments"
STRATEGIES_DIR = LEARNING_DIR / "strategies"
AGENTS_SOURCE = REPO_ROOT / "Agents"
SCRIPTS_SOURCE = REPO_ROOT / "Scripts"


def load_state() -> dict:
    """Load current state from state.json."""
    if not STATE_FILE.exists():
        return {
            "current_experiment_id": None,
            "status": "fresh",
            "next_action": "Run 'triage experiment new <name>' to start first experiment",
            "repos_in_scope": [],
            "experiment_history": [],
            "convergence_tracking": {"improvements": [], "converged": False},
            "checkpoint": None,
            "last_updated": None,
            "last_session_id": None,
            "handoff_notes": "No experiments yet. Ready to start.",
        }
    return json.loads(STATE_FILE.read_text())


def save_state(state: dict) -> None:
    """Save state to state.json."""
    state["last_updated"] = datetime.now().isoformat()
    LEARNING_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def get_next_experiment_id() -> str:
    """Get the next experiment ID (001, 002, etc.)."""
    existing = sorted(EXPERIMENTS_DIR.glob("*")) if EXPERIMENTS_DIR.exists() else []
    existing_ids = []
    for p in existing:
        if p.is_dir():
            try:
                # Extract numeric prefix from folder name like "001_baseline"
                num = int(p.name.split("_")[0])
                existing_ids.append(num)
            except (ValueError, IndexError):
                pass
    next_num = max(existing_ids, default=0) + 1
    return f"{next_num:03d}"


def cmd_resume(args: argparse.Namespace) -> int:
    """Resume from current state."""
    state = load_state()
    
    print("== Triage Experiment Status ==")
    print()
    
    if state["status"] == "fresh":
        print("No experiments yet.")
        print()
        print("To start, run:")
        print("  python3 Scripts/triage_experiment.py new baseline --repos <repo1> <repo2>")
        return 0
    
    print(f"Current experiment: {state.get('current_experiment_id')}")
    print(f"Status: {state.get('status')}")
    print()
    
    if state.get("next_action"):
        print(f"Next action: {state['next_action']}")
        print()
    
    if state.get("repos_in_scope"):
        print(f"Repos in scope: {', '.join(state['repos_in_scope'])}")
        print()
    
    if state.get("checkpoint"):
        cp = state["checkpoint"]
        print("Checkpoint (interrupted run):")
        print(f"  Completed: {', '.join(cp.get('repos_completed', []))}")
        print(f"  Pending: {', '.join(cp.get('repos_pending', []))}")
        print(f"  Current phase: {cp.get('current_phase')}")
        print()
    
    if state.get("handoff_notes"):
        print(f"Notes: {state['handoff_notes']}")
    
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    """Create a new experiment."""
    state = load_state()
    
    exp_id = get_next_experiment_id()
    exp_name = f"{exp_id}_{args.name}"
    exp_dir = EXPERIMENTS_DIR / exp_name
    
    if exp_dir.exists():
        print(f"ERROR: Experiment directory already exists: {exp_dir}")
        return 1
    
    # Create directory structure
    exp_dir.mkdir(parents=True)
    (exp_dir / "Findings" / "Cloud").mkdir(parents=True)
    (exp_dir / "Findings" / "Code").mkdir(parents=True)
    (exp_dir / "Knowledge").mkdir()
    (exp_dir / "Summary").mkdir()
    (exp_dir / "Agents").mkdir()
    (exp_dir / "Scripts").mkdir()
    
    # Copy agent instructions
    for agent_file in AGENTS_SOURCE.glob("*.md"):
        shutil.copy(agent_file, exp_dir / "Agents" / agent_file.name)
    
    # Create changes.md to track modifications
    (exp_dir / "Agents" / "changes.md").write_text(
        f"# Agent Changes for Experiment {exp_id}\n\n"
        f"## Created\n"
        f"- {datetime.now().strftime('%Y-%m-%d %H:%M')} — Initial copy from Agents/\n\n"
        f"## Modifications\n"
        f"*No modifications yet*\n"
    )
    
    # Create experiment.json
    strategy = json.loads((STRATEGIES_DIR / "default.json").read_text()) if (STRATEGIES_DIR / "default.json").exists() else {}
    
    exp_config = {
        "id": exp_id,
        "name": args.name,
        "full_name": exp_name,
        "status": "pending",
        "strategy": strategy,
        "repos": args.repos or [],
        "created_at": datetime.now().isoformat(),
        "started_at": None,
        "completed_at": None,
        "metrics": {},
    }
    (exp_dir / "experiment.json").write_text(json.dumps(exp_config, indent=2))
    
    # Create validation.json placeholder
    (exp_dir / "validation.json").write_text(json.dumps({
        "experiment_id": exp_id,
        "human_feedback": {},
        "overall_accuracy": None,
        "reviewed_at": None,
    }, indent=2))
    
    # Record in database
    db.create_experiment(exp_id, args.name, args.repos or [], strategy.get("version", "default"))
    
    # Update state
    state["current_experiment_id"] = exp_id
    state["status"] = "pending"
    state["next_action"] = f"Run 'triage experiment run {exp_id}' to execute the experiment"
    state["repos_in_scope"] = args.repos or []
    state["experiment_history"].append({
        "id": exp_id,
        "name": args.name,
        "status": "pending",
        "created_at": datetime.now().isoformat(),
    })
    state["handoff_notes"] = f"Experiment {exp_id} created. Ready to run."
    save_state(state)
    
    print(f"Created experiment: {exp_name}")
    print(f"Directory: {exp_dir}")
    print()
    print("Next steps:")
    print(f"  1. Review/modify agents in: {exp_dir / 'Agents'}")
    print(f"  2. Run the experiment: python3 Scripts/triage_experiment.py run {exp_id}")
    
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    """List all experiments."""
    if not EXPERIMENTS_DIR.exists():
        print("No experiments yet.")
        return 0
    
    print("== Experiments ==")
    print()
    print(f"{'ID':<5} {'Name':<25} {'Status':<15} {'Findings':<10} {'Accuracy':<10}")
    print("-" * 70)
    
    for exp_dir in sorted(EXPERIMENTS_DIR.iterdir()):
        if not exp_dir.is_dir():
            continue
        
        config_file = exp_dir / "experiment.json"
        if not config_file.exists():
            continue
        
        config = json.loads(config_file.read_text())
        
        exp_id = config.get("id", "?")
        name = config.get("name", exp_dir.name)[:25]
        status = config.get("status", "?")
        
        metrics = config.get("metrics", {})
        findings = metrics.get("findings_count", "-")
        accuracy = metrics.get("accuracy_rate")
        accuracy_str = f"{accuracy:.0%}" if accuracy else "-"
        
        print(f"{exp_id:<5} {name:<25} {status:<15} {findings:<10} {accuracy_str:<10}")
    
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Show detailed status."""
    # Show state.json status
    cmd_resume(args)
    print()
    
    # Show database status
    db.print_status()
    
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Execute an experiment (placeholder - actual scanning done by agents)."""
    state = load_state()
    
    # Find experiment directory
    exp_dirs = list(EXPERIMENTS_DIR.glob(f"{args.id}_*"))
    if not exp_dirs:
        print(f"ERROR: Experiment {args.id} not found")
        return 1
    
    exp_dir = exp_dirs[0]
    config_file = exp_dir / "experiment.json"
    config = json.loads(config_file.read_text())
    
    if config.get("status") not in ("pending", "running"):
        print(f"Experiment {args.id} is already {config.get('status')}. Cannot re-run.")
        print("Create a new experiment instead.")
        return 1
    
    # Update status
    config["status"] = "running"
    config["started_at"] = datetime.now().isoformat()
    config_file.write_text(json.dumps(config, indent=2))
    
    db.update_experiment(args.id, status="running", started_at=datetime.now().isoformat())
    
    state["status"] = "running"
    state["next_action"] = "Scans in progress. Wait for completion or use 'triage resume' to check status."
    state["handoff_notes"] = f"Experiment {args.id} running."
    save_state(state)
    
    print(f"Experiment {args.id} marked as running.")
    print()
    print("The actual scanning should be performed by the agent using:")
    print(f"  - Agent instructions from: {exp_dir / 'Agents'}")
    print(f"  - Output findings to: {exp_dir / 'Findings'}")
    print(f"  - Update knowledge in: {exp_dir / 'Knowledge'}")
    print()
    print("When scanning is complete, mark it done with:")
    print(f"  python3 Scripts/triage_experiment.py complete {args.id}")
    
    return 0


def cmd_complete(args: argparse.Namespace) -> int:
    """Mark an experiment as completed."""
    state = load_state()
    
    exp_dirs = list(EXPERIMENTS_DIR.glob(f"{args.id}_*"))
    if not exp_dirs:
        print(f"ERROR: Experiment {args.id} not found")
        return 1
    
    exp_dir = exp_dirs[0]
    config_file = exp_dir / "experiment.json"
    config = json.loads(config_file.read_text())
    
    # Count findings
    findings_count = len(list((exp_dir / "Findings").rglob("*.md")))
    
    # Update config
    config["status"] = "completed"
    config["completed_at"] = datetime.now().isoformat()
    config["metrics"]["findings_count"] = findings_count
    
    if config.get("started_at"):
        started = datetime.fromisoformat(config["started_at"])
        duration = (datetime.now() - started).total_seconds()
        config["metrics"]["duration_sec"] = int(duration)
    
    config_file.write_text(json.dumps(config, indent=2))
    
    db.update_experiment(
        args.id,
        status="completed",
        completed_at=datetime.now().isoformat(),
        findings_count=findings_count,
        duration_sec=config["metrics"].get("duration_sec"),
    )
    
    state["status"] = "awaiting_review"
    state["next_action"] = f"Run 'triage experiment review {args.id}' to review findings"
    state["handoff_notes"] = f"Experiment {args.id} completed with {findings_count} findings. Ready for review."
    save_state(state)
    
    print(f"Experiment {args.id} marked as completed.")
    print(f"Findings: {findings_count}")
    print()
    print(f"Next: python3 Scripts/triage_experiment.py review {args.id}")
    
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    """Interactive review of experiment findings (placeholder)."""
    exp_dirs = list(EXPERIMENTS_DIR.glob(f"{args.id}_*"))
    if not exp_dirs:
        print(f"ERROR: Experiment {args.id} not found")
        return 1
    
    exp_dir = exp_dirs[0]
    findings_dir = exp_dir / "Findings"
    
    findings = list(findings_dir.rglob("*.md"))
    
    print(f"== Review Experiment {args.id} ==")
    print()
    print(f"Findings to review: {len(findings)}")
    print()
    
    for i, finding in enumerate(findings, 1):
        rel_path = finding.relative_to(findings_dir)
        print(f"  [{i}] {rel_path}")
    
    print()
    print("To record feedback, edit:")
    print(f"  {exp_dir / 'validation.json'}")
    print()
    print("Or use the interactive reviewer (coming soon).")
    
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    """Compare two experiments."""
    exp1_dirs = list(EXPERIMENTS_DIR.glob(f"{args.id1}_*"))
    exp2_dirs = list(EXPERIMENTS_DIR.glob(f"{args.id2}_*"))
    
    if not exp1_dirs or not exp2_dirs:
        print("ERROR: One or both experiments not found")
        return 1
    
    exp1_dir, exp2_dir = exp1_dirs[0], exp2_dirs[0]
    config1 = json.loads((exp1_dir / "experiment.json").read_text())
    config2 = json.loads((exp2_dir / "experiment.json").read_text())
    
    print(f"== Comparison: {args.id1} vs {args.id2} ==")
    print()
    
    m1 = config1.get("metrics", {})
    m2 = config2.get("metrics", {})
    
    print(f"{'Metric':<20} {args.id1:<15} {args.id2:<15} {'Delta':<15}")
    print("-" * 65)
    
    for metric in ["duration_sec", "findings_count", "accuracy_rate"]:
        v1 = m1.get(metric, "-")
        v2 = m2.get(metric, "-")
        
        if isinstance(v1, (int, float)) and isinstance(v2, (int, float)) and v1 != 0:
            delta = ((v2 - v1) / v1) * 100
            delta_str = f"{delta:+.1f}%"
        else:
            delta_str = "-"
        
        print(f"{metric:<20} {str(v1):<15} {str(v2):<15} {delta_str:<15}")
    
    print()
    
    # Compare findings
    findings1 = {f.name for f in (exp1_dir / "Findings").rglob("*.md")}
    findings2 = {f.name for f in (exp2_dir / "Findings").rglob("*.md")}
    
    only_in_1 = findings1 - findings2
    only_in_2 = findings2 - findings1
    in_both = findings1 & findings2
    
    print(f"Findings in both: {len(in_both)}")
    print(f"Only in {args.id1}: {len(only_in_1)}")
    print(f"Only in {args.id2}: {len(only_in_2)}")
    
    if only_in_1:
        print(f"\nOnly in {args.id1}:")
        for f in sorted(only_in_1)[:5]:
            print(f"  - {f}")
        if len(only_in_1) > 5:
            print(f"  ... and {len(only_in_1) - 5} more")
    
    if only_in_2:
        print(f"\nOnly in {args.id2}:")
        for f in sorted(only_in_2)[:5]:
            print(f"  - {f}")
        if len(only_in_2) > 5:
            print(f"  ... and {len(only_in_2) - 5} more")
    
    return 0


def cmd_learn(args: argparse.Namespace) -> int:
    """Apply learnings from experiment feedback."""
    state = load_state()
    
    exp_dirs = list(EXPERIMENTS_DIR.glob(f"{args.id}_*"))
    if not exp_dirs:
        print(f"ERROR: Experiment {args.id} not found")
        return 1
    
    exp_dir = exp_dirs[0]
    validation_file = exp_dir / "validation.json"
    
    if not validation_file.exists():
        print("No validation feedback found.")
        print(f"First run: python3 Scripts/triage_experiment.py review {args.id}")
        return 1
    
    validation = json.loads(validation_file.read_text())
    feedback = validation.get("human_feedback", {})
    
    if not feedback:
        print("No human feedback recorded yet.")
        return 0
    
    print(f"== Learning from Experiment {args.id} ==")
    print()
    print(f"Feedback items: {len(feedback)}")
    print()
    
    # Analyze feedback
    corrections = []
    for finding, fb in feedback.items():
        if fb.get("learning"):
            corrections.append({
                "finding": finding,
                "verdict": fb.get("verdict"),
                "learning": fb.get("learning"),
            })
    
    if corrections:
        print("Proposed changes:")
        for c in corrections:
            print(f"  - {c['finding']}: {c['learning']}")
        print()
        print("Apply these changes to the next experiment manually,")
        print("or wait for automated learning (coming soon).")
    else:
        print("No actionable learnings identified.")
    
    # Update state
    state["status"] = "learned"
    state["next_action"] = f"Create next experiment: python3 Scripts/triage_experiment.py new optimized_v{int(args.id)+1}"
    save_state(state)
    
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Triage-Saurus Experiment Management",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # resume
    subparsers.add_parser("resume", help="Resume from current state")
    
    # new
    new_parser = subparsers.add_parser("new", help="Create new experiment")
    new_parser.add_argument("name", help="Experiment name (e.g., baseline, optimized_v1)")
    new_parser.add_argument("--repos", nargs="+", default=[], help="Repos to scan")
    
    # list
    subparsers.add_parser("list", help="List all experiments")
    
    # status
    subparsers.add_parser("status", help="Show detailed status")
    
    # run
    run_parser = subparsers.add_parser("run", help="Start running an experiment")
    run_parser.add_argument("id", help="Experiment ID (e.g., 001)")
    
    # complete
    complete_parser = subparsers.add_parser("complete", help="Mark experiment as completed")
    complete_parser.add_argument("id", help="Experiment ID")
    
    # review
    review_parser = subparsers.add_parser("review", help="Review experiment findings")
    review_parser.add_argument("id", help="Experiment ID")
    
    # compare
    compare_parser = subparsers.add_parser("compare", help="Compare two experiments")
    compare_parser.add_argument("id1", help="First experiment ID")
    compare_parser.add_argument("id2", help="Second experiment ID")
    
    # learn
    learn_parser = subparsers.add_parser("learn", help="Apply learnings from feedback")
    learn_parser.add_argument("id", help="Experiment ID")
    
    args = parser.parse_args()
    
    commands = {
        "resume": cmd_resume,
        "new": cmd_new,
        "list": cmd_list,
        "status": cmd_status,
        "run": cmd_run,
        "complete": cmd_complete,
        "review": cmd_review,
        "compare": cmd_compare,
        "learn": cmd_learn,
    }
    
    return commands[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
