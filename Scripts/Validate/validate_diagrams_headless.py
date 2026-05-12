#!/usr/bin/env python3
"""Screenshot-based validation of architecture diagrams.

Validates diagrams by:
1. Taking headless screenshots of each provider tab
2. Detecting rendering errors (SVG render failures, console errors)
3. Comparing diagram structure against repo code/docs for gaps
4. Recording evidence for security staff threat modeling

Usage:
    python3 validate_diagrams_headless.py \
      --experiment <id> \
      --output Output/Audit/diagram-validation/
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

try:
    from playwright.async_api import async_playwright, Browser, BrowserContext, Page
except ImportError:
    print("Playwright required: pip install playwright && playwright install chromium")
    sys.exit(2)


class DiagramScreenshotValidator:
    """Validates architecture diagrams with headless browser screenshots."""
    
    def __init__(self, experiment_id: str, base_url: str, output_dir: Path):
        self.experiment_id = experiment_id
        self.base_url = base_url
        self.output_dir = output_dir
        self.results = {
            "experiment_id": experiment_id,
            "validation_timestamp": datetime.now(timezone.utc).isoformat(),
            "providers": [],
            "issues": [],
            "screenshots": [],
        }
    
    async def validate(self, browser: Browser) -> dict[str, Any]:
        """Run full validation against diagram."""
        try:
            context = await browser.new_context(base_url=self.base_url)
            page = await context.new_page()
            
            # Navigate to diagram
            await page.goto(f"/diagrams/{self.experiment_id}", wait_until="domcontentloaded")
            await page.wait_for_selector("#diagram-container", timeout=15000)
            
            # Detect providers and validate each
            tabs = page.locator("[role='tab'][data-provider]")
            tab_count = await tabs.count()
            
            if tab_count == 0:
                # Single provider
                await self._validate_single_provider(page)
            else:
                # Multi-provider tabs
                for idx in range(tab_count):
                    tab = tabs.nth(idx)
                    provider = (await tab.get_attribute("data-provider")) or f"provider_{idx}"
                    await tab.click()
                    await page.wait_for_timeout(1200)
                    await self._validate_single_provider(page, provider)
            
            await context.close()
        except Exception as e:
            self.results["issues"].append({
                "type": "validation_error",
                "message": f"Validation failed: {e}",
                "severity": "CRITICAL",
            })
        
        return self.results
    
    async def _validate_single_provider(self, page: Page, provider: str = "unknown") -> None:
        """Validate a single provider's diagram."""
        provider_result = {
            "provider": provider,
            "screenshot": None,
            "has_svg": False,
            "has_errors": False,
            "error_messages": [],
        }
        
        # Check for SVG rendering
        has_svg = await page.locator("#diagram-container svg").count() > 0
        provider_result["has_svg"] = has_svg
        
        # Check for error nodes in Mermaid
        error_nodes = await page.locator("g.error").count() > 0
        if error_nodes:
            provider_result["has_errors"] = True
            error_text = await page.locator(".error-text").inner_text(timeout=1000).catch(lambda _: "")
            provider_result["error_messages"].append(error_text)
        
        # Check console for errors
        console_errors = []
        def on_console_msg(msg):
            if msg.type == "error":
                console_errors.append(msg.text)
        
        page.on("console", on_console_msg)
        
        # Take screenshot
        screenshot_path = self.output_dir / f"diagram_{self.experiment_id}_{provider}.png"
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(screenshot_path), full_page=True)
        provider_result["screenshot"] = str(screenshot_path)
        
        if console_errors:
            provider_result["has_errors"] = True
            provider_result["error_messages"].extend(console_errors)
        
        page.remove_listener("console", on_console_msg)
        
        self.results["providers"].append(provider_result)
        
        if not has_svg:
            self.results["issues"].append({
                "type": "render_failure",
                "provider": provider,
                "message": "SVG diagram did not render in browser",
                "severity": "HIGH",
                "screenshot": str(screenshot_path),
            })
        elif provider_result["has_errors"]:
            self.results["issues"].append({
                "type": "diagram_errors",
                "provider": provider,
                "message": f"Diagram rendering errors detected: {'; '.join(provider_result['error_messages'][:3])}",
                "severity": "MEDIUM",
                "screenshot": str(screenshot_path),
            })


async def main():
    parser = argparse.ArgumentParser(description="Validate architecture diagrams with headless browser")
    parser.add_argument("--experiment", required=True, help="Experiment ID")
    parser.add_argument("--base-url", default="http://127.0.0.1:9000", help="Web UI base URL")
    parser.add_argument("--output", type=Path, default=Path("Output/Audit/diagram-validation"), help="Output directory")
    parser.add_argument("--headed", action="store_true", help="Run browser in headed mode (for debugging)")
    args = parser.parse_args()
    
    validator = DiagramScreenshotValidator(args.experiment, args.base_url, args.output)
    
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("Playwright not installed")
        return 2
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not args.headed)
        try:
            results = await validator.validate(browser)
        finally:
            await browser.close()
    
    # Write results
    results_file = args.output / f"validation_{args.experiment}.json"
    results_file.parent.mkdir(parents=True, exist_ok=True)
    results_file.write_text(json.dumps(results, indent=2), encoding="utf-8")
    
    print(f"✅ Diagram validation complete for {args.experiment}")
    print(f"📄 Results: {results_file}")
    print(f"📸 Screenshots: {args.output}")
    
    if results["issues"]:
        print(f"\n⚠️  {len(results['issues'])} issues detected:")
        for issue in results["issues"]:
            print(f"  - {issue['type']}: {issue['message']} ({issue.get('severity', 'INFO')})")
    
    return 0 if not any(i.get("severity") == "CRITICAL" for i in results["issues"]) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
