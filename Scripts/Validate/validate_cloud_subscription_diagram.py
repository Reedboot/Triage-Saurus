#!/usr/bin/env python3
"""Playwright diagnostic for a single Cloud subscription diagram.

Opens /cloud, clicks the subscription row, waits for the subscription diagram
to render, records the relevant network traffic, and saves a screenshot.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("Playwright required: pip install playwright && playwright install chromium", file=sys.stderr)
    raise SystemExit(2)


def _slugify(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in value.strip().lower()).strip("-") or "subscription"


async def run(base_url: str, subscription: str, output_dir: Path, headed: bool) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = output_dir / "subscription-diagram.png"
    report_path = output_dir / "subscription-diagram-trace.json"

    requests: list[dict] = []
    responses: list[dict] = []
    console_errors: list[str] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not headed)
        context = await browser.new_context(viewport={"width": 1600, "height": 1200})
        page = await context.new_page()

        def record_request(req) -> None:
            if "/api/subscriptions" in req.url:
                requests.append({
                    "method": req.method,
                    "url": req.url,
                    "resource_type": req.resource_type,
                })

        def record_response(resp) -> None:
            if "/api/subscriptions" in resp.url:
                responses.append({
                    "status": resp.status,
                    "url": resp.url,
                })

        def record_console(msg) -> None:
            if msg.type == "error":
                console_errors.append(msg.text)

        page.on("request", record_request)
        page.on("response", record_response)
        page.on("console", record_console)

        try:
            await page.goto(f"{base_url}/cloud", wait_until="domcontentloaded")
            await page.locator("#subscriptions-tbody tr").first.wait_for(timeout=20000)

            row = page.locator(".subscription-name-cell", has_text=subscription).first
            await row.wait_for(timeout=10000)
            await row.click()

            await page.locator("#subscription-diagram-wrap").wait_for(state="visible", timeout=20000)
            await page.locator("#subscription-diagram-loading").wait_for(state="hidden", timeout=30000)
            await page.locator("#subscription-diagram-container svg").wait_for(state="visible", timeout=30000)

            await page.locator("#subscription-diagram-container").screenshot(path=str(screenshot_path))
            svg_count = await page.locator("#subscription-diagram-container svg").count()
            title = await page.locator("#subscription-diagram-title").inner_text()
            result = {
                "base_url": base_url,
                "subscription": subscription,
                "subscription_title": title,
                "svg_count": svg_count,
                "screenshot": str(screenshot_path),
                "requests": requests,
                "responses": responses,
                "console_errors": console_errors,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as exc:
            await page.screenshot(path=str(output_dir / "subscription-diagram-failure.png"), full_page=True)
            result = {
                "base_url": base_url,
                "subscription": subscription,
                "error": str(exc),
                "requests": requests,
                "responses": responses,
                "console_errors": console_errors,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        finally:
            await context.close()
            await browser.close()

    report_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a Cloud subscription diagram with Playwright")
    parser.add_argument("--subscription", required=True, help="Subscription display name or ID")
    parser.add_argument("--base-url", default="http://127.0.0.1:9001", help="Triage-Saurus base URL")
    parser.add_argument("--output", type=Path, default=Path("Output/Audit/CloudSubscriptionDiagram"), help="Output directory")
    parser.add_argument("--headed", action="store_true", help="Run Chromium headed")
    args = parser.parse_args()

    output_dir = args.output / _slugify(args.subscription)
    result = asyncio.run(run(args.base_url, args.subscription, output_dir, args.headed))

    if result.get("error"):
        print(f"[error] {result['error']}", file=sys.stderr)
        print(f"[output] {output_dir}")
        return 1

    print(f"[ok] screenshot: {result['screenshot']}")
    print(f"[ok] trace: {output_dir / 'subscription-diagram-trace.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
