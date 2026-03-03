#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

# Add the script's directory to the Python path
sys.path.append(str(Path(__file__).parent))

from context_extraction import extract_context
from report_generation import generate_reports, write_to_database

def main() -> int:
    parser = argparse.ArgumentParser(description="Fast, non-security context discovery for a local repo.")
    parser.add_argument("repo", help="Absolute or relative path to the repo to discover.")
    # Add other arguments from the original script as needed.
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
    write_to_database(context_model)
    print("== Database write complete ==")

    # 3. Report Generation
    print("== Generating reports ==")
    # A real implementation would get the summary directory from args
    summary_dir = str(Path.cwd() / "Output/Summary")
    generate_reports(context_model, summary_dir)
    print("== Report generation complete ==")

    return 0

if __name__ == "__main__":
    sys.exit(main())
