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
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, expect

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
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
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
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
    page.goto(live_server + "/", wait_until="domcontentloaded", timeout=60000)
    return page


# ---------------------------------------------------------------------------
# Tests — page structure
# ---------------------------------------------------------------------------

class TestPageLoad:
    def test_title(self, home: Page):
        """Browser tab title must include 'Triage-Saurus'."""
        expect(home).to_have_title("Triage-Saurus")

    def test_header_dinos(self, home: Page):
        """Dino emoji appears on both sides of the title."""
        dinos = home.locator("header .header-brand .dino")
        expect(dinos).to_have_count(2)
        assert all("🦖" in d for d in dinos.all_inner_texts())

    def test_header_h1(self, home: Page):
        """<h1> says Triage-Saurus."""
        expect(home.locator("header h1")).to_have_text("Triage-Saurus")

    def test_header_has_no_badge(self, home: Page):
        """Legacy badge should not be present."""
        expect(home.locator("header .badge")).to_have_count(0)


class TestScanForm:
    def test_repo_select_present(self, home: Page):
        """Repository selector exists and is visible (custom searchable dropdown)."""
        # Check for the custom dropdown container
        expect(home.locator(".repo-selector-container")).to_be_visible()
        # Verify the hidden select still exists for form submission
        expect(home.locator("#repo-select")).to_have_count(1)

    def test_repo_select_has_placeholder(self, home: Page):
        """Dropdown placeholder option is present."""
        opts = home.locator("#repo-select option").all()
        texts = [o.inner_text() for o in opts]
        assert any("choose a repo" in t.lower() for t in texts), (
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

    def test_clearing_repo_hides_diagram_loading_overlay(self, home: Page):
        """Clearing repo selection should never leave the diagram loading overlay visible."""
        home.evaluate(
            """
            () => {
              const select = document.querySelector('#repo-select');
              const enabledOption = select?.querySelector('option:not([disabled])');
              if (enabledOption) {
                select.value = enabledOption.value;
                select.dispatchEvent(new Event('change', { bubbles: true }));
              }
              const overlay = document.querySelector('#diagram-loading-overlay');
              if (overlay) overlay.hidden = false;
              if (select) {
                select.value = '';
                select.dispatchEvent(new Event('change', { bubbles: true }));
              }
            }
            """
        )
        expect(home.locator("#diagram-loading-overlay")).to_be_hidden()


    def test_hide_diagram_button(self, home: Page):
        """Hide/show diagram toggle button is visible."""
        expect(home.locator("#toggle-diagram-btn-persistent")).to_be_visible()


class TestCompareRow:
    def test_compare_row_not_rendered(self, home: Page):
        """Legacy compare-scans row is not rendered in the current UI."""
        expect(home.locator("#compare-row")).to_have_count(0)

    def test_compare_from_select_not_rendered(self, home: Page):
        """Legacy compare 'From' selector is not rendered."""
        expect(home.locator("#compare-from-select")).to_have_count(0)

    def test_compare_to_select_not_rendered(self, home: Page):
        """Legacy compare 'To' selector is not rendered."""
        expect(home.locator("#compare-to-select")).to_have_count(0)

    def test_run_compare_button_not_rendered(self, home: Page):
        """Legacy run-compare button is not rendered."""
        expect(home.locator("#run-compare-btn")).to_have_count(0)


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

    def test_architecture_ai_progress_bar_present(self, home: Page):
        """Architecture AI progress bar exists and starts hidden."""
        progress = home.locator("#architecture-ai-progress")
        expect(progress).to_be_attached()
        expect(progress).to_be_hidden()

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

    def test_run_scan_clears_previous_diagram_tabs(self, home: Page):
        """Clicking Run Scan should clear previously rendered diagram tabs."""
        home.wait_for_function("window._triage && typeof window._triage.renderDiagrams === 'function'")
        home.evaluate(
            """
            () => {
              window._triage.renderDiagrams([{
                title: 'Stale diagram',
                code: 'flowchart LR; A[Old] --> B[Diagram]'
              }]);
            }
            """
        )
        home.wait_for_selector("#diagram-views svg", state="attached", timeout=15000)

        home.route(
            "**/api/scans/**",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body='{"running_experiment": null}',
            ),
        )
        home.route(
            "**/api/detect-modules",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body='{"modules": []}',
            ),
        )
        home.route(
            "**/scan",
            lambda route: route.fulfill(
                status=200,
                content_type="text/event-stream",
                body='event: experiment\ndata: "test-exp"\n\nevent: done\ndata: {"experiment_id":"test-exp","status":"complete","exit_code":0}\n\n',
            ),
        )

        repo_select = home.locator("#repo-select")
        repo_value = repo_select.evaluate(
            "select => Array.from(select.options).find(option => !option.disabled)?.value || ''"
        )
        assert repo_value, "Expected at least one selectable repository"
        home.evaluate(
            """
            (value) => {
              const select = document.querySelector('#repo-select');
              if (!select) return;
              select.value = value;
              select.dispatchEvent(new Event('change', { bubbles: true }));
            }
            """,
            repo_value,
        )

        home.locator("#scan-btn").click()
        home.wait_for_timeout(200)

        expect(home.locator("#diagram-tabs button")).to_have_count(0)

    def test_not_found_module_confirm_enables_scan(self, home: Page):
        """Confirming a path for a not-found module should select it and enable module scan."""
        try:
            home.wait_for_function(
                "window.Alpine && window.Alpine.store && window.Alpine.store('scan')",
                timeout=10000,
            )
        except PlaywrightTimeoutError:
            pytest.skip("Alpine store unavailable (likely CDN blocked in test environment)")
        home.evaluate(
            """
            () => {
              const store = window.Alpine.store('scan');
              store.detectedModules = [{
                name: 'missing-module',
                inferred_type: 'terraform_module',
                source: 'git::https://example.invalid/missing-module',
                source_file: 'infra/main.tf',
                source_line: 7,
                found_in_repos: false,
                already_scanned: false,
                module_repo_name: null,
                module_repo_path: null,
                selected: false,
                userProvidedPath: '',
                pathInputVisible: true,
                editingPath: false,
                pathValidated: false
              }];
              store.moduleModalVisible = true;
            }
            """
        )

        path_input = home.locator("#module-list .module-item input[type='text']").first
        path_input.fill("/tmp/missing-module")
        home.locator("#module-list .module-item button:has-text('Confirm')").first.click()

        assert home.evaluate(
            """
            () => {
              const mod = window.Alpine.store('scan').detectedModules[0];
              return !!mod.selected &&
                     mod.module_repo_path === '/tmp/missing-module' &&
                     mod.pathInputVisible === false &&
                     mod.pathValidated === true;
            }
            """
        )
        expect(home.locator("#module-scan")).to_be_enabled()

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

    def test_architecture_mode_tabs_and_summary_render(self, home: Page):
        """Architecture diagrams with overlay views should render mode tabs and update the summary."""
        home.wait_for_function("window._triage && typeof window._triage.renderDiagrams === 'function'")
        home.evaluate(
            """
            () => {
              window._triage.renderDiagrams([{
                title: 'Azure Architecture',
                code: 'flowchart LR; A[Gateway] --> B[API]',
                default_view: 'connectivity',
                views: {
                  connectivity: {
                    code: 'flowchart LR; A[Gateway] --> B[API]',
                    title: 'Connectivity view',
                    description: 'Shows the full provider topology.',
                    legend: ['White edges: service relationships'],
                    asset_summary: { entry_points: 1, api_layer: 1, backends: 1, data_stores: 1, public_assets: 1 },
                    attack_paths: [{ title: 'Public ingress into architecture' }]
                  },
                  exposure: {
                    code: 'flowchart LR; Internet --> A[Gateway] --> B[API]',
                    title: 'Exposure view',
                    description: 'Shows internet-facing resources.',
                    legend: ['Red edges: direct public exposure'],
                    asset_summary: { entry_points: 1, api_layer: 1, backends: 1, data_stores: 1, public_assets: 1 },
                    attack_paths: [{ title: 'Public ingress into architecture' }]
                  },
                  attack_paths: {
                    code: 'flowchart LR; Internet -.-> A[Gateway] -.-> B[API]',
                    title: 'Attack-path view',
                    description: 'Shows plausible attacker movement.',
                    legend: ['Dashed red edges: attacker movement'],
                    asset_summary: { entry_points: 1, api_layer: 1, backends: 1, data_stores: 1, public_assets: 1 },
                    attack_paths: [{ title: 'Public ingress into architecture' }, { title: 'Secrets pivot from workloads' }]
                  }
                }
              }]);
            }
            """
        )
        home.wait_for_selector("#diagram-views svg", state="attached", timeout=15000)

        expect(home.locator("#diagram-mode-tabs button")).to_have_count(3)
        home.locator("#diagram-mode-tabs button:has-text('Attack Paths')").click()
        expect(home.locator("#diagram-view-summary")).to_contain_text("Likely attack paths")
        expect(home.locator("#diagram-view-summary")).to_contain_text("Secrets pivot from workloads")

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

        assert sizes["svgWidth"] > 0
        assert sizes["svgHeight"] > 0
        assert sizes["mermaidWidth"] > 0
        assert sizes["mermaidHeight"] > 0
        assert sizes["viewWidth"] > 0
        assert sizes["viewHeight"] > 0

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

    def test_wheel_zoom_is_gentle(self, home: Page):
        """Mouse wheel zoom should change scale more gradually than the buttons."""
        home.wait_for_function("window._triage && typeof window._triage.renderDiagrams === 'function'")
        home.evaluate(
            """
            () => {
              window._triage.renderDiagrams([{
                title: 'Wheel zoom test',
                code: 'flowchart LR; A[Start] --> B[End]'
              }]);
            }
            """
        )
        home.wait_for_selector("#diagram-views svg", state="attached", timeout=15000)
        home.wait_for_timeout(500)

        scale_before = home.evaluate(
            """
            () => {
              const transform = document.getElementById('diagram-zoom-inner')?.style.transform || '';
              const match = transform.match(/scale\\(([^)]+)\\)/);
              return match ? parseFloat(match[1]) : 1;
            }
            """
        )

        home.evaluate(
            """
            () => {
              const svg = document.querySelector('#diagram-views svg');
              svg.dispatchEvent(new WheelEvent('wheel', {
                bubbles: true,
                cancelable: true,
                deltaY: -100,
              }));
            }
            """
        )
        home.wait_for_timeout(250)

        scale_after = home.evaluate(
            """
            () => {
              const transform = document.getElementById('diagram-zoom-inner')?.style.transform || '';
              const match = transform.match(/scale\\(([^)]+)\\)/);
              return match ? parseFloat(match[1]) : 1;
            }
            """
        )

        assert scale_after > scale_before
        assert (scale_after / scale_before) < 1.02, (
            f"Wheel zoom is still too aggressive: {scale_before} -> {scale_after}"
        )

    def test_log_lines_get_source_classes(self, home: Page):
        """Scan log prefixes should map to distinct styling classes."""
        home.wait_for_function("window._triage && typeof window._triage.appendLog === 'function'")
        home.evaluate(
            """
            () => {
              const el = document.getElementById('log-output');
              el.innerHTML = '';
              window._triage.appendLog('[Web] Reconnecting to running experiment 001...');
              window._triage.appendLog('PHASE 1 — Detection (asset discovery)');
              window._triage.appendLog('────────────────────────────────────────');
            }
            """
        )

        classes = home.locator('#log-output > div').evaluate_all(
            "(els) => els.map(el => el.className)"
        )
        assert any('line-web' in c for c in classes), classes
        assert any('phase-1' in c for c in classes), classes
        assert any('line-sep' in c for c in classes), classes


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

    def test_run_compare_not_rendered(self, home: Page):
        """Legacy compare action is not rendered in the current UI."""
        expect(home.locator("#run-compare-btn")).to_have_count(0)

    def test_past_scans_row_hidden_initially(self, home: Page):
        """Past-scans row should not be visible before a repo is selected."""
        row = home.locator("#past-scans-row")
        # It should be in the DOM but not visually shown
        classes = row.get_attribute("class") or ""
        style = row.get_attribute("style") or ""
        assert not row.is_visible() or "display: none" in style, (
            "Past-scans row is unexpectedly visible before any repo is selected"
        )


# ---------------------------------------------------------------------------
# Cloud page (subscription architecture diagrams) tests
# ---------------------------------------------------------------------------

class TestCloudPage:
    """Tests for /cloud subscription list, ingress diagrams, and drill-down."""

    _SUB_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    # Mermaid source that deliberately uses DUPLICATE FQDN labels on both
    # Internet→listener arrows — this is the buggy output the Python backend
    # used to produce.  The frontend fix in patchForeignObjectLabels must not
    # create extra SVG <text> nodes, and the Python backend fix must emit
    # distinct labels instead.
    _MERMAID_DUPLICATE_LABELS = (
        "graph LR\n"
        '    Internet["🌐 Internet"]\n'
        '    test_rg_appgw["App Gateway"]\n'
        '    l_test_rg_appgw_HTTPS_443["🔒 HTTPS:443"]\n'
        '    l_test_rg_appgw_HTTP_80["🔴 HTTP:80"]\n'
        '    Internet -->|"gw.example.com"| l_test_rg_appgw_HTTPS_443\n'
        '    Internet -->|"gw.example.com"| l_test_rg_appgw_HTTP_80\n'
        "    l_test_rg_appgw_HTTPS_443 --> test_rg_appgw\n"
        "    l_test_rg_appgw_HTTP_80 --> test_rg_appgw\n"
        "    linkStyle 0 stroke:orange,stroke-width:2px\n"
        "    linkStyle 1 stroke:red,stroke-width:2px\n"
        "    linkStyle 2 stroke:orange,stroke-width:2px\n"
        "    linkStyle 3 stroke:orange,stroke-width:2px\n"
        "    classDef internet stroke:#d32f2f,stroke-width:2px;\n"
        "    classDef entryPoint stroke:#d32f2f,stroke-width:2px;\n"
        "    classDef listenerHttps stroke:#00897b,stroke-width:2px;\n"
        "    classDef listenerHttp stroke:#d32f2f,stroke-width:2px,stroke-dasharray:4,2;\n"
        "    class Internet internet;\n"
        "    class test_rg_appgw entryPoint;\n"
        "    class l_test_rg_appgw_HTTPS_443 listenerHttps;\n"
        "    class l_test_rg_appgw_HTTP_80 listenerHttp;\n"
    )

    # Same diagram but with distinct labels (what the backend should produce
    # after the fix).
    _MERMAID_DISTINCT_LABELS = (
        "graph LR\n"
        '    Internet["🌐 Internet"]\n'
        '    test_rg_appgw["App Gateway"]\n'
        '    l_test_rg_appgw_HTTPS_443["🔒 HTTPS:443"]\n'
        '    l_test_rg_appgw_HTTP_80["🔴 HTTP:80"]\n'
        '    Internet -->|"gw.example.com"| l_test_rg_appgw_HTTPS_443\n'
        '    Internet -->|"HTTP"| l_test_rg_appgw_HTTP_80\n'
        "    l_test_rg_appgw_HTTPS_443 --> test_rg_appgw\n"
        "    l_test_rg_appgw_HTTP_80 --> test_rg_appgw\n"
        "    linkStyle 0 stroke:orange,stroke-width:2px\n"
        "    linkStyle 1 stroke:red,stroke-width:2px\n"
        "    linkStyle 2 stroke:orange,stroke-width:2px\n"
        "    linkStyle 3 stroke:orange,stroke-width:2px\n"
        "    classDef internet stroke:#d32f2f,stroke-width:2px;\n"
        "    classDef entryPoint stroke:#d32f2f,stroke-width:2px;\n"
        "    classDef listenerHttps stroke:#00897b,stroke-width:2px;\n"
        "    classDef listenerHttp stroke:#d32f2f,stroke-width:2px,stroke-dasharray:4,2;\n"
        "    class Internet internet;\n"
        "    class test_rg_appgw entryPoint;\n"
        "    class l_test_rg_appgw_HTTPS_443 listenerHttps;\n"
        "    class l_test_rg_appgw_HTTP_80 listenerHttp;\n"
    )

    _NODE_MAP = {
        "test_rg_appgw": {
            "title": "test-appgw",
            "arm_type": "microsoft.network/applicationgateways",
            "resources": [{"rg": "test-rg", "name": "test-appgw"}],
            "can_drill": True,
        }
    }

    def _setup_mocks(self, page: Page, mermaid_source: str) -> None:
        """Route-mock the subscription list and diagram API endpoints."""
        import json

        page.route(
            "**/api/subscriptions",
            lambda route: route.fulfill(
                content_type="application/json",
                body=json.dumps({
                    "subscriptions": [{
                        "id": self._SUB_ID,
                        "display_name": "Test Subscription",
                        "environment": "production",
                        "env_badge": "danger",
                        "provider": "Azure",
                        "state": "Enabled",
                        "last_synced": None,
                        "asset_count": 5,
                        "public_count": 1,
                    }]
                }),
            ),
        )

        node_map = self._NODE_MAP
        ingress_views = {
            "connectivity": {
                "mermaid": mermaid_source,
                "css_code": "",
                "icon_map": {},
                "node_drilldown_map": node_map,
                "description": "Connectivity mock view",
                "legend": ["Connectivity legend"],
                "asset_summary": {"entry_points": 1, "api_layer": 0, "backends": 0, "data_stores": 0, "public_assets": 1},
            },
            "exposure": {
                "mermaid": mermaid_source,
                "css_code": "",
                "icon_map": {},
                "node_drilldown_map": node_map,
                "description": "Exposure mock view",
                "legend": ["Exposure legend"],
                "asset_summary": {"entry_points": 1, "api_layer": 0, "backends": 0, "data_stores": 0, "public_assets": 1},
            },
            "attack_paths": {
                "mermaid": mermaid_source,
                "css_code": "",
                "icon_map": {},
                "node_drilldown_map": node_map,
                "description": "Attack mock view",
                "legend": ["Attack legend"],
                "attack_paths": [{"title": "Mock attack", "path": "Internet -> test-appgw"}],
                "asset_summary": {"entry_points": 1, "api_layer": 0, "backends": 0, "data_stores": 0, "public_assets": 1},
            },
        }
        page.route(
            f"**/{self._SUB_ID}/diagram",
            lambda route: route.fulfill(
                content_type="application/json",
                body=json.dumps({
                    "subscription_name": "Test Subscription",
                    "environment": "production",
                    "total_assets": 5,
                    "ingress_diagram": {
                        "mermaid": mermaid_source,
                        "css_code": "",
                        "icon_map": {},
                        "node_drilldown_map": node_map,
                        "default_view": "connectivity",
                        "views": ingress_views,
                        "attack_paths": [{"title": "Mock attack", "path": "Internet -> test-appgw"}],
                        "asset_summary": {"entry_points": 1, "api_layer": 0, "backends": 0, "data_stores": 0, "public_assets": 1},
                    },
                    "diagrams": [{
                        "rg": "test-rg",
                        "mermaid": mermaid_source,
                        "css_code": "",
                        "icon_map": {},
                        "node_drilldown_map": node_map,
                        "asset_count": 2,
                        "public_count": 1,
                        "relationship_count": 1,
                        "default_view": "connectivity",
                        "views": ingress_views,
                        "attack_paths": [{"title": "Mock RG attack", "path": "Internet -> test-appgw"}],
                        "asset_summary": {"entry_points": 1, "api_layer": 0, "backends": 0, "data_stores": 0, "public_assets": 1},
                    }],
                }),
            ),
        )

        page.route(
            f"**/{self._SUB_ID}/drilldown",
            lambda route: route.fulfill(
                content_type="application/json",
                body=json.dumps({
                    "title": "App Gateway — Routing Rules",
                    "view_type": "table",
                    "columns": ["Gateway", "Listener / Hostname", "Protocol", "URL Path", "Backend Pool", "Backend Targets", "WAF Policy"],
                    "rows": [
                        ["test-appgw", "gw.example.com", "HTTPS", "/*", "backend-pool", "10.0.0.1:443", "—"],
                        ["test-appgw", "gw.example.com", "HTTP",  "/*", "backend-pool", "10.0.0.1:80",  "—"],
                    ],
                }),
            ),
        )

    def _load_diagram(self, page: Page, live_server: str, mermaid_source: str) -> None:
        """Navigate to /cloud, mock APIs, click the subscription, wait for SVG."""
        self._setup_mocks(page, mermaid_source)
        page.goto(live_server + "/cloud")
        page.wait_for_selector(".subscription-name-cell", timeout=8000)
        page.locator(".subscription-name-cell").first.click()
        page.wait_for_selector("#ingress-diagram-div svg", timeout=15000)
        page.wait_for_timeout(1500)  # allow post-processing to complete

    # ── Basic page structure ────────────────────────────────────────────────

    def test_cloud_page_loads(self, page: Page, live_server: str):
        """Cloud page renders with correct title."""
        page.goto(live_server + "/cloud")
        expect(page.locator("h1")).to_have_text("Cloud Subscriptions")

    def test_subscription_table_renders(self, page: Page, live_server: str):
        """Subscription list populates from the API."""
        self._setup_mocks(page, self._MERMAID_DISTINCT_LABELS)
        page.goto(live_server + "/cloud")
        page.wait_for_selector("#subscriptions-tbody tr", timeout=8000)
        rows = page.locator("#subscriptions-tbody tr")
        expect(rows).to_have_count(1)
        assert "Test Subscription" in rows.first.inner_text()

    def test_diagram_renders_on_subscription_click(self, page: Page, live_server: str):
        """Clicking a subscription row loads and renders the ingress SVG."""
        self._load_diagram(page, live_server, self._MERMAID_DISTINCT_LABELS)
        expect(page.locator("#ingress-diagram-div svg")).to_be_visible()

    def test_mode_toggle_renders_subscription_views(self, page: Page, live_server: str):
        """Overview/Attack-path mode buttons should render for subscription popup views."""
        self._load_diagram(page, live_server, self._MERMAID_DISTINCT_LABELS)
        expect(page.locator("#subscription-diagram-mode-host .diagram-mode-btn:has-text('Overview')")).to_be_visible()
        expect(page.locator("#subscription-diagram-mode-host .diagram-mode-btn:has-text('Attack paths')")).to_be_visible()
        page.locator("#subscription-diagram-mode-host .diagram-mode-btn:has-text('Attack paths')").click()
        expect(page.locator("#ingress-diagram-div-target-filter")).to_be_visible()

    # ── Bug #1: Internet node label must not be duplicated ─────────────────

    def test_internet_node_text_appears_once(self, page: Page, live_server: str):
        """Internet node label must NOT appear as a duplicate SVG <text> fallback.

        Root cause: patchForeignObjectLabels() was appending an extra SVG <text>
        fallback even when the browser was already rendering the <foreignObject>
        correctly, causing 'Internet' to show twice in different sizes.

        After the fix, the Internet label lives only inside the <foreignObject> HTML —
        there should be zero SVG <text> elements containing 'Internet'.
        """
        self._load_diagram(page, live_server, self._MERMAID_DISTINCT_LABELS)

        count = page.evaluate(
            """
            () => {
                const svg = document.querySelector('#ingress-diagram-div svg');
                if (!svg) return -1;
                return Array.from(svg.querySelectorAll('text'))
                    .filter(t => t.textContent.includes('Internet'))
                    .length;
            }
            """
        )
        assert count == 0, (
            f"Expected 0 SVG <text> elements containing 'Internet', found {count}. "
            "patchForeignObjectLabels() is creating a duplicate fallback text node."
        )

    # ── Bug #2: Internet→listener arrows must have distinct labels ─────────

    def test_listener_arrows_have_distinct_labels(self, page: Page, live_server: str):
        """Multiple Internet→listener arrows must carry distinct edge labels.

        Root cause: _build_ingress_diagram() applied the same FQDN label to
        every listener arrow from Internet, making multiple arrows look like
        one line with stacked duplicate labels.
        """
        self._load_diagram(page, live_server, self._MERMAID_DISTINCT_LABELS)

        edge_label_texts = page.evaluate(
            """
            () => {
                const svg = document.querySelector('#ingress-diagram-div svg');
                if (!svg) return [];
                // Mermaid renders edge labels inside .edgeLabel groups
                return Array.from(svg.querySelectorAll('.edgeLabel'))
                    .map(g => g.textContent.trim())
                    .filter(t => t.length > 0);
            }
            """
        )
        # There must be at least 2 edge labels (two Internet→listener arrows)
        assert len(edge_label_texts) >= 2, (
            f"Expected ≥2 edge labels from the diagram, got {edge_label_texts!r}. "
            "Internet→listener arrows may not be rendering correctly."
        )
        # They must not ALL be identical
        assert len(set(edge_label_texts)) > 1, (
            f"All edge labels are identical: {edge_label_texts!r}. "
            "Internet→listener arrows are sharing the same FQDN label."
        )

    # ── Bug #3: Double-click on drillable node opens drill-down panel ──────

    def test_dblclick_opens_drilldown_panel(self, page: Page, live_server: str):
        """Double-clicking a drillable node (⤵ badge) must open the drill-down modal
        and render a table with the resource details."""
        self._load_diagram(page, live_server, self._MERMAID_DISTINCT_LABELS)

        # The App Gateway node should have been marked drillable
        drillable = page.locator("#ingress-diagram-div svg g.node-drillable")
        expect(drillable).to_have_count(1, timeout=5000)

        drillable.dblclick()

        # Drill-down modal should appear with a data table
        expect(page.locator("#drilldown-modal")).to_be_visible(timeout=8000)
        modal_style = page.locator("#drilldown-modal .modal-content").evaluate(
            """(el) => ({ width: el.style.width, maxWidth: el.style.maxWidth })"""
        )
        assert modal_style["width"] == "98vw", modal_style
        assert modal_style["maxWidth"] == "min(98vw, 1600px)", modal_style
        expect(page.locator("#drilldown-modal table")).to_be_visible(timeout=5000)
        expect(page.locator("#drilldown-modal")).to_contain_text("Resource type:")
        expect(page.locator("#drilldown-modal")).to_contain_text("microsoft.network/applicationgateways")
        # Table should contain the mocked row data
        expect(page.locator("#drilldown-modal table td").first).to_contain_text("test-appgw")
        fqdn_link = page.locator("#drilldown-modal a[href='https://gw.example.com']").first
        expect(fqdn_link).to_be_visible(timeout=5000)
        assert fqdn_link.get_attribute("target") == "_blank"
        assert "noopener" in (fqdn_link.get_attribute("rel") or "")

    def test_dblclick_opens_generic_resource_details(self, page: Page, live_server: str):
        """Double-clicking a non-special resource node must still open generic details."""
        import json

        mermaid_source = (
            "graph LR\n"
            '    test_storage["Storage Account"]\n'
        )
        node_map = {
            "test_storage": {
                "title": "test-storage",
                "arm_type": "Microsoft.Storage/storageAccounts",
                "resources": [{"rg": "test-rg", "name": "test-storage"}],
                "can_drill": False,
            }
        }
        ingress_views = {
            "connectivity": {
                "mermaid": mermaid_source,
                "css_code": "",
                "icon_map": {},
                "node_drilldown_map": node_map,
                "description": "Connectivity mock view",
                "legend": ["Connectivity legend"],
                "asset_summary": {"entry_points": 0, "api_layer": 0, "backends": 0, "data_stores": 1, "public_assets": 0},
            }
        }

        page.route(
            "**/api/subscriptions",
            lambda route: route.fulfill(
                content_type="application/json",
                body=json.dumps({
                    "subscriptions": [{
                        "id": self._SUB_ID,
                        "display_name": "Test Subscription",
                        "environment": "production",
                        "env_badge": "danger",
                        "provider": "Azure",
                        "state": "Enabled",
                        "last_synced": None,
                        "asset_count": 1,
                        "public_count": 0,
                    }]
                }),
            ),
        )
        page.route(
            f"**/{self._SUB_ID}/diagram",
            lambda route: route.fulfill(
                content_type="application/json",
                body=json.dumps({
                    "subscription_name": "Test Subscription",
                    "environment": "production",
                    "total_assets": 1,
                    "ingress_diagram": {
                        "mermaid": mermaid_source,
                        "css_code": "",
                        "icon_map": {},
                        "node_drilldown_map": node_map,
                        "default_view": "connectivity",
                        "views": ingress_views,
                        "asset_summary": {"entry_points": 0, "api_layer": 0, "backends": 0, "data_stores": 1, "public_assets": 0},
                    },
                    "diagrams": [],
                }),
            ),
        )
        page.route(
            f"**/{self._SUB_ID}/drilldown",
            lambda route: route.fulfill(
                content_type="application/json",
                body=json.dumps({
                    "title": "Storage Account — 1 resource",
                    "view_type": "table",
                    "columns": ["Resource", "Resource Group", "Resource Type", "URL / FQDN", "Exposure", "Entry Points", "SKU"],
                    "rows": [["test-storage", "test-rg", "Microsoft.Storage/storageAccounts", "—", {"label": "Private"}, "—", "—"]],
                }),
            ),
        )

        page.goto(live_server + "/cloud")
        page.wait_for_selector(".subscription-name-cell", timeout=8000)
        page.locator(".subscription-name-cell").first.click()
        page.wait_for_selector("#ingress-diagram-div svg", timeout=15000)
        page.wait_for_timeout(1000)

        storage_node = page.locator("#ingress-diagram-div svg g.node-drillable")
        expect(storage_node).to_have_count(1, timeout=5000)
        storage_node.dblclick()

        expect(page.locator("#drilldown-modal")).to_be_visible(timeout=8000)
        expect(page.locator("#drilldown-modal")).to_contain_text("Resource type:")
        expect(page.locator("#drilldown-modal")).to_contain_text("Microsoft.Storage/storageAccounts")
        expect(page.locator("#drilldown-modal table")).to_be_visible(timeout=5000)
        expect(page.locator("#drilldown-modal table")).to_contain_text("Resource Type")
        expect(page.locator("#drilldown-modal table")).to_contain_text("Microsoft.Storage/storageAccounts")


class TestCloudPageAseNestedDrilldown:
    """Playwright coverage for App Service Environment drill-down."""

    _SUB_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    _MERMAID_ASE = (
        "graph LR\n"
        '    test_rg_ase["App Service Environment"]\n'
        "    classDef backend stroke:#388e3c,stroke-width:2px;\n"
        "    class test_rg_ase backend;\n"
    )

    _NODE_MAP = {
        "test_rg_ase": {
            "title": "test-ase",
            "arm_type": "microsoft.web/hostingenvironments",
            "resources": [{"rg": "rg-app", "name": "test-ase"}],
            "can_drill": True,
        }
    }

    def _setup_mocks(self, page: Page) -> None:
        import json

        page.route(
            "**/api/subscriptions",
            lambda route: route.fulfill(
                content_type="application/json",
                body=json.dumps({
                    "subscriptions": [{
                        "id": self._SUB_ID,
                        "display_name": "ASE Subscription",
                        "environment": "production",
                        "env_badge": "danger",
                        "provider": "Azure",
                        "state": "Enabled",
                        "last_synced": None,
                        "asset_count": 3,
                        "public_count": 0,
                    }]
                }),
            ),
        )

        node_map = self._NODE_MAP
        views = {
            "connectivity": {
                "mermaid": self._MERMAID_ASE,
                "css_code": "",
                "icon_map": {},
                "node_drilldown_map": node_map,
                "description": "ASE mock view",
                "legend": ["Hosted apps"],
                "asset_summary": {"entry_points": 0, "api_layer": 0, "backends": 1, "data_stores": 0, "public_assets": 0},
            },
            "exposure": {
                "mermaid": self._MERMAID_ASE,
                "css_code": "",
                "icon_map": {},
                "node_drilldown_map": node_map,
                "description": "ASE mock view",
                "legend": ["Hosted apps"],
                "asset_summary": {"entry_points": 0, "api_layer": 0, "backends": 1, "data_stores": 0, "public_assets": 0},
            },
        }

        page.route(
            f"**/{self._SUB_ID}/diagram",
            lambda route: route.fulfill(
                content_type="application/json",
                body=json.dumps({
                    "subscription_name": "ASE Subscription",
                    "environment": "production",
                    "total_assets": 3,
                    "ingress_diagram": {
                        "mermaid": self._MERMAID_ASE,
                        "css_code": "",
                        "icon_map": {},
                        "node_drilldown_map": node_map,
                        "default_view": "connectivity",
                        "views": views,
                        "attack_paths": [],
                        "asset_summary": {"entry_points": 0, "api_layer": 0, "backends": 1, "data_stores": 0, "public_assets": 0},
                    },
                    "diagrams": [{
                        "rg": "rg-app",
                        "mermaid": self._MERMAID_ASE,
                        "css_code": "",
                        "icon_map": {},
                        "node_drilldown_map": node_map,
                        "asset_count": 3,
                        "public_count": 0,
                        "relationship_count": 0,
                        "default_view": "connectivity",
                        "views": views,
                        "attack_paths": [],
                        "asset_summary": {"entry_points": 0, "api_layer": 0, "backends": 1, "data_stores": 0, "public_assets": 0},
                    }],
                }),
            ),
        )

        page.route(
            f"**/{self._SUB_ID}/drilldown",
            lambda route: route.fulfill(
                content_type="application/json",
                body=json.dumps({
                    "title": "App Service Environment — Hosted Apps",
                    "view_type": "table",
                    "columns": ["App Service / Function App", "Resource Group", "URL / FQDN", "Exposure", "Kind", "Environment"],
                    "rows": [
                        ["ase-app", "rg-app", "ase-app.azurewebsites.net", "🔒 Private", "App Service", "ase-one"],
                        ["ase-fn", "rg-app", "ase-fn.azurewebsites.net", "🔒 Private", "Function App", "ase-one"],
                    ],
                }),
            ),
        )

    def test_dblclick_opens_ase_drilldown(self, page: Page, live_server: str):
        self._setup_mocks(page)
        page.goto(live_server + "/cloud")
        page.wait_for_selector(".subscription-name-cell", timeout=8000)
        page.locator(".subscription-name-cell").first.click()
        page.wait_for_selector("#ingress-diagram-div svg g.node-drillable", timeout=8000)

        page.locator("#ingress-diagram-div svg g.node-drillable").dblclick()

        expect(page.locator("#drilldown-modal")).to_be_visible(timeout=8000)
        expect(page.locator("#drilldown-modal table")).to_be_visible(timeout=5000)
        expect(page.locator("#drilldown-modal table td").first).to_contain_text("ase-app")
        expect(page.locator("#drilldown-modal table")).to_contain_text("Function App")
        fqdn_link = page.locator("#drilldown-modal a[href='https://ase-app.azurewebsites.net']").first
        expect(fqdn_link).to_be_visible(timeout=5000)
        assert fqdn_link.get_attribute("target") == "_blank"
        assert "noopener" in (fqdn_link.get_attribute("rel") or "")


class TestCloudPageApimNestedDrilldown:
    """Playwright coverage for nested APIM method drill-down."""

    _SUB_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    _MERMAID_APIM = (
        "graph LR\n"
        '    test_rg_apim["APIM"]\n'
        "    classDef apiGateway stroke:#00897b,stroke-width:2px;\n"
        "    class test_rg_apim apiGateway;\n"
    )

    def _setup_mocks(self, page: Page) -> None:
        import json

        page.route(
            "**/api/subscriptions",
            lambda route: route.fulfill(
                content_type="application/json",
                body=json.dumps({
                    "subscriptions": [{
                        "id": self._SUB_ID,
                        "display_name": "Nested APIM Subscription",
                        "environment": "production",
                        "env_badge": "danger",
                        "provider": "Azure",
                        "state": "Enabled",
                        "last_synced": None,
                        "asset_count": 1,
                        "public_count": 1,
                    }]
                }),
            ),
        )

        node_map = {
            "test_rg_apim": {
                "title": "test-apim",
                "arm_type": "microsoft.apimanagement/service",
                "resources": [{"rg": "rg-app", "name": "test-apim"}],
                "can_drill": True,
            }
        }

        page.route(
            f"**/{self._SUB_ID}/diagram",
            lambda route: route.fulfill(
                content_type="application/json",
                body=json.dumps({
                    "subscription_name": "Nested APIM Subscription",
                    "environment": "production",
                    "total_assets": 1,
                    "ingress_diagram": {
                        "mermaid": self._MERMAID_APIM,
                        "css_code": "",
                        "icon_map": {},
                        "node_drilldown_map": node_map,
                        "default_view": "overview",
                    },
                    "diagrams": [{
                        "rg": "rg-app",
                        "mermaid": self._MERMAID_APIM,
                        "css_code": "",
                        "icon_map": {},
                        "node_drilldown_map": node_map,
                        "asset_count": 1,
                        "public_count": 1,
                        "relationship_count": 0,
                        "default_view": "overview",
                    }],
                }),
            ),
        )

        page.route(
            f"**/{self._SUB_ID}/drilldown",
            lambda route: route.fulfill(
                content_type="application/json",
                body=json.dumps({
                    "title": "APIM — APIs & Methods",
                    "view_type": "tree_table",
                    "icon_path": "/static/assets/icons/azure/integration/api-management.svg",
                    "columns": ["API", "Method", "Path", "Backend", "Auth"],
                    "rows": [
                        {
                            "id": "test-apim::orders",
                            "parent_id": None,
                            "cells": [
                                {"label": "Orders API", "style": "font-weight:600;"},
                                {"label": "API", "style": "color:#94a3b8;font-size:0.78rem;"},
                                "/orders",
                                "https://orders.example.com",
                                "🔑 Required",
                            ],
                            "child_count": 2,
                            "search_text": "orders api /orders https://orders.example.com required",
                        },
                        {
                            "id": "test-apim::orders::get",
                            "parent_id": "test-apim::orders",
                            "cells": [
                                "Get orders",
                                {"label": "GET", "style": "color:#94a3b8;font-size:0.78rem;"},
                                "/orders",
                                "https://orders.example.com",
                                "🔑 Required",
                            ],
                            "child_count": 0,
                            "search_text": "get orders get /orders https://orders.example.com required",
                        },
                        {
                            "id": "test-apim::orders::post",
                            "parent_id": "test-apim::orders",
                            "cells": [
                                "Create order",
                                {"label": "POST", "style": "color:#94a3b8;font-size:0.78rem;"},
                                "/orders",
                                "https://orders.example.com",
                                "🔑 Required",
                            ],
                            "child_count": 0,
                            "search_text": "create order post /orders https://orders.example.com required",
                        },
                    ],
                    "sections": [{
                        "title": "test-apim",
                        "subtitle": "rg-app",
                        "rows": [
                            {
                                "id": "test-apim::orders",
                                "parent_id": None,
                                "cells": [
                                    {"label": "Orders API", "style": "font-weight:600;"},
                                    {"label": "API", "style": "color:#94a3b8;font-size:0.78rem;"},
                                    "/orders",
                                    "https://orders.example.com",
                                    "🔑 Required",
                                ],
                                "child_count": 2,
                                "search_text": "orders api /orders https://orders.example.com required",
                            },
                            {
                                "id": "test-apim::orders::get",
                                "parent_id": "test-apim::orders",
                                "cells": [
                                    "Get orders",
                                    {"label": "GET", "style": "color:#94a3b8;font-size:0.78rem;"},
                                    "/orders",
                                    "https://orders.example.com",
                                    "🔑 Required",
                                ],
                                "child_count": 0,
                                "search_text": "get orders get /orders https://orders.example.com required",
                            },
                            {
                                "id": "test-apim::orders::post",
                                "parent_id": "test-apim::orders",
                                "cells": [
                                    "Create order",
                                    {"label": "POST", "style": "color:#94a3b8;font-size:0.78rem;"},
                                    "/orders",
                                    "https://orders.example.com",
                                    "🔑 Required",
                                ],
                                "child_count": 0,
                                "search_text": "create order post /orders https://orders.example.com required",
                            },
                        ],
                    }],
                }),
            ),
        )

    def test_apim_node_expands_nested_methods(self, page: Page, live_server: str):
        self._setup_mocks(page)
        page.goto(live_server + "/cloud")
        page.wait_for_selector(".subscription-name-cell", timeout=8000)
        page.locator(".subscription-name-cell").first.click()
        page.wait_for_selector("#ingress-diagram-div svg g.node-drillable", timeout=8000)

        page.locator("#ingress-diagram-div svg g.node-drillable").click()

        expect(page.locator("#drilldown-modal")).to_be_visible(timeout=8000)
        expect(page.locator("#drilldown-modal tr[data-row-id='test-apim::orders']")).to_be_visible(timeout=5000)
        expect(page.locator("#drilldown-modal tr[data-row-id='test-apim::orders::get']")).to_be_hidden(timeout=5000)

        page.locator("#drilldown-modal tr[data-row-id='test-apim::orders'] .expand-toggle").click()

        expect(page.locator("#drilldown-modal tr[data-row-id='test-apim::orders::get']")).to_be_visible(timeout=5000)
        expect(page.locator("#drilldown-modal tr[data-row-id='test-apim::orders::post']")).to_be_visible(timeout=5000)
        expect(page.locator("#drilldown-modal tr[data-row-id='test-apim::orders'] td:first-child img")).to_be_visible(timeout=5000)
        backend_link = page.locator("#drilldown-modal a[href='https://orders.example.com']").first
        expect(backend_link).to_be_visible(timeout=5000)
        assert backend_link.get_attribute("target") == "_blank"
        assert "noopener" in (backend_link.get_attribute("rel") or "")


# ---------------------------------------------------------------------------
# Python unit test: _build_ingress_diagram label generation
# ---------------------------------------------------------------------------

class TestIngressDiagramGeneration:
    """Unit tests for the Python _build_ingress_diagram() backend helper."""

    def _make_rows(self):
        """Minimal provisioned_assets rows for an App Gateway with two listeners."""
        # (name, type, rg, fqdn, is_public, sku, id, has_waf, listeners)
        return [
            (
                "test-appgw",
                "Microsoft.Network/applicationGateways",
                "test-rg",
                "gw.example.com",
                1,      # is_public
                "WAF_v2",
                "fake-id",
                0,      # has_waf
                "HTTPS:443, HTTP:80",   # listeners
            )
        ]

    def _call(self, rows=None, plan_links=None, firewall_policy_rows=None):
        import sys
        import os
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        os.environ.setdefault("FLASK_APP", "web/app.py")
        from web.app import _build_ingress_diagram
        return _build_ingress_diagram(
            rows if rows is not None else self._make_rows(),
            plan_links=plan_links,
            firewall_policy_rows=firewall_policy_rows,
        )

    def test_internet_node_defined_once(self):
        """Internet node must appear exactly once in the generated Mermaid source."""
        result = self._call()
        mermaid = result.get("mermaid", "")
        internet_defs = [
            line for line in mermaid.splitlines()
            if line.strip().startswith('Internet[')
        ]
        assert len(internet_defs) == 1, (
            f"Internet node defined {len(internet_defs)} time(s); expected 1.\n{mermaid}"
        )

    def test_listener_arrows_have_distinct_labels(self):
        """Internet→listener arrows must carry distinct labels (not the same FQDN twice)."""
        result = self._call()
        mermaid = result.get("mermaid", "")
        # Collect labels from Internet --> |"label"| lines
        import re
        internet_labels = re.findall(
            r'Internet\s*-->?\|"([^"]+)"\|', mermaid
        )
        # A gateway with HTTP + HTTPS listeners should produce 2 arrow lines
        assert len(internet_labels) >= 2, (
            f"Expected ≥2 Internet→ arrow labels, got {internet_labels!r}.\n{mermaid}"
        )
        assert len(set(internet_labels)) > 1, (
            f"All Internet→ arrow labels are identical: {internet_labels!r}.\n"
            "The same FQDN is being applied to every listener arrow."
        )

    def test_firewall_policy_node_is_rendered(self):
        """Azure Firewall should surface an attached firewall policy in the diagram."""
        import json

        rows = [
            (
                "fw-one",
                "Microsoft.Network/azureFirewalls",
                "rg-net",
                "",
                1,
                "Standard",
                "fw-id",
                0,
                None,
                0,
                None,
                None,
            ),
        ]
        firewall_policy_rows = [
            (
                "policy-one",
                "rg-net",
                json.dumps(["fw-one"]),
                json.dumps([
                    {
                        "name": "group-one",
                        "priority": 100,
                        "collection_count": 1,
                        "rule_count": 2,
                        "collections": [
                            {
                                "name": "nat-collection",
                                "priority": 100,
                                "type": "FirewallPolicyNatRuleCollection",
                                "action": "Dnat",
                                "rule_count": 2,
                            }
                        ],
                    }
                ]),
                1,
                1,
                "Enabled",
            ),
        ]

        result = self._call(rows=rows, firewall_policy_rows=firewall_policy_rows)
        mermaid = result.get("mermaid", "")
        assert "Policy: policy-one" in mermaid, mermaid
        titles = {v.get("title") for v in result.get("node_drilldown_map", {}).values()}
        assert "policy-one" in titles, titles

    def test_traffic_manager_is_omitted_from_overview_diagram(self):
        """Traffic Manager is DNS-only and should be omitted from overview connectivity diagrams."""
        import json

        tm_targets = json.dumps([
            {
                "name": "disabled-apim",
                "target": "apim.example.com",
                "target_resource_id": "/subscriptions/sub/resourceGroups/rg-api/providers/Microsoft.ApiManagement/service/apim",
                "endpoint_status": "Disabled",
            },
            {
                "name": "appgw-endpoint",
                "target": "backend.example.com",
                "target_resource_id": "/subscriptions/sub/resourceGroups/rg-app/providers/Microsoft.Network/publicIPAddresses/appgw",
                "endpoint_status": "Enabled",
            },
        ])
        rows = [
            (
                "tm",
                "Microsoft.Network/trafficManagerProfiles",
                "rg-tm",
                "tm.example.trafficmanager.net",
                1,
                None,
                "tm-id",
                0,
                None,
                0,
                None,
                tm_targets,
            ),
            (
                "appgw",
                "Microsoft.Network/applicationGateways",
                "rg-app",
                "",
                0,
                "WAF_v2",
                "appgw-id",
                0,
                "HTTPS:443",
                0,
                None,
                None,
            ),
            (
                "apim",
                "Microsoft.ApiManagement/service",
                "rg-api",
                "",
                0,
                None,
                "apim-id",
                0,
                None,
                0,
                None,
                None,
            ),
        ]
        result = self._call(rows=rows)
        mermaid = result.get("mermaid", "")
        tm_node = "rg_tm_tm"
        appgw_node = "rg_app_appgw"
        apim_node = "grp_APIM_Private"
        assert tm_node not in mermaid, mermaid
        assert f'Internet -->|"DNS routing"| {tm_node}' not in mermaid, mermaid
        assert f'{tm_node} -->|"DNS → backend"| {appgw_node}' not in mermaid, mermaid
        assert f'{tm_node} -->|"DNS → apim"| {apim_node}' not in mermaid, mermaid

    def test_drilldown_map_contains_appgw(self):
        """App Gateway must appear in node_drilldown_map with can_drill=True."""
        result = self._call()
        ndm = result.get("node_drilldown_map", {})
        drillable = [k for k, v in ndm.items() if v.get("can_drill")]
        assert drillable, (
            f"No drillable nodes in node_drilldown_map: {ndm}"
        )

    def test_ingress_diagram_includes_overlay_views(self):
        """Ingress payload should expose connectivity/exposure views plus attack-path summaries."""
        result = self._call()
        views = result.get("views", {})
        assert {"connectivity", "exposure"} <= set(views), views
        assert result.get("default_view") == "connectivity"
        assert result.get("attack_paths"), "Expected attack-path summaries in the ingress payload"

    def test_function_app_rows_fold_into_app_service_plan(self):
        """Function App rows should be folded under the App Service Plan node."""
        rows = [
            (
                "test-plan",
                "Microsoft.Web/serverfarms",
                "rg-app",
                "",
                0,
                "P1v3",
                "plan-id",
                0,
                None,
            ),
            (
                "orders-fn-app",
                "Microsoft.Web/sites",
                "rg-app",
                "orders.example.com",
                1,
                "Y1",
                "site-id",
                0,
                None,
            ),
        ]
        result = self._call(
            rows=rows,
            plan_links=[("rg-app", "orders-fn-app", "rg-app", "test-plan")],
        )
        assert result.get("asset_summary", {}).get("backends") == 2, result.get("asset_summary")
        assert "hosted on" in result.get("mermaid", ""), result.get("mermaid", "")
        assert "orders-fn-app" in result.get("mermaid", ""), result.get("mermaid", "")
        titles = {v.get("title") for v in result.get("node_drilldown_map", {}).values()}
        assert titles == {"test-plan", "orders-fn-app"}, titles

    def test_function_app_rows_fold_into_app_service_environment(self):
        """App Service Environment rows should also fold hosted apps beneath the parent node."""
        rows = [
            (
                "test-ase",
                "Microsoft.Web/hostingEnvironments",
                "rg-app",
                "ase-one.westus.appserviceenvironment.net",
                0,
                "ASEv3",
                "ase-id",
                0,
                None,
            ),
            (
                "orders-fn-app",
                "Microsoft.Web/sites",
                "rg-app",
                "orders.example.com",
                0,
                "Y1",
                "site-id",
                0,
                None,
            ),
        ]
        result = self._call(
            rows=rows,
            plan_links=[("rg-app", "orders-fn-app", "rg-app", "test-ase")],
        )
        assert "hosted on" in result.get("mermaid", ""), result.get("mermaid", "")
        assert "orders-fn-app" in result.get("mermaid", ""), result.get("mermaid", "")
        titles = {v.get("title") for v in result.get("node_drilldown_map", {}).values()}
        assert titles == {"test-ase", "orders-fn-app"}, titles

    def test_app_service_plan_drilldown_lists_hosted_apps(self):
        """The App Service Plan drilldown must list hosted app services."""
        import sqlite3

        from web.app import _build_child_table

        conn = sqlite3.connect(":memory:")
        try:
            conn.execute(
                """
                CREATE TABLE provisioned_assets (
                    name TEXT,
                    resource_group TEXT,
                    fqdn TEXT,
                    is_public INTEGER,
                    is_restricted INTEGER,
                    raw_json TEXT,
                    type TEXT,
                    subscription_id TEXT,
                    id TEXT
                )
                """
            )
            plan_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/serverfarms/test-plan"
            site_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/sites/orders-fn-app"
            conn.executemany(
                """
                INSERT INTO provisioned_assets
                    (name, resource_group, fqdn, is_public, is_restricted, raw_json, type, subscription_id, id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        "test-plan",
                        "rg-app",
                        "",
                        0,
                        0,
                        '{"kind": "app"}',
                        "Microsoft.Web/serverfarms",
                        "sub-1",
                        plan_id,
                    ),
                    (
                        "orders-fn-app",
                        "rg-app",
                        "orders.example.com",
                        1,
                        0,
                        f'{{"serverFarmId": "{plan_id}", "kind": "functionapp,linux"}}',
                        "Microsoft.Web/sites",
                        "sub-1",
                        site_id,
                    ),
                ],
            )

            result = _build_child_table(
                conn,
                "sub-1",
                "Microsoft.Web/serverfarms",
                [{"rg": "rg-app", "name": "test-plan"}],
            )
        finally:
            conn.close()

        assert result["view_type"] == "table", result
        assert result["rows"], result
        assert any(row[0] == "orders-fn-app" for row in result["rows"]), result["rows"]

    def test_aks_drilldown_uses_service_type_and_hides_cluster_column(self):
        """AKS drilldown should infer service type and omit redundant cluster column."""
        import sqlite3

        from web.app import _build_child_table

        conn = sqlite3.connect(":memory:")
        try:
            conn.execute(
                """
                CREATE TABLE aks_routes (
                    subscription_id TEXT,
                    cluster_name TEXT,
                    resource_group TEXT,
                    namespace TEXT,
                    ingress_name TEXT,
                    host TEXT,
                    path TEXT,
                    service_name TEXT,
                    service_port TEXT,
                    deployment_name TEXT,
                    git_repository TEXT,
                    pod_template_labels TEXT
                )
                """
            )
            conn.executemany(
                """
                INSERT INTO aks_routes (
                    subscription_id, cluster_name, resource_group, namespace, ingress_name, host, path,
                    service_name, service_port, deployment_name, git_repository, pod_template_labels
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        "sub-1",
                        "aks-one",
                        "rg-app",
                        "orders",
                        "orders-ingress",
                        "orders.example.com",
                        "/api/orders",
                        "orders-svc",
                        "443",
                        "orders-api",
                        "https://example/repo-a",
                        '{"app.kubernetes.io/component":"api"}',
                    ),
                    (
                        "sub-1",
                        "aks-one",
                        "rg-app",
                        "batch",
                        "batch-ingress",
                        "batch.example.com",
                        "/run",
                        "batch-svc",
                        "443",
                        "nightly-cronjob",
                        "https://example/repo-b",
                        '{"workload":"cronjob"}',
                    ),
                ],
            )

            result = _build_child_table(
                conn,
                "sub-1",
                "Microsoft.ContainerService/managedClusters",
                [{"rg": "rg-app", "name": "aks-one"}],
            )
        finally:
            conn.close()

        assert result["view_type"] == "table", result
        assert result["columns"][0] == "Namespace", result["columns"]
        assert result["columns"][1] == "Service Type", result["columns"]
        assert "Cluster" not in result["columns"], result["columns"]
        by_namespace = {row[0]: row[1] for row in result["rows"]}
        assert by_namespace["orders"] == "API", result["rows"]
        assert by_namespace["batch"] == "Job", result["rows"]

    def test_aks_drilldown_repo_links_to_scanned_repo(self):
        """AKS repo column should link to a matching scanned repository when available."""
        import sqlite3

        from web.app import _build_child_table

        conn = sqlite3.connect(":memory:")
        try:
            conn.execute(
                """
                CREATE TABLE aks_routes (
                    subscription_id TEXT,
                    cluster_name TEXT,
                    resource_group TEXT,
                    namespace TEXT,
                    ingress_name TEXT,
                    host TEXT,
                    path TEXT,
                    service_name TEXT,
                    service_port TEXT,
                    deployment_name TEXT,
                    git_repository TEXT,
                    pod_template_labels TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE repositories (
                    id INTEGER PRIMARY KEY,
                    experiment_id TEXT,
                    repo_name TEXT,
                    repo_url TEXT,
                    scanned_at TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT INTO aks_routes (
                    subscription_id, cluster_name, resource_group, namespace, ingress_name, host, path,
                    service_name, service_port, deployment_name, git_repository, pod_template_labels
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "sub-1",
                    "aks-one",
                    "rg-app",
                    "orders",
                    "orders-ingress",
                    "orders.example.com",
                    "/api/orders",
                    "orders-svc",
                    "443",
                    "orders-api",
                    "https://github.com/org/orders-service.git",
                    '{"app.kubernetes.io/component":"api"}',
                ),
            )
            conn.execute(
                """
                INSERT INTO repositories (experiment_id, repo_name, repo_url, scanned_at)
                VALUES (?, ?, ?, ?)
                """,
                ("exp-100", "orders-service", "https://github.com/org/orders-service", "2026-01-01T00:00:00Z"),
            )

            result = _build_child_table(
                conn,
                "sub-1",
                "Microsoft.ContainerService/managedClusters",
                [{"rg": "rg-app", "name": "aks-one"}],
            )
        finally:
            conn.close()

        assert result["view_type"] == "table", result
        repo_cell = result["rows"][0][6]
        assert isinstance(repo_cell, dict), result["rows"]
        assert repo_cell["label"] == "https://github.com/org/orders-service.git", repo_cell
        assert repo_cell["href"] == "/?experiment=exp-100", repo_cell

    def test_app_gateway_drilldown_uses_gateway_level_waf_policy_fallback(self):
        """App Gateway drilldown should surface policy from appgw_waf_policies when rule column is empty."""
        import sqlite3

        from web.app import _build_child_table

        conn = sqlite3.connect(":memory:")
        try:
            conn.execute(
                """
                CREATE TABLE appgw_routing_rules (
                    gateway_name TEXT,
                    listener_name TEXT,
                    hostname TEXT,
                    protocol TEXT,
                    url_path TEXT,
                    backend_pool_name TEXT,
                    backend_fqdns TEXT,
                    backend_port INTEGER,
                    waf_policy_name TEXT,
                    exposure_level TEXT,
                    subscription_id TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE appgw_waf_policies (
                    name TEXT,
                    subscription_id TEXT,
                    state TEXT,
                    associated_gateways TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT INTO appgw_routing_rules (
                    gateway_name, listener_name, hostname, protocol, url_path,
                    backend_pool_name, backend_fqdns, backend_port, waf_policy_name,
                    exposure_level, subscription_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "gw-one",
                    "https-listener",
                    "gw.example.com",
                    "Https",
                    "/*",
                    "pool-one",
                    '["backend.example.com"]',
                    443,
                    None,
                    "Public",
                    "sub-1",
                ),
            )
            conn.execute(
                """
                INSERT INTO appgw_waf_policies (name, subscription_id, state, associated_gateways)
                VALUES (?, ?, ?, ?)
                """,
                ("waf-policy-prod", "sub-1", "Enabled", '["gw-one"]'),
            )

            result = _build_child_table(
                conn,
                "sub-1",
                "Microsoft.Network/applicationGateways",
                [{"rg": "rg-net", "name": "gw-one"}],
            )
        finally:
            conn.close()

        assert result["view_type"] == "tree_table", result
        assert result["sections"], result
        rows = result["sections"][0]["rows"]
        assert rows and all(row.get("parent_id") is None for row in rows), rows
        assert any(
            isinstance(row.get("cells"), list) and "🛡️ waf-policy-prod" in row["cells"][-1].lower()
            for row in rows
        ), rows

    def test_firewall_policy_drilldown_renders_rule_groups(self):
        """Firewall policy drilldown should not crash and should render group rows."""
        import sqlite3

        from web.app import _build_child_table

        conn = sqlite3.connect(":memory:")
        try:
            conn.execute(
                """
                CREATE TABLE firewall_policies (
                    name TEXT,
                    resource_group TEXT,
                    associated_firewalls TEXT,
                    mode TEXT,
                    rule_collection_groups TEXT,
                    nat_rule_count INTEGER,
                    app_rule_count INTEGER,
                    subscription_id TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT INTO firewall_policies (
                    name, resource_group, associated_firewalls, mode, rule_collection_groups,
                    nat_rule_count, app_rule_count, subscription_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "fw-policy-one",
                    "rg-net",
                    '["fw-one"]',
                    "Alert",
                    '[{"name":"group-a","priority":100,"collection_count":2,"rule_count":5}]',
                    1,
                    3,
                    "sub-1",
                ),
            )

            result = _build_child_table(
                conn,
                "sub-1",
                "Microsoft.Network/firewallPolicies",
                [{"rg": "rg-net", "name": "fw-policy-one"}],
            )
        finally:
            conn.close()

        assert result["view_type"] == "tree_table", result
        assert result["sections"], result
        assert result["sections"][0]["title"] == "fw-policy-one", result["sections"]

    def test_apim_drilldown_nests_methods_under_apis(self):
        """APIM drill-down should return expandable API rows with nested methods."""
        import sqlite3

        try:
            from web.app import _build_child_table
        except ModuleNotFoundError:
            from app import _build_child_table

        conn = sqlite3.connect(":memory:")
        try:
            conn.executescript(
                """
                CREATE TABLE apim_api_operations (
                    id TEXT PRIMARY KEY,
                    subscription_id TEXT NOT NULL,
                    apim_name TEXT NOT NULL,
                    api_name TEXT NOT NULL,
                    api_display_name TEXT,
                    api_path TEXT,
                    backend_url TEXT,
                    operation_id TEXT NOT NULL,
                    display_name TEXT,
                    method TEXT,
                    url_template TEXT,
                    description TEXT,
                    requires_subscription INTEGER DEFAULT 1,
                    policy_summary TEXT,
                    last_synced DATETIME
                );
                """
            )
            conn.executemany(
                """
                INSERT INTO apim_api_operations (
                    id, subscription_id, apim_name, api_name, api_display_name,
                    api_path, backend_url, operation_id, display_name, method,
                    url_template, description, requires_subscription, policy_summary, last_synced
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        "test-apim::orders::get",
                        "sub-1",
                        "test-apim",
                        "orders-api",
                        "Orders API",
                        "/orders",
                        "https://orders.example.com",
                        "get-order",
                        "Get orders",
                        "GET",
                        "/{id}",
                        "Get an order",
                        1,
                        None,
                        None,
                    ),
                    (
                        "test-apim::orders::post",
                        "sub-1",
                        "test-apim",
                        "orders-api",
                        "Orders API",
                        "/orders",
                        "https://orders.example.com",
                        "create-order",
                        "Create order",
                        "POST",
                        "",
                        "Create an order",
                        1,
                        None,
                        None,
                    ),
                ],
            )

            result = _build_child_table(
                conn,
                "sub-1",
                "Microsoft.ApiManagement/service",
                [{"rg": "rg-app", "name": "test-apim"}],
            )
        finally:
            conn.close()

        assert result["view_type"] == "tree_table", result
        assert result["sections"], result
        assert result["sections"][0]["title"] == "test-apim", result["sections"]
        rows = result["sections"][0]["rows"]
        parent = next(row for row in rows if row.get("parent_id") is None)
        children = [row for row in rows if row.get("parent_id") == parent["id"]]
        assert parent["child_count"] == 2, parent
        assert parent["cells"][0]["label"] == "Orders API", parent["cells"]
        assert parent["cells"][1]["label"] == "API", parent["cells"]
        assert children[0]["cells"][1]["label"] == "GET", children[0]["cells"]
        assert children[1]["cells"][1]["label"] == "POST", children[1]["cells"]

    def test_gateway_waf_mode_marks_entry_edges_protected(self):
        """Gateway-wide WAF should label entry edges as WAF instead of FQDN/protocol."""
        rows = [
            (
                "appgwone",
                "Microsoft.Network/applicationGateways",
                "rgnet",
                "appgwone.example.com",
                1,
                "WAF_v2",
                "appgw-id",
                0,
                "HTTPS:443, HTTP:80",
                0,
                "PolicyAttached",
                None,
            ),
        ]
        result = self._call(rows=rows)
        mermaid = result.get("mermaid", "")
        assert 'Internet -->|"WAF"| l_rgnet_appgwone_HTTPS_443' in mermaid, mermaid
        assert 'Internet -->|"WAF"| l_rgnet_appgwone_HTTP_80' in mermaid, mermaid
        assert "class rgnet_appgwone entryPointProtected;" in mermaid, mermaid
        assert "linkStyle 0 stroke:#f97316" in mermaid, mermaid
        assert "linkStyle 1 stroke:#f97316" in mermaid, mermaid

    def test_acr_label_shows_public_and_credential_requirement(self):
        """ACR node label should state public/private and credential requirement."""
        rows = [
            (
                "acr-one",
                "Microsoft.ContainerRegistry/registries",
                "rg-acr",
                "acr-one.azurecr.io",
                1,
                "Standard",
                "acr-id",
                0,
                None,
                0,
                None,
                None,
                '{"properties":{"policies":{"anonymousPullEnabled":false}}}',
            ),
        ]
        result = self._call(rows=rows)
        mermaid = result.get("mermaid", "")
        assert "acr-one (Public, Creds required)" in mermaid, mermaid

    def test_event_hub_label_shows_public_and_auth_requirement(self):
        """Event Hub node label should include exposure and auth requirement posture."""
        rows = [
            (
                "events-hubns",
                "Microsoft.EventHub/namespaces",
                "rg-data",
                "events-hubns.servicebus.windows.net",
                1,
                "Standard",
                "eh-id",
                0,
                None,
                0,
                None,
                None,
                "{}",
                '["azure_ad","sas_key"]',
            ),
        ]
        result = self._call(rows=rows)
        mermaid = result.get("mermaid", "")
        assert "events-hubns (Public, Auth required)" in mermaid, mermaid

    def test_app_services_not_grouped_above_threshold(self):
        """App Service and Function App nodes should not collapse into grouped backend nodes."""
        rows = []
        for idx in range(6):
            rows.append(
                (
                    f"appsvc-{idx}",
                    "Microsoft.Web/sites",
                    "rg-app",
                    f"appsvc-{idx}.azurewebsites.net",
                    0,
                    "P1v3",
                    f"site-{idx}",
                    0,
                    None,
                    0,
                    None,
                    None,
                    "{}",
                )
            )
        result = self._call(rows=rows)
        titles = {v.get("title") for v in result.get("node_drilldown_map", {}).values()}
        assert "App Service" not in titles, titles
        for idx in range(6):
            assert f"appsvc-{idx}" in titles, titles

    def test_ip_restricted_key_vault_uses_allowlist_edge(self):
        """IP-restricted data stores should be rendered as orange allowlist edges."""
        rows = [
            (
                "kvone",
                "Microsoft.KeyVault/vaults",
                "rgdata",
                "kvone.vault.azure.net",
                0,
                "standard",
                "kv-id",
                0,
                None,
                1,
                None,
                None,
            ),
        ]
        result = self._call(rows=rows)
        mermaid = result.get("mermaid", "")
        assert 'Internet -->|"IP allowlist (Key Vault)"| rgdata_kvone' in mermaid, mermaid
        assert "linkStyle 0 stroke:#f59e0b" in mermaid, mermaid


class TestSubscriptionResourceGroupDiagrams:
    """Unit tests for per-resource-group subscription diagrams."""

    def _make_rows(self):
        return [
            (
                "test-appgw",
                "Microsoft.Network/applicationGateways",
                "rg-app",
                "gw.example.com",
                1,
                "WAF_v2",
                "fake-appgw-id",
                1,
                "HTTPS:443",
            ),
            (
                "test-apim",
                "Microsoft.ApiManagement/service",
                "rg-app",
                "api.example.com",
                0,
                "Developer",
                "fake-apim-id",
                0,
                None,
            ),
            (
                "test-web",
                "Microsoft.Web/sites",
                "rg-app",
                "",
                0,
                "P1v3",
                "fake-site-id",
                0,
                None,
            ),
            (
                "test-kv",
                "Microsoft.KeyVault/vaults",
                "rg-app",
                "",
                0,
                "standard",
                "fake-kv-id",
                0,
                None,
            ),
        ]

    def _call(self):
        import sys
        import os
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        os.environ.setdefault("FLASK_APP", "web/app.py")
        from web.app import _build_subscription_diagrams_by_rg
        return _build_subscription_diagrams_by_rg("Test Subscription", "production", self._make_rows())

    def test_rg_diagrams_include_mode_views(self):
        diagrams = self._call()
        assert diagrams, "Expected at least one RG diagram"
        first = diagrams[0]
        assert {"connectivity", "exposure"} <= set(first.get("views", {}))
        assert first.get("default_view") == "connectivity"
        assert first.get("relationship_count", 0) >= 1

    def test_rg_connectivity_and_attack_summaries_present(self):
        diagrams = self._call()
        first = diagrams[0]
        connectivity = first["views"]["connectivity"]["mermaid"]
        attack_paths = first.get("attack_paths") or []
        assert "-->" in connectivity, connectivity
        assert attack_paths, "Expected attack-path summaries for RG diagram"
        path_text = " ".join(str(p.get("path") or "") + " " + str(p.get("title") or "") for p in attack_paths).lower()
        assert "internet" in path_text or "secret" in path_text or "backend" in path_text, path_text


class TestCosmosDbFqdnResolution:
    """Regression tests for Cosmos DB endpoint resolution in cloud views."""

    def test_exposure_view_derives_cosmos_fqdn(self):
        import sys

        sys.path.insert(0, str(REPO_ROOT))
        from web.subscription_diagram_helpers import build_subscription_diagrams_by_rg

        rows = [
            (
                "cosmos-one",
                "Microsoft.DocumentDB/databaseAccounts",
                "rg-data",
                "",
                1,
                None,
                "cosmos-id",
                0,
                None,
                0,
                None,
            ),
        ]
        diagrams = build_subscription_diagrams_by_rg(
            "Demo Subscription",
            "production",
            rows,
            sanitise_node_id=lambda s: s.replace("/", "_").replace(" ", "_"),
            friendly_type=lambda arm_type: "Cosmos DB" if arm_type == "Microsoft.DocumentDB/databaseAccounts" else arm_type,
            get_icon_path=lambda _resource_type: None,
            normalize_attack_paths=lambda raw_paths, reviewer=None: raw_paths,
        )

        exposure_mermaid = diagrams[0]["views"]["exposure"]["mermaid"]
        assert "cosmos-one.documents.azure.com" in exposure_mermaid
        assert "Direct data plane" not in exposure_mermaid

    def test_drilldown_table_derives_cosmos_fqdn(self):
        import sqlite3
        import sys

        sys.path.insert(0, str(REPO_ROOT))
        from web.app import _build_child_table

        conn = sqlite3.connect(":memory:")
        try:
            conn.execute(
                """
                CREATE TABLE provisioned_assets (
                    id TEXT PRIMARY KEY,
                    subscription_id TEXT,
                    resource_group TEXT,
                    name TEXT,
                    type TEXT,
                    location TEXT,
                    sku TEXT,
                    tags TEXT,
                    is_public INTEGER DEFAULT 0,
                    fqdn TEXT,
                    pipeline_tag TEXT,
                    raw_json TEXT,
                    endpoints TEXT,
                    auth_methods TEXT,
                    first_detected TEXT,
                    last_synced TEXT,
                    status TEXT DEFAULT 'active',
                    is_restricted INTEGER DEFAULT 0
                )
                """
            )
            conn.execute(
                """
                INSERT INTO provisioned_assets (
                    id, subscription_id, resource_group, name, type, location, sku,
                    tags, is_public, fqdn, pipeline_tag, raw_json, endpoints,
                    auth_methods, first_detected, last_synced, status, is_restricted
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "/subscriptions/sub-1/resourceGroups/rg-data/providers/Microsoft.DocumentDB/databaseAccounts/cosmos-one",
                    "sub-1",
                    "rg-data",
                    "cosmos-one",
                    "Microsoft.DocumentDB/databaseAccounts",
                    "westus",
                    None,
                    None,
                    1,
                    None,
                    None,
                    "{}",
                    None,
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    "active",
                    0,
                ),
            )

            result = _build_child_table(
                conn,
                "sub-1",
                "Microsoft.DocumentDB/databaseAccounts",
                [{"rg": "rg-data", "name": "cosmos-one"}],
            )
        finally:
            conn.close()

        assert result["view_type"] == "table"
        assert result["rows"], result
        assert "SKU" not in result["columns"], result["columns"]
        assert result["rows"][0][3] == "cosmos-one.documents.azure.com", result["rows"]
        assert result["rows"][0][4]["label"] == "🌐 Public", result["rows"]

    def test_drilldown_table_marks_cosmos_ip_restrictions(self):
        import sqlite3
        import sys

        sys.path.insert(0, str(REPO_ROOT))
        from web.app import _build_child_table

        conn = sqlite3.connect(":memory:")
        try:
            conn.execute(
                """
                CREATE TABLE provisioned_assets (
                    id TEXT PRIMARY KEY,
                    subscription_id TEXT,
                    resource_group TEXT,
                    name TEXT,
                    type TEXT,
                    location TEXT,
                    sku TEXT,
                    tags TEXT,
                    is_public INTEGER DEFAULT 0,
                    fqdn TEXT,
                    pipeline_tag TEXT,
                    raw_json TEXT,
                    endpoints TEXT,
                    auth_methods TEXT,
                    first_detected TEXT,
                    last_synced TEXT,
                    status TEXT DEFAULT 'active',
                    is_restricted INTEGER DEFAULT 0,
                    ip_restrictions TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT INTO provisioned_assets (
                    id, subscription_id, resource_group, name, type, location, sku,
                    tags, is_public, fqdn, pipeline_tag, raw_json, endpoints, auth_methods,
                    first_detected, last_synced, status, is_restricted, ip_restrictions
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "/subscriptions/sub-1/resourceGroups/rg-data/providers/Microsoft.DocumentDB/databaseAccounts/cosmos-two",
                    "sub-1",
                    "rg-data",
                    "cosmos-two",
                    "Microsoft.DocumentDB/databaseAccounts",
                    "westus",
                    None,
                    None,
                    0,
                    None,
                    None,
                    "{}",
                    None,
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    "active",
                    1,
                    '["10.10.0.0/24"]',
                ),
            )

            result = _build_child_table(
                conn,
                "sub-1",
                "Microsoft.DocumentDB/databaseAccounts",
                [{"rg": "rg-data", "name": "cosmos-two"}],
            )
        finally:
            conn.close()

        assert result["view_type"] == "table"
        assert result["rows"], result
        assert result["rows"][0][4]["label"] == "⚠️ IP restricted", result["rows"]


class TestSubscriptionOverlayViews:
    """Regression tests for the shared subscription overlay helper."""

    def _call(self, rows):
        import sys

        sys.path.insert(0, str(REPO_ROOT))
        from web.subscription_diagram_helpers import build_subscription_overlay_views

        return build_subscription_overlay_views(
            rows,
            sanitise_node_id=lambda value: "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in value),
            friendly_type=lambda arm_type: "App Gateway" if "applicationgateway" in (arm_type or "").lower() else (arm_type.split("/")[-1] if arm_type else "Resource"),
            get_icon_path=lambda _arm_type: None,
            normalize_attack_paths=lambda raw_paths, reviewer=None: raw_paths,
        )

    def test_gateway_waf_mode_labels_entry_edges(self):
        rows = [
            (
                "appgwone",
                "Microsoft.Network/applicationGateways",
                "rgnet",
                "appgwone.example.com",
                1,
                "WAF_v2",
                "gw-id",
                0,
                "HTTPS:443, HTTP:80",
                0,
                "PolicyAttached",
                None,
            ),
        ]
        overlay = self._call(rows)
        mermaid = overlay["exposure"]["mermaid"]
        assert 'Internet -->|"WAF"| rgnet_appgwone' in mermaid, mermaid
        assert "class rgnet_appgwone entryPointProtected;" in mermaid, mermaid
        assert "linkStyle 0 stroke:#f97316" in mermaid, mermaid


class TestCloudPosture:
    """Regression tests for cloud posture inference."""

    def _make_conn(self):
        import sqlite3

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE repositories (
                id INTEGER PRIMARY KEY,
                experiment_id TEXT,
                repo_name TEXT
            );
            CREATE TABLE repository_subscriptions (
                repository_id INTEGER,
                subscription_id TEXT,
                deploy_role TEXT
            );
            CREATE TABLE provisioned_assets (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                resource_group TEXT,
                name TEXT,
                type TEXT,
                location TEXT,
                sku TEXT,
                tags TEXT,
                is_public INTEGER DEFAULT 0,
                is_restricted INTEGER DEFAULT 0,
                ip_restrictions TEXT,
                endpoints TEXT,
                auth_methods TEXT,
                fqdn TEXT,
                pipeline_tag TEXT,
                raw_json TEXT,
                first_detected TEXT,
                last_synced TEXT,
                status TEXT DEFAULT 'active'
            );
            CREATE TABLE appgw_routing_rules (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                gateway_name TEXT,
                backend_fqdn TEXT,
                backend_pool_name TEXT,
                protocol TEXT,
                port INTEGER,
                waf_policy_name TEXT,
                listener_protocol TEXT
            );
            CREATE TABLE appgw_waf_policies (
                name TEXT,
                subscription_id TEXT,
                state TEXT,
                associated_gateways TEXT
            );
            CREATE TABLE function_app_http_triggers (
                id TEXT PRIMARY KEY,
                subscription_id TEXT NOT NULL,
                function_app_id TEXT NOT NULL,
                function_app_name TEXT NOT NULL,
                resource_group TEXT,
                function_name TEXT NOT NULL,
                route TEXT,
                auth_level TEXT,
                methods TEXT,
                fqdn TEXT,
                full_url TEXT,
                is_public INTEGER DEFAULT 0,
                last_synced TEXT
            );
            """
        )

        conn.execute(
            "INSERT INTO repositories (id, experiment_id, repo_name) VALUES (1, 'exp-1', 'orders')"
        )
        conn.execute(
            "INSERT INTO repository_subscriptions (repository_id, subscription_id, deploy_role) VALUES (1, 'sub-1', 'prod')"
        )
        conn.executemany(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, resource_group, name, type, location, sku, tags,
                is_public, is_restricted, ip_restrictions, endpoints, auth_methods,
                fqdn, pipeline_tag, raw_json, first_detected, last_synced, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/sites/orders-api",
                    "sub-1",
                    "rg-app",
                    "orders-api",
                    "Microsoft.Web/sites",
                    "westus",
                    "B1",
                    "{}",
                    1,
                    0,
                    "[]",
                    "[]",
                    "[]",
                    "orders.example.com",
                    None,
                    "{}",
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    "active",
                ),
                (
                    "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/applicationGateways/appgwone",
                    "sub-1",
                    "rg-net",
                    "appgwone",
                    "Microsoft.Network/applicationGateways",
                    "westus",
                    "WAF_v2",
                    "{}",
                    1,
                    0,
                    "[]",
                    "[]",
                    "[]",
                    "appgwone.example.com",
                    None,
                    '{"_extra": {"waf_mode": "PolicyAttached"}}',
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    "active",
                ),
            ],
        )
        conn.execute(
            """
            INSERT INTO appgw_routing_rules (
                id, subscription_id, gateway_name, backend_fqdn, backend_pool_name,
                protocol, port, waf_policy_name, listener_protocol
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "appgwone::rule-1",
                "sub-1",
                "appgwone",
                "orders.example.com",
                "backend-pool",
                "HTTPS",
                443,
                None,
                "HTTPS",
            ),
        )
        conn.execute(
            """
            INSERT INTO appgw_waf_policies (name, subscription_id, state, associated_gateways)
            VALUES (?, ?, ?, ?)
            """,
            ("policy-one", "sub-1", "Enabled", '["appgwone"]'),
        )
        conn.execute(
            """
            INSERT INTO function_app_http_triggers (
                id, subscription_id, function_app_id, function_app_name, resource_group,
                function_name, route, auth_level, methods, fqdn, full_url, is_public, last_synced
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/sites/orders-api::GetOrders",
                "sub-1",
                "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/sites/orders-api",
                "orders-api",
                "rg-app",
                "GetOrders",
                "orders",
                "function",
                '["GET"]',
                "orders.example.com",
                "https://orders.example.com/api/orders",
                0,
                "2026-06-01T00:00:00Z",
            ),
        )
        return conn

    def test_gateway_waf_policy_marks_repo_behind_waf(self):
        import os
        import sys

        sys.path.insert(0, str(REPO_ROOT))
        os.environ.setdefault("FLASK_APP", "web/app.py")
        from web.app import _compute_cloud_posture

        posture = _compute_cloud_posture(self._make_conn(), "exp-1", "orders")
        assert posture["behind_app_gateway"] is True, posture
        assert posture["app_gateway_name"] == "appgwone", posture
        assert posture["behind_waf"] is True, posture
        assert posture["function_http_trigger_count"] == 1, posture
        assert posture["function_http_auth_required"] is True, posture
        assert posture["function_http_has_anonymous"] is False, posture

    def test_cloud_posture_api_uses_cached_metadata(self, monkeypatch):
        import json
        import os
        import sqlite3
        import sys

        sys.path.insert(0, str(REPO_ROOT))
        os.environ.setdefault("FLASK_APP", "web/app.py")
        import web.app as app_module

        conn = sqlite3.connect(":memory:")
        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
        monkeypatch.setattr(app_module, "_get_experiment_for_repo", lambda conn, repo_name, experiment_id: experiment_id)
        monkeypatch.setattr(
            app_module.db_helpers,
            "get_context_metadata",
            lambda *args, **kwargs: json.dumps({
                "behind_app_gateway": True,
                "behind_waf": True,
                "behind_apim": False,
                "aks_cluster": None,
                "aks_secured": False,
                "endpoint_exposure": "restricted",
                "auth_methods": [],
            }),
        )
        monkeypatch.setattr(
            app_module,
            "_compute_cloud_posture",
            lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not recompute")),
        )

        client = app_module.app.test_client()
        resp = client.get("/api/cloud-posture/exp-1/orders")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["posture"]["behind_waf"] is True

    def test_subscription_diagram_uses_persistent_cache(self, monkeypatch):
        import json
        import os
        import sqlite3
        import sys

        sys.path.insert(0, str(REPO_ROOT))
        os.environ.setdefault("FLASK_APP", "web/app.py")
        import web.app as app_module

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE subscriptions (
                id TEXT PRIMARY KEY,
                display_name TEXT,
                environment TEXT,
                state TEXT,
                last_synced TEXT
            );
            CREATE TABLE provisioned_assets (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                name TEXT,
                type TEXT,
                resource_group TEXT,
                last_synced TEXT
            );
            CREATE TABLE subscription_diagram_cache (
                sub_id TEXT PRIMARY KEY,
                cache_signature TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state, last_synced) VALUES (?, ?, ?, ?, ?)",
            ("sub-1", "Demo Subscription", "production", "Enabled", "2026-06-09T10:00:00Z"),
        )
        conn.execute(
            "INSERT INTO provisioned_assets (id, subscription_id, name, type, resource_group, last_synced) VALUES (?, ?, ?, ?, ?, ?)",
            ("asset-1", "sub-1", "gw-one", "Microsoft.Network/applicationGateways", "rg-net", "2026-06-09T10:00:00Z"),
        )
        signature, _ = app_module._subscription_diagram_cache_signature(conn, "sub-1")
        payload = {
            "subscription_name": "Demo Subscription",
            "environment": "production",
            "total_assets": 1,
            "ingress_diagram": {"mermaid": "graph TD", "node_drilldown_map": {}},
        }
        conn.execute(
            "INSERT INTO subscription_diagram_cache (sub_id, cache_signature, payload_json) VALUES (?, ?, ?)",
            ("sub-1", signature, json.dumps(payload)),
        )
        conn.commit()

        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
        monkeypatch.setattr(
            app_module,
            "_build_ingress_diagram",
            lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not rebuild")),
        )

        client = app_module.app.test_client()
        resp = client.get("/api/subscriptions/sub-1/diagram")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["subscription_name"] == "Demo Subscription"
        assert data["ingress_diagram"]["mermaid"] == "graph TD"


def _cloud_assets_payload():
    plan_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/serverfarms/plan-one"
    app_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/sites/app-one"
    fn_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/sites/fn-one"
    ase_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/hostingEnvironments/ase-one"
    ase_app_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/sites/ase-app"
    ase_fn_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/sites/ase-fn"
    gw_id = "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/applicationGateways/gw-one"
    storage_id = "/subscriptions/sub-1/resourceGroups/rg-data/providers/Microsoft.Storage/storageAccounts/sa-one"
    container_id = f"{storage_id}/blobServices/default/containers/logs"
    sql_id = "/subscriptions/sub-1/resourceGroups/rg-data/providers/Microsoft.Sql/servers/sql-one"
    db_id = f"{sql_id}/databases/appdb"
    listener_id = "listener::gw-one::https::gw.example.com"
    storage_id = "/subscriptions/sub-1/resourceGroups/rg-data/providers/Microsoft.Storage/storageAccounts/sa-one"
    container_id = f"{storage_id}/blobServices/default/containers/logs"
    acr_id = "/subscriptions/sub-1/resourceGroups/rg-data/providers/Microsoft.ContainerRegistry/registries/acr-one"
    sql_id = "/subscriptions/sub-1/resourceGroups/rg-data/providers/Microsoft.Sql/servers/sql-one"
    db_id = f"{sql_id}/databases/appdb"

    assets = [
        {
            "id": plan_id,
            "name": "plan-one",
            "type": "Microsoft.Web/serverfarms",
            "type_label": "App Service Plan",
            "display_type_label": "App Service Plan",
            "resource_group": "rg-app",
            "location": "westus",
            "sku": "P1v3",
            "fqdn": None,
            "is_public": False,
            "status": "active",
            "pipeline_tag": None,
            "first_detected": "2026-06-01T00:00:00Z",
            "last_synced": "2026-06-01T00:00:00Z",
            "sub_id": "sub-1",
            "sub_name": "Demo Subscription",
            "environment": "production",
            "cloud_provider": "Azure",
            "linked_repo": None,
            "kind": None,
            "parent_id": None,
            "parent_name": None,
            "parent_resource_group": None,
            "parent_type_label": None,
            "children_count": 2,
            "is_child": False,
            "depth": 0,
        },
        {
            "id": app_id,
            "name": "app-one",
            "type": "Microsoft.Web/sites",
            "type_label": "App Service",
            "display_type_label": "App Service",
            "resource_group": "rg-app",
            "location": "westus",
            "sku": "B1",
            "fqdn": "app-one.azurewebsites.net",
            "is_public": True,
            "status": "active",
            "pipeline_tag": None,
            "first_detected": "2026-06-01T00:00:00Z",
            "last_synced": "2026-06-01T00:00:00Z",
            "sub_id": "sub-1",
            "sub_name": "Demo Subscription",
            "environment": "production",
            "cloud_provider": "Azure",
            "linked_repo": "org/demo-repo",
            "kind": "app",
            "parent_id": plan_id,
            "parent_name": "plan-one",
            "parent_resource_group": "rg-app",
            "parent_type_label": "App Service Plan",
            "children_count": 0,
            "is_child": True,
            "depth": 1,
        },
        {
            "id": fn_id,
            "name": "fn-one",
            "type": "Microsoft.Web/sites",
            "type_label": "App Service",
            "display_type_label": "Function App",
            "resource_group": "rg-app",
            "location": "westus",
            "sku": "Y1",
            "fqdn": "fn-one.azurewebsites.net",
            "is_public": True,
            "status": "active",
            "pipeline_tag": None,
            "first_detected": "2026-06-01T00:00:00Z",
            "last_synced": "2026-06-01T00:00:00Z",
            "sub_id": "sub-1",
            "sub_name": "Demo Subscription",
            "environment": "production",
            "cloud_provider": "Azure",
            "linked_repo": None,
            "kind": "functionapp,linux",
            "parent_id": plan_id,
            "parent_name": "plan-one",
            "parent_resource_group": "rg-app",
            "parent_type_label": "App Service Plan",
            "children_count": 0,
            "is_child": True,
            "depth": 1,
        },
        {
            "id": ase_id,
            "name": "ase-one",
            "type": "Microsoft.Web/hostingEnvironments",
            "type_label": "App Service Environment",
            "display_type_label": "App Service Environment",
            "resource_group": "rg-app",
            "location": "westus",
            "sku": "ASEv3",
            "fqdn": "ase-one.westus.appserviceenvironment.net",
            "is_public": False,
            "status": "active",
            "pipeline_tag": None,
            "first_detected": "2026-06-01T00:00:00Z",
            "last_synced": "2026-06-01T00:00:00Z",
            "sub_id": "sub-1",
            "sub_name": "Demo Subscription",
            "environment": "production",
            "cloud_provider": "Azure",
            "linked_repo": None,
            "kind": "ASEv3",
            "parent_id": None,
            "parent_name": None,
            "parent_resource_group": None,
            "parent_type_label": None,
            "children_count": 2,
            "is_child": False,
            "depth": 0,
        },
        {
            "id": ase_app_id,
            "name": "ase-app",
            "type": "Microsoft.Web/sites",
            "type_label": "App Service",
            "display_type_label": "App Service",
            "resource_group": "rg-app",
            "location": "westus",
            "sku": "P1v3",
            "fqdn": "ase-app.azurewebsites.net",
            "is_public": False,
            "status": "active",
            "pipeline_tag": None,
            "first_detected": "2026-06-01T00:00:00Z",
            "last_synced": "2026-06-01T00:00:00Z",
            "sub_id": "sub-1",
            "sub_name": "Demo Subscription",
            "environment": "production",
            "cloud_provider": "Azure",
            "linked_repo": None,
            "kind": "app",
            "parent_id": ase_id,
            "parent_name": "ase-one",
            "parent_resource_group": "rg-app",
            "parent_type_label": "App Service Environment",
            "children_count": 0,
            "is_child": True,
            "depth": 1,
        },
        {
            "id": ase_fn_id,
            "name": "ase-fn",
            "type": "Microsoft.Web/sites",
            "type_label": "App Service",
            "display_type_label": "Function App",
            "resource_group": "rg-app",
            "location": "westus",
            "sku": "Y1",
            "fqdn": "ase-fn.azurewebsites.net",
            "is_public": False,
            "status": "active",
            "pipeline_tag": None,
            "first_detected": "2026-06-01T00:00:00Z",
            "last_synced": "2026-06-01T00:00:00Z",
            "sub_id": "sub-1",
            "sub_name": "Demo Subscription",
            "environment": "production",
            "cloud_provider": "Azure",
            "linked_repo": None,
            "kind": "functionapp,linux",
            "parent_id": ase_id,
            "parent_name": "ase-one",
            "parent_resource_group": "rg-app",
            "parent_type_label": "App Service Environment",
            "children_count": 0,
            "is_child": True,
            "depth": 1,
        },
        {
            "id": gw_id,
            "name": "gw-one",
            "type": "Microsoft.Network/applicationGateways",
            "type_label": "App Gateway",
            "display_type_label": "App Gateway",
            "resource_group": "rg-net",
            "location": "westus",
            "sku": "WAF_v2",
            "fqdn": "gw.example.com",
            "is_public": True,
            "status": "active",
            "pipeline_tag": None,
            "first_detected": "2026-06-01T00:00:00Z",
            "last_synced": "2026-06-01T00:00:00Z",
            "sub_id": "sub-1",
            "sub_name": "Demo Subscription",
            "environment": "production",
            "cloud_provider": "Azure",
            "linked_repo": None,
            "kind": None,
            "parent_id": None,
            "parent_name": None,
            "parent_resource_group": None,
            "parent_type_label": None,
            "children_count": 1,
            "is_child": False,
            "depth": 0,
        },
        {
            "id": listener_id,
            "name": "https (HTTPS)",
            "type": "microsoft.network/applicationgatewaylisteners",
            "type_label": "App Gateway Listener",
            "display_type_label": "App Gateway Listener",
            "resource_group": "rg-net",
            "location": None,
            "sku": "HTTPS → gw-one",
            "fqdn": "gw.example.com",
            "is_public": True,
            "status": "active",
            "pipeline_tag": None,
            "first_detected": None,
            "last_synced": None,
            "sub_id": "sub-1",
            "sub_name": "Demo Subscription",
            "environment": "production",
            "cloud_provider": "Azure",
            "linked_repo": None,
            "kind": None,
            "parent_id": gw_id,
            "parent_name": "gw-one",
            "parent_resource_group": "rg-net",
            "parent_type_label": "App Gateway",
            "children_count": 0,
            "is_child": True,
            "depth": 1,
        },
        {
            "id": storage_id,
            "name": "sa-one",
            "type": "Microsoft.Storage/storageAccounts",
            "type_label": "Storage Account",
            "display_type_label": "Storage Account",
            "resource_group": "rg-data",
            "location": "westus",
            "sku": "Standard_LRS",
            "fqdn": "sa-one.blob.core.windows.net",
            "is_public": True,
            "status": "active",
            "pipeline_tag": None,
            "first_detected": "2026-06-01T00:00:00Z",
            "last_synced": "2026-06-01T00:00:00Z",
            "sub_id": "sub-1",
            "sub_name": "Demo Subscription",
            "environment": "production",
            "cloud_provider": "Azure",
            "linked_repo": None,
            "kind": "StorageV2",
            "parent_id": None,
            "parent_name": None,
            "parent_resource_group": None,
            "parent_type_label": None,
            "children_count": 1,
            "is_child": False,
            "depth": 0,
        },
        {
            "id": container_id,
            "name": "logs",
            "type": "Microsoft.Storage/storageAccounts/blobServices/containers",
            "type_label": "Blob Container",
            "display_type_label": "Blob Container",
            "resource_group": "rg-data",
            "location": "westus",
            "sku": "blob",
            "fqdn": "sa-one.blob.core.windows.net/logs",
            "is_public": True,
            "status": "active",
            "pipeline_tag": None,
            "first_detected": "2026-06-01T00:00:00Z",
            "last_synced": "2026-06-01T00:00:00Z",
            "sub_id": "sub-1",
            "sub_name": "Demo Subscription",
            "environment": "production",
            "cloud_provider": "Azure",
            "linked_repo": None,
            "kind": None,
            "parent_id": storage_id,
            "parent_name": "sa-one",
            "parent_resource_group": "rg-data",
            "parent_type_label": "Storage Account",
            "children_count": 0,
            "is_child": True,
            "depth": 1,
        },
        {
            "id": acr_id,
            "name": "acr-one",
            "type": "Microsoft.ContainerRegistry/registries",
            "type_label": "Container Registry",
            "display_type_label": "ACR",
            "resource_group": "rg-data",
            "location": "westus",
            "sku": "Standard",
            "fqdn": "acr-one.azurecr.io",
            "is_public": False,
            "status": "active",
            "pipeline_tag": None,
            "first_detected": "2026-06-01T00:00:00Z",
            "last_synced": "2026-06-01T00:00:00Z",
            "sub_id": "sub-1",
            "sub_name": "Demo Subscription",
            "environment": "production",
            "cloud_provider": "Azure",
            "linked_repo": None,
            "kind": None,
            "parent_id": None,
            "parent_name": None,
            "parent_resource_group": None,
            "parent_type_label": None,
            "children_count": 0,
            "is_child": False,
            "depth": 0,
        },
        {
            "id": sql_id,
            "name": "sql-one",
            "type": "Microsoft.Sql/servers",
            "type_label": "SQL Server",
            "display_type_label": "SQL Server",
            "resource_group": "rg-data",
            "location": "westus",
            "sku": None,
            "fqdn": "sql-one.database.windows.net",
            "is_public": True,
            "status": "active",
            "pipeline_tag": None,
            "first_detected": "2026-06-01T00:00:00Z",
            "last_synced": "2026-06-01T00:00:00Z",
            "sub_id": "sub-1",
            "sub_name": "Demo Subscription",
            "environment": "production",
            "cloud_provider": "Azure",
            "linked_repo": None,
            "kind": None,
            "parent_id": None,
            "parent_name": None,
            "parent_resource_group": None,
            "parent_type_label": None,
            "children_count": 1,
            "is_child": False,
            "depth": 0,
        },
        {
            "id": db_id,
            "name": "appdb",
            "type": "Microsoft.Sql/servers/databases",
            "type_label": "SQL Database",
            "display_type_label": "SQL Database",
            "resource_group": "rg-data",
            "location": "westus",
            "sku": "S0",
            "fqdn": "sql-one.database.windows.net",
            "is_public": True,
            "status": "active",
            "pipeline_tag": None,
            "first_detected": "2026-06-01T00:00:00Z",
            "last_synced": "2026-06-01T00:00:00Z",
            "sub_id": "sub-1",
            "sub_name": "Demo Subscription",
            "environment": "production",
            "cloud_provider": "Azure",
            "linked_repo": None,
            "kind": None,
            "parent_id": sql_id,
            "parent_name": "sql-one",
            "parent_resource_group": "rg-data",
            "parent_type_label": "SQL Server",
            "children_count": 0,
            "is_child": True,
            "depth": 1,
        },
    ]

    return {
        "subscriptions": [{
            "id": "sub-1",
            "display_name": "Demo Subscription",
            "environment": "production",
            "cloud_provider": "Azure",
            "state": "Enabled",
            "last_synced": "2026-06-01T00:00:00Z",
            "total": 13,
            "public_count": 8,
            "stale_count": 0,
        }],
        "assets": assets,
        "type_summary": [
            {"label": "App Service Plan", "count": 1},
            {"label": "App Service", "count": 1},
            {"label": "Function App", "count": 1},
            {"label": "App Service Environment", "count": 1},
            {"label": "App Gateway", "count": 1},
            {"label": "App Gateway Listener", "count": 1},
            {"label": "Storage Account", "count": 1},
            {"label": "ACR", "count": 1},
            {"label": "Blob Container", "count": 1},
            {"label": "SQL Server", "count": 1},
            {"label": "SQL Database", "count": 1},
        ],
        "totals": {
            "assets": 13,
            "public": 8,
            "stale": 0,
            "linked": 1,
        },
    }


class TestCloudAssetsPage:
    """Tests for the all-cloud-assets page tree UI."""

    def _mock_api(self, page: Page) -> None:
        import json

        payload = _cloud_assets_payload()
        page.route(
            "**/api/cloud/assets",
            lambda route: route.fulfill(
                content_type="application/json",
                body=json.dumps(payload),
            ),
        )

    def test_assets_tree_expands_and_omits_status_column(self, page: Page, live_server: str):
        self._mock_api(page)
        page.goto(live_server + "/cloud/assets")
        page.wait_for_selector('tr[data-resource-id="/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/serverfarms/plan-one"]', timeout=8000)

        headers = page.locator("#ca-table thead th").all_inner_texts()
        assert all("Status" not in header for header in headers), headers

        plan_toggle = page.locator('tr[data-resource-id="/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/serverfarms/plan-one"] .expand-toggle')
        expect(plan_toggle).to_have_count(1)
        plan_title = page.locator('tr[data-resource-id="/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/serverfarms/plan-one"] .asset-name')
        toggle_box = plan_toggle.bounding_box()
        title_box = plan_title.bounding_box()
        assert toggle_box and title_box, "Expected plan toggle and title to have layout boxes"
        assert abs(toggle_box["y"] - title_box["y"]) < 4
        expect(page.locator('tr[data-resource-id="/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/sites/app-one"]')).to_be_hidden()

        plan_toggle.click()

        expect(page.locator('tr[data-resource-id="/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/sites/app-one"]')).to_be_visible()
        expect(page.locator('tr[data-resource-id="/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/sites/fn-one"] .type-badge')).to_contain_text("Function App")
        ase_toggle = page.locator('tr[data-resource-id="/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/hostingEnvironments/ase-one"] .expand-toggle')
        expect(ase_toggle).to_have_count(1)
        expect(page.locator('tr[data-resource-id="/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/sites/ase-app"]')).to_be_hidden()
        ase_toggle.click()
        expect(page.locator('tr[data-resource-id="/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/sites/ase-app"]')).to_be_visible()
        expect(page.locator('tr[data-resource-id="/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/sites/ase-fn"] .type-badge')).to_contain_text("Function App")
        gateway_toggle = page.locator('tr[data-resource-id="/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/applicationGateways/gw-one"] .expand-toggle')
        expect(gateway_toggle).to_have_count(1)
        gateway_toggle.click()
        expect(page.locator('tr[data-resource-id="listener::gw-one::https::gw.example.com"]')).to_be_visible()

        storage_toggle = page.locator('tr[data-resource-id="/subscriptions/sub-1/resourceGroups/rg-data/providers/Microsoft.Storage/storageAccounts/sa-one"] .expand-toggle')
        expect(storage_toggle).to_have_count(1)
        storage_toggle.click()
        expect(page.locator('tr[data-resource-id="/subscriptions/sub-1/resourceGroups/rg-data/providers/Microsoft.Storage/storageAccounts/sa-one/blobServices/default/containers/logs"] .type-badge')).to_contain_text("Blob Container")
        expect(page.locator('tr[data-resource-id="/subscriptions/sub-1/resourceGroups/rg-data/providers/Microsoft.ContainerRegistry/registries/acr-one"] .type-badge')).to_contain_text("ACR")
        expect(page.locator("#sidebar-types")).to_contain_text("ACR")

        sql_toggle = page.locator('tr[data-resource-id="/subscriptions/sub-1/resourceGroups/rg-data/providers/Microsoft.Sql/servers/sql-one"] .expand-toggle')
        expect(sql_toggle).to_have_count(1)
        sql_toggle.click()
        expect(page.locator('tr[data-resource-id="/subscriptions/sub-1/resourceGroups/rg-data/providers/Microsoft.Sql/servers/sql-one/databases/appdb"] .type-badge')).to_contain_text("SQL Database")


class TestCloudAssetsApi:
    """Backend tests for `/api/cloud/assets` hierarchy metadata."""

    def _make_conn(self):
        import json
        import sqlite3

        conn = sqlite3.connect(":memory:")
        conn.executescript(
            """
            CREATE TABLE subscriptions (
                id TEXT PRIMARY KEY,
                display_name TEXT,
                environment TEXT,
                state TEXT,
                last_synced TEXT
            );
            CREATE TABLE provisioned_assets (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                resource_group TEXT,
                name TEXT,
                type TEXT,
                location TEXT,
                sku TEXT,
                tags TEXT,
                is_public INTEGER DEFAULT 0,
                fqdn TEXT,
                pipeline_tag TEXT,
                raw_json TEXT,
                first_detected TEXT,
                last_synced TEXT,
                status TEXT DEFAULT 'active'
            );
            CREATE TABLE repositories (
                id INTEGER PRIMARY KEY,
                repo_name TEXT
            );
            CREATE TABLE provisioned_asset_repo_links (
                asset_id TEXT,
                repository_id INTEGER,
                match_method TEXT,
                confidence TEXT
            );
            CREATE TABLE appgw_routing_rules (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                gateway_name TEXT,
                listener_name TEXT,
                hostname TEXT,
                protocol TEXT,
                resource_group TEXT
            );
            """
        )

        plan_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/serverfarms/plan-one"
        app_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/sites/app-one"
        fn_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/sites/fn-one"
        ase_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/hostingEnvironments/ase-one"
        ase_app_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/sites/ase-app"
        ase_fn_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/sites/ase-fn"
        gw_id = "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/applicationGateways/gw-one"
        storage_id = "/subscriptions/sub-1/resourceGroups/rg-data/providers/Microsoft.Storage/storageAccounts/sa-one"
        container_id = "/subscriptions/sub-1/resourceGroups/rg-data/providers/Microsoft.Storage/storageAccounts/sa-one/blobServices/default/containers/logs"
        acr_id = "/subscriptions/sub-1/resourceGroups/rg-data/providers/Microsoft.ContainerRegistry/registries/acr-one"
        blob_id = "/subscriptions/sub-1/resourceGroups/rg-data/providers/Microsoft.Storage/storageAccounts/sa-one/blobServices/default/containers/logs/blobs/hello.txt"
        sql_id = "/subscriptions/sub-1/resourceGroups/rg-data/providers/Microsoft.Sql/servers/sql-one"
        db_id = "/subscriptions/sub-1/resourceGroups/rg-data/providers/Microsoft.Sql/servers/sql-one/databases/appdb"

        now = "2026-06-01T00:00:00Z"
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state, last_synced) VALUES (?,?,?,?,?)",
            ("sub-1", "Demo Subscription", "production", "Enabled", now),
        )
        conn.executemany(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, resource_group, name, type, location, sku,
                tags, is_public, fqdn, pipeline_tag, raw_json, first_detected, last_synced, status
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (plan_id, "sub-1", "rg-app", "plan-one", "Microsoft.Web/serverfarms", "westus", "P1v3",
                 None, 0, None, None, json.dumps({"kind": "app"}), now, now, "active"),
                (app_id, "sub-1", "rg-app", "app-one", "Microsoft.Web/sites", "westus", "B1",
                 None, 1, "app-one.azurewebsites.net", None, json.dumps({
                     "kind": "app",
                     "appServicePlanId": plan_id,
                 }), now, now, "active"),
                (fn_id, "sub-1", "rg-app", "fn-one", "Microsoft.Web/sites", "westus", "Y1",
                 None, 1, "fn-one.azurewebsites.net", None, json.dumps({
                     "kind": "functionapp,linux",
                     "serverFarmId": plan_id,
                 }), now, now, "active"),
                (ase_id, "sub-1", "rg-app", "ase-one", "Microsoft.Web/hostingEnvironments", "westus", "ASEv3",
                 None, 0, "ase-one.westus.appserviceenvironment.net", None, json.dumps({
                    "kind": "ASEv3",
                 }), now, now, "active"),
                (ase_app_id, "sub-1", "rg-app", "ase-app", "Microsoft.Web/sites", "westus", "P1v3",
                 None, 0, "ase-app.azurewebsites.net", None, json.dumps({
                    "kind": "app",
                    "hostingEnvironmentProfile": {"id": ase_id},
                 }), now, now, "active"),
                (ase_fn_id, "sub-1", "rg-app", "ase-fn", "Microsoft.Web/sites", "westus", "Y1",
                 None, 0, "ase-fn.azurewebsites.net", None, json.dumps({
                    "kind": "functionapp,linux",
                    "hostingEnvironmentProfile": {"id": ase_id},
                 }), now, now, "active"),
                (gw_id, "sub-1", "rg-net", "gw-one", "Microsoft.Network/applicationGateways", "westus", "WAF_v2",
                 None, 1, "gw.example.com", None, json.dumps({}), now, now, "active"),
                (storage_id, "sub-1", "rg-data", "sa-one", "Microsoft.Storage/storageAccounts", "westus", "Standard_LRS",
                 None, 1, "sa-one.blob.core.windows.net", None, json.dumps({
                     "kind": "StorageV2",
                 }), now, now, "active"),
                (container_id, "sub-1", "rg-data", "logs", "Microsoft.Storage/storageAccounts/blobServices/containers", "westus", "blob",
                 None, 1, "sa-one.blob.core.windows.net/logs", None, json.dumps({
                     "publicAccess": "blob",
                 }), now, now, "active"),
                (acr_id, "sub-1", "rg-data", "acr-one", "Microsoft.ContainerRegistry/registries", "westus", "Standard",
                 None, 0, "acr-one.azurecr.io", None, json.dumps({
                     "loginServer": "acr-one.azurecr.io",
                 }), now, now, "active"),
                (blob_id, "sub-1", "rg-data", "hello.txt", "Microsoft.Storage/storageAccounts/blobServices/containers/blobs", "westus", "Hot",
                 None, 1, "sa-one.blob.core.windows.net/logs/hello.txt", None, json.dumps({
                     "contentType": "text/plain",
                 }), now, now, "active"),
                (sql_id, "sub-1", "rg-data", "sql-one", "Microsoft.Sql/servers", "westus", None,
                 None, 1, "sql-one.database.windows.net", None, json.dumps({}), now, now, "active"),
                (db_id, "sub-1", "rg-data", "appdb", "Microsoft.Sql/servers/databases", "westus", "S0",
                 None, 1, "sql-one.database.windows.net", None, json.dumps({
                     "status": "Online",
                 }), now, now, "active"),
            ],
        )
        conn.execute("INSERT INTO repositories (id, repo_name) VALUES (?, ?)", (1, "org/demo-repo"))
        conn.execute(
            "INSERT INTO provisioned_asset_repo_links (asset_id, repository_id, match_method, confidence) VALUES (?,?,?,?)",
            (app_id, 1, "manual", "high"),
        )
        conn.execute(
            """
            INSERT INTO appgw_routing_rules (
                id, subscription_id, gateway_name, listener_name, hostname, protocol, resource_group
            ) VALUES (?,?,?,?,?,?,?)
            """,
            ("gw-one::https", "sub-1", "gw-one", "https", "gw.example.com", "https", "rg-net"),
        )
        return conn

    def test_api_cloud_assets_returns_hierarchy_metadata(self, monkeypatch):
        import os
        import sys

        sys.path.insert(0, str(REPO_ROOT))
        os.environ.setdefault("FLASK_APP", "web/app.py")
        import web.app as app_module

        conn = self._make_conn()
        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)

        client = app_module.app.test_client()
        resp = client.get("/api/cloud/assets")
        assert resp.status_code == 200
        data = resp.get_json()
        assets = {a["id"]: a for a in data["assets"]}

        plan = assets["/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/serverfarms/plan-one"]
        app = assets["/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/sites/app-one"]
        fn = assets["/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/sites/fn-one"]
        ase = assets["/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/hostingEnvironments/ase-one"]
        ase_app = assets["/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/sites/ase-app"]
        ase_fn = assets["/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/sites/ase-fn"]
        gw = assets["/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/applicationGateways/gw-one"]
        listener = assets["listener::gw-one::https::gw.example.com"]

        storage = assets["/subscriptions/sub-1/resourceGroups/rg-data/providers/Microsoft.Storage/storageAccounts/sa-one"]
        acr = assets["/subscriptions/sub-1/resourceGroups/rg-data/providers/Microsoft.ContainerRegistry/registries/acr-one"]
        container = assets["/subscriptions/sub-1/resourceGroups/rg-data/providers/Microsoft.Storage/storageAccounts/sa-one/blobServices/default/containers/logs"]
        blob = assets["/subscriptions/sub-1/resourceGroups/rg-data/providers/Microsoft.Storage/storageAccounts/sa-one/blobServices/default/containers/logs/blobs/hello.txt"]
        sql = assets["/subscriptions/sub-1/resourceGroups/rg-data/providers/Microsoft.Sql/servers/sql-one"]
        db = assets["/subscriptions/sub-1/resourceGroups/rg-data/providers/Microsoft.Sql/servers/sql-one/databases/appdb"]
        assert plan["children_count"] == 2
        assert app["parent_id"] == plan["id"]
        assert fn["display_type_label"] == "Function App"
        assert fn["parent_id"] == plan["id"]
        assert ase["display_type_label"] == "App Service Environment"
        assert ase["children_count"] == 2
        assert ase_app["parent_id"] == ase["id"]
        assert ase_app["parent_type_label"] == "App Service Environment"
        assert ase_fn["display_type_label"] == "Function App"
        assert ase_fn["parent_id"] == ase["id"]
        assert gw["children_count"] == 1
        assert listener["parent_id"] == gw["id"]
        assert listener["is_child"] is True
        assert storage["display_type_label"] == "Storage Account"
        assert storage["children_count"] == 1
        assert acr["display_type_label"] == "ACR"
        assert container["parent_id"] == storage["id"]
        assert container["parent_type_label"] == "Storage Account"
        assert container["children_count"] == 1
        assert blob["parent_id"] == container["id"]
        assert blob["display_type_label"] == "Blob"
        assert sql["children_count"] == 1
        assert db["parent_id"] == sql["id"]
        assert db["display_type_label"] == "SQL Database"
        assert any(item["label"] == "App Service Environment" for item in data["type_summary"])
        assert any(item["label"] == "Function App" for item in data["type_summary"])
        assert any(item["label"] == "ACR" for item in data["type_summary"])
