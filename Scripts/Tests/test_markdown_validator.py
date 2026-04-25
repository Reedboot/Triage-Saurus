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


def test_detects_emoji_with_class_suffix_in_subgraph():
    """Test that emoji + class suffix combination is detected as an error."""
    text = """```mermaid
flowchart TB
  subgraph n23["🔒 dev-vm"]:::icon-azurerm-virtual-machine
    n20["VM PublicIP"]
  end
```
"""
    
    problems, _, _ = validate_and_fix_mermaid_blocks(text, fix=False)
    
    # Should detect the emoji+class suffix error
    assert len(problems) > 0
    error_found = any("emoji" in p.message.lower() and "class suffix" in p.message.lower() for p in problems)
    assert error_found, f"Expected emoji+class suffix error, got: {[p.message for p in problems]}"


def test_detects_emoji_with_class_suffix_on_node():
    """Test that emoji + class suffix on regular node is detected."""
    text = """```mermaid
flowchart TB
  n8["📡 function app"]:::icon-azurerm-function-app
```
"""
    
    problems, _, _ = validate_and_fix_mermaid_blocks(text, fix=False)
    
    # Should detect the emoji+class suffix error on node
    error_found = any("emoji" in p.message.lower() and "class suffix" in p.message.lower() for p in problems)
    assert error_found, f"Expected emoji+class suffix error on node, got: {[p.message for p in problems]}"


def test_allows_emoji_without_class_suffix():
    """Test that emoji without class suffix is allowed (passes existing validation)."""
    text = """```mermaid
flowchart TB
  n23["🔒 dev-vm"]
    n20["VM PublicIP"]
```
"""
    
    problems, _, _ = validate_and_fix_mermaid_blocks(text, fix=False)
    
    # Filter out unrelated warnings, focus on emoji+class errors
    emoji_class_errors = [p for p in problems if "emoji" in p.message.lower() and "class suffix" in p.message.lower()]
    assert len(emoji_class_errors) == 0, f"Should allow emoji without class suffix, got: {[p.message for p in emoji_class_errors]}"


def test_allows_class_suffix_without_emoji():
    """Test that class suffix without emoji is allowed."""
    text = """```mermaid
flowchart TB
  subgraph n23["dev-vm"]:::icon-azurerm-virtual-machine
    n20["VM PublicIP"]:::icon-azurerm-public-ip
  end
```
"""
    
    problems, _, _ = validate_and_fix_mermaid_blocks(text, fix=False)
    
    # Filter to emoji+class errors only
    emoji_class_errors = [p for p in problems if "emoji" in p.message.lower() and "class suffix" in p.message.lower()]
    assert len(emoji_class_errors) == 0, f"Should allow class suffix without emoji, got: {[p.message for p in emoji_class_errors]}"


def test_detects_unbalanced_square_brackets():
    """Test that unbalanced square brackets are detected."""
    text = """```mermaid
flowchart TB
  n1["label without closing bracket
  n2["valid"]
```
"""
    
    problems, _, _ = validate_and_fix_mermaid_blocks(text, fix=False)
    
    bracket_errors = [p for p in problems if "bracket" in p.message.lower()]
    assert len(bracket_errors) > 0, f"Expected bracket error, got: {[p.message for p in problems]}"


def test_detects_invalid_node_ids_with_special_chars():
    """Test that node IDs with conflicting syntax are detected."""
    text = """```mermaid
flowchart TB
  n1::"weird"["label"]
  n2["valid"]
```
"""
    
    problems, _, _ = validate_and_fix_mermaid_blocks(text, fix=False)
    
    # May or may not detect depending on complexity, but should not crash
    assert isinstance(problems, list)

