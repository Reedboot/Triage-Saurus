from __future__ import annotations

from .registry import build_blueprint_creator

ENDPOINTS = {"api_export_csv"}
create_blueprint = build_blueprint_creator("export_routes", __name__, ENDPOINTS)
