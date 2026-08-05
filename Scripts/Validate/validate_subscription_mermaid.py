#!/usr/bin/env python3
"""Acceptance checks for a live subscription Mermaid architecture view.

The validator checks:
* graph node/edge integrity and public-asset coverage;
* supplied endpoint route traces;
* sampled modal content;
* browser console and HTTP failures.

Example:
    python3 Scripts/Validate/validate_subscription_mermaid.py \
      --subscription pipeline-customer-production \
      --endpoint https://payuknew.clearbank.co.uk
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote


def _issue(check: str, message: str, severity: str = "HIGH") -> dict[str, str]:
    return {"check": check, "message": message, "severity": severity}


def _coverage_from_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    coverage = summary.get("public_asset_coverage")
    if isinstance(coverage, dict) and isinstance(coverage.get("assets"), list):
        return coverage
    for node in payload.get("nodes") or []:
        if not isinstance(node, dict) or str(node.get("id") or "") != "Internet":
            continue
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        coverage = data.get("public_asset_coverage")
        if isinstance(coverage, dict):
            return coverage
    return None


def validate_payload(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Validate graph integrity and public-asset reconciliation."""
    issues: list[dict[str, str]] = []
    nodes = [node for node in payload.get("nodes") or [] if isinstance(node, dict)]
    node_ids = [str(node.get("id") or "").strip() for node in nodes]
    node_id_set = {node_id for node_id in node_ids if node_id}

    if len(node_ids) != len(node_id_set):
        issues.append(_issue("reconciliation", "Diagram contains duplicate or empty node IDs."))

    for edge in payload.get("edges") or []:
        if not isinstance(edge, dict):
            issues.append(_issue("reconciliation", "Diagram contains a non-object edge."))
            continue
        source = str(edge.get("source") or "").strip()
        target = str(edge.get("target") or "").strip()
        if not source or not target or source not in node_id_set or target not in node_id_set:
            issues.append(
                _issue(
                    "reconciliation",
                    f"Edge has an unresolved endpoint: {source!r} -> {target!r}.",
                )
            )

    coverage = _coverage_from_payload(payload)
    if not coverage:
        issues.append(_issue("coverage", "Public-asset coverage was not included in the payload."))
        return issues

    assets = [asset for asset in coverage.get("assets") or [] if isinstance(asset, dict)]
    statuses = {"visible", "grouped", "missing"}
    asset_ids = set()
    represented = 0
    missing = 0
    for asset in assets:
        asset_id = str(asset.get("id") or "").strip()
        status = str(asset.get("status") or "").strip()
        if not asset_id or asset_id.lower() in asset_ids:
            issues.append(_issue("coverage", "Public-asset coverage contains a duplicate or empty asset ID."))
        asset_ids.add(asset_id.lower())
        if status not in statuses:
            issues.append(_issue("coverage", f"Unknown public-asset status {status!r} for {asset_id!r}."))
        if status == "missing":
            missing += 1
        elif status in {"visible", "grouped"}:
            represented += 1
            if not str(asset.get("node_id") or "").strip():
                issues.append(_issue("coverage", f"Represented asset {asset_id!r} has no node mapping."))

    total = int(coverage.get("total") or 0)
    if total != len(assets):
        issues.append(_issue("coverage", f"Coverage total {total} does not match asset list length {len(assets)}."))
    if int(coverage.get("represented") or 0) != represented:
        issues.append(_issue("coverage", "Coverage represented count does not match asset statuses."))
    if int(coverage.get("missing") or 0) != missing or missing:
        issues.append(_issue("coverage", f"{missing} public assets are missing from the diagram."))

    return issues


def validate_source_reconciliation(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Validate that harvested source nodes and relationships reach the diagram."""
    issues: list[dict[str, str]] = []
    nodes = payload.get("source_node_reconciliation")
    edges = payload.get("source_edge_reconciliation")
    if not isinstance(nodes, list):
        return [_issue("reconciliation", "Source-node reconciliation was not included in the payload.")]
    if not isinstance(edges, list):
        return [_issue("reconciliation", "Source-edge reconciliation was not included in the payload.")]
    missing_nodes = [item for item in nodes if isinstance(item, dict) and item.get("status") == "missing"]
    missing_edges = [item for item in edges if isinstance(item, dict) and item.get("status") == "missing"]
    if missing_nodes:
        issues.append(_issue("reconciliation", f"{len(missing_nodes)} harvested source nodes are not rendered."))
    if missing_edges:
        issues.append(_issue("reconciliation", f"{len(missing_edges)} harvested source relationships are not rendered."))
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    node_summary = summary.get("source_node_reconciliation")
    edge_summary = summary.get("source_edge_reconciliation")
    if isinstance(node_summary, dict) and int(node_summary.get("missing") or 0) != len(missing_nodes):
        issues.append(_issue("reconciliation", "Source-node reconciliation summary is inconsistent."))
    if isinstance(edge_summary, dict) and int(edge_summary.get("missing") or 0) != len(missing_edges):
        issues.append(_issue("reconciliation", "Source-edge reconciliation summary is inconsistent."))
    return issues


def validate_route_trace(trace: dict[str, Any], endpoint: str) -> list[dict[str, str]]:
    """Validate one endpoint trace returned by the route-trace API."""
    issues: list[dict[str, str]] = []
    if not trace.get("resolved"):
        return [_issue("route", f"Endpoint did not resolve: {endpoint}.")]
    chain = trace.get("resolved_chain")
    if not isinstance(chain, list) or len(chain) < 2:
        return [_issue("route", f"Endpoint resolved without a usable hop chain: {endpoint}.")]
    for index, hop in enumerate(chain):
        if not isinstance(hop, dict):
            issues.append(_issue("route", f"Hop {index} for {endpoint} is not an object."))
            continue
        if not str(hop.get("node_id") or hop.get("name") or "").strip():
            issues.append(_issue("route", f"Hop {index} for {endpoint} has no stable identity."))
    terminal = chain[-1] if isinstance(chain[-1], dict) else {}
    terminal_kind = str(terminal.get("kind") or "").strip()
    if terminal_kind in {"unknown_destination", "private_destination", "external_destination"}:
        destination_class = str(terminal.get("destination_class") or "").strip()
        resolution = str(terminal.get("resolution") or "").strip()
        if not destination_class:
            issues.append(_issue("backend", f"Unresolved backend for {endpoint} has no classification."))
        if resolution != "unharvested":
            issues.append(_issue("backend", f"Unresolved backend for {endpoint} has no unharvested resolution marker."))
    return issues


def validate_browser_evidence(evidence: dict[str, Any]) -> list[dict[str, str]]:
    """Validate browser-rendering evidence produced by the live runner."""
    issues: list[dict[str, str]] = []
    if evidence.get("svg_count", 0) < 1:
        issues.append(_issue("rendering", "Mermaid SVG was not rendered."))
    for error in evidence.get("console_errors") or []:
        issues.append(_issue("console", str(error)))
    for response in evidence.get("failed_responses") or []:
        issues.append(_issue("console", f"HTTP failure {response.get('status')}: {response.get('url')}"))
    samples = evidence.get("modal_samples") or []
    if not samples:
        issues.append(_issue("modal", "No modal samples were captured."))
    coverage = evidence.get("modal_coverage") or {}
    if int(coverage.get("grouped_expected") or 0) != int(coverage.get("grouped_matched") or 0):
        issues.append(_issue("modal", "Not every grouped node was matched for drilldown testing."))
    if not coverage.get("resource_types"):
        issues.append(_issue("modal", "No resource types were selected for modal coverage testing."))
    for sample in samples:
        text = str(sample.get("text") or "")
        if "Loading details" in text or "Loading resource details" in text:
            issues.append(_issue("modal", f"Modal remained in a loading state: {sample.get('node', {}).get('text', '')}"))
        if "Unable to load" in text or "Unable to load" in str(sample.get("title") or ""):
            issues.append(_issue("modal", f"Modal reported an error: {sample.get('node', {}).get('text', '')}"))
    return issues


async def run_live(
    base_url: str,
    subscription: str,
    endpoints: list[str],
    sample_nodes: int,
    output_dir: Path,
    headed: bool,
) -> dict[str, Any]:
    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is required for live Mermaid acceptance checks.") from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    evidence: dict[str, Any] = {
        "subscription": subscription,
        "url": f"{base_url}/cloud/architecture?sub={quote(subscription)}&view=mermaid",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "console_errors": [],
        "failed_responses": [],
        "modal_samples": [],
        "route_traces": [],
        "backend_classifications": [],
    }
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=not headed)
        page = await browser.new_page(viewport={"width": 1600, "height": 1200})

        def on_console(message) -> None:
            if message.type == "error":
                evidence["console_errors"].append(message.text)

        def on_response(response) -> None:
            if response.status >= 400:
                evidence["failed_responses"].append({"status": response.status, "url": response.url})

        page.on("console", on_console)
        page.on("response", on_response)
        await page.goto(evidence["url"], wait_until="domcontentloaded", timeout=120000)
        await page.locator("svg g.node[id]").first.wait_for(state="visible", timeout=120000)
        evidence["svg_count"] = await page.locator("svg").count()

        nodes = page.locator("svg g.node[id]")
        rendered_labels = [
            ((await nodes.nth(index).text_content()) or "").strip().replace("\n", " ")
            for index in range(await nodes.count())
        ]
        payload_response = await page.request.get(
            f"{base_url}/api/cloud/architecture",
            params={"sub": subscription, "view": "mermaid"},
            timeout=120000,
        )
        evidence["payload_status"] = payload_response.status
        evidence["payload"] = await payload_response.json()
        payload_nodes = evidence["payload"].get("nodes") or []
        selected_indices = set(range(min(sample_nodes, len(rendered_labels))))
        expected_types: dict[str, int] = {}
        grouped_expected = 0
        matched_grouped = 0
        used_group_indices: set[int] = set()
        used_type_indices: set[int] = set()
        for payload_node in payload_nodes:
            if not isinstance(payload_node, dict):
                continue
            data = payload_node.get("data") if isinstance(payload_node.get("data"), dict) else {}
            label = str(data.get("label") or payload_node.get("id") or "").strip()
            resource_type = str(data.get("resourceType") or data.get("typeLabel") or "unknown").strip()
            is_grouped = bool(data.get("isGroupNode") or data.get("groupedResourceIds"))
            if is_grouped:
                grouped_expected += 1
            normalized_label = " ".join(label.split()).lower()
            matching_index = next(
                (
                    index
                    for index, rendered_label in enumerate(rendered_labels)
                    if (not is_grouped or index not in used_group_indices)
                    if (is_grouped or index not in used_type_indices)
                    if normalized_label and (
                        normalized_label in " ".join(rendered_label.split()).lower()
                        or " ".join(rendered_label.split()).lower() in normalized_label
                    )
                ),
                None,
            )
            if matching_index is None:
                continue
            if is_grouped:
                selected_indices.add(matching_index)
                used_group_indices.add(matching_index)
                matched_grouped += 1
            if resource_type not in expected_types:
                expected_types[resource_type] = matching_index
                used_type_indices.add(matching_index)
        selected_indices.update(expected_types.values())
        evidence["modal_coverage"] = {
            "resource_types": sorted(expected_types),
            "grouped_expected": grouped_expected,
            "grouped_matched": matched_grouped,
            "selected_nodes": len(selected_indices),
        }
        for index in sorted(selected_indices):
            node = nodes.nth(index)
            label = ((await node.text_content()) or "").strip().replace("\n", " ")
            await node.evaluate(
                "element => element.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}))"
            )
            try:
                await page.wait_for_function(
                    """() => {
                        const title = document.querySelector('#modal-title')?.textContent || '';
                        const body = document.querySelector('#modal-body')?.textContent || '';
                        return !title.includes('Loading details') &&
                               !body.includes('Loading details') &&
                               !body.includes('Loading resource details');
                    }""",
                    timeout=15000,
                )
            except PlaywrightTimeoutError:
                pass
            evidence["modal_samples"].append(
                {
                    "node": {"index": index, "text": label},
                    "title": (await page.locator("#modal-title").text_content() or "").strip(),
                    "text": (await page.locator("#modal-body").text_content() or "").strip(),
                }
            )
            await page.locator("#modal-close-btn").click()

        for endpoint in endpoints:
            response = await page.request.get(
                f"{base_url}/api/subscriptions/{quote(subscription, safe='')}/trace-route",
                params={"endpoint": endpoint},
                timeout=120000,
            )
            trace = await response.json()
            evidence["route_traces"].append({"endpoint": endpoint, "status": response.status, "trace": trace})
            chain = trace.get("resolved_chain") if isinstance(trace, dict) else []
            terminal = chain[-1] if isinstance(chain, list) and chain and isinstance(chain[-1], dict) else {}
            if terminal.get("kind") in {"unknown_destination", "private_destination", "external_destination"}:
                evidence["backend_classifications"].append(
                    {
                        "endpoint": endpoint,
                        "classification": terminal.get("destination_class"),
                        "resolution": terminal.get("resolution"),
                        "host": terminal.get("destination_host"),
                    }
                )

        await browser.close()

    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subscription", required=True)
    parser.add_argument("--endpoint", action="append", default=[], help="Endpoint to validate; repeatable.")
    parser.add_argument("--base-url", default="http://127.0.0.1:9001")
    parser.add_argument("--output", type=Path, default=Path("Output/Audit/SubscriptionMermaidAcceptance"))
    parser.add_argument("--sample-nodes", type=int, default=10)
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    try:
        evidence = asyncio.run(
            run_live(args.base_url, args.subscription, args.endpoint, args.sample_nodes, args.output, args.headed)
        )
    except Exception as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2

    issues = validate_browser_evidence(evidence)
    if evidence.get("payload_status") != 200:
        issues.append(_issue("reconciliation", f"Architecture API returned HTTP {evidence.get('payload_status')}."))
    else:
        issues.extend(validate_payload(evidence.get("payload") or {}))
        issues.extend(validate_source_reconciliation(evidence.get("payload") or {}))
    for item in evidence.get("route_traces") or []:
        if item.get("status") != 200:
            issues.append(_issue("route", f"Route API returned HTTP {item.get('status')} for {item.get('endpoint')}."))
        else:
            issues.extend(validate_route_trace(item.get("trace") or {}, item["endpoint"]))

    result = {
        "passed": not issues,
        "issues": issues,
        "evidence": evidence,
    }
    result_path = args.output / "acceptance_report.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"[{'ok' if not issues else 'fail'}] {result_path}")
    for issue in issues:
        print(f"[{issue['severity']}] {issue['check']}: {issue['message']}")
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
