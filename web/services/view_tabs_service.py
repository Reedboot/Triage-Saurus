from __future__ import annotations


def default_view_tabs() -> list[dict[str, str]]:
    return [
        {"key": "tldr", "label": "📊 TL;DR"},
        {"key": "overview", "label": "📝 Overview"},
        {"key": "assets", "label": "🗂️ Assets"},
        {"key": "findings", "label": "🔎 Findings"},
        {"key": "containers", "label": "☸️ Kubernetes"},
        {"key": "roles", "label": "🧑‍💼 Roles & Permissions"},
        {"key": "traffic", "label": "📶 Traffic"},
        {"key": "subscription", "label": "🌐 Global Knowledge Q&A"},
    ]

