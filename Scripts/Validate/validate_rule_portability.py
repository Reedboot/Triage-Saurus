#!/usr/bin/env python3
"""validate_rule_portability.py

Two-phase validation for every opengrep/semgrep rule in Rules/:

  Phase 1 – opengrep validate
    Runs `opengrep validate <file>` to catch syntax errors and unsupported
    pattern constructs.  Fails fast on the first invalid file.

  Phase 2 – portability checks
    Statically inspects each rule's pattern text for hardcoded
    project-specific identifiers that would prevent the rule from being
    reusable across different repositories.

    Checks performed:
      - Terraform resource names that are plain literals instead of
        wildcards/metavariables  (e.g. "bob", "prod-api")
      - UUID / GUID values (tenant IDs, subscription IDs, account IDs)
      - IPv4 addresses
      - Hardcoded hostnames / FQDNs (domain-like strings outside metavariables)
      - Bare numeric cloud account IDs (12+ digit strings)

Usage:
    # Validate a single rule
    python3 Scripts/Validate/validate_rule_portability.py Rules/Misconfigurations/Azure/my-new-rule.yml

    # Validate an entire directory (recursive)
    python3 Scripts/Validate/validate_rule_portability.py Rules/Misconfigurations/

    # Validate all rules
    python3 Scripts/Validate/validate_rule_portability.py Rules/

Exit codes:
    0  – all rules pass both phases
    1  – one or more portability violations found
    2  – opengrep validate reported errors
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterator

import yaml  # PyYAML — already a project dependency

# ---------------------------------------------------------------------------
# Regex patterns for portability checks
# ---------------------------------------------------------------------------

# UUID / GUID  (e.g. tenant_id, subscription_id)
_UUID_RE = re.compile(
    r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
)

# IPv4 address (not inside a CIDR wildcard pattern like 0.0.0.0/0)
_IPV4_RE = re.compile(
    r'\b(?!0\.0\.0\.0)(?!255\.255\.255\.255)'
    r'(?:\d{1,3}\.){3}\d{1,3}'
    r'(?!/0\b)'  # exclude /0 CIDR — intentional "any" patterns
)

# Hostname / FQDN  (two or more labels, known TLD or internal suffix)
# Deliberately narrow: must end in a known TLD or .internal/.local/.corp
_FQDN_RE = re.compile(
    r'\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)'
    r'+(?:com|net|org|io|co|uk|dev|cloud|internal|local|corp|azure|aws|gcp)\b',
    re.IGNORECASE,
)

# Cloud account / subscription IDs — long numeric strings (12+ digits)
_ACCOUNT_ID_RE = re.compile(r'\b\d{12,}\b')

# Terraform resource name that is a plain literal (not a metavariable/wildcard)
# Matches:  resource "some_type" "literal_name"
# Allows:   resource "some_type" "$_"  or  resource "some_type" "$VAR"
_TF_RESOURCE_NAME_RE = re.compile(
    r'resource\s+"[^"]+"\s+"(?!\$)([^"$][^"]*)"'
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_rules(path: Path) -> Iterator[Path]:
    """Yield all .yml / .yaml rule files under *path*."""
    if path.is_file():
        if path.suffix in (".yml", ".yaml"):
            yield path
    else:
        for p in sorted(path.rglob("*.yml")):
            yield p
        for p in sorted(path.rglob("*.yaml")):
            yield p


def _extract_pattern_text(rule: dict) -> list[str]:
    """
    Recursively pull every pattern string out of a rule dict so we can
    run regex checks on the raw text regardless of nesting level.
    """
    texts: list[str] = []
    pattern_keys = {
        "pattern", "pattern-regex", "pattern-not", "pattern-inside",
        "pattern-not-inside", "pattern-either",
    }

    def _walk(node: object) -> None:
        if isinstance(node, str):
            texts.append(node)
        elif isinstance(node, dict):
            for k, v in node.items():
                if k in pattern_keys or k == "patterns":
                    _walk(v)
                # Also descend into metavariable-regex values
                elif k == "regex":
                    pass  # skip regex values — they intentionally contain wildcards
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(rule)
    return texts


# ---------------------------------------------------------------------------
# Phase 1 – opengrep validate
# ---------------------------------------------------------------------------

def run_opengrep_validate(rule_file: Path) -> list[str]:
    """
    Run `opengrep validate <file>`.  Returns a list of error strings; empty
    list means the rule is syntactically valid.
    """
    result = subprocess.run(
        ["opengrep", "validate", str(rule_file)],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return []

    # Strip ANSI escape sequences before parsing
    ansi_escape = re.compile(r'\x1b\[[0-9;]*m')

    errors = []
    skip_block = False  # inside ----- pattern ----- block
    for line in (result.stdout + result.stderr).splitlines():
        clean = ansi_escape.sub("", line).strip()
        if "-----" in clean:          # start/end of pattern echo block
            skip_block = not skip_block
            continue
        if skip_block or not clean:
            continue
        if "[WARNING]" in clean or (
            "[ERROR]" in clean and "Please fix" not in clean
        ):
            errors.append(f"    {clean}")

    if not errors:
        errors.append(f"    opengrep validate exited {result.returncode} (no detail captured)")
    return errors


# ---------------------------------------------------------------------------
# Phase 2 – portability checks
# ---------------------------------------------------------------------------

def check_portability(rule_file: Path, rule: dict) -> list[str]:
    """
    Return a list of human-readable violation strings for *rule*.
    Empty list means the rule passes all portability checks.
    """
    violations: list[str] = []
    rule_id = rule.get("id", "<unknown>")
    patterns = _extract_pattern_text(rule)

    for pat in patterns:
        # --- UUID / GUID (tenant, subscription, account) ---
        for m in _UUID_RE.finditer(pat):
            violations.append(
                f"  [{rule_id}] Hardcoded UUID/GUID '{m.group()}' — "
                "use a metavariable or remove the identifier"
            )

        # --- IPv4 address ---
        for m in _IPV4_RE.finditer(pat):
            violations.append(
                f"  [{rule_id}] Hardcoded IP address '{m.group()}' — "
                "use a CIDR range pattern or metavariable instead"
            )

        # --- Hostname / FQDN ---
        for m in _FQDN_RE.finditer(pat):
            violations.append(
                f"  [{rule_id}] Hardcoded hostname/FQDN '{m.group()}' — "
                "rules must not reference environment-specific hostnames"
            )

        # --- Long numeric account ID ---
        for m in _ACCOUNT_ID_RE.finditer(pat):
            violations.append(
                f"  [{rule_id}] Hardcoded numeric account/subscription ID '{m.group()}' — "
                "use a metavariable instead"
            )

        # --- Terraform literal resource name ---
        for m in _TF_RESOURCE_NAME_RE.finditer(pat):
            literal_name = m.group(1)
            violations.append(
                f"  [{rule_id}] Terraform resource name '{literal_name}' is a hardcoded "
                "literal — replace with '$_' (wildcard) so the rule matches any resource "
                "of that type, not just this one"
            )

    return violations


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate opengrep rules for syntax (via opengrep validate) "
                    "and portability (no hardcoded project-specific identifiers)."
    )
    parser.add_argument(
        "paths",
        nargs="+",
        type=Path,
        help="Rule file(s) or director(ies) to validate",
    )
    parser.add_argument(
        "--skip-opengrep",
        action="store_true",
        help="Skip the opengrep validate phase (portability checks only)",
    )
    parser.add_argument(
        "--skip-portability",
        action="store_true",
        help="Skip portability checks (opengrep validate only)",
    )
    args = parser.parse_args()

    all_files: list[Path] = []
    for p in args.paths:
        all_files.extend(_find_rules(p))

    if not all_files:
        print("No rule files found.")
        return 0

    syntax_failures: dict[Path, list[str]] = {}
    portability_failures: dict[Path, list[str]] = {}

    for rule_file in all_files:
        # --- Phase 1: opengrep validate ---
        if not args.skip_opengrep:
            errs = run_opengrep_validate(rule_file)
            if errs:
                syntax_failures[rule_file] = errs

        # --- Phase 2: portability ---
        if not args.skip_portability:
            try:
                with open(rule_file) as fh:
                    doc = yaml.safe_load(fh)
            except yaml.YAMLError as exc:
                portability_failures[rule_file] = [f"  YAML parse error: {exc}"]
                continue

            rules_list = doc.get("rules", []) if isinstance(doc, dict) else []
            file_violations: list[str] = []
            for rule in rules_list:
                file_violations.extend(check_portability(rule_file, rule))
            if file_violations:
                portability_failures[rule_file] = file_violations

    # --- Report ---
    ok = True

    if syntax_failures:
        ok = False
        print(f"\n❌ SYNTAX ERRORS  ({len(syntax_failures)} file(s))\n")
        for f, errs in syntax_failures.items():
            print(f"  {f}")
            for e in errs:
                print(f"    {e}")

    if portability_failures:
        ok = False
        print(f"\n❌ PORTABILITY VIOLATIONS  ({len(portability_failures)} file(s))\n")
        for f, viols in portability_failures.items():
            print(f"  {f}")
            for v in viols:
                print(v)

    total = len(all_files)
    passed = total - len(syntax_failures) - len(portability_failures)
    if ok:
        print(f"\n✅  All {total} rule(s) passed validation.")
        return 0
    else:
        print(
            f"\n  {passed}/{total} rule(s) passed  |  "
            f"{len(syntax_failures)} syntax error(s)  |  "
            f"{len(portability_failures)} portability violation(s)"
        )
        return 1 if portability_failures else 2


if __name__ == "__main__":
    sys.exit(main())
