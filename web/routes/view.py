from __future__ import annotations

from .registry import build_blueprint_creator

ENDPOINTS = {
    "api_icon_mappings",
    "api_blast_radius",
    "api_diagrams",
    "api_repo_summary",
    "api_experiments",
    "api_diff",
    "api_assets",
    "api_view_tabs",
    "api_view_normalize",
    "api_view_tldr",
    "api_view_risks",
    "api_view_overview",
    "api_module_mappings_save",
    "api_view_assets",
    "api_finding_triage",
    "api_view_findings",
    "api_view_ingress",
    "api_view_subscription",
    "api_subscription_context_upsert",
    "api_view_egress",
    "api_view_traffic",
    "api_view_roles",
    "api_view_containers",
    "list_diagrams",
    "api_repo_subscriptions_get",
    "api_repo_subscriptions_add",
    "api_repo_subscriptions_remove",
    "api_view_subscriptions",
}
create_blueprint = build_blueprint_creator("view_routes", __name__, ENDPOINTS)
