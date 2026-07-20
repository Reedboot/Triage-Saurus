from __future__ import annotations

from .registry import build_blueprint_creator

ENDPOINTS = {
    "index",
    "experiment_diagram_redirect",
    "scan_001_diagram",
    "view_diagram",
    "view_latest_diagram",
    "cloud_architecture_page",
    "cloud_subscriptions_page",
    "cloud_assets_page",
    "help_cloud_page",
    "debug_routes",
}
create_blueprint = build_blueprint_creator("pages_routes", __name__, ENDPOINTS)
