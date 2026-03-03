#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

# Add the script's directory to the Python path
sys.path.append(str(Path(__file__).parent))

from context_extraction import extract_context
from report_generation import generate_reports, write_to_database


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

    # 1. Data Extraction
    print("== Starting context extraction ==")
    context_model = extract_context(str(repo_path))
    print(f"== Extracted {len(context_model.resources)} resources ==")

    # 2. Database Population
    print("== Writing to database ==")
    db_path = args.database if args.database else None
    write_to_database(context_model, db_path, experiment_id=args.experiment_id)
    print("== Database write complete ==")

    # 3. Report Generation
    print("== Generating reports ==")
    if args.output_dir:
        summary_root = Path(args.output_dir).resolve() / "Summary"
    else:
        summary_root = Path.cwd() / "Output" / "Summary"

    generated = generate_reports(context_model, str(summary_root), repo_path=repo_path)
    print("== Report generation complete ==")
    for report in generated:
        print(f"Generated: {report}")

    # Keep repository inventory in sync with discovered repos.
    update_repos_knowledge(repo_path)
    print("Updated: Output/Knowledge/Repos.md")

    return 0

if __name__ == "__main__":
    sys.exit(main())
