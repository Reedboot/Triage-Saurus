#!/usr/bin/env python3
"""Shared output path helpers.

All generated artifacts (Findings/Knowledge/Summary/Audit) live under Output/ by default.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = REPO_ROOT / "Output"

OUTPUT_FINDINGS_DIR = OUTPUT_ROOT / "Findings"
OUTPUT_KNOWLEDGE_DIR = OUTPUT_ROOT / "Knowledge"
OUTPUT_SUMMARY_DIR = OUTPUT_ROOT / "Summary"
OUTPUT_AUDIT_DIR = OUTPUT_ROOT / "Audit"
