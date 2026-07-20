from __future__ import annotations

import html
import os

from flask import Flask
from markupsafe import Markup


def _format_list_text(text):
    """Convert semicolon-separated text to HTML list if it contains multiple items."""
    if not text or not isinstance(text, str):
        return text
    if ";" in text:
        items = [item.strip() for item in text.split(";") if item.strip()]
        if len(items) > 1:
            list_items = "".join(f"<li>{html.escape(item)}</li>" for item in items)
            return Markup(f"<ul style=\"margin: 6px 0; padding-left: 20px;\">{list_items}</ul>")
    return text


def register_jinja_filters(app: Flask) -> None:
    app.jinja_env.filters["basename"] = lambda p: os.path.basename(p or "") if p else ""
    app.jinja_env.filters["format_list"] = _format_list_text

