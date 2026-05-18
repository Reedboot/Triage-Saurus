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


def test_detects_html_entities_in_element_names():
    """Test detection of HTML entities like &gt;, &lt;, &br; in diagram elements."""
    text = """```mermaid
flowchart TB
  A["API &gt; Gateway"]
  B["Data &lt; Storage"]
  C["Item &br; Container"]
  D["Quote &quot; Test"]
```
"""

    problems, new_text, changed = validate_and_fix_mermaid_blocks(text, fix=False)

    # Should detect 4 HTML entity issues
    entity_warnings = [p for p in problems if "HTML entity" in p.message]
    assert len(entity_warnings) >= 3, f"Expected at least 3 HTML entity warnings, got {len(entity_warnings)}"
    assert any("&gt;" in p.message for p in entity_warnings)
    assert any("&lt;" in p.message for p in entity_warnings)
    assert any("&br;" in p.message for p in entity_warnings)


def test_html_entities_warnings_are_warn_level():
    """Test that HTML entity issues are flagged as WARN (not ERROR)."""
    text = """```mermaid
flowchart TB
  A["Result &gt; 100"]
```
"""

    problems, new_text, changed = validate_and_fix_mermaid_blocks(text, fix=False)

    entity_warnings = [p for p in problems if "HTML entity" in p.message]
    assert any(p.level == "WARN" for p in entity_warnings)
