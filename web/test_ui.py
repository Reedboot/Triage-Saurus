#!/usr/bin/env python3
"""Playwright end-to-end tests for the Triage-Saurus web UI.

Starts a real Flask dev server on port 9001 (to avoid clashing with any
running instance on 9000) and drives Chromium through the UI.

Run with:
    pytest web/test_ui.py --headed          # show browser window
    pytest web/test_ui.py                   # headless (CI default)
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests as _requests
from playwright.sync_api import Page, expect

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_PORT = 9001
BASE_URL = f"http://localhost:{TEST_PORT}"


# ---------------------------------------------------------------------------
# Session-scoped fixture: live Flask server
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def live_server():
    """Start the Flask app on TEST_PORT for the full test session."""
    python_exe = str(REPO_ROOT / ".venv" / "bin" / "python3")
    if not Path(python_exe).exists():
        python_exe = sys.executable

    env = os.environ.copy()
    env["FLASK_APP"] = "web/app.py"
    env["FLASK_DEBUG"] = "0"
    env["TRIAGE_DEBUG"] = "0"

    proc = subprocess.Popen(
        [python_exe, "-m", "flask", "run", "--host", "0.0.0.0",
         "--port", str(TEST_PORT), "--no-reload"],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Wait up to 20 s for the TCP port to accept connections
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("localhost", TEST_PORT), timeout=1):
                break
        except OSError:
            time.sleep(0.4)
    else:
        proc.terminate()
        proc.wait()
        pytest.fail(f"Flask server did not start on port {TEST_PORT} within 20 s")

    # Extra half-second for Jinja templates to finish loading
    time.sleep(0.5)
    yield BASE_URL

    proc.terminate()
    proc.wait()


# ---------------------------------------------------------------------------
# Convenience: all tests receive `page` already navigated to "/"
# ---------------------------------------------------------------------------
@pytest.fixture()
def home(page: Page, live_server: str) -> Page:
    page.goto(live_server + "/")
    return page


# ---------------------------------------------------------------------------
# Tests — page structure
# ---------------------------------------------------------------------------

class TestPageLoad:
    def test_title(self, home: Page):
        """Browser tab title must include 'Triage-Saurus'."""
        expect(home).to_have_title("Triage-Saurus — Repo Scanner")

    def test_header_dino(self, home: Page):
        """Dino emoji is visible in the header."""
        dino = home.locator("header .dino")
        expect(dino).to_be_visible()
        assert "🦖" in dino.inner_text()

    def test_header_h1(self, home: Page):
        """<h1> says Triage-Saurus."""
        expect(home.locator("header h1")).to_have_text("Triage-Saurus")

    def test_header_badge(self, home: Page):
        """Badge shows 'Repo Scanner'."""
        expect(home.locator("header .badge")).to_have_text("Repo Scanner")


class TestScanForm:
    def test_repo_select_present(self, home: Page):
        """Repository <select> exists and is visible."""
        expect(home.locator("#repo-select")).to_be_visible()

    def test_repo_select_has_placeholder(self, home: Page):
        """Dropdown placeholder option is present."""
        opts = home.locator("#repo-select option").all()
        texts = [o.inner_text() for o in opts]
        assert any("select a repository" in t.lower() for t in texts), (
            f"No placeholder option found; options: {texts}"
        )

    def test_repo_select_has_repo_options(self, home: Page):
        """Dropdown is populated with at least one real repository option."""
        # Exclude the disabled placeholder – look for enabled options
        enabled = home.locator("#repo-select option:not([disabled])")
        assert enabled.count() >= 1, "No repository options found in the dropdown"

    def test_run_scan_button(self, home: Page):
        """▶ Run Scan button is visible and enabled."""
        btn = home.locator("#scan-btn")
        expect(btn).to_be_visible()
        expect(btn).to_be_enabled()


    def test_hide_diagram_button(self, home: Page):
        """Hide/show diagram toggle button is visible."""
        expect(home.locator("#toggle-diagram-btn-persistent")).to_be_visible()


class TestCompareRow:
    def test_compare_row_visible(self, home: Page):
        """Compare-scans row is rendered in the DOM."""
        expect(home.locator("#compare-row")).to_be_attached()

    def test_compare_from_select(self, home: Page):
        """'From' scan selector exists."""
        expect(home.locator("#compare-from-select")).to_be_attached()

    def test_compare_to_select(self, home: Page):
        """'To' scan selector exists."""
        expect(home.locator("#compare-to-select")).to_be_attached()

    def test_run_compare_button(self, home: Page):
        """Run Compare button is visible."""
        expect(home.locator("#run-compare-btn")).to_be_visible()


class TestLogPanel:
    def test_log_panel_present(self, home: Page):
        """Log panel is present and visible."""
        expect(home.locator("#log-panel")).to_be_visible()

    def test_log_panel_title(self, home: Page):
        """Log panel header says 'Scan Output'."""
        title = home.locator("#log-panel .panel-title")
        expect(title).to_have_text("Scan Output")

    def test_copy_log_button(self, home: Page):
        """📋 Copy button is in the log panel toolbar."""
        expect(home.locator("#copy-log-btn")).to_be_visible()

    def test_clear_log_button(self, home: Page):
        """🧹 Clear button is in the log panel toolbar."""
        expect(home.locator("#clear-log-btn")).to_be_visible()

    def test_sections_button(self, home: Page):
        """📑 Sections button is in the log panel toolbar."""
        expect(home.locator("#toggle-sections-btn")).to_be_visible()

    def test_log_auto_scroll_toggle_button(self, home: Page):
        """Auto-scroll button pauses and resumes the live tail."""
        log_output = home.locator("#log-output")
        button = home.locator("#toggle-log-autoscroll-btn")
        expect(button).to_be_visible()

        home.evaluate(
            """
            () => {
              const el = document.getElementById('log-output');
              el.innerHTML = '';
              for (let i = 0; i < 140; i++) {
                const line = document.createElement('div');
                line.textContent = `seed line ${i}`;
                el.appendChild(line);
              }
              el.scrollTop = el.scrollHeight;
              el.dispatchEvent(new Event('scroll'));
            }
            """
        )
        home.wait_for_timeout(100)
        assert log_output.evaluate(
            "el => el.scrollTop + el.clientHeight >= el.scrollHeight - 4"
        )

        button.click()
        home.wait_for_timeout(100)
        assert "resume auto-scroll" in button.inner_text().lower()

        paused_scroll_top = log_output.evaluate("el => el.scrollTop")
        home.evaluate("window._triage.appendLog('paused-by-button')")
        home.wait_for_timeout(100)
        assert log_output.evaluate(
            "(el, expected) => Math.abs(el.scrollTop - expected) <= 4",
            paused_scroll_top,
        )

        button.click()
        home.wait_for_timeout(100)
        assert "pause auto-scroll" in button.get_attribute("title").lower()
        assert log_output.evaluate(
            "el => el.scrollTop + el.clientHeight >= el.scrollHeight - 4"
        )

    def test_initial_log_placeholder(self, home: Page):
        """Before any scan, the log area shows placeholder text."""
        log_output = home.locator("#log-output")
        expect(log_output).to_be_visible()
        assert "scan output will appear here" in log_output.inner_text().lower()

    def test_section_tab_bar_placeholder(self, home: Page):
        """Section tab bar shows 'Run or load a scan' before first scan."""
        placeholder = home.locator("#tab-bar-placeholder")
        expect(placeholder).to_be_attached()
        assert "run or load a scan" in placeholder.inner_text().lower()

    def test_log_auto_scroll_pauses_when_scrolled_up(self, home: Page):
        """New log lines keep the tail in view unless the user scrolls away."""
        log_output = home.locator("#log-output")
        home.wait_for_function("window._triage && typeof window._triage.appendLog === 'function'")

        home.evaluate(
            """
            () => {
              const el = document.getElementById('log-output');
              el.innerHTML = '';
              for (let i = 0; i < 140; i++) {
                const line = document.createElement('div');
                line.textContent = `seed line ${i}`;
                el.appendChild(line);
              }
              el.scrollTop = el.scrollHeight;
              el.dispatchEvent(new Event('scroll'));
            }
            """
        )
        home.wait_for_timeout(100)

        assert log_output.evaluate(
            "el => el.scrollTop + el.clientHeight >= el.scrollHeight - 4"
        )

        home.evaluate(
            """
            () => {
              const el = document.getElementById('log-output');
              el.scrollTop = 0;
              el.dispatchEvent(new Event('scroll'));
            }
            """
        )
        home.evaluate("window._triage.appendLog('paused auto-scroll test line')")
        home.wait_for_timeout(100)
        assert log_output.evaluate("el => el.scrollTop <= 4")

        home.evaluate(
            """
            () => {
              const el = document.getElementById('log-output');
              el.scrollTop = el.scrollHeight;
              el.dispatchEvent(new Event('scroll'));
            }
            """
        )
        home.evaluate("window._triage.appendLog('resumed auto-scroll test line')")
        home.wait_for_timeout(100)
        assert log_output.evaluate(
            "el => el.scrollTop + el.clientHeight >= el.scrollHeight - 4"
        )

    def test_log_stream_does_not_grow_page(self, home: Page):
        """Streaming output stays inside the panel instead of extending the page."""
        home.wait_for_function("window._triage && typeof window._triage.appendLog === 'function'")

        home.evaluate(
            """
            () => {
              const el = document.getElementById('log-output');
              el.innerHTML = '';
              for (let i = 0; i < 240; i++) {
                const line = document.createElement('div');
                line.textContent = `stream line ${i}`;
                el.appendChild(line);
              }
              el.scrollTop = el.scrollHeight;
              el.dispatchEvent(new Event('scroll'));
            }
            """
        )
        home.wait_for_timeout(100)

        assert home.evaluate(
            "() => document.scrollingElement.scrollHeight <= window.innerHeight + 2"
        )
        assert home.locator("#log-output").evaluate(
            "el => el.scrollTop + el.clientHeight >= el.scrollHeight - 4"
        )


class TestDiagramPanel:
    def test_diagram_panel_present(self, home: Page):
        """Diagram panel is present."""
        expect(home.locator("#diagram-panel")).to_be_visible()

    def test_diagram_panel_title(self, home: Page):
        """Diagram panel header says 'Architecture'."""
        expect(home.locator(".right-panel-title")).to_have_text("Architecture")

    def test_zoom_in_button(self, home: Page):
        """🔍+ Zoom In button is visible."""
        expect(home.locator("#zoom-in-btn")).to_be_visible()

    def test_zoom_out_button(self, home: Page):
        """🔍- Zoom Out button is visible."""
        expect(home.locator("#zoom-out-btn")).to_be_visible()

    def test_zoom_reset_button(self, home: Page):
        """🎯 Fit button is visible."""
        expect(home.locator("#zoom-reset-btn")).to_be_visible()

    def test_refresh_diagram_button(self, home: Page):
        """🔄 Refresh diagram button is visible."""
        expect(home.locator("#refresh-diagram-btn")).to_be_visible()

    def test_architecture_ai_button(self, home: Page):
        """🤖 Architecture AI button is visible."""
        expect(home.locator("#architecture-run-ai-btn")).to_be_visible()

    def test_copy_diagram_button(self, home: Page):
        """📋 Copy source button is visible."""
        expect(home.locator("#copy-diagram-btn")).to_be_visible()

    def test_export_svg_button(self, home: Page):
        """⬇ SVG export button is visible."""
        expect(home.locator("#export-diagram-svg-btn")).to_be_visible()

    def test_export_png_button(self, home: Page):
        """⬇ PNG export button is visible."""
        expect(home.locator("#export-diagram-png-btn")).to_be_visible()

    def test_diagram_placeholder_shown(self, home: Page):
        """Before a scan, the diagram area shows a placeholder."""
        placeholder = home.locator("#diagram-placeholder")
        expect(placeholder).to_be_visible()
        assert "architecture diagram" in placeholder.inner_text().lower()

    def test_toggle_log_button(self, home: Page):
        """📜 Hide/show scan log button is visible in the diagram panel."""
        expect(home.locator("#toggle-log-btn")).to_be_visible()

    def test_rendered_diagram_is_centered_and_fit(self, home: Page):
        """Rendered Mermaid diagrams should center inside the architecture panel."""
        home.wait_for_function("window._triage && typeof window._triage.renderDiagrams === 'function'")
        home.evaluate(
            """
            () => {
              window._triage.renderDiagrams([{
                title: 'Centering test',
                code: 'flowchart LR; A[Start] --> B[Step 1] --> C[Step 2] --> D[Step 3] --> E[Step 4] --> F[Step 5] --> G[Step 6] --> H[End]'
              }]);
            }
            """
        )
        home.wait_for_selector("#diagram-views svg", state="attached", timeout=15000)
        home.wait_for_timeout(1000)

        wrap_box = home.locator("#diagram-zoom-wrap").bounding_box()
        svg_box = home.locator("#diagram-views svg").bounding_box()
        assert wrap_box and svg_box, "Expected both the panel and diagram to have layout boxes"

        wrap_center_x = wrap_box["x"] + (wrap_box["width"] / 2)
        wrap_center_y = wrap_box["y"] + (wrap_box["height"] / 2)
        svg_center_x = svg_box["x"] + (svg_box["width"] / 2)
        svg_center_y = svg_box["y"] + (svg_box["height"] / 2)

        assert abs(svg_center_x - wrap_center_x) < wrap_box["width"] * 0.15
        assert abs(svg_center_y - wrap_center_y) < wrap_box["height"] * 0.25

    def test_rendered_diagram_svg_fills_wrapper(self, home: Page):
        """Rendered Mermaid SVG should fill the active diagram wrapper."""
        home.wait_for_function("window._triage && typeof window._triage.renderDiagrams === 'function'")
        home.evaluate(
            """
            () => {
              window._triage.renderDiagrams([{
                title: 'Wrapper fit test',
                code: 'flowchart TB; A[Start] --> B[Step 1] --> C[Step 2] --> D[Step 3] --> E[End]'
              }]);
            }
            """
        )
        home.wait_for_selector("#diagram-views svg", state="attached", timeout=15000)
        home.wait_for_timeout(500)

        sizes = home.evaluate(
            """
            () => {
              const svg = document.querySelector('#diagram-views svg');
              const mermaid = document.querySelector('#diagram-views .diagram-view.active .mermaid');
              const view = document.querySelector('#diagram-views .diagram-view.active');
              const svgStyle = getComputedStyle(svg);
              const mermaidStyle = getComputedStyle(mermaid);
              const viewStyle = getComputedStyle(view);
              return {
                svgWidth: parseFloat(svgStyle.width),
                svgHeight: parseFloat(svgStyle.height),
                mermaidWidth: parseFloat(mermaidStyle.width),
                mermaidHeight: parseFloat(mermaidStyle.height),
                viewWidth: parseFloat(viewStyle.width),
                viewHeight: parseFloat(viewStyle.height),
              };
            }
            """
        )

        assert abs(sizes["svgWidth"] - sizes["mermaidWidth"]) < 1.0
        assert abs(sizes["svgHeight"] - sizes["mermaidHeight"]) < 1.0
        assert sizes["viewWidth"] - sizes["mermaidWidth"] < 24
        assert sizes["viewHeight"] - sizes["mermaidHeight"] < 24

    def test_high_zoom_pan_moves_by_drag_distance(self, home: Page):
        """Dragging at high zoom should move the diagram by the cursor distance."""
        home.wait_for_function("window._triage && typeof window._triage.renderDiagrams === 'function'")
        home.evaluate(
            """
            () => {
              window._triage.renderDiagrams([{
                title: 'Pan test',
                code: 'flowchart LR; A[Start] --> B[Step 1] --> C[Step 2] --> D[Step 3] --> E[Step 4] --> F[Step 5] --> G[Step 6] --> H[End]'
              }]);
            }
            """
        )
        home.wait_for_selector("#diagram-views svg", state="attached", timeout=15000)
        home.wait_for_timeout(1000)

        zoom_btn = home.locator("#zoom-in-btn")

        def current_scale() -> float:
            return home.evaluate(
                """
                () => {
                  const transform = document.getElementById('diagram-zoom-inner')?.style.transform || '';
                  const match = transform.match(/scale\\(([^)]+)\\)/);
                  return match ? parseFloat(match[1]) : 1;
                }
                """
            )

        for _ in range(25):
            if current_scale() >= 3.0:
                break
            zoom_btn.click()
            home.wait_for_timeout(50)

        assert current_scale() >= 3.0, "Expected the diagram to reach 300%+ zoom"

        svg = home.locator("#diagram-views svg")
        before = svg.bounding_box()
        assert before, "Expected SVG to have a layout box"

        home.evaluate(
            """
            () => {
              const svg = document.querySelector('#diagram-views svg');
              const rect = svg.getBoundingClientRect();
              const startX = rect.left + 20;
              const startY = rect.top + 20;
              svg.dispatchEvent(new MouseEvent('mousedown', {
                bubbles: true,
                cancelable: true,
                clientX: startX,
                clientY: startY,
                button: 0,
              }));
              svg.dispatchEvent(new MouseEvent('mousemove', {
                bubbles: true,
                cancelable: true,
                clientX: startX + 20,
                clientY: startY,
                buttons: 1,
              }));
              svg.dispatchEvent(new MouseEvent('mouseup', {
                bubbles: true,
                cancelable: true,
                clientX: startX + 20,
                clientY: startY,
                button: 0,
              }));
            }
            """
        )
        home.wait_for_timeout(250)

        after = svg.bounding_box()
        assert after, "Expected diagram SVG to still have a layout box after dragging"

        moved_x = after["x"] - before["x"]
        assert 12 <= moved_x <= 30, f"Unexpected drag movement at high zoom: {moved_x}px"


# ---------------------------------------------------------------------------
# Tests — API smoke tests (no browser needed, plain HTTP)
# ---------------------------------------------------------------------------

class TestApiSmoke:
    def test_root_returns_200(self, live_server: str):
        resp = _requests.get(live_server + "/", timeout=5)
        assert resp.status_code == 200

    def test_root_content_type_html(self, live_server: str):
        resp = _requests.get(live_server + "/", timeout=5)
        assert "text/html" in resp.headers.get("Content-Type", "")

    def test_api_scans_unknown_repo_returns_json(self, live_server: str):
        """GET /api/scans/<unknown> must return JSON (even if it's an empty list)."""
        resp = _requests.get(live_server + "/api/scans/no-such-repo-xyz", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_api_scan_requires_post(self, live_server: str):
        """GET /scan should return 405 (POST only)."""
        resp = _requests.get(live_server + "/scan", timeout=5)
        assert resp.status_code == 405

    def test_api_scan_post_missing_body_returns_400(self, live_server: str):
        """POST /scan without repo_path returns 400."""
        resp = _requests.post(live_server + "/scan", data={}, timeout=5)
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Tests — UI interactions
# ---------------------------------------------------------------------------

class TestInteractions:
    def test_hide_diagram_toggle(self, home: Page):
        """Clicking 'Hide diagram' button changes its text."""
        btn = home.locator("#toggle-diagram-btn-persistent")
        initial_text = btn.inner_text()
        btn.click()
        # After click the label should toggle (Hide ↔ Show)
        new_text = btn.inner_text()
        assert initial_text != new_text, (
            "Button text did not change after click — toggle may be broken"
        )

    def test_hide_log_toggle(self, home: Page):
        """Clicking the 'Hide scan' button hides the log panel and expands the diagram."""
        btn = home.locator("#toggle-log-btn")
        log_panel = home.locator("#log-panel")
        workspace = home.locator(".workspace")
        diagram_panel = home.locator("#diagram-panel")
        expect(log_panel).to_be_visible()
        btn.click()
        home.wait_for_timeout(150)
        assert not log_panel.is_visible(), "Log panel did not hide after clicking the toggle button"

        workspace_classes = workspace.get_attribute("class") or ""
        assert "collapsed" in workspace_classes, "Workspace did not switch to the collapsed layout"

        ws_box = workspace.bounding_box()
        diagram_box = diagram_panel.bounding_box()
        assert ws_box and diagram_box, "Expected workspace and diagram panel layout boxes"
        assert abs(diagram_box["x"] - ws_box["x"]) < 4
        assert diagram_box["width"] >= ws_box["width"] - 12

        btn.click()
        home.wait_for_timeout(150)
        assert log_panel.is_visible(), "Log panel did not reappear after toggling back"
        assert "collapsed" not in (workspace.get_attribute("class") or "")

    def test_run_compare_without_selection_shows_alert(self, home: Page):
        """Clicking Run Compare without choosing scans triggers a browser alert."""
        alerted: list[str] = []
        home.on("dialog", lambda d: (alerted.append(d.message), d.accept()))
        home.locator("#run-compare-btn").click()
        home.wait_for_timeout(500)
        assert alerted, "No alert was raised when clicking Run Compare without a selection"
        assert "select" in alerted[0].lower() or "compare" in alerted[0].lower()

    def test_past_scans_row_hidden_initially(self, home: Page):
        """Past-scans row should not be visible before a repo is selected."""
        row = home.locator("#past-scans-row")
        # It should be in the DOM but not visually shown
        classes = row.get_attribute("class") or ""
        style = row.get_attribute("style") or ""
        assert not row.is_visible() or "display: none" in style, (
            "Past-scans row is unexpectedly visible before any repo is selected"
        )
