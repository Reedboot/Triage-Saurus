#!/usr/bin/env python3
"""Test /cloud endpoint diagram rendering with Playwright."""

import asyncio
import sys
import os
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from playwright.async_api import async_playwright, expect
from playwright.sync_api import sync_playwright


def test_cloud_diagram_rendering():
    """Test /cloud page loads and renders Mermaid diagrams."""
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 1024})
        
        try:
            # Navigate to /cloud page
            print("📍 Navigating to http://localhost:9000/cloud...")
            page.goto("http://localhost:9000/cloud", wait_until="networkidle", timeout=15000)
            
            # Take initial screenshot
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            screenshot_dir = Path("/tmp/screenshots")
            screenshot_dir.mkdir(exist_ok=True)
            
            # Screenshot 1: Page loaded
            page.screenshot(path=str(screenshot_dir / f"cloud_1_loaded_{timestamp}.png"))
            print(f"✓ Screenshot 1: Page loaded → {screenshot_dir}/cloud_1_loaded_{timestamp}.png")
            
            # Wait for subscriptions table to load
            print("⏳ Waiting for subscriptions table...")
            page.wait_for_selector("table tbody tr", timeout=5000)
            page.screenshot(path=str(screenshot_dir / f"cloud_2_table_{timestamp}.png"))
            print(f"✓ Screenshot 2: Table loaded → {screenshot_dir}/cloud_2_table_{timestamp}.png")
            
            # Click first "View Diagram" button
            view_btns = page.query_selector_all("button.view-diagram-btn")
            if view_btns:
                print(f"📊 Found {len(view_btns)} diagram buttons, clicking first one...")
                view_btns[0].click()
                
                # Wait for diagram to render (mermaid SVG)
                print("⏳ Waiting for Mermaid SVG to render...")
                page.wait_for_selector(".diagram-container svg", timeout=10000)
                page.screenshot(path=str(screenshot_dir / f"cloud_3_diagram_{timestamp}.png"))
                print(f"✓ Screenshot 3: Diagram rendered → {screenshot_dir}/cloud_3_diagram_{timestamp}.png")
                
                # Check for Internet entry points in diagram
                diagram_html = page.content()
                
                if "internet" in diagram_html.lower() or "entry" in diagram_html.lower():
                    print("✓ Internet entry points found in diagram HTML")
                else:
                    print("⚠ No obvious 'internet' or 'entry' keywords found")
                
                # Check for SVG elements
                svg_count = len(page.query_selector_all("svg"))
                print(f"✓ Found {svg_count} SVG elements")
                
                # Check for mermaid-specific elements
                mermaid_elements = len(page.query_selector_all(".mermaid, [class*='mermaid']"))
                print(f"✓ Found {mermaid_elements} mermaid-related elements")
                
                # Get diagram text content
                diagram_text = page.text_content(".diagram-container")
                print(f"📝 Diagram content preview:\n{diagram_text[:300]}...")
                
                # Screenshot with network tab to verify data loading
                page.screenshot(path=str(screenshot_dir / f"cloud_4_final_{timestamp}.png"))
                print(f"✓ Screenshot 4: Final state → {screenshot_dir}/cloud_4_final_{timestamp}.png")
                
                print("\n✅ Diagram rendering test PASSED")
                return True
            else:
                print("⚠ No diagram buttons found")
                return False
                
        except Exception as e:
            print(f"❌ Test failed: {e}")
            page.screenshot(path=str(screenshot_dir / f"cloud_error_{timestamp}.png"))
            print(f"Error screenshot: {screenshot_dir}/cloud_error_{timestamp}.png")
            raise
        finally:
            browser.close()


if __name__ == "__main__":
    test_cloud_diagram_rendering()
