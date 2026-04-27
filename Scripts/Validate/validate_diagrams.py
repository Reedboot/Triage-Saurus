#!/usr/bin/env python3
"""Standalone tool to validate Mermaid diagrams in generated markdown files.

This tool scans markdown files containing Mermaid diagram blocks and validates them
for syntax errors. It can optionally fix certain issues automatically.

Usage:
  # Validate all diagrams in output directory
  python validate_diagrams.py --path Output/

  # Fix common issues automatically
  python validate_diagrams.py --path Output/ --fix

  # Validate specific file
  python validate_diagrams.py --path Output/Summary/Repos/AzureGoat.md

  # Show detailed reports
  python validate_diagrams.py --path Output/ --verbose
"""

import sys
import argparse
from pathlib import Path
from typing import List, Dict, Optional

# Add Validate directory to path
sys.path.insert(0, str(Path(__file__).parent))

from markdown_validator import validate_and_fix_mermaid_blocks, Problem


class DiagramValidator:
    """Validates Mermaid diagrams in markdown files."""
    
    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.total_files = 0
        self.files_with_errors = 0
        self.total_errors = 0
        self.total_warnings = 0
    
    def validate_file(self, filepath: Path, fix: bool = False) -> List[Problem]:
        """Validate a single markdown file."""
        if not filepath.exists():
            print(f"❌ File not found: {filepath}")
            return []
        
        if not filepath.suffix in ('.md', '.markdown'):
            return []
        
        try:
            text = filepath.read_text(encoding='utf-8', errors='replace')
        except Exception as e:
            print(f"❌ Error reading file {filepath}: {e}")
            return []
        
        problems, new_text, changed = validate_and_fix_mermaid_blocks(text, fix=fix)
        
        if problems:
            self.files_with_errors += 1
        
        for p in problems:
            if p.level == "ERROR":
                self.total_errors += 1
            else:
                self.total_warnings += 1
        
        if fix and changed:
            try:
                filepath.write_text(new_text, encoding='utf-8')
                if self.verbose:
                    print(f"✅ Fixed: {filepath}")
            except Exception as e:
                print(f"❌ Error writing file {filepath}: {e}")
        
        return problems
    
    def validate_directory(self, dirpath: Path, fix: bool = False) -> Dict[str, List[Problem]]:
        """Validate all markdown files in directory (recursively)."""
        results = {}
        
        if not dirpath.exists():
            print(f"❌ Directory not found: {dirpath}")
            return results
        
        markdown_files = list(dirpath.rglob('*.md')) + list(dirpath.rglob('*.markdown'))
        
        if not markdown_files:
            print(f"⚠️  No markdown files found in {dirpath}")
            return results
        
        for filepath in sorted(markdown_files):
            self.total_files += 1
            problems = self.validate_file(filepath, fix=fix)
            
            if problems:
                results[str(filepath)] = problems
        
        return results
    
    def print_summary(self, results: Dict[str, List[Problem]], filepath_or_dir: str) -> None:
        """Print validation summary."""
        print("\n" + "=" * 80)
        print("VALIDATION SUMMARY")
        print("=" * 80)
        print(f"Files scanned: {self.total_files}")
        print(f"Files with issues: {self.files_with_errors}")
        print(f"Errors: {self.total_errors}")
        print(f"Warnings: {self.total_warnings}")
        print("=" * 80)
        
        if not results:
            print("✅ All diagrams are valid!")
            return
        
        print("\n📋 DETAILED RESULTS:\n")
        
        for filepath, problems in sorted(results.items()):
            print(f"\n📄 {filepath}")
            for problem in problems:
                icon = "❌" if problem.level == "ERROR" else "⚠️ "
                line_info = f":{problem.line}" if problem.line else ""
                print(f"  {icon} [{problem.level}] {problem.message}{line_info}")
        
        if self.total_errors > 0:
            print("\n" + "=" * 80)
            print("❌ VALIDATION FAILED - Please fix the errors above")
            print("=" * 80)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Validate Mermaid diagrams in markdown files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    
    parser.add_argument(
        "--path",
        type=Path,
        default=Path("Output"),
        help="Path to file or directory to validate (default: Output/)"
    )
    
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Automatically fix safe issues (style properties, tabs, etc.)"
    )
    
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed output including fixed files"
    )
    
    args = parser.parse_args()
    
    validator = DiagramValidator(verbose=args.verbose)
    
    if args.path.is_file():
        # Validate single file
        print(f"Validating file: {args.path}")
        problems = validator.validate_file(args.path, fix=args.fix)
        results = {str(args.path): problems} if problems else {}
        validator.total_files = 1
    else:
        # Validate directory
        print(f"Validating directory: {args.path}")
        if args.fix:
            print("(with auto-fix enabled)")
        print()
        results = validator.validate_directory(args.path, fix=args.fix)
    
    validator.print_summary(results, str(args.path))
    
    # Exit with error code if there are errors
    if validator.total_errors > 0:
        sys.exit(1)
    
    sys.exit(0)


if __name__ == "__main__":
    main()
