from __future__ import annotations

from .registry import build_blueprint_creator

ENDPOINTS = {
    "api_cloud_architecture",
    "api_cloud_resource_details",
    "api_cloud_route_trace",
    "get_group_members",
    "get_resource_children",
    "get_apim_child_apis",
    "api_cloud_posture",
    "api_cloud_assets_all",
    "api_subscriptions_list",
    "api_subscription_assets",
    "api_harvest_routing",
    "api_harvest_routing_status",
    "api_subscription_diagram",
    "api_subscription_drilldown",
}
create_blueprint = build_blueprint_creator("cloud_routes", __name__, ENDPOINTS)
