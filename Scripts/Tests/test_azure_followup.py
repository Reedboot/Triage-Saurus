import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Scripts" / "Harvest"))

import azure_followup


def test_rejects_cross_subscription_resource(monkeypatch):
    monkeypatch.setattr(
        azure_followup,
        "az_resource_show",
        lambda *_args: {"id": "should-not-be-called"},
    )
    result = azure_followup.collect(
        "sub-1",
        ["/subscriptions/sub-2/resourceGroups/rg/providers/Microsoft.Web/sites/app"],
        ["id"],
    )
    assert result["requests"][0]["status"] == "unresolved"
    assert "outside" in result["requests"][0]["reason"]


def test_selects_allowlisted_fields_and_redacts(monkeypatch):
    monkeypatch.setattr(
        azure_followup,
        "az_resource_show",
        lambda *_args: {
            "id": "/subscriptions/sub-1/resourceGroups/rg/providers/Microsoft.Web/sites/app",
            "name": "app",
            "properties": {
                "publicNetworkAccess": "Enabled",
                "password": "secret",
            },
        },
    )
    result = azure_followup.collect(
        "sub-1",
        ["/subscriptions/sub-1/resourceGroups/rg/providers/Microsoft.Web/sites/app"],
        ["id", "properties.publicNetworkAccess"],
    )
    assert result["requests"][0]["evidence"]["id"].endswith("/app")
    assert result["requests"][0]["evidence"]["properties.publicNetworkAccess"] == "Enabled"


def test_rejects_unapproved_fields():
    with pytest.raises(ValueError, match="unsupported fields"):
        azure_followup.collect("sub-1", ["/subscriptions/sub-1/resourceGroups/rg/providers/X/y"], ["properties.secret"])
