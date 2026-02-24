#!/usr/bin/env python3
"""Experiment management CLI for Triage-Saurus.

Main entry point for the experiment/learning system.

Usage:
    python3 Scripts/triage_experiment.py resume
    python3 Scripts/triage_experiment.py new <name>                    # prompts for repos interactively
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
import hashlib
import json
import shutil
import subprocess
import sys
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


def compute_file_hash(path: Path) -> str:
    """Compute MD5 hash of a file for version tracking."""
    return hashlib.md5(path.read_bytes()).hexdigest()[:12]


def compute_agents_version() -> dict:
    """Compute version info for all agent files.
    
    Returns dict with:
    - combined_hash: Single hash representing all agents
    - files: Dict of filename -> hash for individual tracking
    """
    hashes = {}
    for agent_file in sorted(AGENTS_SOURCE.glob("*.md")):
        hashes[agent_file.name] = compute_file_hash(agent_file)
    
    # Combined hash of all individual hashes
    combined = hashlib.md5("".join(hashes.values()).encode()).hexdigest()[:12]
    
    return {
        "combined_hash": combined,
        "files": hashes,
    }


def ensure_default_strategy() -> dict:
    """Ensure Output/Learning/strategies/default.json exists and return its contents."""
    STRATEGIES_DIR.mkdir(parents=True, exist_ok=True)
    default_path = STRATEGIES_DIR / "default.json"
    if not default_path.exists():
        default_path.write_text(
            json.dumps(
                {
                    "version": "default",
                    "experiment": {
                        "auto_phase1_context_discovery": True,
                        "auto_generate_experiment_architecture": True,
                        "architecture_requirements": {
                            "include_tldr": True,
                            "include_high_level_diagram": True,
                            "include_risk_level_labels": True,
                            "keep_diagrams_simple_one_per_service_type": True,
                            "layout": {
                                "title_icon": "🗺️",
                                "diagram_first": True,
                                "overview_after_diagram": True,
                                "tldr_after_overview": True,
                                "omit_diagram_subheader": True,
                                "include_diagram_key": True,
                            },
                        },
                        "diagram_styling": {
                            "colors": {
                                "security_gateway_stroke": "#ff6b6b",
                                "app_stroke": "#0066cc",
                                "identity_secrets_stroke": "#f59f00",
                                "data_stroke": "#666666",
                                "pipeline_stroke": "#f59f00",
                                "api_gateway_stroke": "#1971c2",
                            }
                        },
                    },
                    "repo_inventory": {"dedupe_by_repo_name": True},
                    "code_finding_conventions": {
                        "title_no_underscores": True,
                        "explain_authn_authz": True,
                        "key_evidence": {
                            "prefer_snippet_when_concentrated": True,
                            "show_file_and_approx_lines_outside_fence": True,
                            "omit_redundant_evidence_pointers": True,
                        },
                        "diagram": {"highlight_broken_control_red_border": True},
                        "include_poc_and_possible_fix": True,
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    try:
        return json.loads(default_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"version": "default"}


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


def discover_repos(repos_root: Path | None = None) -> list[str]:
    """Discover available repos in the repos root directory."""
    if repos_root is None:
        # Try to infer from parent of REPO_ROOT
        repos_root = REPO_ROOT.parent
    
    if not repos_root.exists():
        return []
    
    repos = []
    for p in sorted(repos_root.iterdir()):
        if p.is_dir() and not p.name.startswith(".") and (p / ".git").exists():
            repos.append(p.name)
    return repos


def get_repos_root_from_knowledge() -> Path | None:
    """Try to read repos root from Knowledge/Repos.md."""
    knowledge_file = OUTPUT_ROOT / "Knowledge" / "Repos.md"
    if not knowledge_file.exists():
        return None
    
    text = knowledge_file.read_text(encoding="utf-8", errors="replace")
    # Look for: **Repo root directory:** `/path/to/repos`
    import re
    match = re.search(r"\*\*Repo root directory:\*\*\s*`([^`]+)`", text)
    if match:
        return Path(match.group(1))
    return None


def prompt_for_repos_root() -> Path:
    """Prompt user to confirm or enter repos root directory."""
    # Try knowledge file first
    known_root = get_repos_root_from_knowledge()
    if known_root and known_root.exists():
        print(f"Repos root from Knowledge/Repos.md: {known_root}")
        confirm = input("Use this path? [Y/n]: ").strip().lower()
        if not confirm or confirm == "y":
            return known_root
    
    # Suggest based on parent of current repo
    suggested = REPO_ROOT.parent
    print(f"Suggested repos root: {suggested}")
    user_input = input(f"Enter repos root path (or press Enter to use suggested): ").strip()
    
    if user_input:
        return Path(user_input).expanduser().resolve()
    return suggested


def prompt_for_repos() -> list[str]:
    """Interactively prompt user to select repos for experiment."""
    # Get repos root - either from knowledge, suggestion, or user input
    repos_root = prompt_for_repos_root()
    
    if not repos_root.exists():
        print(f"ERROR: Path does not exist: {repos_root}")
        return []
    
    available = discover_repos(repos_root)
    if not available:
        print(f"No git repos found in {repos_root}")
        print("Enter repo names manually (comma-separated):")
        user_input = input("> ").strip()
        if not user_input:
            return []
        return [r.strip() for r in user_input.split(",") if r.strip()]
    
    print(f"\nAvailable repos in {repos_root}:")
    print("-" * 40)
    
    # Group by type (terraform-* vs others)
    infra_repos = [r for r in available if r.startswith("terraform-")]
    app_repos = [r for r in available if not r.startswith("terraform-")]
    
    if app_repos:
        print("\nApplication repos:")
        for i, repo in enumerate(app_repos, 1):
            print(f"  {i}. {repo}")
    
    if infra_repos:
        print(f"\nInfrastructure repos ({len(infra_repos)} terraform-* repos):")
        print(f"  (Enter 'terraform-*' to select all)")
        for i, repo in enumerate(infra_repos, len(app_repos) + 1):
            print(f"  {i}. {repo}")
    
    print("\n" + "-" * 40)
    print("Enter repo names or numbers (comma-separated), or 'terraform-*' for all IaC:")
    user_input = input("> ").strip()
    
    if not user_input:
        return []
    
    # Handle special patterns
    if user_input == "terraform-*":
        return infra_repos
    
    selected = []
    all_repos = app_repos + infra_repos
    
    for item in user_input.split(","):
        item = item.strip()
        if not item:
            continue
        
        # Check if it's a number
        try:
            idx = int(item) - 1
            if 0 <= idx < len(all_repos):
                selected.append(all_repos[idx])
            else:
                print(f"Warning: Invalid number {item}, skipping")
        except ValueError:
            # It's a name - check if it exists or use as-is
            if item in available:
                selected.append(item)
            elif item.endswith("*"):
                # Pattern matching (e.g., "fi_*")
                prefix = item[:-1]
                matches = [r for r in available if r.startswith(prefix)]
                selected.extend(matches)
            else:
                # Use as-is (might be a repo not in the list)
                selected.append(item)
    
    return list(dict.fromkeys(selected))  # Remove duplicates, preserve order


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
    
    # Prompt for repos if not provided
    repos = args.repos
    if not repos:
        print(f"Creating experiment '{args.name}'...")
        repos = prompt_for_repos()
        if not repos:
            print("ERROR: At least one repo is required for an experiment.")
            return 1
        print(f"\nSelected repos: {', '.join(repos)}")
        confirm = input("Proceed? [Y/n]: ").strip().lower()
        if confirm and confirm != "y":
            print("Aborted.")
            return 1
    
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
    strategy = ensure_default_strategy()
    repos_root = get_repos_root_from_knowledge() or REPO_ROOT.parent
    
    # Prompt for model if not provided
    model = args.model
    if not model:
        print("\nAvailable models:")
        print("  1. claude-sonnet-4 (standard)")
        print("  2. claude-sonnet-4.5 (standard)")
        print("  3. claude-haiku-4.5 (fast/cheap)")
        print("  4. claude-opus-4.5 (premium)")
        print("  5. Other (enter manually)")
        model_choice = input("Select model [1]: ").strip() or "1"
        model_map = {
            "1": "claude-sonnet-4",
            "2": "claude-sonnet-4.5", 
            "3": "claude-haiku-4.5",
            "4": "claude-opus-4.5",
        }
        model = model_map.get(model_choice, model_choice)
    
    # Compute agent version info for tracking improvements
    agents_version = compute_agents_version()
    
    exp_config = {
        "id": exp_id,
        "name": args.name,
        "full_name": exp_name,
        "status": "pending",
        "model": model,
        "agents_version": agents_version,
        "strategy": strategy,
        "repos": repos,
        "repos_root": str(repos_root),
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
    db.create_experiment(exp_id, args.name, repos, strategy.get("version", "default"), model=model)
    
    # Update state
    state["current_experiment_id"] = exp_id
    state["status"] = "pending"
    state["next_action"] = f"Run 'triage experiment run {exp_id}' to execute the experiment"
    state["repos_in_scope"] = repos
    state["model"] = model
    state["experiment_history"].append({
        "id": exp_id,
        "name": args.name,
        "model": model,
        "status": "pending",
        "created_at": datetime.now().isoformat(),
    })
    state["handoff_notes"] = f"Experiment {exp_id} created. Ready to run."
    save_state(state)
    
    print(f"Created experiment: {exp_name}")
    print(f"Model: {model}")
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
    
    # Check if repos are configured - prompt if not
    repos = config.get("repos", [])
    if not repos:
        print(f"Experiment {args.id} has no repos configured.")
        print("Please select repos to scan:\n")
        repos = prompt_for_repos()
        if not repos:
            print("ERROR: At least one repo is required to run an experiment.")
            return 1
        print(f"\nSelected repos: {', '.join(repos)}")
        confirm = input("Proceed? [Y/n]: ").strip().lower()
        if confirm and confirm != "y":
            print("Aborted.")
            return 1
        
        # Update config with selected repos
        config["repos"] = repos
        config_file.write_text(json.dumps(config, indent=2))
        state["repos_in_scope"] = repos
        db.update_experiment(args.id, repos=repos)

    # Phase 1 automation (local heuristics): seed experiment-scoped summaries/knowledge.
    strategy = config.get("strategy", {}) or {}
    auto_phase1 = bool(strategy.get("experiment", {}).get("auto_phase1_context_discovery", False))
    repos_root = Path(config.get("repos_root") or get_repos_root_from_knowledge() or REPO_ROOT.parent).expanduser().resolve()

    if auto_phase1:
        print("Running Phase 1 context discovery (writes to experiment folder)...")
        for r in repos:
            rp = Path(r).expanduser()
            if not rp.is_absolute():
                rp = (repos_root / r).resolve()
            if not rp.is_dir():
                print(f"ERROR: repo path not found: {rp}")
                return 1

            cmd = [
                sys.executable,
                str(SCRIPTS_SOURCE / "discover_repo_context.py"),
                str(rp),
                "--repos-root",
                str(repos_root),
                "--output-dir",
                str(exp_dir),
            ]
            subprocess.run(cmd, check=True)

    # Update status
    config["status"] = "running"
    config["started_at"] = datetime.now().isoformat()
    config_file.write_text(json.dumps(config, indent=2))
    
    db.update_experiment(args.id, status="running", started_at=datetime.now().isoformat())
    
    state["status"] = "running"
    state["next_action"] = "Scans in progress. Wait for completion or use 'triage resume' to check status."
    state["handoff_notes"] = f"Experiment {args.id} running."
    save_state(state)
    
    model = config.get("model", "unknown")
    print(f"Experiment {args.id} marked as running.")
    print(f"Model: {model}")
    print(f"Repos to scan: {', '.join(repos)}")
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
    
    # Compare agent versions
    v1 = config1.get("agents_version", {})
    v2 = config2.get("agents_version", {})
    
    if v1 and v2:
        print("\n== Agent Version Comparison ==")
        h1 = v1.get("combined_hash", "unknown")
        h2 = v2.get("combined_hash", "unknown")
        
        if h1 == h2:
            print(f"Agent versions: IDENTICAL ({h1})")
        else:
            print(f"Agent versions: DIFFERENT")
            print(f"  {args.id1}: {h1}")
            print(f"  {args.id2}: {h2}")
            
            # Show which files changed
            files1 = v1.get("files", {})
            files2 = v2.get("files", {})
            
            changed_files = []
            for fname in set(files1.keys()) | set(files2.keys()):
                fh1 = files1.get(fname, "-")
                fh2 = files2.get(fname, "-")
                if fh1 != fh2:
                    changed_files.append(fname)
            
            if changed_files:
                print(f"\n  Changed agent files ({len(changed_files)}):")
                for fname in sorted(changed_files):
                    print(f"    - {fname}")
                print(f"\n  Check {args.id2}/Agents/changes.md for details")
    
    # Compare models
    model1 = config1.get("model", "unknown")
    model2 = config2.get("model", "unknown")
    if model1 != model2:
        print(f"\n== Model Difference ==")
        print(f"  {args.id1}: {model1}")
        print(f"  {args.id2}: {model2}")
    
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
    new_parser.add_argument("--model", default=None, help="Model used for experiment (e.g., claude-sonnet-4, claude-haiku-4.5)")
    
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
