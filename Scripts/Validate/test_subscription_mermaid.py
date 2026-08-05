from __future__ import annotations

from Scripts.Validate.validate_subscription_mermaid import (
    validate_browser_evidence,
    validate_payload,
    validate_route_trace,
    validate_source_reconciliation,
)


def _payload() -> dict:
    return {
        "summary": {
            "public_asset_coverage": {
                "total": 1,
                "represented": 1,
                "missing": 0,
                "assets": [
                    {
                        "id": "asset-1",
                        "name": "gateway",
                        "type": "Microsoft.Network/applicationGateways",
                        "status": "visible",
                        "node_id": "asset-1",
                    }
                ],
            }
        },
        "nodes": [{"id": "Internet"}, {"id": "asset-1"}],
        "edges": [{"source": "Internet", "target": "asset-1"}],
    }


def test_validate_payload_accepts_complete_graph_and_coverage() -> None:
    assert validate_payload(_payload()) == []


def test_validate_payload_rejects_missing_public_asset() -> None:
    payload = _payload()
    coverage = payload["summary"]["public_asset_coverage"]
    coverage["missing"] = 1
    coverage["represented"] = 0
    coverage["assets"][0]["status"] = "missing"
    coverage["assets"][0]["node_id"] = ""

    issues = validate_payload(payload)

    assert any(issue["check"] == "coverage" for issue in issues)


def test_validate_source_reconciliation_accepts_complete_mapping() -> None:
    payload = _payload()
    payload["source_node_reconciliation"] = [{"id": "asset-1", "status": "visible"}]
    payload["source_edge_reconciliation"] = [{"status": "rendered"}]
    payload["summary"]["source_node_reconciliation"] = {"missing": 0}
    payload["summary"]["source_edge_reconciliation"] = {"missing": 0}

    assert validate_source_reconciliation(payload) == []


def test_validate_route_trace_requires_resolved_chain() -> None:
    assert validate_route_trace({"resolved": False}, "https://example.test")
    assert validate_route_trace(
        {"resolved": True, "resolved_chain": [{"node_id": "internet"}, {"node_id": "gateway"}]},
        "https://example.test",
    ) == []


def test_validate_route_trace_requires_unresolved_backend_classification() -> None:
    trace = {
        "resolved": True,
        "resolved_chain": [
            {"node_id": "internet"},
            {"node_id": "backend", "kind": "external_destination"},
        ],
    }

    issues = validate_route_trace(trace, "https://example.test")

    assert {issue["check"] for issue in issues} == {"backend"}


def test_validate_browser_evidence_rejects_console_and_loading_errors() -> None:
    issues = validate_browser_evidence(
        {
            "svg_count": 1,
            "console_errors": ["HTTP 404"],
            "failed_responses": [],
            "modal_samples": [{"node": {"text": "APIM"}, "text": "Loading details…"}],
            "modal_coverage": {"resource_types": ["APIM"], "grouped_expected": 0, "grouped_matched": 0},
        }
    )

    assert {issue["check"] for issue in issues} == {"console", "modal"}
