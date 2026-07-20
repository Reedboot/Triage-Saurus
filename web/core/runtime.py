from __future__ import annotations

import os

from flask import Flask


def create_flask_app(import_name: str) -> Flask:
    return Flask(import_name)


def _web_host() -> str:
    host = (os.getenv("TRIAGE_WEB_HOST") or os.getenv("HOST") or "0.0.0.0").strip()
    return host or "0.0.0.0"


def _web_port() -> int:
    for var in ("TRIAGE_WEB_PORT", "PORT", "FLASK_RUN_PORT"):
        raw = (os.getenv(var) or "").strip()
        if not raw:
            continue
        try:
            port = int(raw)
        except ValueError:
            continue
        if 1 <= port <= 65535:
            return port
    return 9000

