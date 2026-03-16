#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

# Ensure repo Scripts and subpackages are on PYTHONPATH after the reorg
REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = str(REPO_ROOT / 'Scripts')
# Add Scripts root and all immediate subdirectories to sys.path so modules like
# report_generation, persist_graph, models, etc. can be imported as top-level modules.
scripts_path = Path(SCRIPTS_DIR)
if str(scripts_path) not in sys.path:
    sys.path.insert(0, str(scripts_path))
if scripts_path.exists():
    for sub in scripts_path.iterdir():
        if sub.is_dir():
            sp = str(sub)
            if sp not in sys.path:
                sys.path.insert(0, sp)

from context_extraction import extract_context
from report_generation import generate_reports, write_to_database
from persist_graph import persist_context


def update_repos_knowledge(repo_path: Path) -> None:
    """Ensure Output/Knowledge/Repos.md includes the discovered repository."""
    knowledge_path = Path.cwd() / "Output" / "Knowledge" / "Repos.md"
    knowledge_path.parent.mkdir(parents=True, exist_ok=True)

    repo_root = repo_path.parent
    repo_name = repo_path.name
    bullet = f"- **{repo_name}** - Discovered via context discovery."

    if not knowledge_path.exists():
        knowledge_path.write_text(
            "# 🟣 Repositories\n\n"
            "## Repo Roots\n"
            f"- **Repo root directory:** `{repo_root}`\n\n"
            "## Repository Inventory\n\n"
            "### Application Repos\n\n"
            "### Infrastructure Repos\n"
            f"{bullet}\n",
            encoding="utf-8",
        )
        return

    text = knowledge_path.read_text(encoding="utf-8", errors="replace")
    if f"**{repo_name}**" in text:
        return

    if "### Infrastructure Repos" not in text:
        text = text.rstrip() + "\n\n### Infrastructure Repos\n"

    marker = "### Infrastructure Repos"
    pos = text.find(marker)
    if pos == -1:
        text = text.rstrip() + f"\n\n### Infrastructure Repos\n{bullet}\n"
    else:
        insert_at = pos + len(marker)
        text = text[:insert_at] + f"\n{bullet}" + text[insert_at:]

    if "**Repo root directory:**" not in text:
        roots_marker = "## Repo Roots"
        if roots_marker in text:
            rm_pos = text.find(roots_marker) + len(roots_marker)
            text = text[:rm_pos] + f"\n- **Repo root directory:** `{repo_root}`" + text[rm_pos:]
        else:
            text = (
                f"# 🟣 Repositories\n\n## Repo Roots\n- **Repo root directory:** `{repo_root}`\n\n"
                + text.lstrip()
            )

    knowledge_path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Fast, non-security context discovery for a local repo.")
    parser.add_argument("repo", help="Absolute or relative path to the repo to discover.")
    parser.add_argument("--repos-root", help="Ignored, for compatibility.", required=False)
    parser.add_argument(
        "--output-dir",
        help="Optional experiment directory root; summaries are written under <output-dir>/Summary.",
        required=False,
    )
    parser.add_argument("--database", help="Path to the database file.", required=False)
    parser.add_argument(
        "--experiment-id",
        help="Experiment ID to associate with database records (e.g., 002).",
        required=False,
        default="001",
    )
    args = parser.parse_args()

    repo_path = Path(args.repo).resolve()
    if not repo_path.is_dir():
        print(f"Error: Not a directory: {repo_path}", file=sys.stderr)
        return 1

    repo_name = repo_path.name

    def log(message: str) -> None:
        print(f"[{repo_name}] {message}", flush=True)

    # 1. Data Extraction
    log("== Starting context extraction ==")
    context_model = extract_context(str(repo_path))
    log(f"== Extracted {len(context_model.resources)} resources, {len(context_model.relationships)} relationships ==")

    # 2. Database Population
    log("== Writing to database ==")
    db_path = args.database if args.database else None
    write_to_database(context_model, db_path, experiment_id=args.experiment_id)
    log("== Database write complete ==")

    # 2b. Persist knowledge graph (nodes, typed relationships, enrichment queue)
    log("== Persisting knowledge graph ==")
    try:
        # pass the experiment id / scan id as provenance so persisted relationships are traceable
        persist_context(context_model, scan_id=args.experiment_id, actor_type='context_discovery', actor_id=args.experiment_id)
        log(f"== Knowledge graph: {len(context_model.resources)} nodes, {len(context_model.relationships)} relationships ==")
    except Exception as e:
        log(f"[WARN] Knowledge graph persist failed (non-fatal): {e}")

    # 3. Report Generation
    log("== Generating reports ==")
    if args.output_dir:
        summary_root = Path(args.output_dir).resolve() / "Summary"
    else:
        summary_root = Path.cwd() / "Output" / "Summary"

    generated = generate_reports(context_model, str(summary_root), repo_path=repo_path, experiment_id=args.experiment_id)
    log("== Report generation complete ==")
    for report in generated:
        log(f"Generated: {report}")

    # Keep repository inventory in sync with discovered repos.
    update_repos_knowledge(repo_path)
    log("Updated: Output/Knowledge/Repos.md")

    return 0

if __name__ == "__main__":
    sys.exit(main())
