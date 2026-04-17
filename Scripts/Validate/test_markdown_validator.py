#!/usr/bin/env python3
"""Regression tests for markdown_validator.py."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Scripts" / "Validate"))

from markdown_validator import validate_and_fix_mermaid_blocks


def test_normalizes_underscore_mermaid_style_properties():
    text = """```mermaid
flowchart TB
  style Azure_Cloud stroke:#666, stroke_width:2px, fill_opacity:0.5
```
"""

    problems, new_text, changed = validate_and_fix_mermaid_blocks(text, fix=True)

    assert changed is True
    assert not problems
    assert "stroke_width" not in new_text
    assert "stroke-width" in new_text
    assert "fill_opacity" not in new_text
    assert "fill-opacity" in new_text
