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
                code: 'flowchart LR; Internet["Internet"] --> A[Gateway] --> B[Step 1] --> C[Step 2] --> D[Step 3] --> E[Step 4] --> F[Step 5] --> G[End]'
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
        """Architecture diagrams should render mode tabs and update the summary."""
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
                  attack_paths: {
                    code: 'flowchart LR; Internet -.-> A[Gateway] -.-> B[API]',
                    title: 'Attack-path view',
                    description: 'Shows plausible attacker movement.',
                    legend: ['Dashed red edges: attacker movement'],
                    asset_summary: { entry_points: 1, api_layer: 1, backends: 1, data_stores: 1, public_assets: 1 },
                    attack_paths: [{ title: 'Public ingress into architecture' }, { title: 'Secrets pivot from workloads' }]
                  },
                  react_flow: {
                    type: 'react_flow',
                    title: 'React Flow view',
                    description: 'Interactive architecture graph.',
                    legend: [],
                    asset_summary: { entry_points: 1, api_layer: 1, backends: 1, data_stores: 1, public_assets: 1 },
                    nodes: [
                      { id: 'Internet', position: { x: 20, y: 20 }, data: { label: 'Internet', typeLabel: 'External', tier: 'internet', public: true } },
                      { id: 'gateway', position: { x: 320, y: 20 }, data: { label: 'Gateway', typeLabel: 'Application Gateway', tier: 'entry', public: true } },
                    ],
                    edges: [
                      { id: 'e1', source: 'Internet', target: 'gateway', label: 'public', type: 'smoothstep', style: { stroke: '#ef4444' } },
                    ]
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
        home.locator("#diagram-mode-tabs button:has-text('React Flow')").click()
        expect(home.locator("#diagram-views .react-flow")).to_be_visible()

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
            f"**/api/subscriptions/{self._SUB_ID}/diagram**",
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
            f"**/api/subscriptions/{self._SUB_ID}/drilldown**",
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
        """Navigate to /cloud, mock APIs, click preview, wait for SVG."""
        self._setup_mocks(page, mermaid_source)
        page.goto(live_server + "/cloud")
        page.wait_for_selector(".subscription-preview-btn", timeout=8000)
        page.locator(".subscription-preview-btn").first.click()
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

    def test_diagram_renders_on_subscription_preview(self, page: Page, live_server: str):
        """Clicking preview loads and renders the ingress SVG."""
        self._load_diagram(page, live_server, self._MERMAID_DISTINCT_LABELS)
        expect(page.locator("#ingress-diagram-div svg")).to_be_visible()

    def test_subscription_name_opens_react_flow_new_tab(self, page: Page, live_server: str):
        """Clicking the subscription name should open the React Flow page in a new tab."""
        self._setup_mocks(page, self._MERMAID_DISTINCT_LABELS)
        page.goto(live_server + "/cloud")
        page.wait_for_selector(".subscription-name-cell a", timeout=8000)

        with page.expect_popup() as popup_info:
            page.locator(".subscription-name-cell a").first.click()
        popup = popup_info.value

        expect(popup).to_have_url(f"{live_server}/cloud/architecture?sub={self._SUB_ID}", timeout=15000)

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
        """Clicking a drillable node (⤵ badge) must open the drill-down modal
        and render a table with the resource details."""
        self._load_diagram(page, live_server, self._MERMAID_DISTINCT_LABELS)

        # The App Gateway node should have been marked drillable
        drillable = page.locator("#ingress-diagram-div svg g.node-drillable")
        expect(drillable).to_have_count(1, timeout=5000)

        drillable.click()

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
        expect(page.locator("#drilldown-modal")).to_contain_text("Listener / Hostname")
        # Table should contain the mocked row data
        expect(page.locator("#drilldown-modal table td").first).to_contain_text("test-appgw")
        fqdn_link = page.locator("#drilldown-modal a[href='https://gw.example.com']").first
        expect(fqdn_link).to_be_visible(timeout=5000)
        assert fqdn_link.get_attribute("target") == "_blank"
        assert "noopener" in (fqdn_link.get_attribute("rel") or "")


class TestCloudPageAksIngressModal:
    """Playwright coverage for AKS ingress service drill-down grouping and search."""

    _SUB_ID = "aaaaaaaa-bbbb-cccc-dddd-ffffffffffff"

    _MERMAID_AKS = (
        "graph LR\n"
        '    test_aks["AKS Cluster"]\n'
        "    classDef aksCluster stroke:#2563eb,stroke-width:2px;\n"
        "    class test_aks aksCluster;\n"
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
                        "display_name": "AKS Ingress Subscription",
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

        node_map = {
            "test_aks": {
                "title": "aks-prod",
                "arm_type": "Microsoft.ContainerService/managedClusters",
                "resources": [{"rg": "rg-aks", "name": "aks-prod"}],
                "is_group": True,
                "can_drill": True,
            }
        }

        page.route(
            f"**/{self._SUB_ID}/diagram",
            lambda route: route.fulfill(
                content_type="application/json",
                body=json.dumps({
                    "subscription_name": "AKS Ingress Subscription",
                    "environment": "production",
                    "total_assets": 1,
                    "ingress_diagram": {
                        "mermaid": self._MERMAID_AKS,
                        "css_code": "",
                        "icon_map": {},
                        "node_drilldown_map": node_map,
                        "default_view": "overview",
                    },
                    "diagrams": [{
                        "rg": "rg-aks",
                        "mermaid": self._MERMAID_AKS,
                        "css_code": "",
                        "icon_map": {},
                        "node_drilldown_map": node_map,
                        "asset_count": 1,
                        "public_count": 0,
                        "relationship_count": 0,
                        "default_view": "overview",
                    }],
                }),
            ),
        )

        def _handle_aks_drilldown(route):
            route.fulfill(
                content_type="application/json",
                body=json.dumps({
                    "title": "AKS Cluster — Services with Ingress",
                    "ingress_services": [
                        {
                            "namespace": "orders",
                            "name": "orders-api",
                            "ingress_name": "orders-ingress",
                            "host": "orders.example.com",
                            "path": "/api/orders",
                            "port": "8080",
                        },
                        {
                            "namespace": "payments",
                            "name": "payments-api",
                            "ingress_name": "payments-ingress",
                            "host": "payments.example.com",
                            "path": "/api/payments",
                            "port": "8443",
                        },
                    ],
                }),
            )

        page.route(f"**/api/subscriptions/{self._SUB_ID}/drilldown**", _handle_aks_drilldown)

    def test_aks_ingress_modal_groups_by_namespace_and_searches(self, page: Page, live_server: str):
        self._setup_mocks(page)
        page.goto(live_server + "/cloud")
        page.wait_for_load_state("networkidle")
        page.wait_for_selector(".subscription-preview-btn", timeout=15000)
        page.locator(".subscription-preview-btn").first.click()
        page.wait_for_selector("#ingress-diagram-div svg g.node-drillable", timeout=8000)

        page.locator("#ingress-diagram-div svg g.node-drillable").dblclick()

        expect(page.locator("#drilldown-modal")).to_be_visible(timeout=8000)
        expect(page.locator("#drilldown-modal [data-aks-ingress-search]")).to_be_visible(timeout=5000)
        expect(page.locator("#drilldown-modal details[data-aks-ingress-group]")).to_have_count(2)
        expect(page.locator("#drilldown-modal details[data-aks-ingress-group]").first).to_contain_text("orders")
        expect(page.locator("#drilldown-modal details[data-aks-ingress-group]").last).to_contain_text("payments")
        expect(page.locator("#drilldown-modal tr[data-aks-ingress-row]").first).to_contain_text("orders-api")

        search = page.locator("#drilldown-modal [data-aks-ingress-search]")
        search.fill("payments")

        expect(page.locator("#drilldown-modal tr[data-aks-ingress-row]").nth(0)).to_be_hidden(timeout=5000)
        expect(page.locator("#drilldown-modal tr[data-aks-ingress-row]").nth(1)).to_be_visible(timeout=5000)
        expect(page.locator("#drilldown-modal details[data-aks-ingress-group]").first).to_be_hidden(timeout=5000)
        expect(page.locator("#drilldown-modal details[data-aks-ingress-group]").last).to_be_visible(timeout=5000)

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
        page.wait_for_selector(".subscription-preview-btn", timeout=8000)
        page.locator(".subscription-preview-btn").first.click()
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
                    "title": "App Service Environment — Hosted Plans",
                    "view_type": "tree_table",
                    "columns": ["Plan / App", "Resource Group", "SKU / FQDN", "Exposure", "Kind", "Parent"],
                    "sections": [{
                        "title": "test-ase",
                        "subtitle": "rg-app",
                        "rows": [
                            {
                                "id": "plan-one",
                                "parent_id": None,
                                "child_count": 2,
                                "cells": [
                                    {"label": "plan-one", "style": "font-weight:600;"},
                                    "rg-app",
                                    "P1v3",
                                    "Private",
                                    "App Service Plan",
                                    "test-ase",
                                ],
                            },
                            {
                                "id": "ase-app",
                                "parent_id": "plan-one",
                                "child_count": 0,
                                "cells": [
                                    "ase-app",
                                    "rg-app",
                                    "ase-app.azurewebsites.net",
                                    "🔒 Private",
                                    "App Service",
                                    "plan-one",
                                ],
                            },
                            {
                                "id": "ase-fn",
                                "parent_id": "plan-one",
                                "child_count": 0,
                                "cells": [
                                    "ase-fn",
                                    "rg-app",
                                    "ase-fn.azurewebsites.net",
                                    "🔒 Private",
                                    "Function App",
                                    "plan-one",
                                ],
                            },
                        ],
                    }],
                }),
            ),
        )

    def test_dblclick_opens_ase_drilldown(self, page: Page, live_server: str):
        self._setup_mocks(page)
        page.goto(live_server + "/cloud")
        page.wait_for_load_state("networkidle")
        page.wait_for_selector(".subscription-preview-btn", timeout=15000)
        page.locator(".subscription-preview-btn").first.click()
        page.wait_for_selector("#ingress-diagram-div svg g.node-drillable", timeout=8000)

        page.locator("#ingress-diagram-div svg g.node-drillable").dblclick()

        expect(page.locator("#drilldown-modal")).to_be_visible(timeout=8000)
        expect(page.locator("#drilldown-modal table")).to_be_visible(timeout=5000)
        expect(page.locator("#drilldown-modal table")).to_contain_text("plan-one")
        expect(page.locator("#drilldown-modal table")).to_contain_text("ase-app")
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
                "public_ips": ["20.30.40.50"],
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
        page.wait_for_selector(".subscription-preview-btn", timeout=8000)
        page.locator(".subscription-preview-btn").first.click()
        page.wait_for_selector("#ingress-diagram-div svg g.node-drillable", timeout=8000)

        page.locator("#ingress-diagram-div svg g.node-drillable").click()

        expect(page.locator("#drilldown-modal")).to_be_visible(timeout=8000)
        expect(page.locator("#drilldown-modal")).to_contain_text("Public IP")
        expect(page.locator("#drilldown-modal")).to_contain_text("20.30.40.50")
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

    def _call(self, rows=None, plan_links=None, firewall_policy_rows=None, appgw_routes=None, aks_route_rows=None, appgw_waf_policy_rows=None, apim_backend_rows=None, apim_route_map=None, apim_api_rows=None):
        import sys
        import os
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        os.environ.setdefault("FLASK_APP", "web/app.py")
        from web.app import _build_ingress_diagram
        return _build_ingress_diagram(
            rows if rows is not None else self._make_rows(),
            plan_links=plan_links,
            firewall_policy_rows=firewall_policy_rows,
            appgw_routes=appgw_routes,
            aks_route_rows=aks_route_rows,
            appgw_waf_policy_rows=appgw_waf_policy_rows,
            apim_backend_rows=apim_backend_rows,
            apim_route_map=apim_route_map,
            apim_api_rows=apim_api_rows,
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

    def test_listener_arrows_are_unlabeled(self):
        """Internet→listener arrows should not duplicate protocol/host labels already in nodes."""
        result = self._call()
        mermaid = result.get("mermaid", "")
        import re
        labeled_listener_edges = re.findall(r'Internet\s*-->\|"[^"]+"\|\s*l_', mermaid)
        assert not labeled_listener_edges, (
            "Internet→listener edges should be unlabeled to avoid duplicating node text.\n"
            f"Found labeled edges: {labeled_listener_edges!r}\n{mermaid}"
        )

    def test_https_listeners_route_to_named_waf_policies(self):
        """Each HTTPS listener should route to its own named WAF policy when rule data includes policy names."""
        rows = [
            (
                "test-appgw",
                "Microsoft.Network/applicationGateways",
                "test-rg",
                "gw.example.com",
                1,
                "WAF_v2",
                "fake-id",
                1,
                "HTTPS:443",
            )
        ]
        appgw_routes = [
            (
                "test-appgw",
                "mtls.api.mydomain.co.uk",
                '["apim-one.azure-api.net"]',
                "pool-a",
                "listener-a",
                "/api/a/*",
                "HTTPS",
                "waf-policy-a",
            ),
            (
                "test-appgw",
                "payments.api.mydomain.co.uk",
                '["apim-one.azure-api.net"]',
                "pool-b",
                "listener-b",
                "/api/b/*",
                "HTTPS",
                "waf-policy-b",
            ),
        ]

        result = self._call(rows=rows, appgw_routes=appgw_routes)
        mermaid = result.get("mermaid", "")
        assert "WAF: waf-policy-a" in mermaid, mermaid
        assert "WAF: waf-policy-b" in mermaid, mermaid
        assert (
            'l_test_rg_test_appgw_HTTPS_mtls_api_mydomain_co_uk --> waf_test_rg_test_appgw_waf_policy_a'
            in mermaid
        ), mermaid
        assert (
            'l_test_rg_test_appgw_HTTPS_payments_api_mydomain_co_uk --> waf_test_rg_test_appgw_waf_policy_b'
            in mermaid
        ), mermaid

    def test_firewall_policy_node_is_rendered(self):
        """Azure Firewall should surface its policy as a property, not a separate node."""
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
        assert "fwpol_rg_net_fw_one" not in mermaid, mermaid

    def test_sql_server_nodes_use_real_names_in_group_labels(self):
        """SQL data-store grouping should keep the resource names visible."""
        import json

        rows = [
            (
                "payments-sql",
                "Microsoft.Sql/servers",
                "rg-data",
                "payments-sql.database.windows.net",
                0,
                "Standard",
                "sql-payments-id",
                0,
                None,
                1,
                None,
                None,
                json.dumps({
                    "properties": {
                        "publicNetworkAccess": "Enabled",
                        "networkAcls": {"defaultAction": "Deny"},
                    }
                }),
                json.dumps(["azure_ad"]),
                None,
            ),
            (
                "orders-sql",
                "Microsoft.Sql/servers",
                "rg-data",
                "orders-sql.database.windows.net",
                0,
                "Standard",
                "sql-orders-id",
                0,
                None,
                1,
                None,
                None,
                json.dumps({
                    "properties": {
                        "publicNetworkAccess": "Enabled",
                        "networkAcls": {"defaultAction": "Deny"},
                    }
                }),
                json.dumps(["azure_ad"]),
                None,
            ),
        ]

        result = self._call(rows=rows)
        mermaid = result.get("mermaid", "")
        assert "payments-sql" in mermaid, mermaid
        assert "orders-sql" in mermaid, mermaid
        assert "SQL (2×)" not in mermaid, mermaid

    def test_private_appgw_keeps_listener_chain_to_waf_and_gateway(self):
        """Listener nodes should still route to WAF/AppGW when Internet edges are absent."""
        rows = [
            (
                "appgw-private",
                "Microsoft.Network/applicationGateways",
                "rgnet",
                "appgw-private.internal",
                0,
                "WAF_v2",
                "appgw-private-id",
                1,
                "HTTPS:443, HTTP:80",
                0,
                "Prevention",
                None,
            ),
        ]
        result = self._call(rows=rows)
        mermaid = result.get("mermaid", "")
        assert 'l_rgnet_appgw_private_HTTPS_443 --> waf_rgnet_appgw_private' in mermaid, mermaid
        assert 'l_rgnet_appgw_private_HTTP_80 --> waf_rgnet_appgw_private' in mermaid, mermaid
        assert 'waf_rgnet_appgw_private --> rgnet_appgw_private' in mermaid, mermaid

    def test_gateway_level_waf_policy_sits_between_listener_and_gateway(self):
        """Gateway-level WAF policies should sit between listeners and the App Gateway."""
        rows = [
            (
                "appgw-cop",
                "Microsoft.Network/applicationGateways",
                "rgnet",
                "appgw-cop.example.com",
                1,
                "WAF_v2",
                "appgw-cop-id",
                1,
                "HTTPS:443",
                0,
                "PolicyAttached",
                None,
            ),
        ]
        appgw_waf_policy_rows = [
            ("cop-waf-policy", '["appgw-cop"]', "Enabled"),
        ]

        result = self._call(rows=rows, appgw_waf_policy_rows=appgw_waf_policy_rows)
        mermaid = result.get("mermaid", "")
        assert "WAF: cop-waf-policy" in mermaid, mermaid
        assert "waf_rgnet_appgw_cop_cop_waf_policy --> rgnet_appgw_cop" in mermaid, mermaid

    def test_backend_does_not_get_generic_data_edges(self):
        """Backends should no longer fan out to every data store by category."""
        import json

        rows = [
            (
                "worker-one",
                "Microsoft.Web/sites",
                "rg-app",
                "",
                0,
                "Standard",
                "worker-id",
                0,
                None,
                0,
                None,
                None,
                json.dumps({}),
                None,
                None,
            ),
            (
                "storage-one",
                "Microsoft.Storage/storageAccounts",
                "rg-data",
                "storage-one.blob.core.windows.net",
                0,
                "Standard",
                "storage-id",
                0,
                None,
                0,
                None,
                None,
                json.dumps({}),
                None,
                None,
            ),
        ]

        result = self._call(rows=rows)
        mermaid = result.get("mermaid", "")
        assert 'rg_app_worker_one -->|"HTTPS"| rg_data_storage_one' not in mermaid, mermaid

    def test_backend_only_links_explicit_data_reference(self):
        """Explicit JSON references should still create a backend→data-store edge."""
        import json

        rows = [
            (
                "worker-explicit",
                "Microsoft.Web/sites",
                "rg-app",
                "",
                0,
                "Standard",
                "worker-explicit-id",
                0,
                None,
                0,
                None,
                None,
                json.dumps({
                    "properties": {
                        "connectionString": "DefaultEndpointsProtocol=https;AccountName=explicitstore;EndpointSuffix=core.windows.net"
                    }
                }),
                None,
                None,
            ),
            (
                "explicitstore",
                "Microsoft.Storage/storageAccounts",
                "rg-data",
                "explicitstore.blob.core.windows.net",
                0,
                "Standard",
                "storage-explicit-id",
                0,
                None,
                0,
                None,
                None,
                json.dumps({}),
                None,
                None,
            ),
        ]

        result = self._call(rows=rows)
        mermaid = result.get("mermaid", "")
        assert 'rg_app_worker_explicit -->|"HTTPS"| rg_data_explicitstore' in mermaid, mermaid

    def test_listener_level_waf_overrides_disabled_gateway_waf(self):
        """Disabled gateway WAF policies should stay hidden when listeners carry their own WAFs."""
        rows = [
            (
                "appgw-shared",
                "Microsoft.Network/applicationGateways",
                "rgnet",
                "appgw-shared.example.com",
                1,
                "WAF_v2",
                "appgw-shared-id",
                1,
                "HTTPS:443, HTTP:80",
                0,
                "PolicyAttached",
                None,
            ),
        ]
        appgw_routes = [
            (
                "appgw-shared",
                "listener-one.example.com",
                '["backend.example.com"]',
                "pool-a",
                "listener-one",
                "/api/one/*",
                "HTTPS",
                "listener-policy-a",
            ),
            (
                "appgw-shared",
                "listener-two.example.com",
                '["backend.example.com"]',
                "pool-b",
                "listener-two",
                "/api/two/*",
                "HTTPS",
                "listener-policy-b",
            ),
        ]
        appgw_waf_policy_rows = [
            ("shared-gateway-policy", '["appgw-shared"]', "Disabled"),
        ]

        result = self._call(rows=rows, appgw_routes=appgw_routes, appgw_waf_policy_rows=appgw_waf_policy_rows)
        mermaid = result.get("mermaid", "")
        assert "shared-gateway-policy" not in mermaid, mermaid
        assert "l_rgnet_appgw_shared_HTTPS_listener_one_example_com --> waf_rgnet_appgw_shared_listener_policy_a" in mermaid, mermaid
        assert "l_rgnet_appgw_shared_HTTPS_listener_two_example_com --> waf_rgnet_appgw_shared_listener_policy_b" in mermaid, mermaid
        assert "waf_rgnet_appgw_shared_listener_policy_a --> rgnet_appgw_shared" in mermaid, mermaid
        assert "waf_rgnet_appgw_shared_listener_policy_b --> rgnet_appgw_shared" in mermaid, mermaid

    def test_apim_direct_exposure_uses_api_product_label(self):
        """APIM public ingress should be labelled as an API product."""
        rows = [
            (
                "apim-prod",
                "Microsoft.ApiManagement/service",
                "rg-api",
                "apim.example.com",
                1,
                "Developer",
                "apim-id",
                0,
                None,
                0,
                None,
                None,
                "{}",
                None,
                None,
            ),
        ]

        result = self._call(rows=rows)
        mermaid = result.get("mermaid", "")
        assert 'API "Product"' in mermaid, mermaid

    def test_aks_ingress_routes_render_as_entry_points(self):
        """AKS ingress routes should appear as public entry points with a hop to the cluster."""
        import json

        rows = [
            (
                "aks-prod",
                "Microsoft.ContainerService/managedClusters",
                "rg-aks",
                "",
                0,
                "Standard",
                "aks-id",
                0,
                None,
                0,
                None,
                None,
                json.dumps({}),
                None,
                None,
            ),
        ]
        aks_route_rows = [
            (
                "aks-prod",
                "default",
                "orders-ingress",
                "aks.example.com",
                "/*",
                "Public",
                "orders-api",
                80,
                "orders-api",
                "https://github.com/org/repo",
                "rg-aks",
                json.dumps({"app.kubernetes.io/component": "api"}),
            )
        ]

        result = self._call(rows=rows, aks_route_rows=aks_route_rows)
        mermaid = result.get("mermaid", "")
        assert any('["orders-api' in line for line in mermaid.splitlines()), mermaid
        assert "/static/assets/icons/kubernetes/ingress.svg" in mermaid, mermaid
        assert "rg_aks_aks_prod" in mermaid, mermaid
        assert any("orders_api" in line and "rg_aks_aks_prod" in line for line in mermaid.splitlines()), mermaid

    def test_aks_service_is_hidden_when_ingress_hop_is_missing(self):
        """AKS services without a concrete ingress route should stay implicit."""
        import json

        rows = [
            (
                "aks-prod",
                "Microsoft.ContainerService/managedClusters",
                "rg-aks",
                "",
                0,
                "Standard",
                "aks-id",
                0,
                None,
                0,
                None,
                None,
                json.dumps({}),
                None,
                None,
            ),
        ]
        aks_route_rows = [
            (
                "aks-prod",
                "default",
                "",
                "orders.example.internal",
                "/*",
                "Public",
                "orders-api-8080",
                8080,
                "orders-api",
                "https://github.com/org/repo",
                "rg-aks",
                json.dumps({"app.kubernetes.io/component": "api"}),
            )
        ]

        result = self._call(rows=rows, aks_route_rows=aks_route_rows)
        mermaid = result.get("mermaid", "")
        assert "rg_aks_aks_prod" in mermaid, mermaid
        assert "orders-api-8080 🔒" not in mermaid, mermaid
        assert "belongs to" not in mermaid, mermaid

    def test_internal_aks_ingress_hostname_is_service_metadata(self):
        """Internal AKS ingress DNS should be hidden and retained on the service drilldown."""
        import json

        rows = [
            (
                "portalui",
                "Microsoft.Web/sites",
                "rg-aks",
                "portalui.azurewebsites.net",
                0,
                "Standard",
                "portalui-id",
                0,
                None,
                0,
                None,
                json.dumps([
                    {"target": "production-authentication-totp.internal.cbinnovation.uk", "name": "production-authentication-totp.internal.cbinnovation.uk"}
                ]),
                json.dumps({}),
                None,
                None,
            ),
            (
                "production-shared-aks-uksouth",
                "Microsoft.ContainerService/managedClusters",
                "rg-aks",
                "",
                0,
                "Standard",
                "aks-id",
                0,
                None,
                0,
                None,
                None,
                json.dumps({}),
                None,
                None,
            ),
        ]
        aks_route_rows = [
            (
                "production-shared-aks-uksouth",
                "default",
                "authentication-totp-ingress",
                "production-authentication-totp.internal.cbinnovation.uk",
                "/*",
                "Internal",
                "authentication-totp",
                80,
                "authentication-totp",
                "git@example.com/totp",
                "rg-aks",
                json.dumps({"app.kubernetes.io/component": "api"}),
            )
        ]

        result = self._call(rows=rows, aks_route_rows=aks_route_rows)
        mermaid = result.get("mermaid", "")
        service_node = next(
            value
            for value in result.get("node_drilldown_map", {}).values()
            if "kubernetes" in str(value.get("arm_type") or "").lower()
            and "service" in str(value.get("arm_type") or "").lower()
        )
        assert "Internet -->" not in mermaid, mermaid
        assert "production-authentication-totp.internal.cbinnovation.uk" not in mermaid, mermaid
        assert "authentication-totp 🔒" not in mermaid, mermaid
        assert '["authentication-totp"]' in mermaid, mermaid
        assert service_node["ingress_host"] == "production-authentication-totp.internal.cbinnovation.uk", service_node
        assert service_node["ingress_path"] == "/*", service_node

    def test_appgw_routes_to_internal_aks_service_without_public_ingress_node(self):
        """A private AKS storefront should be reached through App Gateway, not directly from the Internet."""
        import json

        rows = [
            (
                "agw-marketlane-edge",
                "Microsoft.Network/applicationGateways",
                "rg-marketlane-edge",
                "shop.marketlane-retail.com",
                1,
                "WAF_v2",
                "appgw-id",
                1,
                "HTTPS:443",
                0,
                "Prevention",
                None,
                json.dumps({}),
                None,
                None,
            ),
            (
                "aks-marketlane-platform",
                "Microsoft.ContainerService/managedClusters",
                "rg-marketlane-app",
                "",
                0,
                "Standard",
                "aks-id",
                0,
                None,
                0,
                None,
                None,
                json.dumps({}),
                None,
                None,
            ),
        ]
        appgw_routes = [
            (
                "agw-marketlane-edge",
                "shop.marketlane-retail.com",
                '["store.marketlane-retail.internal"]',
                "bhs-marketlane-web",
                "listener-marketlane-web",
                "/*",
                "HTTPS",
                "waf-marketlane-edge",
            ),
        ]
        aks_route_rows = [
            (
                "aks-marketlane-platform",
                "storefront",
                "storefront-ingress",
                "store.marketlane-retail.internal",
                "/*",
                "Internal",
                "store-web",
                "80",
                "store-web",
                "https://github.com/marketlane/storefront.git",
                "rg-marketlane-app",
                json.dumps({"app.kubernetes.io/component": "frontend"}),
            ),
        ]

        result = self._call(
            rows=rows,
            appgw_routes=appgw_routes,
            aks_route_rows=aks_route_rows,
        )
        mermaid = result.get("mermaid", "")
        service_nid = "rg_marketlane_app_aks_service_aks_marketlane_platform_storefront_store_web_80"
        cluster_nid = "rg_marketlane_app_aks_marketlane_platform"
        pool_nid = "agpool_rg_marketlane_edge_agw_marketlane_edge_bhs_marketlane_web"
        service_node = result.get("node_drilldown_map", {}).get(service_nid, {})

        assert f'{pool_nid} --> {service_nid}' in mermaid, mermaid
        assert f"{service_nid} --> {cluster_nid}" in mermaid, mermaid
        assert "/static/assets/icons/kubernetes/ingress.svg" not in mermaid, mermaid
        assert f"Internet --> {service_nid}" not in mermaid, mermaid
        assert service_node.get("ingress_host") == "store.marketlane-retail.internal", service_node

    def test_apim_public_ip_is_rendered_on_apim_details(self):
        """APIM-linked Public IPs should appear on the APIM drilldown, not as a separate node."""
        import json

        public_ip_id = "/subscriptions/sub/resourceGroups/rg-api/providers/Microsoft.Network/publicIPAddresses/apim-public"
        rows = [
            (
                "apim-prod",
                "Microsoft.ApiManagement/service",
                "rg-api",
                "apim.example.com",
                1,
                "Developer",
                "apim-id",
                0,
                None,
                0,
                None,
                None,
                json.dumps({
                    "properties": {
                        "ipConfigurations": [
                            {
                                "properties": {
                                    "publicIPAddress": {"id": public_ip_id},
                                }
                            }
                        ]
                    }
                }),
                None,
                None,
            ),
            (
                "apim-public",
                "Microsoft.Network/publicIPAddresses",
                "rg-api",
                None,
                1,
                "Standard",
                public_ip_id,
                0,
                None,
                0,
                None,
                None,
                json.dumps({"properties": {"ipAddress": "20.30.40.50"}}),
                None,
                None,
            ),
        ]

        result = self._call(rows=rows)
        mermaid = result.get("mermaid", "")
        assert "grp_APIM_Public --> rg_api_apim_public" not in mermaid, mermaid
        assert 'rg_api_apim_public["' not in mermaid, mermaid

    def test_apim_backend_target_stays_attached_to_apim(self):
        """APIM backend targets should render as APIM children, not floating in Azure no-VNet."""
        import json

        rows = [
            (
                "apim-prod",
                "Microsoft.ApiManagement/service",
                "rg-api",
                "apim.example.com",
                1,
                "Developer",
                "apim-id",
                0,
                None,
                0,
                None,
                None,
                json.dumps({"properties": {}}),
                None,
                None,
            ),
            (
                "backend1",
                "APIM Backend Target",
                "rg-api",
                None,
                0,
                None,
                "apim-prod::backend1",
                0,
                None,
                0,
                None,
                None,
                json.dumps({
                    "apim_name": "apim-prod",
                    "backend_id": "backend1",
                    "backend_url": "https://backend.example.com",
                    "_extra": {"display_label": "backend1"},
                }),
                None,
                None,
            ),
        ]

        result = self._call(rows=rows)
        mermaid = result.get("mermaid", "")
        assert "rg_api_backend1" in mermaid, mermaid
        assert "class rg_api_backend1 apimBackendPool;" in mermaid, mermaid
        assert "grp_APIM_Public" in mermaid, mermaid

    def test_internal_apim_does_not_render_internet_ingress(self):
        """Internal APIM should only be reached through App Gateway, even with outbound public IPs."""
        import json
        rows = [
            (
                "appgw-prod",
                "Microsoft.Network/applicationGateways",
                "rg-edge",
                "api.example.com",
                1,
                "WAF_v2",
                "appgw-id",
                1,
                "HTTPS:443",
                0,
                "Prevention",
                None,
                json.dumps({}),
                None,
                None,
            ),
            (
                "apim-prod",
                "Microsoft.ApiManagement/service",
                "rg-api",
                "apim-prod.azure-api.net",
                0,
                "Developer",
                "apim-id",
                0,
                None,
                0,
                None,
                None,
                json.dumps({
                    "properties": {
                        "virtualNetworkType": "Internal",
                        "publicNetworkAccess": "Enabled",
                        "publicIPAddresses": ["20.90.204.14"],
                    }
                }),
                None,
                None,
            ),
        ]
        appgw_routes = [
            (
                "appgw-prod",
                "api.example.com",
                '["apim-prod.azure-api.net"]',
                "api-pool",
                "api-listener",
                "/*",
                "HTTPS",
                "waf-prod",
            ),
        ]

        result = self._call(rows=rows, appgw_routes=appgw_routes)
        mermaid = result.get("mermaid", "")
        assert not any(
            line.strip().startswith("Internet") and "grp_APIM" in line
            for line in mermaid.splitlines()
        ), mermaid
        assert "agpool_rg_edge_appgw_prod_api_pool --> grp_APIM_Private" in mermaid, mermaid

    def test_apim_with_vnet_and_subnet_is_nested_in_network_boundary(self):
        """APIM nodes with subnet metadata should render inside the network subgraph."""
        import json

        rows = [
            (
                "example-api-uksouth",
                "Microsoft.ApiManagement/service",
                "rg-api",
                "example-api-uksouth.azure-api.net",
                1,
                "Developer",
                "apim-id",
                0,
                None,
                0,
                None,
                None,
                json.dumps({
                    "_extra": {
                        "vnet_name": "prod-vnet",
                        "vnet_resource_group": "rg-net",
                        "subnet_name": "api-subnet",
                        "subnet_id": "/subscriptions/sub/resourceGroups/rg-net/providers/Microsoft.Network/virtualNetworks/prod-vnet/subnets/api-subnet",
                    }
                }),
                None,
                None,
            ),
        ]

        result = self._call(rows=rows)
        mermaid = result.get("mermaid", "")
        assert "Network: prod-vnet" in mermaid, mermaid
        assert "Subnet: api-subnet" in mermaid, mermaid
        assert "example-api-uksouth.azure-api.net" in mermaid, mermaid
        assert mermaid.index("Network: prod-vnet") < mermaid.index("Subnet: api-subnet") < mermaid.index("grp_APIM_Public"), mermaid

    def test_apim_apis_inherit_parent_subnet(self):
        """APIM-hosted APIs should render inside the parent APIM subnet."""
        import json

        rows = [
            (
                "apim-marketlane-edge",
                "Microsoft.ApiManagement/service",
                "rg-marketlane-edge",
                "apim-marketlane.azure-api.net",
                1,
                "Developer",
                "apim-id",
                0,
                None,
                0,
                None,
                None,
                json.dumps({
                    "_extra": {
                        "vnet_name": "vnet-marketlane-edge",
                        "vnet_resource_group": "rg-marketlane-network",
                        "subnet_name": "snet-apim",
                        "subnet_id": "/subscriptions/sub/resourceGroups/rg-marketlane-network/providers/Microsoft.Network/virtualNetworks/vnet-marketlane-edge/subnets/snet-apim",
                    }
                }),
                None,
                None,
            ),
        ]
        apim_api_rows = [
            {
                "apim_name": "apim-marketlane-edge",
                "apim_resource_id": "apim-id",
                "api_name": "catalog-marketlane",
                "api_display_name": "Catalog API",
                "api_path": "catalog",
            },
            {
                "apim_name": "apim-marketlane-edge",
                "apim_resource_id": "apim-id",
                "api_name": "orders-marketlane",
                "api_display_name": "Orders API",
                "api_path": "orders",
            },
        ]

        result = self._call(rows=rows, apim_api_rows=apim_api_rows)
        mermaid = result.get("mermaid", "")
        lines = mermaid.splitlines()
        catalog_line = next(line for line in lines if "Catalog API</div>" in line)
        orders_line = next(line for line in lines if "Orders API</div>" in line)

        assert "Network: vnet-marketlane-edge" in mermaid, mermaid
        assert "Subnet: snet-apim" in mermaid, mermaid
        assert catalog_line.startswith("            "), mermaid
        assert orders_line.startswith("            "), mermaid
        assert "Azure (no VNet integration)" not in mermaid, mermaid
        assert mermaid.count(" --> grp_APIM_Public") == 2, mermaid
        assert "hosted in" not in mermaid, mermaid

    def test_apim_api_links_as_hosted_instead_of_routing(self):
        """APIM API nodes should not render a misleading Routing edge to the gateway host."""
        import json

        rows = [
            (
                "apim-marketlane-edge",
                "Microsoft.ApiManagement/service",
                "rg-marketlane-edge",
                "apim-marketlane.azure-api.net",
                1,
                "Developer",
                "apim-id",
                0,
                None,
                0,
                None,
                None,
                json.dumps({}),
                None,
                None,
            ),
            (
                "catalog-marketlane",
                "APIM API",
                "rg-marketlane-edge",
                None,
                0,
                None,
                "catalog-marketlane",
                0,
                None,
                0,
                None,
                None,
                json.dumps({
                    "apim_name": "apim-marketlane-edge",
                    "_extra": {
                        "display_label": "Catalog API",
                        "api_display_name": "Catalog API",
                        "api_path": "catalog",
                    },
                }),
                None,
                None,
            ),
        ]

        result = self._call(rows=rows)
        mermaid = result.get("mermaid", "")
        node_map = result.get("node_drilldown_map", {})
        api_node = next(
            value
            for value in node_map.values()
            if value.get("arm_type") == "APIM API"
        )
        apim_node = next(
            value
            for value in node_map.values()
            if value.get("arm_type") == "Microsoft.ApiManagement/service"
        )
        assert "Catalog API" in mermaid, mermaid
        assert " --> grp_APIM_Public" in mermaid, mermaid
        assert "hosted in" not in mermaid, mermaid
        assert 'Catalog API -->|"Routing"| grp_APIM_Public' not in mermaid, mermaid
        assert apim_node.get("title") == "APIM", apim_node
        assert api_node.get("icon_path", "").endswith("api-center.svg"), api_node
        assert api_node.get("icon_class") == "icon-azurerm-api-center", api_node

    def test_network_attached_services_render_inside_vnet_and_subnet_groups(self):
        """Network-aware services should be nested under VNet and subnet subgraphs."""
        import json

        rows = [
            (
                "orders-site",
                "Microsoft.Web/sites",
                "rg-app",
                "orders.example.com",
                1,
                "B1",
                "site-id",
                0,
                None,
                0,
                None,
                None,
                json.dumps({
                    "_extra": {
                        "vnet_name": "prod-vnet",
                        "vnet_resource_group": "rg-net",
                        "subnet_name": "app-subnet",
                        "subnet_id": "/subscriptions/sub/resourceGroups/rg-net/providers/Microsoft.Network/virtualNetworks/prod-vnet/subnets/app-subnet",
                    }
                }),
                None,
                None,
            ),
        ]

        result = self._call(rows=rows)
        mermaid = result.get("mermaid", "")
        assert "Network: prod-vnet" in mermaid, mermaid
        assert "Subnet: app-subnet" in mermaid, mermaid
        assert "subgraph net_" in mermaid, mermaid
        assert "subgraph sub_" in mermaid, mermaid

    def test_aks_cluster_is_grouped_in_network_boundary(self):
        """AKS clusters with subnet data should render in the network boundary."""
        import json

        rows = [
            (
                "aks-prod",
                "Microsoft.ContainerService/managedClusters",
                "rg-aks",
                "",
                0,
                "Standard",
                "aks-id",
                0,
                None,
                0,
                None,
                None,
                json.dumps({
                    "_extra": {
                        "vnet_name": "aks-vnet",
                        "vnet_resource_group": "rg-net",
                        "subnet_name": "aks-subnet",
                        "subnet_id": "/subscriptions/sub/resourceGroups/rg-net/providers/Microsoft.Network/virtualNetworks/aks-vnet/subnets/aks-subnet",
                    }
                }),
                None,
                None,
            ),
        ]

        result = self._call(rows=rows)
        mermaid = result.get("mermaid", "")
        assert "Network: aks-vnet" in mermaid, mermaid
        assert "Subnet: aks-subnet" in mermaid, mermaid
        assert mermaid.index("Network: aks-vnet") < mermaid.index("Subnet: aks-subnet") < mermaid.index("aks-prod"), mermaid

    def test_kubernetes_service_links_to_owning_aks_cluster(self):
        """Kubernetes Services should use an unlabeled edge to their owning AKS cluster."""
        import json

        rows = [
            (
                "aks-prod",
                "Microsoft.ContainerService/managedClusters",
                "rg-aks",
                "",
                0,
                "Standard",
                "aks-id",
                0,
                None,
                0,
                None,
                None,
                json.dumps({
                    "_extra": {
                        "vnet_name": "aks-vnet",
                        "vnet_resource_group": "rg-net",
                        "subnet_name": "aks-subnet",
                        "subnet_id": "/subscriptions/sub/resourceGroups/rg-net/providers/Microsoft.Network/virtualNetworks/aks-vnet/subnets/aks-subnet",
                    }
                }),
                None,
                None,
            ),
        ]
        aks_route_rows = [
            (
                "aks-prod",
                "orders",
                "orders-ingress",
                "orders.internal",
                "/*",
                "Internal",
                "orders-api",
                "8080",
                "orders-api",
                None,
                "rg-aks",
                {},
            ),
        ]

        result = self._call(rows=rows, aks_route_rows=aks_route_rows)
        mermaid = result.get("mermaid", "")
        lines = mermaid.splitlines()
        service_line = next(line for line in lines if "rg_aks_aks_service_aks_prod_orders_orders_api_8080[" in line)
        cluster_line = next(line for line in lines if "aks-prod</div>" in line)
        service_nid = service_line.strip().split("[", 1)[0]
        cluster_nid = cluster_line.strip().split("[", 1)[0]

        assert service_line.startswith("            "), mermaid
        assert cluster_line.startswith("            "), mermaid
        assert sum('["orders-api"]' in line for line in lines) == 1, mermaid
        assert f"{service_nid} --> {cluster_nid}" in mermaid, mermaid
        assert f'{service_nid} -->|"belongs to"| {cluster_nid}' not in mermaid, mermaid
        assert f'{service_nid} -->|"targets"| {cluster_nid}' not in mermaid, mermaid

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
        """Ingress payload should expose connectivity views plus attack-path summaries."""
        result = self._call()
        views = result.get("views", {})
        assert "connectivity" in views, views
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
        assert "1 app" in result.get("mermaid", ""), result.get("mermaid", "")
        titles = {v.get("title") for v in result.get("node_drilldown_map", {}).values()}
        assert "test-plan" in titles, titles
        assert "orders-fn-app" not in titles, titles

    def test_site_hosting_plan_links_handle_nested_properties_plan_id(self):
        """Hosted sites should still link to plans when the plan id lives under properties."""
        import json
        import sqlite3

        from web.app import _build_site_hosting_plan_links

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            conn.execute(
                """
                CREATE TABLE provisioned_assets (
                    name TEXT,
                    resource_group TEXT,
                    raw_json TEXT,
                    type TEXT,
                    subscription_id TEXT
                )
                """
            )
            plan_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/serverfarms/production-institution-portal-uksouth"
            conn.execute(
                """
                INSERT INTO provisioned_assets (name, resource_group, raw_json, type, subscription_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "institution-portal",
                    "rg-app",
                    json.dumps({"properties": {"serverFarmId": plan_id}}),
                    "Microsoft.Web/sites",
                    "sub-1",
                ),
            )

            links = _build_site_hosting_plan_links(conn, "sub-1")
            assert links == [
                (
                    "rg-app",
                    "institution-portal",
                    "rg-app",
                    "production-institution-portal-uksouth",
                )
            ], links
        finally:
            conn.close()

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
        assert "1 app" in result.get("mermaid", ""), result.get("mermaid", "")
        titles = {v.get("title") for v in result.get("node_drilldown_map", {}).values()}
        assert "test-ase" in titles, titles
        assert "orders-fn-app" not in titles, titles

    def test_app_service_plan_drilldown_lists_hosted_apps(self):
        """The App Service Plan drilldown must list hosted app services."""
        import json
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
                    location TEXT,
                    sku TEXT,
                    is_public INTEGER,
                    is_restricted INTEGER,
                    raw_json TEXT,
                    type TEXT,
                    subscription_id TEXT,
                    id TEXT
                )
                """
            )
            ase_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/hostingEnvironments/test-ase"
            plan_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/serverfarms/test-plan"
            site_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/sites/orders-fn-app"
            conn.executemany(
                """
                INSERT INTO provisioned_assets
                    (name, resource_group, fqdn, location, sku, is_public, is_restricted, raw_json, type, subscription_id, id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        "test-ase",
                        "rg-app",
                        "test-ase.appserviceenvironment.net",
                        "ukwest",
                        "ASEv3",
                        0,
                        0,
                        json.dumps({
                            "properties": {
                                "virtualNetwork": {
                                    "id": "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/virtualNetworks/vnet-prod",
                                },
                                "subnet": {
                                    "id": "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/virtualNetworks/vnet-prod/subnets/ase-subnet",
                                },
                            }
                        }),
                        "Microsoft.Web/hostingEnvironments",
                        "sub-1",
                        ase_id,
                    ),
                    (
                        "test-plan",
                        "rg-app",
                        "",
                        "ukwest",
                        "P1v3",
                        0,
                        0,
                        json.dumps({"kind": "app", "hostingEnvironmentProfile": {"id": ase_id}}),
                        "Microsoft.Web/serverfarms",
                        "sub-1",
                        plan_id,
                    ),
                    (
                        "orders-fn-app",
                        "rg-app",
                        "orders.example.com",
                        "",
                        "",
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
        assert result["title"] == "App Service Plan — Hosted Apps", result
        assert result["rows"], result
        assert any(row[0] == "orders-fn-app" for row in result["rows"]), result["rows"]
        assert result["parent_resource"]["name"] == "test-plan", result
        assert result["parent_resource"]["type_label"] == "App Service Plan", result
        assert result["parent_resource"]["network"]["vnet"] == "vnet-prod", result
        assert result["parent_resource"]["network"]["subnet"] == "ase-subnet", result

    def test_app_service_environment_drilldown_uses_correct_parent_type(self):
        """ASE drilldown should nest apps under their App Service Plans."""
        import sqlite3

        from web.app import _build_child_table

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
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
            ase_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/hostingEnvironments/test-ase"
            plan_one_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/serverfarms/plan-one"
            plan_two_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/serverfarms/plan-two"
            site_one_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/sites/orders-app"
            site_two_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/sites/orders-fn-app"
            conn.executemany(
                """
                INSERT INTO provisioned_assets
                    (name, resource_group, fqdn, is_public, is_restricted, raw_json, type, subscription_id, id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        "test-ase",
                        "rg-app",
                        "test-ase.appserviceenvironment.net",
                        0,
                        0,
                        '{"kind": "app"}',
                        "Microsoft.Web/hostingEnvironments",
                        "sub-1",
                        ase_id,
                    ),
                    (
                        "plan-one",
                        "rg-app",
                        "",
                        0,
                        0,
                        f'{{"hostingEnvironmentProfile": {{"id": "{ase_id}"}}, "sku": {{"name": "P1v3"}}}}',
                        "Microsoft.Web/serverfarms",
                        "sub-1",
                        plan_one_id,
                    ),
                    (
                        "plan-two",
                        "rg-app",
                        "",
                        0,
                        0,
                        f'{{"hostingEnvironmentProfile": {{"id": "{ase_id}"}}, "sku": {{"name": "P0v3"}}}}',
                        "Microsoft.Web/serverfarms",
                        "sub-1",
                        plan_two_id,
                    ),
                    (
                        "orders-app",
                        "rg-app",
                        "orders.example.com",
                        1,
                        0,
                        f'{{"appServicePlanId": "{plan_one_id}", "kind": "app"}}',
                        "Microsoft.Web/sites",
                        "sub-1",
                        site_one_id,
                    ),
                    (
                        "orders-fn-app",
                        "rg-app",
                        "orders-fn.azurewebsites.net",
                        1,
                        0,
                        f'{{"serverFarmId": "{plan_one_id}", "kind": "functionapp,linux"}}',
                        "Microsoft.Web/sites",
                        "sub-1",
                        site_two_id,
                    ),
                ],
            )

            result = _build_child_table(
                conn,
                "sub-1",
                "Microsoft.Web/hostingEnvironments",
                [{"rg": "rg-app", "name": "test-ase"}],
            )
        finally:
            conn.close()

        assert result["view_type"] == "tree_table", result
        assert result["title"] == "App Service Environment — Hosted Plans", result
        rows = result["sections"][0]["rows"]
        plan_rows = [row for row in rows if row.get("parent_id") is None]
        assert [row["cells"][0]["label"] for row in plan_rows] == ["plan-one", "plan-two"], rows
        plan_one = next(row for row in rows if row.get("id") == plan_one_id)
        apps = [row for row in rows if row.get("parent_id") == plan_one_id]
        assert plan_one["child_count"] == 2, plan_one
        assert [row["cells"][0] for row in apps] == ["orders-app", "orders-fn-app"], apps

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

    def test_aks_service_drilldown_shows_ingress_namespace_and_port(self):
        """AKS service drilldown should surface ingress host, namespace, and port."""
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
                    "https://example/repo-a",
                    '{"app.kubernetes.io/component":"api"}',
                ),
            )
            result = _build_child_table(
                conn,
                "sub-1",
                "microsoft.kubernetes/services",
                [{"rg": "rg-app", "name": "orders-svc"}],
                node={
                    "arm_type": "microsoft.kubernetes/services",
                    "source_cluster_name": "aks-one",
                    "source_cluster_rg": "rg-app",
                    "source_namespace": "orders",
                    "source_service": "orders-svc",
                    "source_service_port": "443",
                    "source_deployment": "orders-api",
                    "label": "orders-svc",
                    "short_name": "orders-svc",
                    "name": "orders-svc",
                },
            )
        finally:
            conn.close()

        assert result["view_type"] == "table", result
        assert result["columns"] == [
            "Namespace",
            "Ingress / FQDN",
            "Port",
            "Path",
            "Service",
            "Deployment",
            "Cluster",
            "Resource Group",
        ], result["columns"]
        assert result["rows"] == [[
            "orders",
            "orders.example.com",
            "443",
            "/api/orders",
            "orders-svc",
            "orders-api",
            "aks-one",
            "rg-app",
        ]], result["rows"]

    def test_cloud_resource_details_resolves_synthetic_aks_service(self, monkeypatch):
        """Synthetic Kubernetes Service IDs should resolve to AKS route details."""
        import sqlite3

        import web.app as app_module

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            conn.execute(
                """
                CREATE TABLE subscriptions (
                    id TEXT,
                    display_name TEXT,
                    environment TEXT,
                    state TEXT
                )
                """
            )
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
                CREATE TABLE provisioned_assets (
                    id TEXT,
                    subscription_id TEXT,
                    name TEXT,
                    type TEXT,
                    resource_group TEXT,
                    location TEXT,
                    sku TEXT,
                    fqdn TEXT,
                    raw_json TEXT,
                    is_public INTEGER,
                    status TEXT,
                    waf_mode TEXT,
                    is_restricted INTEGER,
                    last_synced TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE appgw_routing_rules (
                    subscription_id TEXT,
                    gateway_name TEXT,
                    listener_name TEXT,
                    hostname TEXT,
                    protocol TEXT,
                    url_path TEXT,
                    backend_pool_name TEXT,
                    backend_fqdns TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE apim_backends (
                    subscription_id TEXT,
                    apim_name TEXT,
                    backend_id TEXT,
                    title TEXT,
                    url TEXT,
                    protocol TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE apim_api_routes (
                    subscription_id TEXT,
                    apim_name TEXT,
                    backend_id TEXT,
                    api_name TEXT,
                    api_display_name TEXT,
                    api_path TEXT
                )
                """
            )
            conn.execute(
                "INSERT INTO subscriptions (id, display_name, environment, state) VALUES (?, ?, ?, ?)",
                ("sub-1", "Test Subscription", "production", "Enabled"),
            )
            conn.execute(
                """
                INSERT INTO provisioned_assets (
                    id, subscription_id, name, type, resource_group, location, sku, fqdn, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "aks-id",
                    "sub-1",
                    "aks-one",
                    "Microsoft.ContainerService/managedClusters",
                    "rg-app",
                    "uksouth",
                    "Standard",
                    None,
                    "{}",
                ),
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
                    "https://example/repo-a",
                    '{"app.kubernetes.io/component":"api"}',
                ),
            )
            conn.execute(
                """
                INSERT INTO appgw_routing_rules (
                    subscription_id, gateway_name, listener_name, hostname, protocol,
                    url_path, backend_pool_name, backend_fqdns
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "sub-1",
                    "appgw-one",
                    "orders-listener",
                    "orders.example.com",
                    "HTTPS",
                    "/orders/*",
                    "orders-pool",
                    '["orders.example.com"]',
                ),
            )
            conn.execute(
                """
                INSERT INTO apim_backends (
                    subscription_id, apim_name, backend_id, title, url, protocol
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "sub-1",
                    "apim-one",
                    "orders-backend",
                    "orders-backend",
                    "https://orders.example.com",
                    "http",
                ),
            )
            conn.execute(
                """
                INSERT INTO apim_api_routes (
                    subscription_id, apim_name, backend_id, api_name, api_display_name, api_path
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "sub-1",
                    "apim-one",
                    "orders-backend",
                    "orders",
                    "Orders API",
                    "/orders",
                ),
            )

            monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
            client = app_module.app.test_client()
            resource_id = "production_aks_uksouth_production_aks_uksouth_production_account_products_orchestrator_api_service_80"
            resp = client.get(
                "/api/cloud/resource-details",
                query_string={
                    "id": resource_id,
                    "name": "production-shared-aks-uksouth-production-account-products-orchestrator-api-service-80",
                    "resource_group": "rg-app",
                    "type": "Kubernetes Service",
                    "sub": "sub-1",
                },
            )
        finally:
            try:
                conn.close()
            except Exception:
                pass

        assert resp.status_code == 200, resp.get_data(as_text=True)
        payload = resp.get_json()
        assert payload["type_label"] == "Kubernetes Service", payload
        assert payload["configuration"]["namespace"] == "orders", payload
        assert payload["configuration"]["ingress_fqdn"] == "orders.example.com", payload
        assert payload["configuration"]["port"] == "443", payload
        assert payload["parent_resource"]["location"] == "uksouth", payload
        assert payload["network"]["routing_targets"] == [{
            "target": "aks-one",
            "name": "aks-one",
            "target_resource_id": "aks-id",
            "resource_group": "rg-app",
            "type": "Microsoft.ContainerService/managedClusters",
        }], payload
        assert payload["invoked_by"] == [
            {
                "name": "orders-pool",
                "type": "App Gateway Backend Pool",
                "via": "appgw-one / orders-listener",
                "path": "/orders/*",
                "protocol": "HTTPS",
            },
            {
                "name": "orders-backend",
                "type": "APIM Backend Target",
                "via": "apim-one / Orders API",
                "path": "/orders",
                "protocol": "http",
            },
        ], payload

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
        assert any(row.get("parent_id") is None for row in rows), rows
        assert any(row.get("parent_id") is not None for row in rows), rows
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

    def test_web_app_drilldown_lists_deployment_slots(self):
        """App Service / Function App drill-down should list deployment slots."""
        import json
        import sqlite3

        try:
            from web.app import _build_child_table
        except ModuleNotFoundError:
            from app import _build_child_table

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            conn.executescript(
                """
                CREATE TABLE subscriptions (
                    id TEXT PRIMARY KEY,
                    display_name TEXT,
                    environment TEXT,
                    state TEXT
                );
                CREATE TABLE provisioned_assets (
                    id TEXT PRIMARY KEY,
                    subscription_id TEXT,
                    resource_group TEXT,
                    name TEXT,
                    type TEXT,
                    fqdn TEXT,
                    is_public INTEGER DEFAULT 0,
                    is_restricted INTEGER DEFAULT 0,
                    raw_json TEXT
                );
                """
            )
            conn.execute(
                "INSERT INTO subscriptions (id, display_name, environment, state) VALUES (?, ?, ?, ?)",
                ("sub-1", "Test Subscription", "production", "Enabled"),
            )
            conn.execute(
                """
                INSERT INTO provisioned_assets (
                    id, subscription_id, resource_group, name, type, fqdn,
                    is_public, is_restricted, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "slot-1",
                    "sub-1",
                    "rg-app",
                    "functions_windows-staging",
                    "Microsoft.Web/sites/slots",
                    "functions-staging.azurewebsites.net",
                    1,
                    0,
                    json.dumps({
                        "kind": "functionapp,linux",
                        "_extra": {
                            "slot_parent": "functions_windows",
                            "slot_name": "staging",
                        },
                    }),
                ),
            )

            result = _build_child_table(
                conn,
                "sub-1",
                "Microsoft.Web/sites",
                [{"rg": "rg-app", "name": "functions_windows"}],
            )
        finally:
            conn.close()

        assert result["view_type"] == "table", result
        assert result["title"] == "App Service / Function App — Deployment Slots", result
        assert result["rows"], result
        assert result["rows"][0][0] == "functions_windows-staging", result["rows"]
        assert result["rows"][0][4] == "Function App Slot", result["rows"]

    def test_function_app_deployment_slots_use_function_app_title(self):
        """Function app drill-down should not use the generic App Service title."""
        import json
        import sqlite3

        try:
            from web.app import _build_child_table
        except ModuleNotFoundError:
            from app import _build_child_table

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            conn.executescript(
                """
                CREATE TABLE subscriptions (
                    id TEXT PRIMARY KEY,
                    display_name TEXT,
                    environment TEXT,
                    state TEXT
                );
                CREATE TABLE provisioned_assets (
                    id TEXT PRIMARY KEY,
                    subscription_id TEXT,
                    resource_group TEXT,
                    name TEXT,
                    type TEXT,
                    fqdn TEXT,
                    is_public INTEGER DEFAULT 0,
                    is_restricted INTEGER DEFAULT 0,
                    raw_json TEXT
                );
                """
            )
            conn.execute(
                "INSERT INTO subscriptions (id, display_name, environment, state) VALUES (?, ?, ?, ?)",
                ("sub-1", "Test Subscription", "production", "Enabled"),
            )
            conn.execute(
                """
                INSERT INTO provisioned_assets (
                    id, subscription_id, resource_group, name, type, fqdn,
                    is_public, is_restricted, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "fn-1",
                    "sub-1",
                    "rg-app",
                    "production-fi-api-uksouth",
                    "Microsoft.Web/sites",
                    "production-fi-api-uksouth.azurewebsites.net",
                    1,
                    0,
                    json.dumps({"kind": "functionapp,linux"}),
                ),
            )

            result = _build_child_table(
                conn,
                "sub-1",
                "Microsoft.Web/sites",
                [{"rg": "rg-app", "name": "production-fi-api-uksouth"}],
            )
        finally:
            conn.close()

        assert result["title"] == "Function App — Deployment Slots", result

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
        assert 'Internet --> rgnet_appgwone' in mermaid, mermaid
        assert "class rgnet_appgwone entryPointProtected;" in mermaid, mermaid
        assert "linkStyle 0 stroke:#f97316" in mermaid, mermaid

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

    def test_appgw_apim_backend_url_routes_to_apim_node(self):
        """App Gateway backend URLs should resolve to the APIM node even when URL-shaped."""
        rows = [
            (
                "cop-resource-server-apim",
                "Microsoft.Network/applicationGateways",
                "rg-app",
                "cop-resource-server-apim.example.com",
                1,
                "WAF_v2",
                "appgw-id",
                0,
                "HTTPS:443",
                0,
                None,
                None,
            ),
            (
                "production-api-uksouth",
                "Microsoft.ApiManagement/service",
                "rg-api",
                "production-api-uksouth.azure-api.net",
                1,
                "Developer",
                "apim-id",
                0,
                None,
                0,
                None,
                None,
                "{}",
            ),
        ]
        appgw_routes = [
            (
                "cop-resource-server-apim",
                "cop-resource-server-apim.example.com",
                '["https://production-api-uksouth.azure-api.net/"]',
                "backend-pool",
                "listener-one",
                "/*",
                "HTTPS",
                None,
            ),
        ]

        result = self._call(rows=rows, appgw_routes=appgw_routes)
        mermaid = result.get("mermaid", "")
        assert 'agpool_rg_app_cop_resource_server_apim_backend_pool --> grp_APIM_Public' in mermaid, mermaid
        assert "production-api-uksouth.azure-api.net" in mermaid, mermaid

    def test_apim_api_nodes_and_backend_targets_render_as_separate_hops(self):
        """APIM routes should show API → APIM → backend target → workload."""
        rows = [
            (
                "production-api-uksouth",
                "Microsoft.ApiManagement/service",
                "rg-api",
                "production-api-uksouth.azure-api.net",
                1,
                "Developer",
                "/subscriptions/000/resourceGroups/rg-api/providers/Microsoft.ApiManagement/service/production-api-uksouth",
                0,
                None,
                0,
                None,
                None,
                "{}",
                None,
                None,
            ),
            (
                "production-internal-transfers-fn-uksouth",
                "Microsoft.Web/sites",
                "rg-backend",
                "production-internal-transfers-fn-uksouth.azurewebsites.net",
                1,
                "Y1",
                "/subscriptions/000/resourceGroups/rg-backend/providers/Microsoft.Web/sites/production-internal-transfers-fn-uksouth",
                0,
                None,
                0,
                None,
                None,
                "{}",
                None,
                None,
            ),
        ]
        apim_api_rows = [
            {
                "apim_name": "production-api-uksouth",
                "apim_resource_id": "/subscriptions/000/resourceGroups/rg-api/providers/Microsoft.ApiManagement/service/production-api-uksouth",
                "api_name": "orders-api",
                "api_display_name": "Orders API",
                "api_path": "/orders",
            }
        ]
        apim_backend_rows = [
            {
                "apim_name": "production-api-uksouth",
                "backend_id": "internal-transfers",
                "title": "internal-transfers",
                "url": "https://production-internal-transfers-fn-uksouth.azurewebsites.net/",
            }
        ]
        apim_route_map = {
            "production-api-uksouth": [
                "https://production-internal-transfers-fn-uksouth.azurewebsites.net/",
            ]
        }

        result = self._call(
            rows=rows,
            apim_api_rows=apim_api_rows,
            apim_backend_rows=apim_backend_rows,
            apim_route_map=apim_route_map,
        )
        mermaid = result.get("mermaid", "")
        assert "Orders API" in mermaid, mermaid
        assert "internal-transfers" in mermaid, mermaid
        assert "rg_api_production_api_uksouth__orders_api --> grp_APIM_Public" in mermaid, mermaid
        assert "grp_APIM_Public --> rg_api_production_api_uksouth__internal_transfers" in mermaid, mermaid
        assert " -->|\"Routing\"| " not in mermaid, mermaid

    def test_apim_backend_targets_keep_display_label_in_modal_and_subnet_graph(self):
        """APIM backend targets should render under the APIM subnet and keep a readable popup title."""
        import json

        rows = [
            (
                "core-api-uksouth",
                "Microsoft.ApiManagement/service",
                "rg-api",
                "core-api-uksouth.azure-api.net",
                1,
                "Developer",
                "/subscriptions/000/resourceGroups/rg-api/providers/Microsoft.ApiManagement/service/core-api-uksouth",
                0,
                None,
                0,
                None,
                None,
                json.dumps({
                    "_extra": {
                        "vnet_name": "vnet-core",
                        "vnet_resource_group": "rg-network",
                        "subnet_name": "snet-apim",
                        "subnet_id": "/subscriptions/000/resourceGroups/rg-network/providers/Microsoft.Network/virtualNetworks/vnet-core/subnets/snet-apim",
                    }
                }),
                None,
                None,
            ),
            (
                "core-api-uksouth-eventgrid-bridge",
                "Microsoft.Web/sites",
                "rg-app",
                "core-api-uksouth-eventgrid-bridge.azurewebsites.net",
                1,
                "Y1",
                "/subscriptions/000/resourceGroups/rg-app/providers/Microsoft.Web/sites/core-api-uksouth-eventgrid-bridge",
                0,
                None,
                0,
                None,
                None,
                "{}",
                None,
                None,
            ),
        ]
        apim_backend_rows = [
            {
                "apim_name": "core-api-uksouth",
                "backend_id": "prodgreen-eventgrid-bridge",
                "title": "prodgreen-eventgrid-bridge.internal.cbinnovation.uk",
                "url": "https://prodgreen-eventgrid-bridge.internal.cbinnovation.uk/",
            }
        ]
        apim_route_map = {
            "core-api-uksouth": [
                "https://prodgreen-eventgrid-bridge.internal.cbinnovation.uk/",
            ]
        }

        result = self._call(
            rows=rows,
            apim_backend_rows=apim_backend_rows,
            apim_route_map=apim_route_map,
        )
        mermaid = result.get("mermaid", "")
        node_map = result.get("node_drilldown_map", {})
        target = next(
            value
            for value in node_map.values()
            if value.get("arm_type") == "APIM Backend Target"
        )

        assert "prodgreen-eventgrid-bridge.internal.cbinnovation.uk 🔒" not in mermaid, mermaid
        assert "prodgreen-eventgrid-bridge.internal.cbinnovation.uk</div>" in mermaid, mermaid
        assert "rg_api_cbuk_core_prodgreen_api_uksouth__prodgreen_eventgrid_bridge_internal_cbinnovation_uk" in mermaid, mermaid
        assert target.get("title") == "prodgreen-eventgrid-bridge.internal.cbinnovation.uk", target
        assert target.get("resources", [{}])[0].get("name") == "core-api-uksouth::prodgreen-eventgrid-bridge.internal.cbinnovation.uk", target

    def test_marketlane_apim_backend_targets_use_backend_id_and_apim_subnet(self):
        """APIM backend targets should keep the backend id label and inherit APIM subnet placement."""
        import json

        rows = [
            (
                "apim-marketlane-edge",
                "Microsoft.ApiManagement/service",
                "rg-marketlane-edge",
                "apim-marketlane.azure-api.net",
                1,
                "Developer",
                "apim-id",
                0,
                None,
                0,
                None,
                None,
                json.dumps({
                    "_extra": {
                        "vnet_name": "vnet-marketlane-core",
                        "vnet_resource_group": "rg-marketlane-network",
                        "subnet_name": "snet-marketlane-apim",
                        "subnet_id": "/subscriptions/sub/resourceGroups/rg-marketlane-network/providers/Microsoft.Network/virtualNetworks/vnet-marketlane-core/subnets/snet-marketlane-apim",
                    }
                }),
                None,
                None,
            ),
            (
                "aks-marketlane-platform-orders-orders-api-8080",
                "Microsoft.ContainerService/managedClusters",
                "rg-marketlane-app",
                "",
                0,
                "Standard",
                "aks-id",
                0,
                None,
                0,
                None,
                None,
                "{}",
                None,
                None,
            ),
        ]
        apim_backend_rows = [
            {
                "apim_name": "apim-marketlane-edge",
                "backend_id": "aks-marketlane-platform-orders-orders-api-8080",
                "title": "aks-marketlane-platform-orders-orders-api-8080",
                "url": "https://aks-marketlane-platform-orders-orders-api-8080",
            }
        ]
        apim_route_map = {
            "apim-marketlane-edge": ["https://aks-marketlane-platform-orders-orders-api-8080"],
        }

        result = self._call(
            rows=rows,
            apim_backend_rows=apim_backend_rows,
            apim_route_map=apim_route_map,
        )
        mermaid = result.get("mermaid", "")
        assert "rg_marketlane_edge_apim_marketlane_edge__aks_marketlane_platform_orders_orders_api_8080" in mermaid, mermaid
        assert "Network: vnet-marketlane-core" in mermaid, mermaid
        assert "Subnet: snet-marketlane-apim" in mermaid, mermaid
        assert "grp_APIM_Public --> rg_marketlane_edge_apim_marketlane_edge__aks_marketlane_platform_orders_orders_api_8080" in mermaid, mermaid
        assert 'grp_APIM_Public -->|"Routing"| rg_marketlane_edge_apim_marketlane_edge__aks_marketlane_platform_orders_orders_api_8080' not in mermaid, mermaid
        assert 'rg_marketlane_edge_apim_marketlane_edge__aks_marketlane_platform_orders_orders_api_8080 --> rg_marketlane_app_aks_marketlane_platform_orders_orders_api_8080' in mermaid, mermaid

    def test_harvested_apim_backend_target_inherits_parent_subnet(self):
        """Harvested APIM backend targets should inherit subnet placement and use an unlabeled parent edge."""
        import json

        rows = [
            (
                "apim-marketlane-edge",
                "Microsoft.ApiManagement/service",
                "rg-marketlane-edge",
                "apim-marketlane.azure-api.net",
                1,
                "Developer",
                "apim-id",
                0,
                None,
                0,
                None,
                None,
                json.dumps({
                    "_extra": {
                        "vnet_name": "vnet-marketlane-core",
                        "vnet_resource_group": "rg-marketlane-network",
                        "subnet_name": "snet-marketlane-apim",
                        "subnet_id": "/subscriptions/sub/resourceGroups/rg-marketlane-network/providers/Microsoft.Network/virtualNetworks/vnet-marketlane-core/subnets/snet-marketlane-apim",
                    }
                }),
                None,
                None,
            ),
            (
                "orders-backend",
                "APIM Backend Target",
                "rg-marketlane-edge",
                None,
                0,
                None,
                "apim-marketlane-edge::orders-backend",
                0,
                None,
                0,
                None,
                None,
                json.dumps({
                    "apim_name": "apim-marketlane-edge",
                    "backend_id": "orders-backend",
                    "_extra": {"display_label": "Orders backend"},
                }),
                None,
                None,
            ),
        ]

        result = self._call(rows=rows)
        mermaid = result.get("mermaid", "")
        target_line = next(line for line in mermaid.splitlines() if "Orders backend</div>" in line)

        assert "Network: vnet-marketlane-core" in mermaid, mermaid
        assert "Subnet: snet-marketlane-apim" in mermaid, mermaid
        assert target_line.startswith("            "), mermaid
        assert "grp_APIM_Public --> rg_marketlane_edge_orders_backend" in mermaid, mermaid
        assert 'grp_APIM_Public -->|"Routing"| rg_marketlane_edge_orders_backend' not in mermaid, mermaid

    def test_marketlane_orders_backend_routes_through_internal_dns_and_service(self):
        """The orders APIM target should route through AKS ingress DNS and its Kubernetes Service."""
        import json

        rows = [
            (
                "apim-marketlane-edge",
                "Microsoft.ApiManagement/service",
                "rg-marketlane-edge",
                "apim-marketlane.azure-api.net",
                1,
                "Developer",
                "apim-id",
                0,
                None,
                0,
                None,
                [
                    {
                        "target": "https://orders.marketlane-retail.internal/",
                        "name": "orders.marketlane-retail.internal",
                    },
                    {
                        "target": "aks-marketlane-platform-orders-orders-api-8080",
                        "name": "orders-api",
                    },
                ],
                json.dumps({
                    "_extra": {
                        "routing_targets": [
                            {
                                "target": "https://orders.marketlane-retail.internal/",
                                "name": "orders.marketlane-retail.internal",
                            },
                        ],
                    },
                }),
                None,
                None,
            ),
            (
                "aks-marketlane-platform",
                "Microsoft.ContainerService/managedClusters",
                "rg-marketlane-app",
                "",
                0,
                "Standard",
                "aks-id",
                0,
                None,
                0,
                None,
                None,
                json.dumps({
                    "_extra": {
                        "vnet_name": "vnet-marketlane-core",
                        "vnet_resource_group": "rg-marketlane-network",
                        "subnet_name": "snet-marketlane-aks",
                        "subnet_id": "/subscriptions/sub/resourceGroups/rg-marketlane-network/providers/Microsoft.Network/virtualNetworks/vnet-marketlane-core/subnets/snet-marketlane-aks",
                    }
                }),
                None,
                None,
            ),
        ]
        backend_id = "aks-marketlane-platform-orders-orders-api-8080"
        backend_target_nid = f"rg_marketlane_edge_apim_marketlane_edge__{backend_id.replace('-', '_')}"
        service_nid = "rg_marketlane_app_aks_service_aks_marketlane_platform_orders_orders_api_8080"
        cluster_nid = "rg_marketlane_app_aks_marketlane_platform"
        apim_backend_rows = [
            {
                "apim_name": "apim-marketlane-edge",
                "backend_id": backend_id,
                "title": backend_id,
                "url": "https://orders.marketlane-retail.internal/",
            }
        ]
        apim_route_map = {
            "apim-marketlane-edge": ["https://orders.marketlane-retail.internal/"],
        }
        aks_route_rows = [
            (
                "aks-marketlane-platform",
                "orders",
                "orders-ingress",
                "orders.marketlane-retail.internal",
                "/api/orders/*",
                "Internal",
                "orders-api",
                "8080",
                "orders-api",
                None,
                "rg-marketlane-app",
                {},
            ),
        ]

        result = self._call(
            rows=rows,
            apim_backend_rows=apim_backend_rows,
            apim_route_map=apim_route_map,
            aks_route_rows=aks_route_rows,
        )
        mermaid = result.get("mermaid", "")

        assert f"grp_APIM_Public --> {backend_target_nid}" in mermaid, mermaid
        service_node = result.get("node_drilldown_map", {}).get(service_nid, {})

        assert f"{backend_target_nid} --> {service_nid}" in mermaid, mermaid
        assert f"{service_nid} --> {cluster_nid}" in mermaid, mermaid
        assert "orders.marketlane-retail.internal" not in mermaid, mermaid
        assert service_node.get("ingress_host") == "orders.marketlane-retail.internal", service_node
        assert service_node.get("ingress_path") == "/api/orders/*", service_node
        assert f"grp_APIM_Public --> {service_nid}" not in mermaid, mermaid
        assert f"{backend_target_nid} --> {cluster_nid}" not in mermaid, mermaid

    def test_harvested_and_synthetic_apim_backends_are_deduplicated(self):
        """The same APIM backend should render once when present in both input sources."""
        import json

        rows = [
            (
                "apim-marketlane-edge",
                "Microsoft.ApiManagement/service",
                "rg-marketlane-edge",
                "apim-marketlane.azure-api.net",
                1,
                "Developer",
                "apim-id",
                0,
                None,
                0,
                None,
                None,
                "{}",
                None,
                None,
            ),
            (
                "store-backend",
                "APIM Backend Target",
                "rg-marketlane-edge",
                None,
                0,
                None,
                "apim-marketlane-edge::store-backend",
                0,
                None,
                0,
                None,
                None,
                json.dumps({
                    "apim_name": "apim-marketlane-edge",
                    "backend_id": "store-backend",
                    "_extra": {"display_label": "store-backend"},
                }),
                None,
                None,
            ),
        ]
        apim_backend_rows = [
            {
                "apim_name": "apim-marketlane-edge",
                "backend_id": "store-backend",
                "title": "store-backend",
                "url": "https://store.marketlane-retail.azurewebsites.net",
            },
        ]
        apim_route_map = {
            "apim-marketlane-edge": ["https://store.marketlane-retail.azurewebsites.net"],
        }

        result = self._call(
            rows=rows,
            apim_backend_rows=apim_backend_rows,
            apim_route_map=apim_route_map,
        )
        mermaid = result.get("mermaid", "")
        backend_nodes = [
            value
            for value in result.get("node_drilldown_map", {}).values()
            if value.get("arm_type") == "APIM Backend Target"
            and value.get("title") == "store-backend"
        ]

        assert mermaid.count("store-backend</div>") == 1, mermaid
        assert len(backend_nodes) == 1, backend_nodes

    def test_apim_does_not_infer_untargeted_backend_edges(self):
        """APIM should only render confirmed backend routes, not heuristic fanout edges."""
        import json

        rows = [
            (
                "production-api-uksouth",
                "Microsoft.ApiManagement/service",
                "rg-api",
                "production-api-uksouth.azure-api.net",
                1,
                "Developer",
                "apim-id",
                0,
                None,
                0,
                None,
                None,
                "{}",
            ),
            (
                "production-internal-transfers-fn-uksouth",
                "Microsoft.Web/sites",
                "production-internal-transfers-fn-uksouth",
                "production-internal-transfers-fn-uksouth.production-shared-uksouth.appserviceenvironment.net",
                1,
                "Y1",
                "fn-id",
                0,
                None,
                0,
                None,
                None,
                json.dumps({"kind": "functionapp"}),
            ),
        ]
        result = self._call(rows=rows)
        mermaid = result.get("mermaid", "")
        mermaid = result.get("mermaid", "")
        assert "grp_APIM_Public -->" not in mermaid, mermaid

    def test_appgw_appserviceenvironment_backend_urls_route_to_backend_site(self):
        """App Gateway backend URLs should resolve to ASE-hosted backend sites."""
        import json

        rows = [
            (
                "cop-resource-server-apim",
                "Microsoft.Network/applicationGateways",
                "rg-app",
                "cop-resource-server-apim.example.com",
                1,
                "WAF_v2",
                "appgw-id",
                0,
                "HTTPS:443",
            ),
            (
                "cards-management-web",
                "Microsoft.Web/sites",
                "rg-backend",
                "cards-management-web.azurewebsites.net",
                1,
                "B1",
                "site-id",
                0,
                None,
            ),
            (
                "institution-portal-crm",
                "Microsoft.Web/sites",
                "rg-backend",
                "institution-portal-crm.azurewebsites.net",
                1,
                "B1",
                "site-id-2",
                0,
                None,
            ),
        ]
        appgw_routes = [
            (
                "cop-resource-server-apim",
                "cop-resource-server-apim.example.com",
                json.dumps([
                    "https://production-cards-management-web-uksouth.production-shared-uksouth.appserviceenvironment.net/",
                    "https://production-institution-portal-crm-uksouth.production-shared-uksouth.appserviceenvironment.net/",
                ]),
                "backend-pool",
                "listener-one",
                "/*",
                "HTTPS",
                None,
            ),
        ]

        result = self._call(rows=rows, appgw_routes=appgw_routes)
        mermaid = result.get("mermaid", "")
        assert 'agpool_rg_app_cop_resource_server_apim_backend_pool --> rg_backend_cards_management_web' in mermaid, mermaid
        assert 'agpool_rg_app_cop_resource_server_apim_backend_pool --> rg_backend_institution_portal_crm' in mermaid, mermaid

    def test_appgw_appserviceenvironment_backend_urls_route_to_multiple_backend_sites(self):
        """App Gateway backend URLs should resolve to all matching ASE-hosted backend sites."""
        import json

        rows = [
            (
                "cop-resource-server-apim",
                "Microsoft.Network/applicationGateways",
                "rg-app",
                "cop-resource-server-apim.example.com",
                1,
                "WAF_v2",
                "appgw-id",
                0,
                "HTTPS:443",
            ),
            (
                "institution-portal-internal",
                "Microsoft.Web/sites",
                "rg-backend",
                "institution-portal-internal.azurewebsites.net",
                1,
                "B1",
                "site-id-3",
                0,
                None,
            ),
            (
                "transactionnotifications",
                "Microsoft.Web/sites",
                "rg-backend",
                "transactionnotifications.azurewebsites.net",
                1,
                "B1",
                "site-id-4",
                0,
                None,
            ),
        ]
        appgw_routes = [
            (
                "cop-resource-server-apim",
                "cop-resource-server-apim.example.com",
                json.dumps(["https://production-institution-portal-internal-uksouth.production-shared-uksouth.appserviceenvironment.net/"]),
                "institution-portal-internal",
                "listener-one",
                "/*",
                "HTTPS",
                None,
            ),
            (
                "cop-resource-server-apim",
                "cop-resource-server-apim.example.com",
                json.dumps(["https://production-transaction-notifications-f-uksouth.production-shared-uksouth.appserviceenvironment.net/"]),
                "transactionnotifications",
                "listener-two",
                "/*",
                "HTTPS",
                None,
            ),
        ]

        result = self._call(rows=rows, appgw_routes=appgw_routes)
        mermaid = result.get("mermaid", "")
        assert 'agpool_rg_app_cop_resource_server_apim_institution_portal_internal --> rg_backend_institution_portal_internal' in mermaid, mermaid
        assert 'agpool_rg_app_cop_resource_server_apim_transactionnotifications --> rg_backend_transactionnotifications' in mermaid, mermaid


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

    def _call(self, rows=None, aks_route_rows=None):
        import sys
        import os
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        os.environ.setdefault("FLASK_APP", "web/app.py")
        from web.app import _build_subscription_diagrams_by_rg
        return _build_subscription_diagrams_by_rg(
            "Test Subscription",
            "production",
            rows if rows is not None else self._make_rows(),
            aks_route_rows=aks_route_rows,
        )

    def test_rg_diagrams_include_mode_views(self):
        diagrams = self._call()
        assert diagrams, "Expected at least one RG diagram"
        first = diagrams[0]
        assert "connectivity" in set(first.get("views", {}))
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

    def test_rg_connectivity_shows_fqdn_for_routed_gateway(self):
        import sys
        import os

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        os.environ.setdefault("FLASK_APP", "web/app.py")
        from web.subscription_diagram_helpers import build_subscription_diagrams_by_rg

        rows = [
            (
                "appgw-one",
                "Microsoft.Network/applicationGateways",
                "rg-net",
                "appgw-one.example.com",
                1,
                "WAF_v2",
                "gw-id",
                1,
                "HTTPS:443",
                0,
                "WAF_v2",
                '[{"target":"api.example.com"}]',
                "{}",
                None,
            ),
        ]

        diagrams = build_subscription_diagrams_by_rg(
            "Test Subscription",
            "production",
            rows,
            sanitise_node_id=lambda s: s.replace("/", "_").replace(" ", "_"),
            friendly_type=lambda arm_type: "App Gateway" if "applicationgateway" in (arm_type or "").lower() else arm_type,
            get_icon_path=lambda _resource_type: None,
            normalize_attack_paths=lambda raw_paths, reviewer=None: raw_paths,
        )

        mermaid = diagrams[0]["views"]["connectivity"]["mermaid"]
        assert "appgw-one.example.com" in mermaid, mermaid

    def test_rg_connectivity_renders_aks_ingress_and_service_routes(self):
        aks_route_rows = [
            (
                "sts",
                "orders",
                "orders-ingress",
                "orders.example.com",
                "/api/*",
                "Internal",
                "orders-service",
                "8080",
                "orders-deploy",
                "git@example.com/orders",
                "rg-app",
                '{"app":"orders"}',
            )
        ]

        rows = self._make_rows() + [
            (
                "sts",
                "Microsoft.Web/sites",
                "rg-app",
                "sts.example.com",
                0,
                "P1v3",
                "sts-id",
                0,
                None,
                0,
                None,
                '[{"target":"sts","target_resource_id":"aks-id","name":"sts"}]',
                None,
                None,
            ),
            (
                "sts",
                "Microsoft.ContainerService/managedClusters",
                "rg-app",
                "",
                0,
                None,
                "aks-id",
                0,
                None,
                0,
                None,
                None,
                None,
                None,
            ),
        ]
        diagrams = self._call(rows=rows, aks_route_rows=aks_route_rows)
        mermaid = diagrams[0]["views"]["connectivity"]["mermaid"]
        assert "orders.example.com" in mermaid, mermaid
        assert "orders-ingress" in mermaid, mermaid
        assert "orders-service" in mermaid, mermaid
        assert "orders_ingress" in mermaid and "orders_service" in mermaid, mermaid

    def test_rg_connectivity_does_not_self_route_aks_ingress_nodes(self):
        import sys
        import os

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        os.environ.setdefault("FLASK_APP", "web/app.py")
        from web.subscription_diagram_helpers import build_subscription_diagrams_by_rg

        rows = [
            (
                "aks-prod",
                "Microsoft.ContainerService/managedClusters",
                "rg-app",
                "",
                0,
                None,
                "aks-id",
                0,
                None,
                0,
                None,
                None,
                None,
                None,
            ),
        ]
        aks_route_rows = [
            (
                "aks-prod",
                "default",
                "orders-ingress",
                "orders.example.com",
                "/*",
                "Internal",
                "orders-service",
                "8080",
                "orders-deploy",
                "git@example.com/orders",
                "rg-app",
                '{"app":"orders"}',
            )
        ]

        diagrams = build_subscription_diagrams_by_rg(
            "Test Subscription",
            "production",
            rows,
            aks_route_rows=aks_route_rows,
            sanitise_node_id=lambda s: s.replace("/", "_").replace(" ", "_"),
            friendly_type=lambda arm_type: "App Gateway" if "applicationgateway" in (arm_type or "").lower() else arm_type,
            get_icon_path=lambda _resource_type: None,
            normalize_attack_paths=lambda raw_paths, reviewer=None: raw_paths,
        )

        mermaid = diagrams[0]["views"]["connectivity"]["mermaid"]
        for line in mermaid.splitlines():
            if "-->" not in line:
                continue
            left, right = line.split("-->", 1)
            source = left.strip().split()[-1]
            target = right.strip().split()[-1]
            assert source != target, mermaid


class TestCosmosDbFqdnResolution:
    """Regression tests for Cosmos DB endpoint resolution in cloud views."""

    def test_connectivity_view_derives_cosmos_fqdn(self):
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

        connectivity_mermaid = diagrams[0]["views"]["connectivity"]["mermaid"]
        assert "cosmos-one.documents.azure.com" in connectivity_mermaid
        assert "Direct data plane" not in connectivity_mermaid

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

    def test_generic_drilldown_filters_rows_to_selected_resource_type(self):
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
            conn.executemany(
                """
                INSERT INTO provisioned_assets (
                    id, subscription_id, resource_group, name, type, location, sku,
                    tags, is_public, fqdn, pipeline_tag, raw_json, endpoints, auth_methods,
                    first_detected, last_synced, status, is_restricted
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        "/subscriptions/sub-1/resourceGroups/rg-redis/providers/Microsoft.Cache/Redis/example-service-connector",
                        "sub-1",
                        "rg-redis",
                        "example-service-connector",
                        "Microsoft.Cache/Redis",
                        "uksouth",
                        "Standard",
                        None,
                        0,
                        "example-service-connector.redis.cache.windows.net",
                        None,
                        "{}",
                        None,
                        None,
                        "2026-06-01T00:00:00Z",
                        "2026-06-01T00:00:00Z",
                        "active",
                        0,
                    ),
                    (
                        "/subscriptions/sub-1/resourceGroups/rg-cosmos/providers/Microsoft.DocumentDB/databaseAccounts/example-service-connector",
                        "sub-1",
                        "rg-cosmos",
                        "example-service-connector",
                        "Microsoft.DocumentDB/databaseAccounts",
                        "uksouth",
                        None,
                        None,
                        0,
                        "example-service-connector.documents.azure.com:443",
                        None,
                        "{}",
                        None,
                        None,
                        "2026-06-01T00:00:00Z",
                        "2026-06-01T00:00:00Z",
                        "active",
                        1,
                    ),
                    (
                        "/subscriptions/sub-1/resourceGroups/rg-redis/providers/Microsoft.Network/privateEndpoints/example-service-connector",
                        "sub-1",
                        "rg-redis",
                        "example-service-connector",
                        "Microsoft.Network/privateEndpoints",
                        "uksouth",
                        None,
                        None,
                        0,
                        "example-service-connector.redis.cache.windows.net",
                        None,
                        "{}",
                        None,
                        None,
                        "2026-06-01T00:00:00Z",
                        "2026-06-01T00:00:00Z",
                        "active",
                        0,
                    ),
                ],
            )

            result = _build_child_table(
                conn,
                "sub-1",
                "Microsoft.Cache/Redis",
                [{"rg": "rg-redis", "name": "example-service-connector"}],
            )
        finally:
            conn.close()

        assert result["view_type"] == "table"
        assert len(result["rows"]) == 1, result["rows"]
        assert result["rows"][0][2] == "Redis Cache", result["rows"]

    def test_generic_drilldown_disambiguates_duplicate_resource_names(self):
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
            conn.executemany(
                """
                INSERT INTO provisioned_assets (
                    id, subscription_id, resource_group, name, type, location, sku,
                    tags, is_public, fqdn, pipeline_tag, raw_json, endpoints, auth_methods,
                    first_detected, last_synced, status, is_restricted
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        "/subscriptions/sub-1/resourceGroups/blue-network-ukwest/providers/Microsoft.Network/publicIPAddresses/bastion",
                        "sub-1",
                        "blue-network-ukwest",
                        "bastion",
                        "Microsoft.Network/publicIPAddresses",
                        "uksouth",
                        "Standard",
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
                    (
                        "/subscriptions/sub-1/resourceGroups/green-network-ukwest/providers/Microsoft.Network/publicIPAddresses/bastion",
                        "sub-1",
                        "green-network-ukwest",
                        "bastion",
                        "Microsoft.Network/publicIPAddresses",
                        "uksouth",
                        "Standard",
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
                ],
            )

            result = _build_child_table(
                conn,
                "sub-1",
                "Microsoft.Network/publicIPAddresses",
                [{"rg": "blue-network-ukwest", "name": "bastion"}, {"rg": "green-network-ukwest", "name": "bastion"}],
            )
        finally:
            conn.close()

        assert result["view_type"] == "table"
        assert {row[0] for row in result["rows"]} == {
            "bastion (blue-network-ukwest)",
            "bastion (green-network-ukwest)",
        }, result["rows"]


class TestSubscriptionOverlayViews:
    """Regression tests for the shared subscription overlay helper."""

    def _call(self, rows):
        import sys

        sys.path.insert(0, str(REPO_ROOT))
        from web.subscription_diagram_helpers import build_subscription_diagrams_by_rg

        return build_subscription_diagrams_by_rg(
            "Test Subscription",
            "production",
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
        overlay = self._call(rows)[0]
        mermaid = overlay["views"]["connectivity"]["mermaid"]
        assert 'Internet --> rgnet_appgwone' in mermaid, mermaid
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

    def test_subscription_architecture_payload_handles_current_waf_schema(self):
        import json
        import sqlite3

        from web.app import _build_subscription_architecture_payload

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
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
                    fqdn TEXT,
                    is_public INTEGER DEFAULT 0,
                    status TEXT,
                    pipeline_tag TEXT,
                    first_detected TEXT,
                    last_synced TEXT,
                    raw_json TEXT,
                    is_restricted INTEGER DEFAULT 0,
                    waf_mode TEXT
                );
                CREATE TABLE appgw_routing_rules (
                    id TEXT PRIMARY KEY,
                    subscription_id TEXT,
                    gateway_name TEXT,
                    backend_fqdns TEXT,
                    backend_pool_name TEXT,
                    listener_name TEXT,
                    url_path TEXT,
                    protocol TEXT,
                    waf_policy_name TEXT,
                    resource_group TEXT,
                    hostname TEXT
                );
                CREATE TABLE appgw_waf_policies (
                    name TEXT,
                    subscription_id TEXT,
                    resource_group TEXT,
                    mode TEXT,
                    state TEXT,
                    managed_rule_sets TEXT,
                    custom_rules_count INTEGER DEFAULT 0,
                    associated_gateways TEXT
                );
                """
            )
            conn.execute(
                "INSERT INTO subscriptions (id, display_name, environment, state, last_synced) VALUES (?, ?, ?, ?, ?)",
                ("sub-1", "Test Subscription", "production", "Enabled", "2026-06-01T00:00:00Z"),
            )
            conn.execute(
                """
                INSERT INTO appgw_waf_policies (
                    name, subscription_id, resource_group, mode, state, managed_rule_sets,
                    custom_rules_count, associated_gateways
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "policy-one",
                    "sub-1",
                    "rg-net",
                    "Prevention",
                    "Enabled",
                    '[{"type": "OWASP", "version": "3.2"}]',
                    2,
                    '["appgw-one"]',
                ),
            )
            conn.execute(
                """
                INSERT INTO provisioned_assets (
                    id, subscription_id, resource_group, name, type, location, sku,
                    fqdn, is_public, status, pipeline_tag, first_detected, last_synced,
                    raw_json, is_restricted, waf_mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/applicationGateways/appgw-one",
                    "sub-1",
                    "rg-net",
                    "appgw-one",
                    "Microsoft.Network/applicationGateways",
                    "uksouth",
                    "WAF_v2",
                    "appgw-one.example.com",
                    1,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps({}),
                    0,
                    "WAF_v2",
                ),
            )
            conn.execute(
                """
                INSERT INTO appgw_routing_rules (
                    id, subscription_id, gateway_name, backend_fqdns, backend_pool_name,
                    listener_name, url_path, protocol, waf_policy_name, resource_group, hostname
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "appgw-one::rule-one",
                    "sub-1",
                    "appgw-one",
                    json.dumps(["orders.example.com"]),
                    "pool-one",
                    "listener-one",
                    "/*",
                    "HTTPS",
                    "policy-one",
                    "rg-net",
                    "appgw-one.example.com",
                ),
            )

            payload = _build_subscription_architecture_payload(conn, "sub-1")
        finally:
            conn.close()

        waf_nodes = [node for node in payload["nodes"] if node["data"].get("typeLabel") == "WAF Policy"]
        assert len(waf_nodes) == 1, payload
        assert waf_nodes[0]["data"].get("label") == "policy-one", waf_nodes[0]

    def test_subscription_architecture_payload_places_waf_before_gateway(self):
        import json
        import sqlite3

        from web.app import _build_ingress_diagram

        rows = [[
            "appgw-one",
            "Microsoft.Network/applicationGateways",
            "rg-net",
            "appgw-one.example.com",
            1,
            "WAF_v2",
            "gw-1",
            True,
            [],
            False,
            "Prevention",
            [],
            "{}",
            [],
            None,
        ]]
        appgw_routes = [(
            "appgw-one",
            "appgw-one.example.com",
            json.dumps(["api.example.com"]),
            "pool-a",
            "listener-a",
            "/*",
            "HTTPS",
            "policy-one",
        )]
        waf_rows = [(
            "policy-one",
            json.dumps(["appgw-one"]),
            "Enabled",
        )]

        mermaid = _build_ingress_diagram(
            rows,
            appgw_routes=appgw_routes,
            appgw_waf_policy_rows=waf_rows,
        )["mermaid"]
        assert mermaid.index("waf_rg_net_appgw_one_policy_one") < mermaid.index("rg_net_appgw_one"), mermaid
        assert "waf_rg_net_appgw_one_policy_one --> rg_net_appgw_one" in mermaid, mermaid

    def test_subscription_architecture_payload_joins_appgw_backend_pool_to_apim(self):
        import json
        import sqlite3

        from web.app import _build_subscription_architecture_payload

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
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
                    fqdn TEXT,
                    is_public INTEGER DEFAULT 0,
                    status TEXT,
                    pipeline_tag TEXT,
                    first_detected TEXT,
                    last_synced TEXT,
                    raw_json TEXT,
                    is_restricted INTEGER DEFAULT 0,
                    waf_mode TEXT
                );
                CREATE TABLE appgw_routing_rules (
                    id TEXT PRIMARY KEY,
                    subscription_id TEXT,
                    gateway_name TEXT,
                    backend_fqdns TEXT,
                    backend_pool_name TEXT,
                    listener_name TEXT,
                    url_path TEXT,
                    protocol TEXT,
                    waf_policy_name TEXT,
                    resource_group TEXT,
                    hostname TEXT
                );
                """
            )
            conn.execute(
                "INSERT INTO subscriptions (id, display_name, environment, state, last_synced) VALUES (?, ?, ?, ?, ?)",
                ("sub-1", "Test Subscription", "production", "Enabled", "2026-06-01T00:00:00Z"),
            )
            gw_id = "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/applicationGateways/appgw-one"
            apim_id = "/subscriptions/sub-1/resourceGroups/rg-api/providers/Microsoft.ApiManagement/service/apim-one"
            conn.executemany(
                """
                INSERT INTO provisioned_assets (
                    id, subscription_id, resource_group, name, type, location, sku,
                    fqdn, is_public, status, pipeline_tag, first_detected, last_synced,
                    raw_json, is_restricted, waf_mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        gw_id,
                        "sub-1",
                        "rg-net",
                        "appgw-one",
                        "Microsoft.Network/applicationGateways",
                        "uksouth",
                        "WAF_v2",
                        "appgw-one.example.com",
                        1,
                        "active",
                        None,
                        "2026-06-01T00:00:00Z",
                        "2026-06-01T00:00:00Z",
                        json.dumps({}),
                        0,
                        "WAF_v2",
                    ),
                    (
                        apim_id,
                        "sub-1",
                        "rg-api",
                        "apim-one",
                        "Microsoft.ApiManagement/service",
                        "uksouth",
                        "Developer",
                        "example-api-uksouth.azure-api.net",
                        1,
                        "active",
                        None,
                        "2026-06-01T00:00:00Z",
                        "2026-06-01T00:00:00Z",
                        json.dumps({}),
                        0,
                        None,
                    ),
                ],
            )
            conn.execute(
                """
                INSERT INTO appgw_routing_rules (
                    id, subscription_id, gateway_name, backend_fqdns, backend_pool_name,
                    listener_name, url_path, protocol, waf_policy_name, resource_group, hostname
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "appgw-one::route-1",
                    "sub-1",
                    "appgw-one",
                    '["example-api-uksouth.azure-api.net"]',
                    "cop-auth-server-phase2-apim",
                    "listener-one",
                    "/*",
                    "HTTPS",
                    None,
                    "rg-net",
                    "appgw-one.example.com",
                ),
            )

            payload = _build_subscription_architecture_payload(conn, "sub-1", view_mode="full")
        finally:
            conn.close()

        edges = payload["edges"]
        node_labels = [str(node.get("data", {}).get("label", "")) for node in payload.get("nodes", [])]
        assert any(e["source"] == gw_id and e["target"] == apim_id for e in edges), edges
        assert any("example-api-uksouth.azure-api.net" in label for label in node_labels), node_labels

    def test_subscription_architecture_payload_joins_appgw_backend_pool_to_ase_sites(self):
        import json
        import sqlite3

        from web.app import _build_subscription_architecture_payload

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
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
                    fqdn TEXT,
                    is_public INTEGER DEFAULT 0,
                    status TEXT,
                    pipeline_tag TEXT,
                    first_detected TEXT,
                    last_synced TEXT,
                    raw_json TEXT,
                    is_restricted INTEGER DEFAULT 0,
                    waf_mode TEXT
                );
                CREATE TABLE appgw_routing_rules (
                    id TEXT PRIMARY KEY,
                    subscription_id TEXT,
                    gateway_name TEXT,
                    backend_fqdns TEXT,
                    backend_pool_name TEXT,
                    listener_name TEXT,
                    url_path TEXT,
                    protocol TEXT,
                    waf_policy_name TEXT,
                    resource_group TEXT,
                    hostname TEXT
                );
                """
            )
            conn.execute(
                "INSERT INTO subscriptions (id, display_name, environment, state, last_synced) VALUES (?, ?, ?, ?, ?)",
                ("sub-1", "Test Subscription", "production", "Enabled", "2026-06-01T00:00:00Z"),
            )
            gw_id = "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/applicationGateways/appgw-one"
            tn_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/sites/transactionnotifications"
            portal_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/sites/institution-portal"
            conn.executemany(
                """
                INSERT INTO provisioned_assets (
                    id, subscription_id, resource_group, name, type, location, sku,
                    fqdn, is_public, status, pipeline_tag, first_detected, last_synced,
                    raw_json, is_restricted, waf_mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        gw_id,
                        "sub-1",
                        "rg-net",
                        "appgw-one",
                        "Microsoft.Network/applicationGateways",
                        "uksouth",
                        "WAF_v2",
                        "appgw-one.example.com",
                        1,
                        "active",
                        None,
                        "2026-06-01T00:00:00Z",
                        "2026-06-01T00:00:00Z",
                        json.dumps({}),
                        0,
                        "WAF_v2",
                    ),
                    (
                        tn_id,
                        "sub-1",
                        "rg-app",
                        "transactionnotifications",
                        "Microsoft.Web/sites",
                        "uksouth",
                        "P1v3",
                        "example-transaction-notifications-f-uksouth.production-shared-uksouth.appserviceenvironment.net",
                        0,
                        "active",
                        None,
                        "2026-06-01T00:00:00Z",
                        "2026-06-01T00:00:00Z",
                        json.dumps({"kind": "app"}),
                        0,
                        None,
                    ),
                    (
                        portal_id,
                        "sub-1",
                        "rg-app",
                        "institution-portal",
                        "Microsoft.Web/sites",
                        "uksouth",
                        "P1v3",
                        "example-institution-portal-uksouth.production-shared-uksouth.appserviceenvironment.net",
                        0,
                        "active",
                        None,
                        "2026-06-01T00:00:00Z",
                        "2026-06-01T00:00:00Z",
                        json.dumps({"kind": "app"}),
                        0,
                        None,
                    ),
                ],
            )
            conn.execute(
                """
                INSERT INTO appgw_routing_rules (
                    id, subscription_id, gateway_name, backend_fqdns, backend_pool_name,
                    listener_name, url_path, protocol, waf_policy_name, resource_group, hostname
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "appgw-one::route-ase",
                    "sub-1",
                    "appgw-one",
                    json.dumps([
                        "example-transaction-notifications-f-uksouth.production-shared-uksouth.appserviceenvironment.net",
                        "example-institution-portal-uksouth.production-shared-uksouth.appserviceenvironment.net",
                    ]),
                    "ase-backend-pool",
                    "listener-one",
                    "/*",
                    "HTTPS",
                    None,
                    "rg-net",
                    "appgw-one.example.com",
                ),
            )

            payload = _build_subscription_architecture_payload(conn, "sub-1", view_mode="full")
        finally:
            conn.close()

        edges = payload["edges"]
        assert any(e["source"] == gw_id and e["target"] == tn_id for e in edges), edges
        assert any(e["source"] == gw_id and e["target"] == portal_id for e in edges), edges

    def test_connectivity_mermaid_resolves_ase_backend_pool_hostnames_to_ase_node(self, monkeypatch):
        import json
        import sqlite3

        import web.app as app_module

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
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
                    fqdn TEXT,
                    is_public INTEGER DEFAULT 0,
                    status TEXT,
                    pipeline_tag TEXT,
                    first_detected TEXT,
                    last_synced TEXT,
                    raw_json TEXT,
                    is_restricted INTEGER DEFAULT 0,
                    waf_mode TEXT
                );
                CREATE TABLE appgw_routing_rules (
                    id TEXT PRIMARY KEY,
                    subscription_id TEXT,
                    gateway_name TEXT,
                    backend_fqdns TEXT,
                    backend_pool_name TEXT,
                    listener_name TEXT,
                    url_path TEXT,
                    protocol TEXT,
                    waf_policy_name TEXT,
                    resource_group TEXT,
                    hostname TEXT
                );
                """
            )
            conn.execute(
                "INSERT INTO subscriptions (id, display_name, environment, state, last_synced) VALUES (?, ?, ?, ?, ?)",
                ("sub-1", "Test Subscription", "production", "Enabled", "2026-06-01T00:00:00Z"),
            )
            gw_id = "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/applicationGateways/appgw-one"
            ase_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/hostingEnvironments/production-shared-uksouth"
            conn.executemany(
                """
                INSERT INTO provisioned_assets (
                    id, subscription_id, resource_group, name, type, location, sku,
                    fqdn, is_public, status, pipeline_tag, first_detected, last_synced,
                    raw_json, is_restricted, waf_mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        gw_id,
                        "sub-1",
                        "rg-net",
                        "appgw-one",
                        "Microsoft.Network/applicationGateways",
                        "uksouth",
                        "WAF_v2",
                        "appgw-one.example.com",
                        1,
                        "active",
                        None,
                        "2026-06-01T00:00:00Z",
                        "2026-06-01T00:00:00Z",
                        json.dumps({}),
                        0,
                        "WAF_v2",
                    ),
                    (
                        ase_id,
                        "sub-1",
                        "rg-app",
                        "production-shared-uksouth",
                        "Microsoft.Web/hostingEnvironments",
                        "uksouth",
                        "ASEv3",
                        "production-shared-uksouth.appserviceenvironment.net",
                        0,
                        "active",
                        None,
                        "2026-06-01T00:00:00Z",
                        "2026-06-01T00:00:00Z",
                        json.dumps({"kind": "ASEv3"}),
                        0,
                        None,
                    ),
                ],
            )
            conn.execute(
                """
                INSERT INTO appgw_routing_rules (
                    id, subscription_id, gateway_name, backend_fqdns, backend_pool_name,
                    listener_name, url_path, protocol, waf_policy_name, resource_group, hostname
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "appgw-one::route-ase",
                    "sub-1",
                    "appgw-one",
                    json.dumps([
                        "production-institution-portal-uksouth.production-shared-uksouth.appserviceenvironment.net",
                    ]),
                    "institution-portal",
                    "listener-one",
                    "/*",
                    "HTTPS",
                    None,
                    "rg-net",
                    "appgw-one.example.com",
                ),
            )
            conn.commit()

            monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
            client = app_module.app.test_client()
            resp = client.get("/api/subscriptions/sub-1/diagram")
        finally:
            conn.close()

        assert resp.status_code == 200, resp.get_data(as_text=True)
        mermaid = resp.get_json()["ingress_diagram"]["mermaid"]
        assert 'production_shared_uksouth["' in mermaid, mermaid
        assert "appserviceenvironment.net" not in mermaid, mermaid
        assert any(
            line.startswith("    rg_net_appgw_one -->") and "rg_app_production_shared_uksouth" in line
            for line in mermaid.splitlines()
        ), mermaid

    def test_subscription_architecture_payload_routes_appgw_pool_to_hidden_ase_child_parent(self, monkeypatch):
        import json
        import sqlite3

        import web.app as app_module

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
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
                    fqdn TEXT,
                    is_public INTEGER DEFAULT 0,
                    status TEXT,
                    pipeline_tag TEXT,
                    first_detected TEXT,
                    last_synced TEXT,
                    raw_json TEXT,
                    is_restricted INTEGER DEFAULT 0,
                    waf_mode TEXT
                );
                CREATE TABLE appgw_routing_rules (
                    id TEXT PRIMARY KEY,
                    subscription_id TEXT,
                    gateway_name TEXT,
                    backend_fqdns TEXT,
                    backend_pool_name TEXT,
                    listener_name TEXT,
                    url_path TEXT,
                    protocol TEXT,
                    waf_policy_name TEXT,
                    resource_group TEXT,
                    hostname TEXT
                );
                """
            )
            conn.execute(
                "INSERT INTO subscriptions (id, display_name, environment, state, last_synced) VALUES (?, ?, ?, ?, ?)",
                ("sub-1", "Test Subscription", "production", "Enabled", "2026-06-01T00:00:00Z"),
            )
            gw_id = "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/applicationGateways/appgw-one"
            ase_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/hostingEnvironments/production-shared-uksouth"
            site_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/sites/institution-portal"
            conn.executemany(
                """
                INSERT INTO provisioned_assets (
                    id, subscription_id, resource_group, name, type, location, sku,
                    fqdn, is_public, status, pipeline_tag, first_detected, last_synced,
                    raw_json, is_restricted, waf_mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        gw_id,
                        "sub-1",
                        "rg-net",
                        "appgw-one",
                        "Microsoft.Network/applicationGateways",
                        "uksouth",
                        "WAF_v2",
                        "appgw-one.example.com",
                        1,
                        "active",
                        None,
                        "2026-06-01T00:00:00Z",
                        "2026-06-01T00:00:00Z",
                        json.dumps({}),
                        0,
                        "WAF_v2",
                    ),
                    (
                        ase_id,
                        "sub-1",
                        "rg-app",
                        "production-shared-uksouth",
                        "Microsoft.Web/hostingEnvironments",
                        "uksouth",
                        "ASEv3",
                        "production-shared-uksouth.appserviceenvironment.net",
                        0,
                        "active",
                        None,
                        "2026-06-01T00:00:00Z",
                        "2026-06-01T00:00:00Z",
                        json.dumps({"kind": "ASEv3"}),
                        0,
                        None,
                    ),
                    (
                        site_id,
                        "sub-1",
                        "rg-app",
                        "institution-portal",
                        "Microsoft.Web/sites",
                        "uksouth",
                        "P1v3",
                        "institution-portal.azurewebsites.net",
                        0,
                        "active",
                        None,
                        "2026-06-01T00:00:00Z",
                        "2026-06-01T00:00:00Z",
                        json.dumps({"siteConfig": {"hostingEnvironmentProfile": {"id": ase_id}}}),
                        0,
                        None,
                    ),
                ],
            )
            conn.execute(
                """
                INSERT INTO appgw_routing_rules (
                    id, subscription_id, gateway_name, backend_fqdns, backend_pool_name,
                    listener_name, url_path, protocol, waf_policy_name, resource_group, hostname
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "appgw-one::route-ase",
                    "sub-1",
                    "appgw-one",
                    json.dumps([
                        "production-institution-portal-uksouth.production-shared-uksouth.appserviceenvironment.net",
                    ]),
                    "institution-portal",
                    "listener-one",
                    "/*",
                    "HTTPS",
                    None,
                    "rg-net",
                    "appgw-one.example.com",
                ),
            )
            conn.commit()

            monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
            monkeypatch.setattr(app_module, "_SUBSCRIPTION_DIAGRAM_CACHE", {})
            client = app_module.app.test_client()
            resp = client.get("/api/subscriptions/sub-1/diagram")
        finally:
            conn.close()

        assert resp.status_code == 200, resp.get_data(as_text=True)
        mermaid = resp.get_json()["ingress_diagram"]["mermaid"]
        assert "agpool_rg_net_appgw_one_institution_portal" in mermaid, mermaid
        assert "rg_app_production_shared_uksouth" in mermaid, mermaid
        assert any(
            line.startswith("    agpool_rg_net_appgw_one_institution_portal -->")
            and "rg_app_production_shared_uksouth" in line
            for line in mermaid.splitlines()
        ), mermaid

    def test_subscription_diagram_renders_apim_backend_pool_chain(self, monkeypatch):
        """APIM routes should render as APIM → backend pool → destination service."""
        import json
        import os
        import sqlite3
        import sys
        import tempfile

        sys.path.insert(0, str(REPO_ROOT))
        os.environ.setdefault("FLASK_APP", "web/app.py")
        import web.app as app_module

        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp.close()
        conn = sqlite3.connect(tmp.name)
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
                resource_group TEXT,
                name TEXT,
                type TEXT,
                location TEXT,
                sku TEXT,
                fqdn TEXT,
                is_public INTEGER DEFAULT 0,
                status TEXT,
                pipeline_tag TEXT,
                first_detected TEXT,
                last_synced TEXT,
                raw_json TEXT,
                is_restricted INTEGER DEFAULT 0,
                waf_mode TEXT
            );
            CREATE TABLE apim_backends (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                apim_name TEXT,
                backend_id TEXT,
                title TEXT,
                description TEXT,
                url TEXT,
                protocol TEXT,
                circuit_breaker TEXT,
                credentials TEXT,
                tls_validate_cert INTEGER,
                last_synced TEXT
            );
            CREATE TABLE apim_api_routes (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                apim_name TEXT,
                apim_resource_id TEXT,
                api_name TEXT,
                api_display_name TEXT,
                api_path TEXT,
                api_protocols TEXT,
                backend_id TEXT,
                backend_url TEXT,
                service_url TEXT,
                requires_subscription INTEGER,
                gateway_hosts TEXT,
                exposure_level TEXT,
                policy_summary TEXT,
                sf_service_instance_name TEXT,
                sf_resolve_condition TEXT,
                last_synced TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state, last_synced) VALUES (?, ?, ?, ?, ?)",
            ("sub-1", "Test Subscription", "production", "Enabled", "2026-06-01T00:00:00Z"),
        )
        conn.executemany(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, resource_group, name, type, location, sku, fqdn,
                is_public, status, pipeline_tag, first_detected, last_synced, raw_json,
                is_restricted, waf_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "/subscriptions/sub-1/resourceGroups/rg-api/providers/Microsoft.ApiManagement/service/production-api-uksouth",
                    "sub-1",
                    "rg-api",
                    "production-api-uksouth",
                    "Microsoft.ApiManagement/service",
                    "uksouth",
                    "Developer",
                    "production-api-uksouth.azure-api.net",
                    1,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps({"properties": {}}),
                    0,
                    None,
                ),
                (
                    "/subscriptions/sub-1/resourceGroups/rg-backend/providers/Microsoft.Web/sites/production-internal-transfers-fn-uksouth",
                    "sub-1",
                    "rg-backend",
                    "production-internal-transfers-fn-uksouth",
                    "Microsoft.Web/sites",
                    "uksouth",
                    "Y1",
                    "production-internal-transfers-fn-uksouth.production-shared-uksouth.appserviceenvironment.net",
                    0,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps({"kind": "functionapp"}),
                    0,
                    None,
                ),
            ],
        )
        conn.execute(
            """
            INSERT INTO apim_backends (
                id, subscription_id, apim_name, backend_id, title, description, url,
                protocol, circuit_breaker, credentials, tls_validate_cert, last_synced
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "production-api-uksouth::internal-transfers",
                "sub-1",
                "production-api-uksouth",
                "internal-transfers",
                "internal-transfers",
                None,
                "https://production-internal-transfers-fn-uksouth.production-shared-uksouth.appserviceenvironment.net",
                "http",
                None,
                None,
                1,
                "2026-06-01T00:00:00Z",
            ),
        )
        conn.execute(
            """
            INSERT INTO apim_api_routes (
                id, subscription_id, apim_name, apim_resource_id, api_name, api_display_name,
                api_path, api_protocols, backend_id, backend_url, service_url,
                requires_subscription, gateway_hosts, exposure_level, policy_summary,
                sf_service_instance_name, sf_resolve_condition, last_synced
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "production-api-uksouth::internal-transfers",
                "sub-1",
                "production-api-uksouth",
                "/subscriptions/sub-1/resourceGroups/rg-api/providers/Microsoft.ApiManagement/service/production-api-uksouth",
                "internal-transfers",
                "Internal Transfers",
                "/internal-transfers",
                json.dumps(["HTTPS"]),
                "internal-transfers",
                "https://production-internal-transfers-fn-uksouth.production-shared-uksouth.appserviceenvironment.net",
                "https://production-api-uksouth.azure-api.net",
                1,
                json.dumps([]),
                "Public",
                None,
                None,
                None,
                "2026-06-01T00:00:00Z",
            ),
        )
        conn.commit()

        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
        monkeypatch.setattr(app_module, "_SUBSCRIPTION_DIAGRAM_CACHE", {})
        client = app_module.app.test_client()
        resp = client.get("/api/subscriptions/sub-1/diagram")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        mermaid = resp.get_json()["ingress_diagram"]["mermaid"]
        assert "rg_api_production_api_uksouth__internal_transfers" in mermaid, mermaid
        assert "class rg_api_production_api_uksouth__internal_transfers apimBackendPool;" in mermaid, mermaid
        assert "internal-transfers-fn 🔒" not in mermaid, mermaid
        assert "internal-transfers-fn</div>" in mermaid, mermaid
        assert any(
            "grp_APIM_Public -->" in line and "internal_transfers" in line
            for line in mermaid.splitlines()
        ), mermaid
        assert any(
            "internal_transfers" in line and "production_internal_transfers_fn_uksouth" in line
            for line in mermaid.splitlines()
        ), mermaid
        os.unlink(tmp.name)

    def test_subscription_diagram_does_not_render_self_routes(self):
        """A routing target that resolves to the same node must not draw a self-loop."""
        import json
        import os
        import sys

        sys.path.insert(0, str(REPO_ROOT))
        os.environ.setdefault("FLASK_APP", "web/app.py")
        import web.app as app_module

        rows = [
            (
                "prodgreen-fincrime-casemanagementorchestrator-api",
                "APIM backend target",
                "rg-api",
                "prodgreen-fincrime-casemanagementorchestrator-api.internal.cbinnovation.uk",
                0,
                "Standard",
                "backend-id",
                0,
                None,
                0,
                None,
                json.dumps([
                    {
                        "target": "prodgreen-fincrime-casemanagementorchestrator-api.internal.cbinnovation.uk",
                        "name": "prodgreen-fincrime-casemanagementorchestrator-api.internal.cbinnovation.uk",
                    }
                ]),
                json.dumps({"properties": {}}),
                None,
                None,
            ),
        ]

        result = app_module._build_ingress_diagram(rows)
        mermaid = result.get("mermaid", "")
        assert "routes to" not in mermaid, mermaid

    def test_subscription_diagram_renders_apim_backend_pool_chain_with_private_ase_alias(self, monkeypatch):
        """APIM backend pools should still connect when the backend URL is a private ASE alias."""
        import json
        import os
        import sqlite3
        import sys
        import tempfile

        sys.path.insert(0, str(REPO_ROOT))
        os.environ.setdefault("FLASK_APP", "web/app.py")
        import web.app as app_module

        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp.close()
        conn = sqlite3.connect(tmp.name)
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
                resource_group TEXT,
                name TEXT,
                type TEXT,
                location TEXT,
                sku TEXT,
                fqdn TEXT,
                is_public INTEGER DEFAULT 0,
                status TEXT,
                pipeline_tag TEXT,
                first_detected TEXT,
                last_synced TEXT,
                raw_json TEXT,
                is_restricted INTEGER DEFAULT 0,
                waf_mode TEXT
            );
            CREATE TABLE apim_backends (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                apim_name TEXT,
                backend_id TEXT,
                title TEXT,
                description TEXT,
                url TEXT,
                protocol TEXT,
                circuit_breaker TEXT,
                credentials TEXT,
                tls_validate_cert INTEGER,
                last_synced TEXT
            );
            CREATE TABLE apim_api_routes (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                apim_name TEXT,
                apim_resource_id TEXT,
                api_name TEXT,
                api_display_name TEXT,
                api_path TEXT,
                api_protocols TEXT,
                backend_id TEXT,
                backend_url TEXT,
                service_url TEXT,
                requires_subscription INTEGER,
                gateway_hosts TEXT,
                exposure_level TEXT,
                policy_summary TEXT,
                sf_service_instance_name TEXT,
                sf_resolve_condition TEXT,
                last_synced TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state, last_synced) VALUES (?, ?, ?, ?, ?)",
            ("sub-1", "Test Subscription", "production", "Enabled", "2026-06-01T00:00:00Z"),
        )
        apim_id = "/subscriptions/sub-1/resourceGroups/rg-api/providers/Microsoft.ApiManagement/service/production-api-uksouth"
        backend_site_id = "/subscriptions/sub-1/resourceGroups/rg-backend/providers/Microsoft.Web/sites/transactionnotifications"
        conn.executemany(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, resource_group, name, type, location, sku,
                fqdn, is_public, status, pipeline_tag, first_detected, last_synced,
                raw_json, is_restricted, waf_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    apim_id,
                    "sub-1",
                    "rg-api",
                    "production-api-uksouth",
                    "Microsoft.ApiManagement/service",
                    "uksouth",
                    "Developer",
                    "production-api-uksouth.azure-api.net",
                    1,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps({"properties": {}}),
                    0,
                    None,
                ),
                (
                    backend_site_id,
                    "sub-1",
                    "rg-backend",
                    "transactionnotifications",
                    "Microsoft.Web/sites",
                    "uksouth",
                    "P1v3",
                    "transactionnotifications.azurewebsites.net",
                    0,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps({"kind": "app"}),
                    0,
                    None,
                ),
            ],
        )
        conn.execute(
            """
            INSERT INTO apim_backends (
                id, subscription_id, apim_name, backend_id, title, description, url,
                protocol, circuit_breaker, credentials, tls_validate_cert, last_synced
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "production-api-uksouth::transactionnotifications",
                "sub-1",
                "production-api-uksouth",
                "transactionnotifications",
                "transactionnotifications",
                None,
                "https://production-transaction-notifications-f-uksouth.production-shared-uksouth.appserviceenvironment.net",
                "http",
                None,
                None,
                1,
                "2026-06-01T00:00:00Z",
            ),
        )
        conn.execute(
            """
            INSERT INTO apim_api_routes (
                id, subscription_id, apim_name, apim_resource_id, api_name, api_display_name,
                api_path, api_protocols, backend_id, backend_url, service_url,
                requires_subscription, gateway_hosts, exposure_level, policy_summary,
                sf_service_instance_name, sf_resolve_condition, last_synced
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "production-api-uksouth::transactionnotifications",
                "sub-1",
                "production-api-uksouth",
                apim_id,
                "transactionnotifications",
                "transactionnotifications",
                "/transactionnotifications",
                json.dumps(["HTTPS"]),
                "transactionnotifications",
                "https://production-transaction-notifications-f-uksouth.production-shared-uksouth.appserviceenvironment.net",
                "https://production-api-uksouth.azure-api.net",
                1,
                json.dumps([]),
                "Internal",
                None,
                None,
                None,
                "2026-06-01T00:00:00Z",
            ),
        )
        conn.commit()

        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
        monkeypatch.setattr(app_module, "_SUBSCRIPTION_DIAGRAM_CACHE", {})
        client = app_module.app.test_client()
        resp = client.get("/api/subscriptions/sub-1/diagram")
        try:
            assert resp.status_code == 200, resp.get_data(as_text=True)
            mermaid = resp.get_json()["ingress_diagram"]["mermaid"]
        finally:
            conn.close()
            os.unlink(tmp.name)

        assert any(
            "grp_APIM_Public -->" in line and "transactionnotifications" in line
            for line in mermaid.splitlines()
        ), mermaid
        assert any(
            "transactionnotifications" in line
            and "rg_backend_transactionnotifications" in line
            for line in mermaid.splitlines()
        ), mermaid

    def test_subscription_diagram_renders_servicebus_trigger_edge_to_function_app(self, monkeypatch):
        import json
        import os
        import sqlite3
        import sys
        import tempfile

        sys.path.insert(0, str(REPO_ROOT))
        os.environ.setdefault("FLASK_APP", "web/app.py")
        import web.app as app_module

        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp.close()
        conn = sqlite3.connect(tmp.name)
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
                resource_group TEXT,
                name TEXT,
                type TEXT,
                location TEXT,
                sku TEXT,
                fqdn TEXT,
                is_public INTEGER DEFAULT 0,
                status TEXT,
                pipeline_tag TEXT,
                first_detected TEXT,
                last_synced TEXT,
                raw_json TEXT,
                is_restricted INTEGER DEFAULT 0,
                waf_mode TEXT
            );
            CREATE TABLE function_app_servicebus_triggers (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                function_app_id TEXT,
                function_app_name TEXT,
                resource_group TEXT,
                function_name TEXT,
                trigger_type TEXT,
                entity_type TEXT,
                entity_name TEXT,
                subscription_name TEXT,
                connection TEXT,
                last_synced TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state, last_synced) VALUES (?, ?, ?, ?, ?)",
            ("sub-1", "Test Subscription", "production", "Enabled", "2026-06-01T00:00:00Z"),
        )
        topic_name = "mydomain.service.events.payments.servicedirectcreditrecalledevent"
        fn_name = "production-service-dcr-webhook-uksouth"
        ns_name = "production-servicebus-uksouth"
        ns_id = "/subscriptions/sub-1/resourceGroups/rg-msg/providers/Microsoft.ServiceBus/namespaces/" + ns_name
        topic_id = "/subscriptions/sub-1/resourceGroups/rg-msg/providers/Microsoft.ServiceBus/namespaces/production-servicebus-uksouth/topics/" + topic_name
        fn_id = "/subscriptions/sub-1/resourceGroups/rg-fn/providers/Microsoft.Web/sites/" + fn_name
        conn.executemany(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, resource_group, name, type, location, sku,
                fqdn, is_public, status, pipeline_tag, first_detected, last_synced,
                raw_json, is_restricted, waf_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    ns_id,
                    "sub-1",
                    "rg-msg",
                    ns_name,
                    "Microsoft.ServiceBus/namespaces",
                    "uksouth",
                    "Standard",
                    "production-servicebus-uksouth.servicebus.windows.net",
                    0,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps({}),
                    0,
                    None,
                ),
                (
                    topic_id,
                    "sub-1",
                    "rg-msg",
                    topic_name,
                    "Microsoft.ServiceBus/namespaces/topics",
                    "uksouth",
                    "Standard",
                    None,
                    0,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps({}),
                    0,
                    None,
                ),
                (
                    fn_id,
                    "sub-1",
                    "rg-fn",
                    fn_name,
                    "Microsoft.Web/sites",
                    "uksouth",
                    "Y1",
                    fn_name + ".production-shared-uksouth.appserviceenvironment.net",
                    0,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps({"kind": "functionapp"}),
                    0,
                    None,
                ),
            ],
        )
        conn.execute(
            """
            INSERT INTO function_app_servicebus_triggers (
                id, subscription_id, function_app_id, function_app_name, resource_group,
                function_name, trigger_type, entity_type, entity_name, subscription_name,
                connection, last_synced
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fn_id + "::DirectCreditRecalled::servicebus::topic::" + topic_name,
                "sub-1",
                fn_id,
                fn_name,
                "rg-fn",
                "DirectCreditRecalled",
                "servicebustrigger",
                "topic",
                topic_name,
                "service_direct_credit_recalled_event_topic_subscription",
                "ServiceBusConnection",
                "2026-06-01T00:00:00Z",
            ),
        )
        conn.commit()

        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
        monkeypatch.setattr(app_module, "_SUBSCRIPTION_DIAGRAM_CACHE", {})
        client = app_module.app.test_client()
        resp = client.get("/api/subscriptions/sub-1/diagram")
        try:
            assert resp.status_code == 200, resp.get_data(as_text=True)
            mermaid = resp.get_json()["ingress_diagram"]["mermaid"]
        finally:
            conn.close()
            os.unlink(tmp.name)

        ns_nid = app_module._sanitise_node_id("rg-msg_" + ns_name)
        fn_nid = app_module._sanitise_node_id("rg-fn_" + fn_name)
        assert any(
            ns_nid in line and fn_nid in line and "AMQP" in line
            for line in mermaid.splitlines()
        ), mermaid

    def test_appgw_pool_resolves_to_site_not_plan_when_fqdn_unresolvable(self, monkeypatch):
        """When an App Gateway pool FQDN doesn't match the site's registered FQDN,
        the pool-name fallback must route to the App Service site node (not the
        App Service Plan).  The site→plan 'Hosts' edge is already drawn separately,
        so landing on the plan skips the site entirely."""
        import json
        import sqlite3

        import web.app as app_module

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
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
                    fqdn TEXT,
                    is_public INTEGER DEFAULT 0,
                    status TEXT,
                    pipeline_tag TEXT,
                    first_detected TEXT,
                    last_synced TEXT,
                    raw_json TEXT,
                    is_restricted INTEGER DEFAULT 0,
                    waf_mode TEXT
                );
                CREATE TABLE appgw_routing_rules (
                    id TEXT PRIMARY KEY,
                    subscription_id TEXT,
                    gateway_name TEXT,
                    backend_fqdns TEXT,
                    backend_pool_name TEXT,
                    listener_name TEXT,
                    url_path TEXT,
                    protocol TEXT,
                    waf_policy_name TEXT,
                    resource_group TEXT,
                    hostname TEXT
                );
                """
            )
            conn.execute(
                "INSERT INTO subscriptions (id, display_name, environment, state, last_synced) VALUES (?, ?, ?, ?, ?)",
                ("sub-1", "Test Subscription", "production", "Enabled", "2026-06-01T00:00:00Z"),
            )
            gw_id = "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/applicationGateways/appgw-one"
            plan_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/serverfarms/my-service-plan"
            site_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/sites/my-app"
            conn.executemany(
                """
                INSERT INTO provisioned_assets (
                    id, subscription_id, resource_group, name, type, location, sku,
                    fqdn, is_public, status, pipeline_tag, first_detected, last_synced,
                    raw_json, is_restricted, waf_mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        gw_id,
                        "sub-1",
                        "rg-net",
                        "appgw-one",
                        "Microsoft.Network/applicationGateways",
                        "uksouth",
                        "WAF_v2",
                        "appgw-one.example.com",
                        1,
                        "active",
                        None,
                        "2026-06-01T00:00:00Z",
                        "2026-06-01T00:00:00Z",
                        json.dumps({}),
                        0,
                        "WAF_v2",
                    ),
                    (
                        plan_id,
                        "sub-1",
                        "rg-app",
                        "my-service-plan",
                        "Microsoft.Web/serverfarms",
                        "uksouth",
                        "P1v3",
                        None,
                        0,
                        "active",
                        None,
                        "2026-06-01T00:00:00Z",
                        "2026-06-01T00:00:00Z",
                        json.dumps({}),
                        0,
                        None,
                    ),
                    (
                        site_id,
                        "sub-1",
                        "rg-app",
                        "my-app",
                        "Microsoft.Web/sites",
                        "uksouth",
                        "P1v3",
                        # The site's registered FQDN is azurewebsites.net, but the
                        # App Gateway uses a private/custom FQDN that won't match.
                        "my-app.azurewebsites.net",
                        0,
                        "active",
                        None,
                        "2026-06-01T00:00:00Z",
                        "2026-06-01T00:00:00Z",
                        json.dumps({"serverFarmId": plan_id}),
                        0,
                        None,
                    ),
                ],
            )
            conn.execute(
                """
                INSERT INTO appgw_routing_rules (
                    id, subscription_id, gateway_name, backend_fqdns, backend_pool_name,
                    listener_name, url_path, protocol, waf_policy_name, resource_group, hostname
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "appgw-one::route-plan",
                    "sub-1",
                    "appgw-one",
                    # Private FQDN that won't match the site's azurewebsites.net FQDN
                    json.dumps(["10.0.1.5"]),
                    # Pool name matches the site name — pool-name fallback should fire
                    "my-app",
                    "listener-one",
                    "/*",
                    "HTTPS",
                    None,
                    "rg-net",
                    "appgw-one.example.com",
                ),
            )
            conn.commit()

            monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
            monkeypatch.setattr(app_module, "_SUBSCRIPTION_DIAGRAM_CACHE", {})
            client = app_module.app.test_client()
            resp = client.get("/api/subscriptions/sub-1/diagram")
        finally:
            conn.close()

        assert resp.status_code == 200, resp.get_data(as_text=True)
        mermaid = resp.get_json()["ingress_diagram"]["mermaid"]
        site_nid = "rg_app_my_app"
        plan_nid = "rg_app_my_service_plan"
        # The pool backend-pool node must exist
        assert "agpool_rg_net_appgw_one_my_app" in mermaid, mermaid
        # Arrow must go from the gateway to the SITE (not to the plan)
        assert any(
            "rg_net_appgw_one" in line and "-->" in line and site_nid in line
            for line in mermaid.splitlines()
        ), f"Expected gateway→site arrow, got:\n{mermaid}"
        # Arrow must NOT go directly from the gateway to the plan
        assert not any(
            "rg_net_appgw_one" in line and "-->" in line and plan_nid in line
            for line in mermaid.splitlines()
        ), f"AppGW arrow went to plan (should go to site):\n{mermaid}"

    def test_appgw_shared_backend_pool_renders_one_main_arrow_per_pool(self, monkeypatch):
        import json
        import sqlite3

        import web.app as app_module

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
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
                    fqdn TEXT,
                    is_public INTEGER DEFAULT 0,
                    status TEXT,
                    pipeline_tag TEXT,
                    first_detected TEXT,
                    last_synced TEXT,
                    raw_json TEXT,
                    is_restricted INTEGER DEFAULT 0,
                    waf_mode TEXT
                );
                CREATE TABLE appgw_routing_rules (
                    id TEXT PRIMARY KEY,
                    subscription_id TEXT,
                    gateway_name TEXT,
                    backend_fqdns TEXT,
                    backend_pool_name TEXT,
                    listener_name TEXT,
                    hostname TEXT,
                    url_path TEXT,
                    protocol TEXT,
                    waf_policy_name TEXT,
                    resource_group TEXT
                );
                """
            )
            conn.execute(
                "INSERT INTO subscriptions (id, display_name, environment, state, last_synced) VALUES (?, ?, ?, ?, ?)",
                ("sub-1", "Test Subscription", "production", "Enabled", "2026-06-01T00:00:00Z"),
            )
            gw_id = "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/applicationGateways/AppGatewayShared"
            conn.execute(
                """
                INSERT INTO provisioned_assets (
                    id, subscription_id, resource_group, name, type, location, sku,
                    fqdn, is_public, status, pipeline_tag, first_detected, last_synced,
                    raw_json, is_restricted, waf_mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    gw_id,
                    "sub-1",
                    "rg-net",
                    "AppGatewayShared",
                    "Microsoft.Network/applicationGateways",
                    "eastus",
                    "WAF_v2",
                    "appgatewayshared.example.com",
                    1,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps({}),
                    0,
                    "WAF_v2",
                ),
            )
            route_rows = [
                ("route-1", "listener-a", "listener-a.example.com", "HTTPS", "transactionnotification", json.dumps(["transactionnotification.contoso.internal"])),
                ("route-2", "listener-a", "listener-a.example.com", "HTTP", "transactionnotification", json.dumps(["transactionnotification.contoso.internal"])),
                ("route-3", "listener-b", "listener-b.example.com", "HTTPS", "transactionnotification", json.dumps(["transactionnotification.contoso.internal"])),
                ("route-4", "listener-b", "listener-b.example.com", "HTTP", "transactionnotification", json.dumps(["transactionnotification.contoso.internal"])),
            ]
            conn.executemany(
                """
                INSERT INTO appgw_routing_rules (
                    id, subscription_id, gateway_name, backend_fqdns, backend_pool_name,
                    listener_name, hostname, url_path, protocol, waf_policy_name, resource_group
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        row_id,
                        "sub-1",
                        "AppGatewayShared",
                        backend_fqdns,
                        pool_name,
                        listener_name,
                        hostname,
                        "/*",
                        protocol,
                        None,
                        "rg-net",
                    )
                    for row_id, listener_name, hostname, protocol, pool_name, backend_fqdns in route_rows
                ],
            )
            conn.commit()

            monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
            client = app_module.app.test_client()
            resp = client.get("/api/subscriptions/sub-1/diagram")
        finally:
            conn.close()

        assert resp.status_code == 200, resp.get_data(as_text=True)
        mermaid = resp.get_json()["ingress_diagram"]["mermaid"]
        gw_nid = app_module._sanitise_node_id("rg-net_AppGatewayShared")
        pool_nid = app_module._sanitise_node_id(f"agpool_{gw_nid}_transactionnotification")
        pool_edges = [
            line for line in mermaid.splitlines()
            if line.startswith(f"    {gw_nid} -->") and pool_nid in line
        ]
        assert len(pool_edges) == 1, mermaid

    def test_api_cloud_architecture_connectivity_renders_apim_backend_pool_chain(self, monkeypatch):
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
                resource_group TEXT,
                name TEXT,
                type TEXT,
                location TEXT,
                sku TEXT,
                fqdn TEXT,
                is_public INTEGER DEFAULT 0,
                status TEXT,
                pipeline_tag TEXT,
                first_detected TEXT,
                last_synced TEXT,
                raw_json TEXT,
                is_restricted INTEGER DEFAULT 0,
                waf_mode TEXT
            );
            CREATE TABLE apim_backends (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                apim_name TEXT,
                backend_id TEXT,
                title TEXT,
                description TEXT,
                url TEXT,
                protocol TEXT,
                circuit_breaker TEXT,
                credentials TEXT,
                tls_validate_cert INTEGER,
                last_synced TEXT
            );
            CREATE TABLE apim_api_routes (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                apim_name TEXT,
                apim_resource_id TEXT,
                api_name TEXT,
                api_display_name TEXT,
                api_path TEXT,
                api_protocols TEXT,
                backend_id TEXT,
                backend_url TEXT,
                service_url TEXT,
                requires_subscription INTEGER,
                gateway_hosts TEXT,
                exposure_level TEXT,
                policy_summary TEXT,
                sf_service_instance_name TEXT,
                sf_resolve_condition TEXT,
                last_synced TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state, last_synced) VALUES (?, ?, ?, ?, ?)",
            ("sub-1", "Test Subscription", "production", "Enabled", "2026-06-01T00:00:00Z"),
        )
        apim_id = "/subscriptions/sub-1/resourceGroups/rg-api/providers/Microsoft.ApiManagement/service/production-api-uksouth"
        backend_site_id = "/subscriptions/sub-1/resourceGroups/rg-backend/providers/Microsoft.Web/sites/transactionnotifications"
        conn.executemany(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, resource_group, name, type, location, sku,
                fqdn, is_public, status, pipeline_tag, first_detected, last_synced,
                raw_json, is_restricted, waf_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    apim_id,
                    "sub-1",
                    "rg-api",
                    "production-api-uksouth",
                    "Microsoft.ApiManagement/service",
                    "uksouth",
                    "Developer",
                    "production-api-uksouth.azure-api.net",
                    1,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps({"properties": {}}),
                    0,
                    None,
                ),
                (
                    backend_site_id,
                    "sub-1",
                    "rg-backend",
                    "transactionnotifications",
                    "Microsoft.Web/sites",
                    "uksouth",
                    "P1v3",
                    "transactionnotifications.azurewebsites.net",
                    0,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps({"kind": "app"}),
                    0,
                    None,
                ),
            ],
        )
        conn.execute(
            """
            INSERT INTO apim_backends (
                id, subscription_id, apim_name, backend_id, title, description, url,
                protocol, circuit_breaker, credentials, tls_validate_cert, last_synced
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "production-api-uksouth::transactionnotifications",
                "sub-1",
                "production-api-uksouth",
                "transactionnotifications",
                "transactionnotifications",
                None,
                "https://production-transaction-notifications-f-uksouth.production-shared-uksouth.appserviceenvironment.net",
                "http",
                None,
                None,
                1,
                "2026-06-01T00:00:00Z",
            ),
        )
        conn.execute(
            """
            INSERT INTO apim_api_routes (
                id, subscription_id, apim_name, apim_resource_id, api_name, api_display_name,
                api_path, api_protocols, backend_id, backend_url, service_url,
                requires_subscription, gateway_hosts, exposure_level, policy_summary,
                sf_service_instance_name, sf_resolve_condition, last_synced
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "production-api-uksouth::transactionnotifications",
                "sub-1",
                "production-api-uksouth",
                apim_id,
                "transactionnotifications",
                "transactionnotifications",
                "/transactionnotifications",
                json.dumps(["HTTPS"]),
                "transactionnotifications",
                "https://production-transaction-notifications-f-uksouth.production-shared-uksouth.appserviceenvironment.net",
                "https://production-api-uksouth.azure-api.net",
                1,
                json.dumps([]),
                "Internal",
                None,
                None,
                None,
                "2026-06-01T00:00:00Z",
            ),
        )
        conn.commit()

        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
        client = app_module.app.test_client()
        resp = client.get("/api/cloud/architecture?sub=sub-1&view=connectivity")
        try:
            assert resp.status_code == 200, resp.get_data(as_text=True)
            mermaid = resp.get_json()["views"]["connectivity"]["mermaid"]
        finally:
            conn.close()

        assert "class rg_api_production_api_uksouth__transactionnotifications apimBackendPool;" in mermaid, mermaid
        assert any(
            "rg_api_production_api_uksouth__transactionnotifications -->" in line
            and "rg_backend_transactionnotifications" in line
            for line in mermaid.splitlines()
        ), mermaid

    def test_subscription_architecture_payload_keeps_appgw_pool_edge_when_multiple_pools_share_ase_target(self, monkeypatch):
        import json
        import sqlite3

        import web.app as app_module

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
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
                    fqdn TEXT,
                    is_public INTEGER DEFAULT 0,
                    status TEXT,
                    pipeline_tag TEXT,
                    first_detected TEXT,
                    last_synced TEXT,
                    raw_json TEXT,
                    is_restricted INTEGER DEFAULT 0,
                    waf_mode TEXT
                );
                CREATE TABLE appgw_routing_rules (
                    id TEXT PRIMARY KEY,
                    subscription_id TEXT,
                    gateway_name TEXT,
                    backend_fqdns TEXT,
                    backend_pool_name TEXT,
                    listener_name TEXT,
                    url_path TEXT,
                    protocol TEXT,
                    waf_policy_name TEXT,
                    resource_group TEXT,
                    hostname TEXT
                );
                """
            )
            conn.execute(
                "INSERT INTO subscriptions (id, display_name, environment, state, last_synced) VALUES (?, ?, ?, ?, ?)",
                ("sub-1", "Test Subscription", "production", "Enabled", "2026-06-01T00:00:00Z"),
            )
            gw_id = "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/applicationGateways/appgw-one"
            ase_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/hostingEnvironments/production-shared-uksouth"
            conn.executemany(
                """
                INSERT INTO provisioned_assets (
                    id, subscription_id, resource_group, name, type, location, sku,
                    fqdn, is_public, status, pipeline_tag, first_detected, last_synced,
                    raw_json, is_restricted, waf_mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        gw_id,
                        "sub-1",
                        "rg-net",
                        "appgw-one",
                        "Microsoft.Network/applicationGateways",
                        "uksouth",
                        "WAF_v2",
                        "appgw-one.example.com",
                        1,
                        "active",
                        None,
                        "2026-06-01T00:00:00Z",
                        "2026-06-01T00:00:00Z",
                        json.dumps({}),
                        0,
                        "WAF_v2",
                    ),
                    (
                        ase_id,
                        "sub-1",
                        "rg-app",
                        "production-shared-uksouth",
                        "Microsoft.Web/hostingEnvironments",
                        "uksouth",
                        "ASEv3",
                        "production-shared-uksouth.appserviceenvironment.net",
                        0,
                        "active",
                        None,
                        "2026-06-01T00:00:00Z",
                        "2026-06-01T00:00:00Z",
                        json.dumps({"kind": "ASEv3"}),
                        0,
                        None,
                    ),
                ],
            )
            conn.executemany(
                """
                INSERT INTO appgw_routing_rules (
                    id, subscription_id, gateway_name, backend_fqdns, backend_pool_name,
                    listener_name, url_path, protocol, waf_policy_name, resource_group, hostname
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        "appgw-one::route-fiapi",
                        "sub-1",
                        "appgw-one",
                        json.dumps([
                            "production-fi-api-uksouth.production-shared-uksouth.appserviceenvironment.net",
                        ]),
                        "fiapi",
                        "listener-one",
                        "/*",
                        "HTTPS",
                        None,
                        "rg-net",
                        "appgw-one.example.com",
                    ),
                    (
                        "appgw-one::route-ase",
                        "sub-1",
                        "appgw-one",
                        json.dumps([
                            "production-institution-portal-uksouth.production-shared-uksouth.appserviceenvironment.net",
                        ]),
                        "institution-portal",
                        "listener-two",
                        "/*",
                        "HTTPS",
                        None,
                        "rg-net",
                        "appgw-one.example.com",
                    ),
                ],
            )
            conn.commit()

            monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
            monkeypatch.setattr(app_module, "_SUBSCRIPTION_DIAGRAM_CACHE", {})
            client = app_module.app.test_client()
            resp = client.get("/api/subscriptions/sub-1/diagram")
        finally:
            conn.close()

        assert resp.status_code == 200, resp.get_data(as_text=True)
        mermaid = resp.get_json()["ingress_diagram"]["mermaid"]
        assert any(
            line.startswith("    agpool_rg_net_appgw_one_institution_portal -->")
            and "rg_app_production_shared_uksouth" in line
            for line in mermaid.splitlines()
        ), mermaid
        assert any(
            line.startswith("    agpool_rg_net_appgw_one_fiapi -->")
            and "rg_app_production_shared_uksouth" in line
            for line in mermaid.splitlines()
        ), mermaid

    def test_routing_target_resolver_matches_confirmed_app_fqdn_from_ase_host(self):
        import web.app as app_module

        node_id = app_module._resolve_routing_target_node_id(
            {
                "target": "production-fi-api-uksouth.production-shared-uksouth.appserviceenvironment.net",
                "fqdn": "production-fi-api-uksouth.production-shared-uksouth.appserviceenvironment.net",
                "name": "production-fi-api-uksouth.azurewebsites.net",
            },
            node_by_fqdn={"production-fi-api-uksouth.azurewebsites.net": "node-fi-api"},
        )

        assert node_id == "node-fi-api"

    def test_subscription_architecture_payload_links_nested_ase_siteconfig_parent(self):
        import json
        import sqlite3

        from web.app import _build_subscription_architecture_payload

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
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
                    fqdn TEXT,
                    is_public INTEGER DEFAULT 0,
                    status TEXT,
                    pipeline_tag TEXT,
                    first_detected TEXT,
                    last_synced TEXT,
                    raw_json TEXT,
                    is_restricted INTEGER DEFAULT 0,
                    waf_mode TEXT
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
                CREATE TABLE function_app_servicebus_triggers (
                    id TEXT PRIMARY KEY,
                    subscription_id TEXT NOT NULL,
                    function_app_id TEXT NOT NULL,
                    function_app_name TEXT NOT NULL,
                    resource_group TEXT,
                    function_name TEXT NOT NULL,
                    trigger_type TEXT,
                    entity_type TEXT,
                    entity_name TEXT,
                    subscription_name TEXT,
                    connection TEXT,
                    last_synced TEXT
                );
                """
            )
            conn.execute(
                "INSERT INTO subscriptions (id, display_name, environment, state, last_synced) VALUES (?, ?, ?, ?, ?)",
                ("sub-1", "Test Subscription", "production", "Enabled", "2026-06-01T00:00:00Z"),
            )
            ase_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/hostingEnvironments/production-shared-uksouth"
            site_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/sites/institution-portal"
            conn.executemany(
                """
                INSERT INTO provisioned_assets (
                    id, subscription_id, resource_group, name, type, location, sku,
                    fqdn, is_public, status, pipeline_tag, first_detected, last_synced,
                    raw_json, is_restricted, waf_mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        ase_id,
                        "sub-1",
                        "rg-app",
                        "production-shared-uksouth",
                        "Microsoft.Web/hostingEnvironments",
                        "uksouth",
                        "ASEv3",
                        "production-shared-uksouth.appserviceenvironment.net",
                        0,
                        "active",
                        None,
                        "2026-06-01T00:00:00Z",
                        "2026-06-01T00:00:00Z",
                        json.dumps({"kind": "ASEv3"}),
                        0,
                        None,
                    ),
                    (
                        site_id,
                        "sub-1",
                        "rg-app",
                        "institution-portal",
                        "Microsoft.Web/sites",
                        "uksouth",
                        "P1v3",
                        "institution-portal.azurewebsites.net",
                        1,
                        "active",
                        None,
                        "2026-06-01T00:00:00Z",
                        "2026-06-01T00:00:00Z",
                        json.dumps({"siteConfig": {"hostingEnvironmentProfile": {"id": ase_id}}}),
                        0,
                        None,
                    ),
                ],
            )

            payload = _build_subscription_architecture_payload(conn, "sub-1", view_mode="full")
        finally:
            conn.close()

        edges = payload["edges"]
        assert any(e["source"] == ase_id and e["target"] == site_id for e in edges), edges
        assert any(e["source"] == site_id and e["target"] == ase_id for e in edges), edges

    def test_subscription_architecture_payload_infers_service_fabric_network_from_vmss_children(self):
        import json
        import os
        import sqlite3
        import sys

        from web.app import _build_subscription_architecture_payload

        sys.path.insert(0, str(REPO_ROOT))
        os.environ.setdefault("FLASK_APP", "web/app.py")
        import web.app as app_module
        original_get_db = app_module._get_db_with_schema

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
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
                    fqdn TEXT,
                    is_public INTEGER DEFAULT 0,
                    status TEXT,
                    pipeline_tag TEXT,
                    first_detected TEXT,
                    last_synced TEXT,
                    raw_json TEXT,
                    is_restricted INTEGER DEFAULT 0,
                    waf_mode TEXT
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
                CREATE TABLE function_app_servicebus_triggers (
                    id TEXT PRIMARY KEY,
                    subscription_id TEXT NOT NULL,
                    function_app_id TEXT NOT NULL,
                    function_app_name TEXT NOT NULL,
                    resource_group TEXT,
                    function_name TEXT NOT NULL,
                    trigger_type TEXT,
                    entity_type TEXT,
                    entity_name TEXT,
                    subscription_name TEXT,
                    connection TEXT,
                    last_synced TEXT
                );
                """
            )
            conn.execute(
                "INSERT INTO subscriptions (id, display_name, environment, state, last_synced) VALUES (?, ?, ?, ?, ?)",
                ("sub-1", "Test Subscription", "production", "Enabled", "2026-06-01T00:00:00Z"),
            )
            cluster_id = "/subscriptions/sub-1/resourceGroups/production-sf-uksouth/providers/Microsoft.ServiceFabric/clusters/production-sf"
            vmss_id = "/subscriptions/sub-1/resourceGroups/production-sf-uksouth/providers/Microsoft.Compute/virtualMachineScaleSets/sharedz1"
            subnet_id = "/subscriptions/sub-1/resourceGroups/production-network-uksouth/providers/Microsoft.Network/virtualNetworks/production/subnets/service_fabric_zonal"
            conn.executemany(
                """
                INSERT INTO provisioned_assets (
                    id, subscription_id, resource_group, name, type, location, sku,
                    fqdn, is_public, status, pipeline_tag, first_detected, last_synced,
                    raw_json, is_restricted, waf_mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        cluster_id,
                        "sub-1",
                        "production-sf-uksouth",
                        "production-sf",
                        "Microsoft.ServiceFabric/clusters",
                        "uksouth",
                        "Standard",
                        None,
                        0,
                        "active",
                        None,
                        "2026-06-01T00:00:00Z",
                        "2026-06-01T00:00:00Z",
                        json.dumps({"clusterCodeVersion": "11.2.274.1"}),
                        0,
                        None,
                    ),
                    (
                        vmss_id,
                        "sub-1",
                        "production-sf-uksouth",
                        "sharedz1",
                        "Microsoft.Compute/virtualMachineScaleSets",
                        "uksouth",
                        "Standard",
                        None,
                        0,
                        "active",
                        None,
                        "2026-06-01T00:00:00Z",
                        "2026-06-01T00:00:00Z",
                        json.dumps({
                            "properties": {
                                "virtualMachineProfile": {
                                    "networkProfile": {
                                        "networkInterfaceConfigurations": [
                                            {
                                                "properties": {
                                                    "ipConfigurations": [
                                                        {
                                                            "properties": {
                                                                "subnet": {"id": subnet_id}
                                                            }
                                                        }
                                                    ]
                                                }
                                            }
                                        ]
                                    }
                                }
                            }
                        }),
                        0,
                        None,
                    ),
                ],
            )

            payload = _build_subscription_architecture_payload(conn, "sub-1", view_mode="full")
            app_module._get_db_with_schema = lambda: conn
            graph_resp = app_module.app.test_client().get("/api/cloud/architecture?sub=sub-1&view=connectivity")
            assert graph_resp.status_code == 200, graph_resp.get_data(as_text=True)
            graph = graph_resp.get_json()
            mermaid = graph["views"]["connectivity"]["mermaid"]
            cluster_node_id = app_module._sanitise_node_id("production-sf-uksouth_production-sf")
            vmss_node_id = app_module._sanitise_node_id("production-sf-uksouth_sharedz1")
            assert f'{cluster_node_id} -->|"contains"| {vmss_node_id}' in mermaid, mermaid
        finally:
            app_module._get_db_with_schema = original_get_db
            conn.close()

        cluster_node = next(node for node in payload["nodes"] if node["id"] == cluster_id)
        assert cluster_node["data"].get("vnetName") == "production", cluster_node
        assert cluster_node["data"].get("subnetName") == "service_fabric_zonal", cluster_node

    def test_subscription_architecture_payload_links_service_fabric_vmss_across_resource_groups(self):
        import json
        import os
        import sqlite3
        import sys

        from web.app import _build_subscription_architecture_payload

        sys.path.insert(0, str(REPO_ROOT))
        os.environ.setdefault("FLASK_APP", "web/app.py")
        import web.app as app_module
        original_get_db = app_module._get_db_with_schema

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
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
                    fqdn TEXT,
                    is_public INTEGER DEFAULT 0,
                    status TEXT,
                    pipeline_tag TEXT,
                    first_detected TEXT,
                    last_synced TEXT,
                    raw_json TEXT,
                    is_restricted INTEGER DEFAULT 0,
                    waf_mode TEXT
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
                CREATE TABLE function_app_servicebus_triggers (
                    id TEXT PRIMARY KEY,
                    subscription_id TEXT NOT NULL,
                    function_app_id TEXT NOT NULL,
                    function_app_name TEXT NOT NULL,
                    resource_group TEXT,
                    function_name TEXT NOT NULL,
                    trigger_type TEXT,
                    entity_type TEXT,
                    entity_name TEXT,
                    subscription_name TEXT,
                    connection TEXT,
                    last_synced TEXT
                );
                """
            )
            conn.execute(
                "INSERT INTO subscriptions (id, display_name, environment, state, last_synced) VALUES (?, ?, ?, ?, ?)",
                ("sub-1", "Test Subscription", "production", "Enabled", "2026-06-01T00:00:00Z"),
            )
            cluster_id = "/subscriptions/sub-1/resourceGroups/production-sf-uksouth/providers/Microsoft.ServiceFabric/clusters/production-sf"
            vmss_id = "/subscriptions/sub-1/resourceGroups/production-compute-uksouth/providers/Microsoft.Compute/virtualMachineScaleSets/sharedz1"
            subnet_id = "/subscriptions/sub-1/resourceGroups/production-network-uksouth/providers/Microsoft.Network/virtualNetworks/production/subnets/service_fabric_zonal"
            conn.executemany(
                """
                INSERT INTO provisioned_assets (
                    id, subscription_id, resource_group, name, type, location, sku,
                    fqdn, is_public, status, pipeline_tag, first_detected, last_synced,
                    raw_json, is_restricted, waf_mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        cluster_id,
                        "sub-1",
                        "production-sf-uksouth",
                        "production-sf",
                        "Microsoft.ServiceFabric/clusters",
                        "uksouth",
                        "Standard",
                        None,
                        0,
                        "active",
                        None,
                        "2026-06-01T00:00:00Z",
                        "2026-06-01T00:00:00Z",
                        json.dumps({"_extra": {"node_types": [{"name": "sharedz1"}]}}),
                        0,
                        None,
                    ),
                    (
                        vmss_id,
                        "sub-1",
                        "production-compute-uksouth",
                        "sharedz1",
                        "Microsoft.Compute/virtualMachineScaleSets",
                        "uksouth",
                        "Standard",
                        None,
                        0,
                        "active",
                        None,
                        "2026-06-01T00:00:00Z",
                        "2026-06-01T00:00:00Z",
                        json.dumps({
                            "properties": {
                                "virtualMachineProfile": {
                                    "networkProfile": {
                                        "networkInterfaceConfigurations": [
                                            {
                                                "properties": {
                                                    "ipConfigurations": [
                                                        {
                                                            "properties": {
                                                                "subnet": {"id": subnet_id}
                                                            }
                                                        }
                                                    ]
                                                }
                                            }
                                        ]
                                    }
                                }
                            }
                        }),
                        0,
                        None,
                    ),
                ],
            )

            app_module._get_db_with_schema = lambda: conn
            graph_resp = app_module.app.test_client().get("/api/cloud/architecture?sub=sub-1&view=connectivity")
            assert graph_resp.status_code == 200, graph_resp.get_data(as_text=True)
            graph = graph_resp.get_json()
            mermaid = graph["views"]["connectivity"]["mermaid"]
            cluster_node_id = app_module._sanitise_node_id("production-sf-uksouth_production-sf")
            vmss_node_id = app_module._sanitise_node_id("production-compute-uksouth_sharedz1")
            assert f'{cluster_node_id} -->|"contains"| {vmss_node_id}' in mermaid, mermaid
        finally:
            app_module._get_db_with_schema = original_get_db
            conn.close()

    def test_service_fabric_cluster_drilldown_lists_vmss(self):
        import json
        import sqlite3

        from web.app import _build_child_table

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
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
                    fqdn TEXT,
                    is_public INTEGER DEFAULT 0,
                    status TEXT,
                    pipeline_tag TEXT,
                    first_detected TEXT,
                    last_synced TEXT,
                    raw_json TEXT,
                    is_restricted INTEGER DEFAULT 0,
                    waf_mode TEXT
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
                CREATE TABLE function_app_servicebus_triggers (
                    id TEXT PRIMARY KEY,
                    subscription_id TEXT NOT NULL,
                    function_app_id TEXT NOT NULL,
                    function_app_name TEXT NOT NULL,
                    resource_group TEXT,
                    function_name TEXT NOT NULL,
                    trigger_type TEXT,
                    entity_type TEXT,
                    entity_name TEXT,
                    subscription_name TEXT,
                    connection TEXT,
                    last_synced TEXT
                );
                """
            )
            conn.execute(
                "INSERT INTO subscriptions (id, display_name, environment, state, last_synced) VALUES (?, ?, ?, ?, ?)",
                ("sub-1", "Test Subscription", "production", "Enabled", "2026-06-01T00:00:00Z"),
            )
            cluster_id = "/subscriptions/sub-1/resourceGroups/rg-sf/providers/Microsoft.ServiceFabric/clusters/sf-prod"
            vmss_id = "/subscriptions/sub-1/resourceGroups/rg-sf/providers/Microsoft.Compute/virtualMachineScaleSets/sharedz1"
            conn.executemany(
                """
                INSERT INTO provisioned_assets (
                    id, subscription_id, resource_group, name, type, location, sku,
                    fqdn, is_public, status, pipeline_tag, first_detected, last_synced,
                    raw_json, is_restricted, waf_mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        cluster_id,
                        "sub-1",
                        "rg-sf",
                        "sf-prod",
                        "Microsoft.ServiceFabric/clusters",
                        "uksouth",
                        "Standard",
                        None,
                        0,
                        "active",
                        None,
                        "2026-06-01T00:00:00Z",
                        "2026-06-01T00:00:00Z",
                        json.dumps({
                            "_extra": {
                                "node_types": [{"name": "sharedz1"}],
                                "subnet_id": "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/virtualNetworks/production/subnets/service_fabric",
                            }
                        }),
                        0,
                        None,
                    ),
                    (
                        vmss_id,
                        "sub-1",
                        "rg-sf",
                        "sharedz1",
                        "Microsoft.Compute/virtualMachineScaleSets",
                        "uksouth",
                        "Standard",
                        "sharedz1.eastus.cloudapp.azure.com",
                        0,
                        "active",
                        None,
                        "2026-06-01T00:00:00Z",
                        "2026-06-01T00:00:00Z",
                        json.dumps({
                            "sku": {"capacity": 5},
                            "properties": {
                                "virtualMachineProfile": {
                                    "networkProfile": {
                                        "networkInterfaceConfigurations": [
                                            {
                                                "properties": {
                                                    "ipConfigurations": [
                                                        {
                                                            "properties": {
                                                                "subnet": {"id": "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/virtualNetworks/production/subnets/service_fabric"}
                                                            }
                                                        }
                                                    ]
                                                }
                                            }
                                        ]
                                    }
                                }
                            }
                        }),
                        0,
                        None,
                    ),
                ],
            )

            result = _build_child_table(
                conn,
                "sub-1",
                "Microsoft.ServiceFabric/clusters",
                [{"rg": "rg-sf", "name": "sf-prod"}],
            )
        finally:
            conn.close()

        assert result["view_type"] == "table", result
        assert result["title"] == "Service Fabric Cluster — VM Scale Sets", result
        assert result["rows"], result
        assert any(row[1] == "sharedz1" for row in result["rows"]), result["rows"]
        assert all(len(row) == 7 for row in result["rows"]), result["rows"]

    def test_cloud_architecture_page_labels_tabs_mermaid_and_react_flow(self, monkeypatch):
        import os
        import sqlite3
        import sys

        sys.path.insert(0, str(REPO_ROOT))
        os.environ.setdefault("FLASK_APP", "web/app.py")
        import web.app as app_module

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE subscriptions (
                id TEXT PRIMARY KEY,
                display_name TEXT,
                environment TEXT,
                state TEXT,
                last_synced TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state, last_synced) VALUES (?, ?, ?, ?, ?)",
            ("sub-1", "Test Subscription", "production", "Enabled", "2026-06-01T00:00:00Z"),
        )
        conn.commit()
        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)

        client = app_module.app.test_client()
        resp = client.get("/cloud/architecture?sub=sub-1")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'role="combobox"' in html
        assert 'role="listbox"' in html
        assert "Test Subscription (production)" in html
        assert "ingress-diagram-div-target-filter" not in html
        assert "Overview" in html
        assert "Attack paths" in html
        assert "Miro" not in html

    def test_api_cloud_resource_details_handles_nsg_null_sku(self, monkeypatch):
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
                waf_mode TEXT,
                first_detected TEXT,
                last_synced TEXT,
                status TEXT DEFAULT 'active',
                is_restricted INTEGER DEFAULT 0
            );
            """
        )
        nsg_id = "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/networkSecurityGroups/production_windows"
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state, last_synced) VALUES (?, ?, ?, ?, ?)",
            ("sub-1", "Test Subscription", "production", "Enabled", "2026-06-01T00:00:00Z"),
        )
        conn.execute(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, resource_group, name, type, location, sku, tags,
                is_public, fqdn, pipeline_tag, raw_json, waf_mode, first_detected, last_synced, status, is_restricted
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                nsg_id,
                "sub-1",
                "rg-net",
                "production_windows",
                "Microsoft.Network/networkSecurityGroups",
                "westus",
                None,
                "{}",
                0,
                None,
                None,
                json.dumps({"sku": None, "properties": None}),
                None,
                "2026-06-01T00:00:00Z",
                "2026-06-01T00:00:00Z",
                "active",
                0,
            ),
        )
        conn.executemany(
            """
            INSERT INTO function_app_http_triggers (
                id, subscription_id, function_app_id, function_app_name, resource_group,
                function_name, route, auth_level, methods, fqdn, full_url, is_public, last_synced
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    fn_id + "::HttpPing",
                    "sub-1",
                    fn_id,
                    "fn-one",
                    "rg-app",
                    "HttpPing",
                    "ping",
                    "function",
                    json.dumps(["GET"]),
                    "fn-one.azurewebsites.net",
                    "https://fn-one.azurewebsites.net/api/ping",
                    1,
                    "2026-06-01T00:00:00Z",
                ),
            ],
        )
        conn.executemany(
            """
            INSERT INTO function_app_servicebus_triggers (
                id, subscription_id, function_app_id, function_app_name, resource_group,
                function_name, trigger_type, entity_type, entity_name, subscription_name,
                connection, last_synced
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    fn_id + "::DirectCredit",
                    "sub-1",
                    fn_id,
                    "fn-one",
                    "rg-app",
                    "DirectCredit",
                    "servicebustrigger",
                    "topic",
                    "mydomain.service.events.payments.servicedirectcreditrecalledevent",
                    "sb-subscription",
                    "servicebus-connection",
                    "2026-06-01T00:00:00Z",
                ),
            ],
        )
        conn.commit()

        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)

        client = app_module.app.test_client()
        resp = client.get("/api/cloud/resource-details", query_string={"id": nsg_id})

        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data["type_label"] == "NSG"
        assert data["configuration"]["sku_tier"] is None

    def test_api_cloud_architecture_returns_payload_with_current_waf_schema(self, monkeypatch):
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
                resource_group TEXT,
                name TEXT,
                type TEXT,
                location TEXT,
                sku TEXT,
                fqdn TEXT,
                is_public INTEGER DEFAULT 0,
                status TEXT,
                pipeline_tag TEXT,
                first_detected TEXT,
                last_synced TEXT,
                raw_json TEXT,
                is_restricted INTEGER DEFAULT 0,
                waf_mode TEXT
            );
            CREATE TABLE appgw_waf_policies (
                name TEXT,
                subscription_id TEXT,
                resource_group TEXT,
                mode TEXT,
                state TEXT,
                managed_rule_sets TEXT,
                custom_rules_count INTEGER DEFAULT 0,
                associated_gateways TEXT
            );
            CREATE TABLE firewall_policies (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                name TEXT,
                resource_group TEXT,
                associated_firewalls TEXT,
                mode TEXT,
                threat_intelligence_mode TEXT,
                dns_proxy_enabled INTEGER DEFAULT 0,
                rule_collection_groups TEXT,
                nat_rule_count INTEGER DEFAULT 0,
                app_rule_count INTEGER DEFAULT 0,
                last_synced TEXT
            );
            CREATE TABLE firewall_app_rules (
                firewall_policy_id TEXT,
                subscription_id TEXT,
                firewall_name TEXT
            );
            CREATE TABLE firewall_nat_rules (
                firewall_policy_id TEXT,
                subscription_id TEXT,
                firewall_name TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state, last_synced) VALUES (?, ?, ?, ?, ?)",
            ("sub-1", "Test Subscription", "production", "Enabled", "2026-06-01T00:00:00Z"),
        )
        conn.execute(
            """
            INSERT INTO appgw_waf_policies (
                name, subscription_id, resource_group, mode, state, managed_rule_sets,
                custom_rules_count, associated_gateways
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "policy-one",
                "sub-1",
                "rg-net",
                "Prevention",
                "Enabled",
                '[{"type": "OWASP", "version": "3.2"}]',
                2,
                '["appgw-one"]',
            ),
        )
        conn.execute(
            """
            INSERT INTO firewall_policies (
                id, subscription_id, name, resource_group, associated_firewalls, mode,
                threat_intelligence_mode, dns_proxy_enabled, rule_collection_groups,
                nat_rule_count, app_rule_count, last_synced
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "fw-1",
                "sub-1",
                "fw-policy-one",
                "rg-net",
                '["fw-one"]',
                "Alert",
                "Alert",
                0,
                "[]",
                1,
                3,
                "2026-06-01T00:00:00Z",
            ),
        )
        conn.commit()

        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
        client = app_module.app.test_client()
        resp = client.get("/api/cloud/architecture?sub=sub-1&view=reactflow")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["subscription_id"] == "sub-1"
        assert any(node["data"].get("typeLabel") == "WAF Policy" for node in data["nodes"])
        assert any(node["data"].get("typeLabel") == "Network Firewall" for node in data["nodes"])

    def test_api_cloud_architecture_apim_children_layout_in_subnet(self, monkeypatch):
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
                resource_group TEXT,
                name TEXT,
                type TEXT,
                location TEXT,
                sku TEXT,
                fqdn TEXT,
                is_public INTEGER DEFAULT 0,
                status TEXT,
                pipeline_tag TEXT,
                first_detected TEXT,
                last_synced TEXT,
                raw_json TEXT,
                is_restricted INTEGER DEFAULT 0,
                waf_mode TEXT
            );
            CREATE TABLE apim_api_routes (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                apim_name TEXT,
                apim_resource_id TEXT,
                api_name TEXT,
                api_display_name TEXT,
                api_path TEXT,
                api_protocols TEXT,
                backend_id TEXT,
                backend_url TEXT,
                service_url TEXT,
                requires_subscription INTEGER DEFAULT 1,
                gateway_hosts TEXT,
                exposure_level TEXT,
                last_synced TEXT
            );
            CREATE TABLE apim_backends (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                apim_name TEXT,
                backend_id TEXT,
                title TEXT,
                description TEXT,
                url TEXT,
                protocol TEXT,
                circuit_breaker TEXT,
                credentials TEXT,
                tls_validate_cert INTEGER DEFAULT 1,
                last_synced TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state, last_synced) VALUES (?, ?, ?, ?, ?)",
            ("sub-1", "Test Subscription", "production", "Enabled", "2026-06-01T00:00:00Z"),
        )
        apim_id = "/subscriptions/sub-1/resourceGroups/rg-edge/providers/Microsoft.ApiManagement/service/apim-marketlane-edge"
        subnet_id = "/subscriptions/sub-1/resourceGroups/rg-edge/providers/Microsoft.Network/virtualNetworks/vnet-marketlane-core/subnets/snet-marketlane-apim"
        conn.execute(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, resource_group, name, type, location, sku, fqdn,
                is_public, status, pipeline_tag, first_detected, last_synced, raw_json,
                is_restricted, waf_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                apim_id,
                "sub-1",
                "rg-edge",
                "apim-marketlane-edge",
                "Microsoft.ApiManagement/service",
                "uksouth",
                "Developer",
                "apim-marketlane.azure-api.net",
                1,
                "active",
                None,
                "2026-06-01T00:00:00Z",
                "2026-06-01T00:00:00Z",
                json.dumps({
                    "_extra": {
                        "vnet_name": "vnet-marketlane-core",
                        "vnet_resource_group": "rg-edge",
                        "subnet_name": "snet-marketlane-apim",
                        "subnet_id": subnet_id,
                    }
                }),
                0,
                None,
            ),
        )
        conn.execute(
            """
            INSERT INTO apim_api_routes (
                id, subscription_id, apim_name, apim_resource_id, api_name, api_display_name,
                api_path, api_protocols, backend_id, backend_url, service_url, requires_subscription,
                gateway_hosts, exposure_level, last_synced
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "route-1",
                "sub-1",
                "apim-marketlane-edge",
                apim_id,
                "catalog-marketlane",
                "Catalog API",
                "/catalog",
                json.dumps(["https"]),
                "catalog-backend",
                "https://store.marketlane-retail.azurewebsites.net",
                "https://store.marketlane-retail.azurewebsites.net",
                1,
                json.dumps(["apim-marketlane.azure-api.net"]),
                "Public",
                "2026-06-01T00:00:00Z",
            ),
        )
        conn.execute(
            """
            INSERT INTO apim_backends (
                id, subscription_id, apim_name, backend_id, title, description, url, protocol,
                circuit_breaker, credentials, tls_validate_cert, last_synced
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "backend-1",
                "sub-1",
                "apim-marketlane-edge",
                "catalog-backend",
                "store-backend",
                None,
                "https://store.marketlane-retail.azurewebsites.net",
                "https",
                None,
                None,
                1,
                "2026-06-01T00:00:00Z",
            ),
        )
        conn.commit()

        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
        client = app_module.app.test_client()
        resp = client.get("/api/cloud/architecture?sub=sub-1&view=reactflow")

        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        nodes = {node["id"]: node for node in data["nodes"]}
        subnet_node = next(
            node for node in data["nodes"]
            if str((node.get("data") or {}).get("typeLabel") or "").lower() == "subnet"
            and str((node.get("data") or {}).get("label") or "").lower() == "snet-marketlane-apim"
        )
        api_node = nodes["apim-api::apim-marketlane-edge::catalog-marketlane"]
        backend_node = nodes["apim-backend::apim-marketlane-edge::store-backend"]
        assert api_node["data"]["layoutParentId"] == subnet_node["id"]
        assert backend_node["data"]["layoutParentId"] == subnet_node["id"]
        assert api_node["data"]["parentNodeId"] == apim_id
        assert backend_node["data"]["parentNodeId"] == apim_id

    def test_api_cloud_architecture_surfaces_missing_assets_for_overview_mode(self, monkeypatch):
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
                resource_group TEXT,
                name TEXT,
                type TEXT,
                location TEXT,
                sku TEXT,
                fqdn TEXT,
                is_public INTEGER DEFAULT 0,
                status TEXT,
                pipeline_tag TEXT,
                first_detected TEXT,
                last_synced TEXT,
                raw_json TEXT,
                is_restricted INTEGER DEFAULT 0,
                waf_mode TEXT
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
            CREATE TABLE function_app_servicebus_triggers (
                id TEXT PRIMARY KEY,
                subscription_id TEXT NOT NULL,
                function_app_id TEXT NOT NULL,
                function_app_name TEXT NOT NULL,
                resource_group TEXT,
                function_name TEXT NOT NULL,
                trigger_type TEXT,
                entity_type TEXT,
                entity_name TEXT,
                subscription_name TEXT,
                connection TEXT,
                last_synced TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state, last_synced) VALUES (?, ?, ?, ?, ?)",
            ("sub-1", "Test Subscription", "production", "Enabled", "2026-06-01T00:00:00Z"),
        )
        for idx, name in enumerate(["alpha-gateway", "beta-gateway", "gamma-gateway", "zeta-gateway"], start=1):
            conn.execute(
                """
                INSERT INTO provisioned_assets (
                    id, subscription_id, resource_group, name, type, location, sku, fqdn,
                    is_public, status, pipeline_tag, first_detected, last_synced, raw_json,
                    is_restricted, waf_mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"gw-{idx}",
                    "sub-1",
                    "rg-net",
                    name,
                    "Microsoft.Network/applicationGateways",
                    "westeurope",
                    "Standard_v2",
                    f"{name}.example.com",
                    1,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    "{}",
                    0,
                    None,
                ),
            )
            conn.execute(
                """
                INSERT INTO provisioned_assets (
                    id, subscription_id, resource_group, name, type, location, sku,
                    fqdn, is_public, status, pipeline_tag, first_detected, last_synced,
                    raw_json, is_restricted, waf_mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "/subscriptions/sub-1/resourceGroups/rg-api/providers/Microsoft.ApiManagement/service/apim-shared",
                    "sub-1",
                    "rg-api",
                    "apim-shared",
                    "Microsoft.ApiManagement/service",
                    "eastus",
                    "Developer",
                    "apim-shared.azure-api.net",
                    1,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps({}),
                    0,
                    None,
                ),
            )
        conn.commit()

        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
        client = app_module.app.test_client()
        resp = client.get("/api/cloud/architecture?sub=sub-1&view=overview")

        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        summary = data["summary"]
        assert summary["resource_count"] == 4
        assert summary["displayed_resource_count"] == 4
        assert summary["omitted_resource_count"] == 0
        assert summary["missing_asset_count"] == 0
        assert summary["missing_assets"] == []

    def test_api_cloud_architecture_overview_keeps_backend_type_diversity(self, monkeypatch):
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
                resource_group TEXT,
                name TEXT,
                type TEXT,
                location TEXT,
                sku TEXT,
                fqdn TEXT,
                is_public INTEGER DEFAULT 0,
                status TEXT,
                pipeline_tag TEXT,
                first_detected TEXT,
                last_synced TEXT,
                raw_json TEXT,
                is_restricted INTEGER DEFAULT 0,
                waf_mode TEXT
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
            CREATE TABLE function_app_servicebus_triggers (
                id TEXT PRIMARY KEY,
                subscription_id TEXT NOT NULL,
                function_app_id TEXT NOT NULL,
                function_app_name TEXT NOT NULL,
                resource_group TEXT,
                function_name TEXT NOT NULL,
                trigger_type TEXT,
                entity_type TEXT,
                entity_name TEXT,
                subscription_name TEXT,
                connection TEXT,
                last_synced TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state, last_synced) VALUES (?, ?, ?, ?, ?)",
            ("sub-1", "Test Subscription", "production", "Enabled", "2026-06-01T00:00:00Z"),
        )

        backend_rows = [
            ("aks-1", "rg-backend", "aks-one", "Microsoft.ContainerService/managedClusters", "aks-one.azurecr.io"),
            ("app-1", "rg-backend", "app-one", "Microsoft.Web/sites", "app-one.azurewebsites.net"),
            ("fn-1", "rg-backend", "fn-one", "Microsoft.Web/sites", "fn-one.azurewebsites.net"),
            ("ase-1", "rg-backend", "ase-one", "Microsoft.Web/hostingEnvironments", "ase-one.appserviceenvironment.net"),
            ("acr-1", "rg-backend", "acr-one", "Microsoft.ContainerRegistry/registries", "acr-one.azurecr.io"),
            ("df-1", "rg-backend", "df-one", "Microsoft.DataFactory/factories", "df-one.adf.azure.com"),
            ("sf-1", "rg-backend", "sf-one", "Microsoft.ServiceFabric/clusters", "sf-one.eastus.cloudapp.azure.com"),
            ("kv-1", "rg-backend", "kv-one", "Microsoft.KeyVault/vaults", "kv-one.vault.azure.net"),
        ]
        data_rows = [
            (f"sql-{idx}", "rg-data", f"sql-{idx}", "Microsoft.Sql/servers", f"sql-{idx}.database.windows.net")
            for idx in range(1, 14)
        ]
        for row_id, rg, name, resource_type, fqdn in backend_rows + data_rows:
            conn.execute(
                """
                INSERT INTO provisioned_assets (
                    id, subscription_id, resource_group, name, type, location, sku, fqdn,
                    is_public, status, pipeline_tag, first_detected, last_synced, raw_json,
                    is_restricted, waf_mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row_id,
                    "sub-1",
                    rg,
                    name,
                    resource_type,
                    "westeurope",
                    "Standard",
                    fqdn,
                    0,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps({}),
                    0,
                    None,
                ),
            )
        conn.commit()

        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
        client = app_module.app.test_client()
        resp = client.get("/api/cloud/architecture?sub=sub-1&view=overview")

        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        mermaid = data["mermaid"]
        expected_icons = {
            "kubernetes-service.svg",
            "app-service.svg",
            "app-service-environment.svg",
            "container-registries.svg",
            "data-factories.svg",
            "service-fabric-clusters.svg",
            "key-vault.svg",
        }
        assert expected_icons.issubset(set(part for part in expected_icons if part in mermaid)), mermaid
        assert "SQL" in mermaid, mermaid

    def test_api_cloud_architecture_mermaid_mode_returns_full_payload(self, monkeypatch):
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
                resource_group TEXT,
                name TEXT,
                type TEXT,
                location TEXT,
                sku TEXT,
                fqdn TEXT,
                is_public INTEGER DEFAULT 0,
                status TEXT,
                pipeline_tag TEXT,
                first_detected TEXT,
                last_synced TEXT,
                raw_json TEXT,
                is_restricted INTEGER DEFAULT 0,
                waf_mode TEXT
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
            CREATE TABLE function_app_servicebus_triggers (
                id TEXT PRIMARY KEY,
                subscription_id TEXT NOT NULL,
                function_app_id TEXT NOT NULL,
                function_app_name TEXT NOT NULL,
                resource_group TEXT,
                function_name TEXT NOT NULL,
                trigger_type TEXT,
                entity_type TEXT,
                entity_name TEXT,
                subscription_name TEXT,
                connection TEXT,
                last_synced TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state, last_synced) VALUES (?, ?, ?, ?, ?)",
            ("sub-1", "Test Subscription", "production", "Enabled", "2026-06-01T00:00:00Z"),
        )

        subnet_id = "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/virtualNetworks/vnet-a/subnets/subnet-a"
        conn.executemany(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, resource_group, name, type, location, sku, fqdn,
                is_public, status, pipeline_tag, first_detected, last_synced, raw_json,
                is_restricted, waf_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "sql-1",
                    "sub-1",
                    "rg-data",
                    "sql-prod",
                    "Microsoft.Sql/servers",
                    "westeurope",
                    "GeneralPurpose",
                    "sql-prod.database.windows.net",
                    0,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps({}),
                    0,
                    None,
                ),
                (
                    "sb-1",
                    "sub-1",
                    "rg-messaging",
                    "sb-prod",
                    "Microsoft.ServiceBus/namespaces",
                    "westeurope",
                    "Standard",
                    "sb-prod.servicebus.windows.net",
                    0,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps({}),
                    0,
                    None,
                ),
                (
                    "eh-1",
                    "sub-1",
                    "rg-messaging",
                    "eh-prod",
                    "Microsoft.EventHub/namespaces",
                    "westeurope",
                    "Standard",
                    "eh-prod.servicebus.windows.net",
                    0,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps({}),
                    0,
                    None,
                ),
                (
                    "plan-1",
                    "sub-1",
                    "rg-app",
                    "app-plan",
                    "Microsoft.Web/serverfarms",
                    "westeurope",
                    "P1v3",
                    None,
                    0,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps({"properties": {"virtualNetworkSubnetId": subnet_id}}),
                    0,
                    None,
                ),
            ],
        )
        conn.executemany(
            """
            INSERT INTO function_app_http_triggers (
                id, subscription_id, function_app_id, function_app_name, resource_group,
                function_name, route, auth_level, methods, fqdn, full_url, is_public, last_synced
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    fn_id + "::HttpPing",
                    "sub-1",
                    fn_id,
                    "fn-one",
                    "rg-app",
                    "HttpPing",
                    "ping",
                    "function",
                    json.dumps(["GET"]),
                    "fn-one.azurewebsites.net",
                    "https://fn-one.azurewebsites.net/api/ping",
                    1,
                    "2026-06-01T00:00:00Z",
                ),
            ],
        )
        conn.executemany(
            """
            INSERT INTO function_app_servicebus_triggers (
                id, subscription_id, function_app_id, function_app_name, resource_group,
                function_name, trigger_type, entity_type, entity_name, subscription_name,
                connection, last_synced
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    fn_id + "::DirectCredit",
                    "sub-1",
                    fn_id,
                    "fn-one",
                    "rg-app",
                    "DirectCredit",
                    "servicebustrigger",
                    "topic",
                    "mydomain.service.events.payments.servicedirectcreditrecalledevent",
                    "sb-subscription",
                    "servicebus-connection",
                    "2026-06-01T00:00:00Z",
                ),
            ],
        )
        conn.execute(
            """
            INSERT INTO function_app_http_triggers (
                id, subscription_id, function_app_id, function_app_name, resource_group,
                function_name, route, auth_level, methods, fqdn, full_url, is_public, last_synced
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fn_id + "::HttpPing",
                "sub-1",
                fn_id,
                "fn-one",
                "rg-app",
                "HttpPing",
                "ping",
                "function",
                json.dumps(["GET"]),
                "fn-one.azurewebsites.net",
                "https://fn-one.azurewebsites.net/api/ping",
                1,
                "2026-06-01T00:00:00Z",
            ),
        )
        conn.execute(
            """
            INSERT INTO function_app_servicebus_triggers (
                id, subscription_id, function_app_id, function_app_name, resource_group,
                function_name, trigger_type, entity_type, entity_name, subscription_name,
                connection, last_synced
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fn_id + "::DirectCredit",
                "sub-1",
                fn_id,
                "fn-one",
                "rg-app",
                "DirectCredit",
                "servicebustrigger",
                "topic",
                "mydomain.service.events.payments.servicedirectcreditrecalledevent",
                "sb-subscription",
                "servicebus-connection",
                "2026-06-01T00:00:00Z",
            ),
        )
        conn.commit()

        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
        client = app_module.app.test_client()
        resp = client.get("/api/cloud/architecture?sub=sub-1&view=mermaid")

        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        summary = data["summary"]
        assert summary["omitted_resource_count"] == 0
        assert summary["missing_assets"] == []

        nodes = data["nodes"]
        labels = {str(node.get("data", {}).get("label", "")) for node in nodes}
        assert {"sql-prod", "sb-prod", "eh-prod", "app-plan"}.issubset(labels)

        plan_node = next(node for node in nodes if node.get("data", {}).get("label") == "app-plan")
        subnet_node = next(node for node in nodes if node.get("data", {}).get("label") == "subnet-a")
        assert plan_node["data"].get("parentNodeId") == subnet_node["id"]

    def test_api_cloud_architecture_nests_appgw_listeners_under_gateway_subnet(self, monkeypatch):
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
                resource_group TEXT,
                name TEXT,
                type TEXT,
                location TEXT,
                sku TEXT,
                fqdn TEXT,
                is_public INTEGER DEFAULT 0,
                status TEXT,
                pipeline_tag TEXT,
                first_detected TEXT,
                last_synced TEXT,
                raw_json TEXT,
                is_restricted INTEGER DEFAULT 0,
                waf_mode TEXT
            );
            CREATE TABLE appgw_routing_rules (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                gateway_name TEXT,
                backend_fqdns TEXT,
                backend_pool_name TEXT,
                listener_name TEXT,
                hostname TEXT,
                url_path TEXT,
                protocol TEXT,
                waf_policy_name TEXT,
                resource_group TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state, last_synced) VALUES (?, ?, ?, ?, ?)",
            ("sub-1", "Test Subscription", "production", "Enabled", "2026-06-01T00:00:00Z"),
        )
        conn.execute(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, resource_group, name, type, location, sku, fqdn,
                is_public, status, pipeline_tag, first_detected, last_synced, raw_json, is_restricted, waf_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "gw-1",
                "sub-1",
                "rg-net",
                "appgatewaycop",
                "Microsoft.Network/applicationGateways",
                "eastus",
                "WAF_v2",
                "appgatewaycop.example.com",
                1,
                "active",
                "",
                "2026-06-01T00:00:00Z",
                "2026-06-01T00:00:00Z",
                json.dumps(
                    {
                        "_extra": {
                            "vnet_name": "prod-vnet",
                            "vnet_resource_group": "rg-net",
                            "subnet_name": "appgw-subnet",
                            "subnet_id": "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/virtualNetworks/prod-vnet/subnets/appgw-subnet",
                        }
                    }
                ),
                0,
                None,
            ),
        )
        conn.execute(
            """
            INSERT INTO appgw_routing_rules (
                id, subscription_id, gateway_name, backend_fqdns, backend_pool_name,
                listener_name, hostname, url_path, protocol, waf_policy_name, resource_group
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "gw-1::listener-1",
                "sub-1",
                "appgatewaycop",
                json.dumps(["cop-resource-server-apim.example.com"]),
                "pool-one",
                "listener-1",
                "cop-resource-server-apim.example.com",
                "/*",
                "HTTPS",
                None,
                "rg-net",
            ),
        )
        conn.commit()

        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
        client = app_module.app.test_client()
        resp = client.get("/api/cloud/architecture?sub=sub-1&view=reactflow")
        assert resp.status_code == 200
        data = resp.get_json()
        nodes = {node["id"]: node for node in data["nodes"]}
        listener = nodes["listener::appgatewaycop::listener-1::cop-resource-server-apim.example.com"]
        gateway = nodes["gw-1"]
        assert listener["data"]["parentNodeId"] == gateway["id"]
        assert listener["data"]["subnetName"] == "appgw-subnet"

    def test_api_cloud_architecture_renders_bastion_public_ip_hierarchy(self, monkeypatch):
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
                resource_group TEXT,
                name TEXT,
                type TEXT,
                location TEXT,
                sku TEXT,
                fqdn TEXT,
                is_public INTEGER DEFAULT 0,
                status TEXT,
                pipeline_tag TEXT,
                first_detected TEXT,
                last_synced TEXT,
                raw_json TEXT,
                is_restricted INTEGER DEFAULT 0,
                waf_mode TEXT
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
                resource_group TEXT,
                mode TEXT,
                state TEXT,
                managed_rule_sets TEXT,
                custom_rules_count INTEGER DEFAULT 0,
                associated_gateways TEXT
            );
            CREATE TABLE firewall_policies (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                name TEXT,
                resource_group TEXT,
                associated_firewalls TEXT,
                mode TEXT,
                threat_intelligence_mode TEXT,
                dns_proxy_enabled INTEGER DEFAULT 0,
                rule_collection_groups TEXT,
                nat_rule_count INTEGER DEFAULT 0,
                app_rule_count INTEGER DEFAULT 0,
                last_synced TEXT
            );
            CREATE TABLE firewall_app_rules (
                firewall_policy_id TEXT,
                subscription_id TEXT,
                firewall_name TEXT
            );
            CREATE TABLE firewall_nat_rules (
                firewall_policy_id TEXT,
                subscription_id TEXT,
                firewall_name TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state, last_synced) VALUES (?, ?, ?, ?, ?)",
            ("sub-1", "Test Subscription", "production", "Enabled", "2026-06-01T00:00:00Z"),
        )
        public_ip_id = "/subscriptions/sub-1/resourceGroups/blue-network-ukwest/providers/Microsoft.Network/publicIPAddresses/bastion"
        conn.executemany(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, resource_group, name, type, location, sku, fqdn,
                is_public, status, pipeline_tag, first_detected, last_synced, raw_json,
                is_restricted, waf_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "/subscriptions/sub-1/resourceGroups/blue-network-ukwest/providers/Microsoft.Network/bastionHosts/bastion",
                    "sub-1",
                    "blue-network-ukwest",
                    "bastion",
                    "Microsoft.Network/bastionHosts",
                    "uksouth",
                    "Standard",
                    None,
                    0,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps(
                        {
                            "properties": {
                                "ipConfigurations": [
                                    {
                                        "properties": {
                                            "subnet": {
                                                "id": "/subscriptions/sub-1/resourceGroups/blue-network-ukwest/providers/Microsoft.Network/virtualNetworks/blue-vnet/subnets/AzureBastionSubnet"
                                            },
                                            "publicIPAddress": {"id": public_ip_id},
                                        }
                                    }
                                ]
                            }
                        }
                    ),
                    0,
                    None,
                ),
                (
                    "/subscriptions/sub-1/resourceGroups/blue-network-ukwest/providers/Microsoft.Network/virtualNetworks/blue-vnet",
                    "sub-1",
                    "blue-network-ukwest",
                    "blue-vnet",
                    "Microsoft.Network/virtualNetworks",
                    "uksouth",
                    "Standard",
                    None,
                    0,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    "{}",
                    0,
                    None,
                ),
                (
                    public_ip_id,
                    "sub-1",
                    "blue-network-ukwest",
                    "bastion",
                    "Microsoft.Network/publicIPAddresses",
                    "uksouth",
                    "Standard",
                    None,
                    1,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps({"properties": {"ipAddress": "20.30.40.50"}}),
                    0,
                    None,
                ),
            ],
        )
        conn.execute(
            """
            INSERT INTO function_app_http_triggers (
                id, subscription_id, function_app_id, function_app_name, resource_group,
                function_name, route, auth_level, methods, fqdn, full_url, is_public, last_synced
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fn_id + "::HttpPing",
                "sub-1",
                fn_id,
                "fn-one",
                "rg-app",
                "HttpPing",
                "ping",
                "function",
                json.dumps(["GET"]),
                "fn-one.azurewebsites.net",
                "https://fn-one.azurewebsites.net/api/ping",
                1,
                "2026-06-01T00:00:00Z",
            ),
        )
        conn.execute(
            """
            INSERT INTO function_app_servicebus_triggers (
                id, subscription_id, function_app_id, function_app_name, resource_group,
                function_name, trigger_type, entity_type, entity_name, subscription_name,
                connection, last_synced
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fn_id + "::DirectCredit",
                "sub-1",
                fn_id,
                "fn-one",
                "rg-app",
                "DirectCredit",
                "servicebustrigger",
                "topic",
                "mydomain.service.events.payments.servicedirectcreditrecalledevent",
                "sb-subscription",
                "servicebus-connection",
                "2026-06-01T00:00:00Z",
            ),
        )
        conn.commit()

        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
        client = app_module.app.test_client()
        resp = client.get("/api/cloud/architecture?sub=sub-1&view=connectivity")

        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        mermaid = data["views"]["connectivity"]["mermaid"]
        bastion_id = "blue_network_ukwest_bastion_bastion"
        public_ip_id = "blue_network_ukwest_public_ip_bastion"
        # Public IP nodes should NOT be rendered in Mermaid (surfaced as properties instead)
        assert f"{public_ip_id}[\"" not in mermaid, f"Public IP should not be a node in Mermaid. Found in: {mermaid}"
        # Bastion node should exist
        assert f"{bastion_id}[\"" in mermaid, mermaid
        # Internet should connect to bastion with public IP
        assert f"Internet -->|\"Public IP" in mermaid, f"Internet should connect to bastion with public IP. Mermaid: {mermaid}"
        assert f"| {bastion_id}" in mermaid, f"Connection should target bastion node. Mermaid: {mermaid}"
        # Bastion should also show the private-side hop into the VNet
        private_edge_line = next(line for line in mermaid.splitlines() if "Private access" in line)
        assert bastion_id in private_edge_line, mermaid
        assert "blue_vnet" in private_edge_line, mermaid
        bastion_node_line = next(line for line in mermaid.splitlines() if f"{bastion_id}[" in line)
        assert "WAF" not in bastion_node_line, mermaid

    def test_api_cloud_resource_details_includes_parent_and_public_ip(self, monkeypatch):
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
                resource_group TEXT,
                name TEXT,
                type TEXT,
                location TEXT,
                sku TEXT,
                fqdn TEXT,
                is_public INTEGER DEFAULT 0,
                status TEXT,
                pipeline_tag TEXT,
                first_detected TEXT,
                last_synced TEXT,
                raw_json TEXT,
                is_restricted INTEGER DEFAULT 0,
                waf_mode TEXT
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
            CREATE TABLE function_app_servicebus_triggers (
                id TEXT PRIMARY KEY,
                subscription_id TEXT NOT NULL,
                function_app_id TEXT NOT NULL,
                function_app_name TEXT NOT NULL,
                resource_group TEXT,
                function_name TEXT NOT NULL,
                trigger_type TEXT,
                entity_type TEXT,
                entity_name TEXT,
                subscription_name TEXT,
                connection TEXT,
                last_synced TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state, last_synced) VALUES (?, ?, ?, ?, ?)",
            ("sub-1", "Test Subscription", "production", "Enabled", "2026-06-01T00:00:00Z"),
        )
        public_ip_id = "/subscriptions/sub-1/resourceGroups/blue-network-ukwest/providers/Microsoft.Network/publicIPAddresses/bastion"
        subnet_id = "/subscriptions/sub-1/resourceGroups/blue-network-ukwest/providers/Microsoft.Network/virtualNetworks/blue-vnet/subnets/bastion-subnet"
        bastion_id = "/subscriptions/sub-1/resourceGroups/blue-network-ukwest/providers/Microsoft.Network/bastionHosts/bastion"
        conn.executemany(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, resource_group, name, type, location, sku, fqdn,
                is_public, status, pipeline_tag, first_detected, last_synced, raw_json,
                is_restricted, waf_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    subnet_id,
                    "sub-1",
                    "blue-network-ukwest",
                    "bastion-subnet",
                    "Microsoft.Network/virtualNetworks/subnets",
                    "uksouth",
                    None,
                    None,
                    0,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps({
                        "properties": {"addressPrefix": "10.0.1.0/24"},
                        "_extra": {
                            "parent_vnet_name": "blue-vnet",
                            "parent_vnet_resource_group": "blue-network-ukwest",
                            "subnet_name": "bastion-subnet",
                        },
                    }),
                    0,
                    None,
                ),
                (
                    bastion_id,
                    "sub-1",
                    "blue-network-ukwest",
                    "bastion",
                    "Microsoft.Network/bastionHosts",
                    "uksouth",
                    "Standard",
                    None,
                    0,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps({
                        "properties": {
                            "ipConfigurations": [
                                {
                                    "properties": {
                                        "publicIPAddress": {
                                            "id": public_ip_id,
                                        }
                                    }
                                }
                            ]
                        }
                    }),
                    0,
                    None,
                ),
                (
                    public_ip_id,
                    "sub-1",
                    "blue-network-ukwest",
                    "bastion",
                    "Microsoft.Network/publicIPAddresses",
                    "uksouth",
                    "Standard",
                    "bastion.example.com",
                    1,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps({
                        "properties": {
                            "ipAddress": "20.30.40.50",
                            "dnsSettings": {
                                "fqdn": "bastion.example.com"
                            },
                        }
                    }),
                    0,
                    None,
                ),
            ],
        )
        conn.execute(
            """
            INSERT INTO function_app_http_triggers (
                id, subscription_id, function_app_id, function_app_name, resource_group,
                function_name, route, auth_level, methods, fqdn, full_url, is_public, last_synced
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fn_id + "::HttpPing",
                "sub-1",
                fn_id,
                "fn-one",
                "rg-app",
                "HttpPing",
                "ping",
                "function",
                json.dumps(["GET"]),
                "fn-one.azurewebsites.net",
                "https://fn-one.azurewebsites.net/api/ping",
                1,
                "2026-06-01T00:00:00Z",
            ),
        )
        conn.execute(
            """
            INSERT INTO function_app_servicebus_triggers (
                id, subscription_id, function_app_id, function_app_name, resource_group,
                function_name, trigger_type, entity_type, entity_name, subscription_name,
                connection, last_synced
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fn_id + "::DirectCredit",
                "sub-1",
                fn_id,
                "fn-one",
                "rg-app",
                "DirectCredit",
                "servicebustrigger",
                "topic",
                "mydomain.service.events.payments.servicedirectcreditrecalledevent",
                "sb-subscription",
                "servicebus-connection",
                "2026-06-01T00:00:00Z",
            ),
        )
        conn.commit()

        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
        client = app_module.app.test_client()
        resp = client.get("/api/cloud/resource-details", query_string={"id": public_ip_id})

        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data["icon_path"].endswith("publicipaddresses.svg") or data["icon_path"]
        assert data["network"]["public_ips"] == ["20.30.40.50"]
        assert data["network"]["dns_names"][0] == "bastion.example.com"
        assert data["parent_resource"]["name"] == "bastion"
        assert data["parent_resource"]["type_label"] == "Bastion"
        assert data["parent_resource"]["icon_path"]

    def test_kubernetes_service_uses_kubernetes_service_icon(self):
        import os
        import sys

        sys.path.insert(0, str(REPO_ROOT))
        os.environ.setdefault("FLASK_APP", "web/app.py")
        import web.app as app_module

        icon_path = app_module._get_icon_path("microsoft.kubernetes/services")
        icon_class = app_module._get_icon_class("microsoft.kubernetes/services")
        friendly_icon_path = app_module._get_icon_path("Kubernetes Service")
        friendly_icon_class = app_module._get_icon_class("Kubernetes Service")

        assert icon_path and icon_path.endswith("azure/containers/kubernetes-service.svg"), icon_path
        assert icon_class == "icon-azurerm-kubernetes-service", icon_class
        assert friendly_icon_path and friendly_icon_path.endswith("azure/containers/kubernetes-service.svg"), friendly_icon_path
        assert friendly_icon_class == "icon-azurerm-kubernetes-service", friendly_icon_class

    def test_api_cloud_resource_details_treats_internal_apim_as_private(self, monkeypatch):
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
                state TEXT
            );
            CREATE TABLE provisioned_assets (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                resource_group TEXT,
                name TEXT,
                type TEXT,
                location TEXT,
                sku TEXT,
                fqdn TEXT,
                is_public INTEGER DEFAULT 0,
                status TEXT,
                pipeline_tag TEXT,
                first_detected TEXT,
                last_synced TEXT,
                raw_json TEXT,
                is_restricted INTEGER DEFAULT 0,
                waf_mode TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state) VALUES (?, ?, ?, ?)",
            ("sub-1", "Test Subscription", "production", "Enabled"),
        )
        apim_id = "/subscriptions/sub-1/resourceGroups/rg-api/providers/Microsoft.ApiManagement/service/apim-prod"
        pip_id = "/subscriptions/sub-1/resourceGroups/rg-api/providers/Microsoft.Network/publicIPAddresses/apim-prod-pip"
        conn.execute(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, resource_group, name, type, location, sku, fqdn,
                is_public, status, pipeline_tag, first_detected, last_synced, raw_json,
                is_restricted, waf_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                apim_id,
                "sub-1",
                "rg-api",
                "apim-prod",
                "Microsoft.ApiManagement/service",
                "uksouth",
                "Premium",
                "apim-prod.azure-api.net",
                1,
                "active",
                None,
                "2026-06-01T00:00:00Z",
                "2026-06-01T00:00:00Z",
                json.dumps({
                    "properties": {
                        "publicNetworkAccess": "Enabled",
                        "virtualNetworkType": "Internal",
                        "publicIpAddress": {
                            "id": pip_id,
                        },
                    }
                }),
                0,
                None,
            ),
        )
        conn.execute(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, resource_group, name, type, location, sku, fqdn,
                is_public, status, pipeline_tag, first_detected, last_synced, raw_json,
                is_restricted, waf_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pip_id,
                "sub-1",
                "rg-api",
                "apim-prod-pip",
                "Microsoft.Network/publicIPAddresses",
                "uksouth",
                "Standard",
                None,
                1,
                "active",
                None,
                "2026-06-01T00:00:00Z",
                "2026-06-01T00:00:00Z",
                json.dumps({
                    "properties": {
                        "ipAddress": "52.160.10.10",
                    }
                }),
                0,
                None,
            ),
        )
        conn.commit()

        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
        client = app_module.app.test_client()
        resp = client.get("/api/cloud/resource-details", query_string={"id": apim_id})

        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data["security"]["is_public"] is False
        assert data["security"]["public_network_access"] == "Enabled"
        assert data["network"]["virtual_network_type"] == "Internal"
        assert data["network"]["public_ips"] == ["52.160.10.10"]

    def test_api_cloud_resource_details_treats_external_apim_as_public(self, monkeypatch):
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
                state TEXT
            );
            CREATE TABLE provisioned_assets (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                resource_group TEXT,
                name TEXT,
                type TEXT,
                location TEXT,
                sku TEXT,
                fqdn TEXT,
                is_public INTEGER DEFAULT 0,
                status TEXT,
                pipeline_tag TEXT,
                first_detected TEXT,
                last_synced TEXT,
                raw_json TEXT,
                is_restricted INTEGER DEFAULT 0,
                waf_mode TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state) VALUES (?, ?, ?, ?)",
            ("sub-1", "Test Subscription", "production", "Enabled"),
        )
        apim_id = "/subscriptions/sub-1/resourceGroups/rg-api/providers/Microsoft.ApiManagement/service/apim-prod"
        conn.execute(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, resource_group, name, type, location, sku, fqdn,
                is_public, status, pipeline_tag, first_detected, last_synced, raw_json,
                is_restricted, waf_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                apim_id,
                "sub-1",
                "rg-api",
                "apim-prod",
                "Microsoft.ApiManagement/service",
                "uksouth",
                "Premium",
                "apim-prod.azure-api.net",
                0,
                "active",
                None,
                "2026-06-01T00:00:00Z",
                "2026-06-01T00:00:00Z",
                json.dumps({
                    "properties": {
                        "publicNetworkAccess": "Enabled",
                        "virtualNetworkType": "External",
                    }
                }),
                0,
                None,
            ),
        )
        conn.commit()

        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
        client = app_module.app.test_client()
        resp = client.get("/api/cloud/resource-details", query_string={"id": apim_id})

        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data["security"]["is_public"] is True
        assert data["security"]["public_network_access"] == "Enabled"
        assert data["network"]["virtual_network_type"] == "External"

    def test_api_cloud_resource_details_surfaces_apim_outbound_public_ip(self, monkeypatch):
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
                state TEXT
            );
            CREATE TABLE provisioned_assets (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                resource_group TEXT,
                name TEXT,
                type TEXT,
                location TEXT,
                sku TEXT,
                fqdn TEXT,
                is_public INTEGER DEFAULT 0,
                status TEXT,
                pipeline_tag TEXT,
                first_detected TEXT,
                last_synced TEXT,
                raw_json TEXT,
                is_restricted INTEGER DEFAULT 0,
                waf_mode TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state) VALUES (?, ?, ?, ?)",
            ("sub-1", "Test Subscription", "production", "Enabled"),
        )
        apim_id = "/subscriptions/sub-1/resourceGroups/rg-api/providers/Microsoft.ApiManagement/service/apim-prod"
        conn.execute(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, resource_group, name, type, location, sku, fqdn,
                is_public, status, pipeline_tag, first_detected, last_synced, raw_json,
                is_restricted, waf_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                apim_id,
                "sub-1",
                "rg-api",
                "apim-prod",
                "Microsoft.ApiManagement/service",
                "uksouth",
                "Premium",
                "apim-prod.azure-api.net",
                0,
                "active",
                None,
                "2026-06-01T00:00:00Z",
                "2026-06-01T00:00:00Z",
                json.dumps({
                    "properties": {
                        "publicNetworkAccess": "Enabled",
                        "virtualNetworkType": "Internal",
                        "publicIPAddresses": ["20.90.204.14"],
                    }
                }),
                0,
                None,
            ),
        )
        conn.commit()

        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
        client = app_module.app.test_client()
        resp = client.get("/api/cloud/resource-details", query_string={"id": apim_id})

        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data["security"]["is_public"] is False
        assert data["network"]["virtual_network_type"] == "Internal"
        assert data["network"]["outbound_public_ips"] == ["20.90.204.14"]
        assert data["network"]["public_ips"] == ["20.90.204.14"]

    def test_api_cloud_resource_details_includes_apim_backends(self, monkeypatch):
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
                state TEXT
            );
            CREATE TABLE provisioned_assets (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                resource_group TEXT,
                name TEXT,
                type TEXT,
                location TEXT,
                sku TEXT,
                fqdn TEXT,
                is_public INTEGER DEFAULT 0,
                status TEXT,
                pipeline_tag TEXT,
                first_detected TEXT,
                last_synced TEXT,
                raw_json TEXT,
                is_restricted INTEGER DEFAULT 0,
                waf_mode TEXT
            );
            CREATE TABLE apim_backends (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                apim_name TEXT,
                backend_id TEXT,
                title TEXT,
                description TEXT,
                url TEXT,
                protocol TEXT,
                backend_type TEXT,
                circuit_breaker TEXT,
                credentials TEXT,
                tls_validate_cert INTEGER,
                last_synced TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state) VALUES (?, ?, ?, ?)",
            ("sub-1", "Test Subscription", "production", "Enabled"),
        )
        apim_id = "/subscriptions/sub-1/resourceGroups/rg-api/providers/Microsoft.ApiManagement/service/apim-prod"
        conn.execute(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, resource_group, name, type, location, sku, fqdn,
                is_public, status, pipeline_tag, first_detected, last_synced, raw_json,
                is_restricted, waf_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                apim_id,
                "sub-1",
                "rg-api",
                "apim-prod",
                "Microsoft.ApiManagement/service",
                "uksouth",
                "Premium",
                "apim-prod.azure-api.net",
                0,
                "active",
                None,
                "2026-06-01T00:00:00Z",
                "2026-06-01T00:00:00Z",
                json.dumps({"properties": {}}),
                0,
                None,
            ),
        )
        conn.executemany(
            """
            INSERT INTO apim_backends (
                id, subscription_id, apim_name, backend_id, title, description, url,
                protocol, backend_type, circuit_breaker, credentials, tls_validate_cert, last_synced
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "apim-prod::orders-api",
                    "sub-1",
                    "apim-prod",
                    "orders-api",
                    "orders-api",
                    "Orders backend",
                    "https://orders.example.com",
                    "https",
                    "Custom URL",
                    None,
                    None,
                    1,
                    "2026-06-01T00:00:00Z",
                ),
                (
                    "apim-prod::inventory-sf",
                    "sub-1",
                    "apim-prod",
                    "inventory-sf",
                    "inventory-sf",
                    "Inventory service fabric backend",
                    "fabric:/Inventory/Backend",
                    "http",
                    "Service Fabric",
                    None,
                    None,
                    1,
                    "2026-06-01T00:00:00Z",
                ),
                (
                    "apim-prod::billing-resource",
                    "sub-1",
                    "apim-prod",
                    "billing-resource",
                    "billing-resource",
                    "Billing API hosted in Azure App Service",
                    "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/sites/billing-api",
                    "http",
                    "Azure Resource",
                    None,
                    None,
                    1,
                    "2026-06-01T00:00:00Z",
                ),
            ],
        )
        conn.commit()

        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
        client = app_module.app.test_client()
        resp = client.get("/api/cloud/resource-details", query_string={"id": apim_id})

        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert [backend["name"] for backend in data["backends"]] == ["billing-resource", "inventory-sf", "orders-api"], data["backends"]
        assert [backend["type"] for backend in data["backends"]] == ["Azure Resource", "Service Fabric", "Custom URL"], data["backends"]
        assert data["backends"][0]["runtime_url"] == "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/sites/billing-api"
        assert data["backends"][2]["runtime_url"] == "https://orders.example.com"

    def test_api_cloud_resource_details_includes_apim_api_targets(self, monkeypatch):
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
                state TEXT
            );
            CREATE TABLE provisioned_assets (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                resource_group TEXT,
                name TEXT,
                type TEXT,
                location TEXT,
                sku TEXT,
                fqdn TEXT,
                is_public INTEGER DEFAULT 0,
                status TEXT,
                pipeline_tag TEXT,
                first_detected TEXT,
                last_synced TEXT,
                raw_json TEXT,
                is_restricted INTEGER DEFAULT 0,
                waf_mode TEXT
            );
            CREATE TABLE apim_api_routes (
                apim_name TEXT,
                subscription_id TEXT,
                api_name TEXT,
                api_display_name TEXT,
                api_path TEXT,
                backend_id TEXT,
                backend_url TEXT,
                service_url TEXT,
                exposure_level TEXT,
                requires_subscription INTEGER
            );
            """
        )
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state) VALUES (?, ?, ?, ?)",
            ("sub-1", "Test Subscription", "production", "Enabled"),
        )
        apim_id = "/subscriptions/sub-1/resourceGroups/rg-api/providers/Microsoft.ApiManagement/service/apim-prod"
        conn.execute(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, resource_group, name, type, location, sku, fqdn,
                is_public, status, pipeline_tag, first_detected, last_synced, raw_json,
                is_restricted, waf_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                apim_id,
                "sub-1",
                "rg-api",
                "apim-prod",
                "Microsoft.ApiManagement/service",
                "uksouth",
                "Premium",
                "apim-prod.azure-api.net",
                1,
                "active",
                None,
                "2026-06-01T00:00:00Z",
                "2026-06-01T00:00:00Z",
                json.dumps({"properties": {}}),
                0,
                None,
            ),
        )
        conn.execute(
            """
            INSERT INTO apim_api_routes (
                apim_name, subscription_id, api_name, api_display_name, api_path,
                backend_id, backend_url, service_url, exposure_level, requires_subscription
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "apim-prod",
                "sub-1",
                "orders-api",
                "Orders API",
                "/orders",
                "orders-backend",
                "https://orders.example.com",
                "https://apim-prod.azure-api.net",
                "Public",
                1,
            ),
        )
        conn.commit()

        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
        client = app_module.app.test_client()
        resp = client.get("/api/cloud/resource-details", query_string={"id": apim_id})

        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data["apis"][0]["api_display_name"] == "Orders API", data["apis"]
        assert data["apis"][0]["backend_target"] == "orders-backend", data["apis"]
        assert data["apis"][0]["backend_url"] == "https://orders.example.com", data["apis"]

    def test_api_cloud_resource_details_resolves_apim_backend_target(self, monkeypatch):
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
                state TEXT
            );
            CREATE TABLE provisioned_assets (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                resource_group TEXT,
                name TEXT,
                type TEXT,
                location TEXT,
                sku TEXT,
                fqdn TEXT,
                is_public INTEGER DEFAULT 0,
                status TEXT,
                pipeline_tag TEXT,
                first_detected TEXT,
                last_synced TEXT,
                raw_json TEXT,
                is_restricted INTEGER DEFAULT 0,
                waf_mode TEXT
            );
            CREATE TABLE apim_backends (
                apim_name TEXT,
                backend_id TEXT,
                title TEXT,
                description TEXT,
                url TEXT,
                protocol TEXT,
                subscription_id TEXT
            );
            CREATE TABLE apim_api_routes (
                apim_name TEXT,
                api_name TEXT,
                api_display_name TEXT,
                api_path TEXT,
                backend_url TEXT,
                service_url TEXT,
                requires_subscription INTEGER,
                subscription_id TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state) VALUES (?, ?, ?, ?)",
            ("sub-1", "Test Subscription", "production", "Enabled"),
        )
        apim_id = "/subscriptions/sub-1/resourceGroups/rg-api/providers/Microsoft.ApiManagement/service/core-api-uksouth"
        conn.execute(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, resource_group, name, type, location, sku, fqdn,
                is_public, status, pipeline_tag, first_detected, last_synced, raw_json,
                is_restricted, waf_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                apim_id,
                "sub-1",
                "rg-api",
                "core-api-uksouth",
                "Microsoft.ApiManagement/service",
                "uksouth",
                "Premium",
                "core-api-uksouth.azure-api.net",
                0,
                "active",
                None,
                "2026-06-01T00:00:00Z",
                "2026-06-01T00:00:00Z",
                json.dumps({"properties": {"virtualNetworkType": "Internal"}}),
                0,
                None,
            ),
        )
        conn.execute(
            """
            INSERT INTO apim_backends (apim_name, backend_id, title, description, url, protocol, subscription_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "core-api-uksouth",
                "prodgreen-eventgrid-bridge",
                "prodgreen-eventgrid-bridge.internal.cbinnovation.uk",
                "EventGrid bridge backend",
                "https://prodgreen-eventgrid-bridge.internal.cbinnovation.uk/",
                "https",
                "sub-1",
            ),
        )
        conn.execute(
            """
            INSERT INTO apim_api_routes (
                apim_name, api_name, api_display_name, api_path, backend_url, service_url,
                requires_subscription, subscription_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "core-api-uksouth",
                "bridge-api",
                "Bridge API",
                "/bridge",
                "https://prodgreen-eventgrid-bridge.internal.cbinnovation.uk/",
                "https://prodgreen-eventgrid-bridge.internal.cbinnovation.uk/",
                1,
                "sub-1",
            ),
        )
        conn.commit()

        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
        client = app_module.app.test_client()
        resp = client.get(
            "/api/cloud/resource-details",
            query_string={
                "id": "cbuk_core_prodgreen_api_uksouth_prodgreen_eventgrid_bridge_internal_cbinnovation_uk",
                "name": "prodgreen-eventgrid-bridge.internal.cbinnovation.uk",
                "resource_group": "core-api-uksouth",
                "type": "APIM Backend Target",
                "sub": "sub-1",
            },
        )

        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data["type_label"] == "APIM Backend Target"
        assert data["name"] == "prodgreen-eventgrid-bridge.internal.cbinnovation.uk"
        assert data["parent_resource"]["name"] == "core-api-uksouth"
        assert data["configuration"]["backend_id"] == "prodgreen-eventgrid-bridge"
        assert data["network"]["subnet"] is None or isinstance(data["network"]["subnet"], str)

    def test_api_cloud_resource_details_infers_appgw_parent_from_backend_route(self, monkeypatch):
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
                state TEXT
            );
            CREATE TABLE provisioned_assets (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                resource_group TEXT,
                name TEXT,
                type TEXT,
                location TEXT,
                sku TEXT,
                fqdn TEXT,
                is_public INTEGER DEFAULT 0,
                status TEXT,
                pipeline_tag TEXT,
                first_detected TEXT,
                last_synced TEXT,
                raw_json TEXT,
                is_restricted INTEGER DEFAULT 0,
                waf_mode TEXT
            );
            CREATE TABLE appgw_routing_rules (
                id TEXT,
                subscription_id TEXT,
                gateway_name TEXT,
                backend_fqdns TEXT,
                backend_pool_name TEXT,
                listener_name TEXT,
                hostname TEXT,
                url_path TEXT,
                protocol TEXT,
                waf_policy_name TEXT,
                resource_group TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state) VALUES (?, ?, ?, ?)",
            ("sub-1", "Test Subscription", "production", "Enabled"),
        )
        gw_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Network/applicationGateways/cop-resource-server-apim"
        plan_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/serverfarms/cards-plan"
        site_id = "/subscriptions/sub-1/resourceGroups/rg-backend/providers/Microsoft.Web/sites/cards-management-web"
        conn.execute(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, resource_group, name, type, location, sku, fqdn,
                is_public, status, pipeline_tag, first_detected, last_synced, raw_json,
                is_restricted, waf_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan_id,
                "sub-1",
                "rg-app",
                "cards-plan",
                "Microsoft.Web/serverfarms",
                "uksouth",
                "P1v3",
                None,
                0,
                "active",
                None,
                "2026-06-01T00:00:00Z",
                "2026-06-01T00:00:00Z",
                json.dumps({}),
                0,
                None,
            ),
        )
        conn.execute(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, resource_group, name, type, location, sku, fqdn,
                is_public, status, pipeline_tag, first_detected, last_synced, raw_json,
                is_restricted, waf_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                gw_id,
                "sub-1",
                "rg-app",
                "cop-resource-server-apim",
                "Microsoft.Network/applicationGateways",
                "uksouth",
                "WAF_v2",
                "cop-resource-server-apim.example.com",
                1,
                "active",
                None,
                "2026-06-01T00:00:00Z",
                "2026-06-01T00:00:00Z",
                json.dumps({}),
                0,
                None,
            ),
        )
        conn.execute(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, resource_group, name, type, location, sku, fqdn,
                is_public, status, pipeline_tag, first_detected, last_synced, raw_json,
                is_restricted, waf_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                site_id,
                "sub-1",
                "rg-backend",
                "cards-management-web",
                "Microsoft.Web/sites",
                "uksouth",
                "B1",
                "cards-management-web.azurewebsites.net",
                1,
                "active",
                None,
                "2026-06-01T00:00:00Z",
                "2026-06-01T00:00:00Z",
                json.dumps({"serverFarmId": plan_id}),
                0,
                None,
            ),
        )
        conn.execute(
            """
            INSERT INTO appgw_routing_rules (
                id, subscription_id, gateway_name, backend_fqdns, backend_pool_name,
                listener_name, hostname, url_path, protocol, waf_policy_name, resource_group
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "gw-1::listener-1",
                "sub-1",
                "cop-resource-server-apim",
                json.dumps([
                    "https://production-cards-management-web-uksouth.production-shared-uksouth.appserviceenvironment.net/"
                ]),
                "backend-pool",
                "listener-1",
                "cop-resource-server-apim.example.com",
                "/*",
                "HTTPS",
                None,
                "rg-app",
            ),
        )
        conn.commit()

        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
        client = app_module.app.test_client()
        resp = client.get("/api/cloud/resource-details", query_string={"id": site_id})

        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data["name"] == "cards-management-web"
        assert data["type_label"] == "App Service"
        assert data["parent_resource"]["name"] == "cards-plan"
        assert data["parent_resource"]["type_label"] == "App Service Plan"

    def test_api_cloud_resource_details_infers_ase_parent_for_internal_backend_site(self, monkeypatch):
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
                state TEXT
            );
            CREATE TABLE provisioned_assets (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                resource_group TEXT,
                name TEXT,
                type TEXT,
                location TEXT,
                sku TEXT,
                fqdn TEXT,
                is_public INTEGER DEFAULT 0,
                status TEXT,
                pipeline_tag TEXT,
                first_detected TEXT,
                last_synced TEXT,
                raw_json TEXT,
                is_restricted INTEGER DEFAULT 0,
                waf_mode TEXT
            );
            CREATE TABLE appgw_routing_rules (
                id TEXT,
                subscription_id TEXT,
                gateway_name TEXT,
                backend_fqdns TEXT,
                backend_pool_name TEXT,
                listener_name TEXT,
                hostname TEXT,
                url_path TEXT,
                protocol TEXT,
                waf_policy_name TEXT,
                resource_group TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state) VALUES (?, ?, ?, ?)",
            ("sub-1", "Test Subscription", "production", "Enabled"),
        )
        ase_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/hostingEnvironments/production-shared-uksouth"
        gw_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Network/applicationGateways/cop-resource-server-apim"
        site_id = "/subscriptions/sub-1/resourceGroups/rg-backend/providers/Microsoft.Web/sites/institution-portal-internal"
        conn.executemany(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, resource_group, name, type, location, sku, fqdn,
                is_public, status, pipeline_tag, first_detected, last_synced, raw_json,
                is_restricted, waf_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    ase_id,
                    "sub-1",
                    "rg-app",
                    "production-shared-uksouth",
                    "Microsoft.Web/hostingEnvironments",
                    "uksouth",
                    "ASEv3",
                    "production-shared-uksouth.appserviceenvironment.net",
                    0,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps({}),
                    0,
                    None,
                ),
                (
                    gw_id,
                    "sub-1",
                    "rg-app",
                    "cop-resource-server-apim",
                    "Microsoft.Network/applicationGateways",
                    "uksouth",
                    "WAF_v2",
                    "cop-resource-server-apim.example.com",
                    1,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps({}),
                    0,
                    None,
                ),
                (
                    site_id,
                    "sub-1",
                    "rg-backend",
                    "institution-portal-internal",
                    "Microsoft.Web/sites",
                    "uksouth",
                    "B1",
                    "institution-portal-internal.azurewebsites.net",
                    1,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps({"hostingEnvironmentProfile": {"id": ase_id}}),
                    0,
                    None,
                ),
            ],
        )
        conn.execute(
            """
            INSERT INTO appgw_routing_rules (
                id, subscription_id, gateway_name, backend_fqdns, backend_pool_name,
                listener_name, hostname, url_path, protocol, waf_policy_name, resource_group
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "gw-1::listener-1",
                "sub-1",
                "cop-resource-server-apim",
                json.dumps([
                    "https://production-institution-portal-internal-uksouth.production-shared-uksouth.appserviceenvironment.net/"
                ]),
                "backend-pool",
                "listener-1",
                "cop-resource-server-apim.example.com",
                "/*",
                "HTTPS",
                None,
                "rg-app",
            ),
        )
        conn.commit()

        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
        client = app_module.app.test_client()
        resp = client.get("/api/cloud/resource-details", query_string={"id": site_id})

        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data["parent_resource"]["name"] == "production-shared-uksouth"
        assert data["parent_resource"]["type_label"] == "App Service Environment"

    def test_api_cloud_apim_child_apis_returns_table_payload(self, monkeypatch):
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
                state TEXT
            );
            CREATE TABLE provisioned_assets (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                resource_group TEXT,
                name TEXT,
                type TEXT,
                raw_json TEXT
            );
            CREATE TABLE apim_api_routes (
                apim_name TEXT,
                subscription_id TEXT,
                api_name TEXT,
                api_display_name TEXT,
                api_path TEXT,
                api_protocols TEXT,
                backend_url TEXT,
                service_url TEXT,
                exposure_level TEXT,
                requires_subscription INTEGER
            );
            CREATE TABLE apim_api_operations (
                operation_id TEXT,
                display_name TEXT,
                method TEXT,
                url_template TEXT,
                description TEXT,
                requires_subscription INTEGER,
                apim_name TEXT,
                api_name TEXT,
                subscription_id TEXT
            );
            """
        )
        apim_id = "/subscriptions/sub-1/resourceGroups/rg-api/providers/Microsoft.ApiManagement/service/apim-prod"
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state) VALUES (?, ?, ?, ?)",
            ("sub-1", "Test Subscription", "production", "Enabled"),
        )
        conn.execute(
            """
            INSERT INTO provisioned_assets (id, subscription_id, resource_group, name, type, raw_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                apim_id,
                "sub-1",
                "rg-api",
                "apim-prod",
                "Microsoft.ApiManagement/service",
                json.dumps({"properties": {}}),
            ),
        )
        conn.execute(
            """
            INSERT INTO apim_api_routes (
                apim_name, subscription_id, api_name, api_display_name, api_path, api_protocols,
                backend_url, service_url, exposure_level, requires_subscription
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "apim-prod",
                "sub-1",
                "orders",
                "Orders API",
                "/orders",
                json.dumps(["HTTPS"]),
                "https://orders.internal",
                "https://apim-prod.azure-api.net",
                "Public",
                1,
            ),
        )
        conn.execute(
            """
            INSERT INTO apim_api_operations (
                operation_id, display_name, method, url_template, description,
                requires_subscription, apim_name, api_name, subscription_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "orders-get",
                "Get orders",
                "GET",
                "/orders",
                "List orders",
                1,
                "apim-prod",
                "orders",
                "sub-1",
            ),
        )
        conn.commit()

        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
        client = app_module.app.test_client()
        resp = client.get("/api/cloud/apim-child-apis", query_string={"resource_id": apim_id})

        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data["view_type"] == "table", data
        assert data["api_count"] == 1, data
        assert data["columns"][:3] == ["API", "Path", "Protocols"], data["columns"]
        assert data["rows"][0][0] == "Orders API", data["rows"]
        assert data["rows"][0][5] == 1, data["rows"]

    def test_api_cloud_resource_details_surfaces_waf_policy_summary(self, monkeypatch):
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
                state TEXT
            );
            CREATE TABLE appgw_waf_policies (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                name TEXT,
                resource_group TEXT,
                mode TEXT,
                state TEXT,
                request_body_check INTEGER DEFAULT 0,
                max_body_kb INTEGER,
                managed_rule_sets TEXT,
                custom_rules_count INTEGER DEFAULT 0,
                exclusions_count INTEGER DEFAULT 0,
                associated_gateways TEXT,
                last_synced TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state) VALUES (?, ?, ?, ?)",
            ("sub-1", "Test Subscription", "production", "Enabled"),
        )
        conn.execute(
            """
            INSERT INTO appgw_waf_policies (
                id, subscription_id, name, resource_group, mode, state, request_body_check,
                max_body_kb, managed_rule_sets, custom_rules_count, exclusions_count,
                associated_gateways, last_synced
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "policy-one",
                "sub-1",
                "policy-one",
                "rg-net",
                "Prevention",
                "Enabled",
                1,
                128,
                '[{"type": "OWASP", "version": "3.2"}]',
                2,
                3,
                '["appgw-one"]',
                "2026-06-01T00:00:00Z",
            ),
        )
        conn.commit()

        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
        client = app_module.app.test_client()
        resp = client.get(
            "/api/cloud/resource-details",
            query_string={
                "id": "policy-one",
                "name": "policy-one",
                "resource_group": "rg-net",
                "type": "Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies",
                "sub": "sub-1",
            },
        )

        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data["type_label"] == "WAF Policy"
        assert data["waf_policy"]["managed_rules_enabled"] is True
        assert data["waf_policy"]["custom_rules_count"] == 2
        assert data["waf_policy"]["exclusions_count"] == 3
        assert data["waf_policy"]["associated_gateways"] == ["appgw-one"]

    def test_api_cloud_resource_details_includes_storage_account_details(self, monkeypatch):
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
                state TEXT
            );
            CREATE TABLE provisioned_assets (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                resource_group TEXT,
                name TEXT,
                type TEXT,
                location TEXT,
                sku TEXT,
                fqdn TEXT,
                is_public INTEGER DEFAULT 0,
                status TEXT,
                pipeline_tag TEXT,
                first_detected TEXT,
                last_synced TEXT,
                raw_json TEXT,
                is_restricted INTEGER DEFAULT 0,
                waf_mode TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state) VALUES (?, ?, ?, ?)",
            ("sub-1", "Test Subscription", "production", "Enabled"),
        )
        storage_id = "/subscriptions/sub-1/resourceGroups/rg-data/providers/Microsoft.Storage/storageAccounts/sa-one"
        conn.execute(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, resource_group, name, type, location, sku, fqdn,
                is_public, status, pipeline_tag, first_detected, last_synced, raw_json,
                is_restricted, waf_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                storage_id,
                "sub-1",
                "rg-data",
                "sa-one",
                "Microsoft.Storage/storageAccounts",
                "westus",
                "Standard_LRS",
                "sa-one.blob.core.windows.net",
                1,
                "active",
                None,
                "2026-06-01T00:00:00Z",
                "2026-06-01T00:00:00Z",
                json.dumps(
                    {
                        "kind": "StorageV2",
                        "properties": {
                            "primaryEndpoints": {
                                "blob": "https://sa-one.blob.core.windows.net/",
                            },
                            "allowBlobPublicAccess": False,
                        },
                    }
                ),
                0,
                None,
            ),
        )
        conn.commit()

        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
        client = app_module.app.test_client()
        resp = client.get("/api/cloud/resource-details", query_string={"id": storage_id})

        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data["name"] == "sa-one"
        assert data["type_label"] == "Storage Account"
        assert data["network"]["dns_names"][0] == "sa-one.blob.core.windows.net"

    def test_api_cloud_resource_details_resolves_node_id_via_name_rg_type(self, monkeypatch):
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
                state TEXT
            );
            CREATE TABLE provisioned_assets (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                resource_group TEXT,
                name TEXT,
                type TEXT,
                location TEXT,
                sku TEXT,
                fqdn TEXT,
                is_public INTEGER DEFAULT 0,
                status TEXT,
                pipeline_tag TEXT,
                first_detected TEXT,
                last_synced TEXT,
                raw_json TEXT,
                is_restricted INTEGER DEFAULT 0,
                waf_mode TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state) VALUES (?, ?, ?, ?)",
            ("sub-1", "Production", "production", "Enabled"),
        )
        bastion_id = "/subscriptions/sub-1/resourceGroups/green-network-ukwest/providers/Microsoft.Network/bastionHosts/bastion"
        conn.execute(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, resource_group, name, type, location, sku, fqdn,
                is_public, status, pipeline_tag, first_detected, last_synced, raw_json,
                is_restricted, waf_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bastion_id,
                "sub-1",
                "green-network-ukwest",
                "bastion",
                "Microsoft.Network/bastionHosts",
                "ukwest",
                "Standard",
                None,
                0,
                "active",
                None,
                "2026-06-01T00:00:00Z",
                "2026-06-01T00:00:00Z",
                json.dumps({"properties": {}}),
                0,
                None,
            ),
        )
        conn.commit()

        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
        client = app_module.app.test_client()
        resp = client.get(
            "/api/cloud/resource-details",
            query_string={
                "id": "green_network_ukwest_bastion_bastion",
                "name": "bastion",
                "resource_group": "green-network-ukwest",
                "type": "Microsoft.Network/bastionHosts",
                "sub": "Production",
            },
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data["id"] == bastion_id
        assert data["name"] == "bastion"

    def test_api_cloud_architecture_omits_public_ip_nodes_and_surfaces_on_bastion(self, monkeypatch):
        import json
        import os
        import sqlite3
        import sys
        import tempfile

        sys.path.insert(0, str(REPO_ROOT))
        os.environ.setdefault("FLASK_APP", "web/app.py")
        import web.app as app_module

        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp.close()
        conn = sqlite3.connect(tmp.name)
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE subscriptions (
                id TEXT PRIMARY KEY,
                display_name TEXT,
                environment TEXT,
                state TEXT
            );
            CREATE TABLE provisioned_assets (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                resource_group TEXT,
                name TEXT,
                type TEXT,
                location TEXT,
                sku TEXT,
                fqdn TEXT,
                is_public INTEGER DEFAULT 0,
                status TEXT,
                pipeline_tag TEXT,
                first_detected TEXT,
                last_synced TEXT,
                raw_json TEXT,
                is_restricted INTEGER DEFAULT 0,
                waf_mode TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state) VALUES (?, ?, ?, ?)",
            ("sub-1", "Test Subscription", "production", "Enabled"),
        )
        public_ip_id = "/subscriptions/sub-1/resourceGroups/blue-network-ukwest/providers/Microsoft.Network/publicIPAddresses/bastion"
        subnet_id = "/subscriptions/sub-1/resourceGroups/blue-network-ukwest/providers/Microsoft.Network/virtualNetworks/blue-vnet/subnets/bastion-subnet"
        bastion_id = "/subscriptions/sub-1/resourceGroups/blue-network-ukwest/providers/Microsoft.Network/bastionHosts/bastion"
        conn.executemany(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, resource_group, name, type, location, sku, fqdn,
                is_public, status, pipeline_tag, first_detected, last_synced, raw_json,
                is_restricted, waf_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    subnet_id,
                    "sub-1",
                    "blue-network-ukwest",
                    "bastion-subnet",
                    "Microsoft.Network/virtualNetworks/subnets",
                    "uksouth",
                    None,
                    None,
                    0,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps({
                        "properties": {"addressPrefix": "10.0.1.0/24"},
                        "_extra": {
                            "parent_vnet_name": "blue-vnet",
                            "parent_vnet_resource_group": "blue-network-ukwest",
                            "subnet_name": "bastion-subnet",
                        },
                    }),
                    0,
                    None,
                ),
                (
                    bastion_id,
                    "sub-1",
                    "blue-network-ukwest",
                    "bastion",
                    "Microsoft.Network/bastionHosts",
                    "uksouth",
                    "Standard",
                    None,
                    0,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps(
                        {
                            "properties": {
                                "ipConfigurations": [
                                    {
                                        "properties": {
                                            "subnet": {"id": subnet_id},
                                            "publicIPAddress": {"id": public_ip_id},
                                        }
                                    }
                                ]
                            }
                        }
                    ),
                    0,
                    None,
                ),
                (
                    public_ip_id,
                    "sub-1",
                    "blue-network-ukwest",
                    "bastion",
                    "Microsoft.Network/publicIPAddresses",
                    "uksouth",
                    "Standard",
                    "bastion.example.com",
                    1,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps({"properties": {"ipAddress": "20.30.40.50", "dnsSettings": {"fqdn": "bastion.example.com"}}}),
                    0,
                    None,
                ),
            ],
        )
        conn.commit()

        def _db():
            c = sqlite3.connect(tmp.name)
            c.row_factory = sqlite3.Row
            return c

        monkeypatch.setattr(app_module, "_get_db_with_schema", _db)
        client = app_module.app.test_client()

        graph_resp = client.get("/api/cloud/architecture?sub=sub-1&view=reactflow")
        assert graph_resp.status_code == 200, graph_resp.get_data(as_text=True)
        graph = graph_resp.get_json()
        assert not any("publicipaddresses" in str((n.get("data") or {}).get("resourceType", "")).lower() for n in graph["nodes"])
        bastion_node = next(n for n in graph["nodes"] if str((n.get("data") or {}).get("resourceType", "")).lower().endswith("/bastionhosts"))
        assert "blue-network-ukwest" in str((bastion_node.get("data") or {}).get("label", ""))

        details_resp = client.get("/api/cloud/resource-details", query_string={"id": bastion_id})
        assert details_resp.status_code == 200, details_resp.get_data(as_text=True)
        details = details_resp.get_json()
        assert details["fqdn"] == "bastion.example.com"
        assert "20.30.40.50" in details["network"]["public_ips"]

        diagram_resp = client.get("/api/subscriptions/sub-1/diagram")
        assert diagram_resp.status_code == 200, diagram_resp.get_data(as_text=True)
        diagram = diagram_resp.get_json()
        mermaid = diagram["ingress_diagram"]["mermaid"]
        assert "Subnet: bastion-subnet" in mermaid, mermaid
        subnet_block = mermaid[mermaid.index("Subnet: bastion-subnet"):]
        assert "Bastion" in subnet_block.split("end", 1)[0], mermaid

        diagram_resp = client.get("/api/subscriptions/sub-1/diagram")
        assert diagram_resp.status_code == 200, diagram_resp.get_data(as_text=True)
        diagram = diagram_resp.get_json()
        mermaid = diagram["ingress_diagram"]["mermaid"]
        assert "Subnet: bastion-subnet" in mermaid, mermaid
        assert "Bastion" in mermaid, mermaid
        subnet_block = mermaid[mermaid.index("Subnet: bastion-subnet"):]
        assert "Bastion" in subnet_block.split("end", 1)[0], mermaid
        os.unlink(tmp.name)

    def test_api_cloud_architecture_surfaces_public_load_balancer_via_associated_public_ip(self, monkeypatch):
        import json
        import os
        import sqlite3
        import sys
        import tempfile

        sys.path.insert(0, str(REPO_ROOT))
        os.environ.setdefault("FLASK_APP", "web/app.py")
        import web.app as app_module

        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp.close()
        conn = sqlite3.connect(tmp.name)
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE subscriptions (
                id TEXT PRIMARY KEY,
                display_name TEXT,
                environment TEXT,
                state TEXT
            );
            CREATE TABLE provisioned_assets (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                resource_group TEXT,
                name TEXT,
                type TEXT,
                location TEXT,
                sku TEXT,
                fqdn TEXT,
                is_public INTEGER DEFAULT 0,
                status TEXT,
                pipeline_tag TEXT,
                first_detected TEXT,
                last_synced TEXT,
                raw_json TEXT,
                is_restricted INTEGER DEFAULT 0,
                waf_mode TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state) VALUES (?, ?, ?, ?)",
            ("sub-1", "Test Subscription", "production", "Enabled"),
        )
        load_balancer_id = "/subscriptions/sub-1/resourceGroups/blue-pbi-gateway-ukwest/providers/Microsoft.Network/loadBalancers/pbi-gateway"
        vmss_id = "/subscriptions/sub-1/resourceGroups/blue-pbi-gateway-ukwest/providers/Microsoft.Compute/virtualMachineScaleSets/power_bi_gateway"
        public_ip_id = "/subscriptions/sub-1/resourceGroups/blue-pbi-gateway-ukwest/providers/Microsoft.Network/publicIPAddresses/backend"
        conn.executemany(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, resource_group, name, type, location, sku, fqdn,
                is_public, status, pipeline_tag, first_detected, last_synced, raw_json,
                is_restricted, waf_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    load_balancer_id,
                    "sub-1",
                    "blue-pbi-gateway-ukwest",
                    "pbi-gateway",
                    "Microsoft.Network/loadBalancers",
                    "ukwest",
                    "Standard",
                    "pbi-gateway.example.contoso.com",
                    1,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps(
                        {
                            "_extra": {
                                "routing_targets": [
                                    {
                                        "target": "power_bi_gateway",
                                        "name": "power_bi_gateway",
                                        "type": "Microsoft.Compute/virtualMachineScaleSets",
                                    }
                                ]
                            },
                            "properties": {
                                "frontendIPConfigurations": [
                                    {"properties": {"publicIPAddress": {"id": public_ip_id}}}
                                ]
                            }
                        }
                    ),
                    0,
                    None,
                ),
                (
                    vmss_id,
                    "sub-1",
                    "blue-pbi-gateway-ukwest",
                    "power_bi_gateway",
                    "Microsoft.Compute/virtualMachineScaleSets",
                    "ukwest",
                    "Standard_A8_v2",
                    None,
                    0,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps({"properties": {"virtualMachineProfile": {"networkProfile": {"networkInterfaceConfigurations": []}}}}),
                    0,
                    None,
                ),
                (
                    public_ip_id,
                    "sub-1",
                    "blue-pbi-gateway-ukwest",
                    "backend",
                    "Microsoft.Network/publicIPAddresses",
                    "ukwest",
                    "Standard",
                    None,
                    1,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps({"properties": {"ipAddress": "20.30.40.50"}}),
                    0,
                    None,
                ),
            ],
        )
        conn.commit()

        def _db():
            c = sqlite3.connect(tmp.name)
            c.row_factory = sqlite3.Row
            return c

        monkeypatch.setattr(app_module, "_get_db_with_schema", _db)
        client = app_module.app.test_client()

        graph_resp = client.get("/api/cloud/architecture?sub=sub-1&view=connectivity")
        assert graph_resp.status_code == 200, graph_resp.get_data(as_text=True)
        graph = graph_resp.get_json()
        mermaid = graph["views"]["connectivity"]["mermaid"]
        lb_node_id = "blue_pbi_gateway_ukwest_pbi_gateway"
        vmss_node_id = "blue_pbi_gateway_ukwest_power_bi_gateway"
        assert f'{lb_node_id}["' in mermaid, mermaid
        assert f'{lb_node_id} -->|"Load balancing"| {vmss_node_id}' in mermaid, mermaid
        assert "pbi-gateway.example.contoso.com" not in mermaid, mermaid
        assert "#06b6d4" not in mermaid, mermaid
        assert "simulation_knowledgecentre" not in mermaid, mermaid

        details_resp = client.get("/api/cloud/resource-details", query_string={"id": load_balancer_id})
        assert details_resp.status_code == 200, details_resp.get_data(as_text=True)
        details = details_resp.get_json()
        assert "20.30.40.50" in details["network"]["public_ips"]
        assert any(str(item.get("target") or "") == "power_bi_gateway" for item in details["network"]["routing_targets"])

        rf_resp = client.get("/api/cloud/architecture?sub=sub-1&view=reactflow")
        assert rf_resp.status_code == 200, rf_resp.get_data(as_text=True)
        rf_graph = rf_resp.get_json()
        assert any(
            edge.get("source") == load_balancer_id and edge.get("target") == vmss_id
            for edge in rf_graph["edges"]
        ), rf_graph["edges"]

        os.unlink(tmp.name)

    def test_api_cloud_architecture_reactflow_ignores_synthetic_network_parent_cycles(self, monkeypatch):
        import json
        import os
        import sqlite3
        import sys
        import tempfile

        sys.path.insert(0, str(REPO_ROOT))
        os.environ.setdefault("FLASK_APP", "web/app.py")
        import web.app as app_module

        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp.close()
        conn = sqlite3.connect(tmp.name)
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE subscriptions (
                id TEXT PRIMARY KEY,
                display_name TEXT,
                environment TEXT,
                state TEXT
            );
            CREATE TABLE provisioned_assets (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                resource_group TEXT,
                name TEXT,
                type TEXT,
                location TEXT,
                sku TEXT,
                fqdn TEXT,
                is_public INTEGER DEFAULT 0,
                status TEXT,
                pipeline_tag TEXT,
                first_detected TEXT,
                last_synced TEXT,
                raw_json TEXT,
                is_restricted INTEGER DEFAULT 0,
                waf_mode TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state) VALUES (?, ?, ?, ?)",
            ("sub-1", "Test Subscription", "production", "Enabled"),
        )
        cluster_id = "/subscriptions/sub-1/resourceGroups/rg-sf/providers/Microsoft.ServiceFabric/clusters/cluster-one"
        conn.execute(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, resource_group, name, type, location, sku, fqdn,
                is_public, status, pipeline_tag, first_detected, last_synced, raw_json,
                is_restricted, waf_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cluster_id,
                "sub-1",
                "rg-sf",
                "cluster-one",
                "Microsoft.ServiceFabric/clusters",
                "ukwest",
                "Standard",
                None,
                0,
                "active",
                None,
                "2026-06-01T00:00:00Z",
                "2026-06-01T00:00:00Z",
                json.dumps(
                    {
                        "properties": {
                            "virtualMachineProfile": {
                                "networkProfile": {
                                    "networkInterfaceConfigurations": [
                                        {
                                            "properties": {
                                                "ipConfigurations": [
                                                    {
                                                        "properties": {
                                                            "subnet": {
                                                                "id": "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/virtualNetworks/prod-vnet/subnets/cluster-subnet"
                                                            }
                                                        }
                                                    }
                                                ]
                                            }
                                        }
                                    ]
                                }
                            }
                        }
                    }
                ),
                0,
                None,
            ),
        )
        conn.commit()

        def _db():
            c = sqlite3.connect(tmp.name)
            c.row_factory = sqlite3.Row
            return c

        monkeypatch.setattr(app_module, "_get_db_with_schema", _db)
        client = app_module.app.test_client()

        resp = client.get("/api/cloud/architecture?sub=sub-1&view=reactflow")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        graph = resp.get_json()
        nodes = graph["nodes"]
        by_id = {str(node.get("id") or ""): node for node in nodes if node.get("id")}

        assert all(
            str((node.get("data") or {}).get("parentNodeId") or "").strip() != str(node.get("id") or "").strip()
            for node in nodes
        ), graph["nodes"]

        for node_id in by_id:
            seen = set()
            cursor = node_id
            while True:
                parent_id = str((by_id.get(cursor, {}).get("data") or {}).get("parentNodeId") or "").strip()
                if not parent_id:
                    break
                assert parent_id not in seen, graph["nodes"]
                seen.add(parent_id)
                if parent_id not in by_id:
                    break
                cursor = parent_id

        os.unlink(tmp.name)

    def test_api_cloud_architecture_surfaces_public_vmss_via_associated_public_ip(self, monkeypatch):
        import json
        import os
        import sqlite3
        import sys
        import tempfile

        sys.path.insert(0, str(REPO_ROOT))
        os.environ.setdefault("FLASK_APP", "web/app.py")
        import web.app as app_module

        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp.close()
        conn = sqlite3.connect(tmp.name)
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE subscriptions (
                id TEXT PRIMARY KEY,
                display_name TEXT,
                environment TEXT,
                state TEXT
            );
            CREATE TABLE provisioned_assets (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                resource_group TEXT,
                name TEXT,
                type TEXT,
                location TEXT,
                sku TEXT,
                fqdn TEXT,
                is_public INTEGER DEFAULT 0,
                status TEXT,
                pipeline_tag TEXT,
                first_detected TEXT,
                last_synced TEXT,
                raw_json TEXT,
                is_restricted INTEGER DEFAULT 0,
                waf_mode TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state) VALUES (?, ?, ?, ?)",
            ("sub-1", "Test Subscription", "production", "Enabled"),
        )
        vmss_id = "/subscriptions/sub-1/resourceGroups/rg-compute/providers/Microsoft.Compute/virtualMachineScaleSets/scale-set-one"
        public_ip_id = "/subscriptions/sub-1/resourceGroups/rg-compute/providers/Microsoft.Network/publicIPAddresses/vmss-pip"
        conn.executemany(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, resource_group, name, type, location, sku, fqdn,
                is_public, status, pipeline_tag, first_detected, last_synced, raw_json,
                is_restricted, waf_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    vmss_id,
                    "sub-1",
                    "rg-compute",
                    "scale-set-one",
                    "Microsoft.Compute/virtualMachineScaleSets",
                    "ukwest",
                    "Standard_DS2_v2",
                    None,
                    0,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps(
                        {
                            "properties": {
                                "virtualMachineProfile": {
                                    "storageProfile": {
                                        "osDisk": {
                                            "osType": "Linux"
                                        }
                                    },
                                    "networkProfile": {
                                        "networkInterfaceConfigurations": [
                                            {
                                                "properties": {
                                                    "ipConfigurations": [
                                                        {
                                                            "properties": {
                                                                "subnet": {
                                                                    "id": "/subscriptions/sub-1/resourceGroups/rg-compute/providers/Microsoft.Network/virtualNetworks/rg-vnet/subnets/vmss-subnet"
                                                                },
                                                                "publicIPAddress": {"id": public_ip_id}
                                                            }
                                                        }
                                                    ]
                                                }
                                            }
                                        ]
                                    }
                                }
                            }
                        }
                    ),
                    0,
                    None,
                ),
                (
                    public_ip_id,
                    "sub-1",
                    "rg-compute",
                    "vmss-pip",
                    "Microsoft.Network/publicIPAddresses",
                    "ukwest",
                    "Standard",
                    None,
                    1,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps({"properties": {"ipAddress": "20.30.40.60"}}),
                    0,
                    None,
                ),
            ],
        )
        conn.commit()

        def _db():
            c = sqlite3.connect(tmp.name)
            c.row_factory = sqlite3.Row
            return c

        monkeypatch.setattr(app_module, "_get_db_with_schema", _db)
        client = app_module.app.test_client()

        graph_resp = client.get("/api/cloud/architecture?sub=sub-1&view=connectivity")
        assert graph_resp.status_code == 200, graph_resp.get_data(as_text=True)
        graph = graph_resp.get_json()
        mermaid = graph["views"]["connectivity"]["mermaid"]
        vmss_node_id = app_module._sanitise_node_id("rg-compute_scale-set-one")
        assert f'{vmss_node_id}["' in mermaid, mermaid
        assert "scale-set-one.example.contoso.com" not in mermaid, mermaid
        assert "20.30.40.60" not in mermaid, mermaid
        assert 'Internet -->|"Public IP' in mermaid, mermaid
        assert f"| {vmss_node_id}" in mermaid, mermaid

        details_resp = client.get("/api/cloud/resource-details", query_string={"id": vmss_id})
        assert details_resp.status_code == 200, details_resp.get_data(as_text=True)
        details = details_resp.get_json()
        assert details["name"] == "scale-set-one"
        assert details["configuration"]["operating_system"] == "Linux"
        assert details["network"]["vnet"] == "rg-vnet"
        assert details["network"]["subnet"] == "vmss-subnet"
        assert "20.30.40.60" in details["network"]["public_ips"]

        os.unlink(tmp.name)

    def test_api_cloud_resource_details_prefers_service_fabric_network_parent_over_key_vault(self, monkeypatch):
        import json
        import os
        import sqlite3
        import sys
        import tempfile

        sys.path.insert(0, str(REPO_ROOT))
        os.environ.setdefault("FLASK_APP", "web/app.py")
        import web.app as app_module

        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp.close()
        conn = sqlite3.connect(tmp.name)
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE subscriptions (
                id TEXT PRIMARY KEY,
                display_name TEXT,
                environment TEXT,
                state TEXT
            );
            CREATE TABLE provisioned_assets (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                resource_group TEXT,
                name TEXT,
                type TEXT,
                location TEXT,
                sku TEXT,
                fqdn TEXT,
                is_public INTEGER DEFAULT 0,
                status TEXT,
                pipeline_tag TEXT,
                first_detected TEXT,
                last_synced TEXT,
                raw_json TEXT,
                is_restricted INTEGER DEFAULT 0,
                waf_mode TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state) VALUES (?, ?, ?, ?)",
            ("sub-1", "Test Subscription", "production", "Enabled"),
        )
        cluster_id = "/subscriptions/sub-1/resourceGroups/production-sf-uksouth/providers/Microsoft.ServiceFabric/clusters/production-sf"
        key_vault_id = "/subscriptions/sub-1/resourceGroups/production-sf-uksouth/providers/Microsoft.KeyVault/vaults/production-kv"
        subnet_id = "/subscriptions/sub-1/resourceGroups/production-network-uksouth/providers/Microsoft.Network/virtualNetworks/production/subnets/service_fabric_zonal"
        conn.executemany(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, resource_group, name, type, location, sku, fqdn,
                is_public, status, pipeline_tag, first_detected, last_synced, raw_json,
                is_restricted, waf_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    cluster_id,
                    "sub-1",
                    "production-sf-uksouth",
                    "production-sf",
                    "Microsoft.ServiceFabric/clusters",
                    "uksouth",
                    "Standard",
                    None,
                    0,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps({
                        "properties": {
                            "virtualMachineProfile": {
                                "networkProfile": {
                                    "networkInterfaceConfigurations": [
                                        {
                                            "properties": {
                                                "ipConfigurations": [
                                                    {
                                                        "properties": {
                                                            "subnet": {"id": subnet_id}
                                                        }
                                                    }
                                                ]
                                            }
                                        }
                                    ]
                                }
                            }
                        }
                    }),
                    0,
                    None,
                ),
                (
                    key_vault_id,
                    "sub-1",
                    "production-sf-uksouth",
                    "production-kv",
                    "Microsoft.KeyVault/vaults",
                    "uksouth",
                    "Standard",
                    None,
                    0,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps({"properties": {"vaultUri": "https://production-sf.vault.azure.net/"}}),
                    0,
                    None,
                ),
            ],
        )
        conn.commit()

        def _db():
            c = sqlite3.connect(tmp.name)
            c.row_factory = sqlite3.Row
            return c

        monkeypatch.setattr(app_module, "_get_db_with_schema", _db)
        client = app_module.app.test_client()

        details_resp = client.get("/api/cloud/resource-details", query_string={"id": cluster_id})
        assert details_resp.status_code == 200, details_resp.get_data(as_text=True)
        details = details_resp.get_json()
        assert details["name"] == "production-sf"
        assert details["parent_resource"]["type_label"] != "Key Vault"
        assert details["parent_resource"]["type_label"] in {"Subnet", "Virtual Network"}
        assert details["network"]["vnet"] == "production"
        assert details["network"]["subnet"] == "service_fabric_zonal"

        os.unlink(tmp.name)

    def test_api_cloud_resource_details_surfaces_aks_os_type(self, monkeypatch):
        import json
        import os
        import sqlite3
        import sys
        import tempfile

        sys.path.insert(0, str(REPO_ROOT))
        os.environ.setdefault("FLASK_APP", "web/app.py")
        import web.app as app_module

        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp.close()
        conn = sqlite3.connect(tmp.name)
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE subscriptions (
                id TEXT PRIMARY KEY,
                display_name TEXT,
                environment TEXT,
                state TEXT
            );
            CREATE TABLE provisioned_assets (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                resource_group TEXT,
                name TEXT,
                type TEXT,
                location TEXT,
                sku TEXT,
                fqdn TEXT,
                is_public INTEGER DEFAULT 0,
                status TEXT,
                pipeline_tag TEXT,
                first_detected TEXT,
                last_synced TEXT,
                raw_json TEXT,
                is_restricted INTEGER DEFAULT 0,
                waf_mode TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state) VALUES (?, ?, ?, ?)",
            ("sub-1", "Test Subscription", "production", "Enabled"),
        )
        aks_id = "/subscriptions/sub-1/resourceGroups/rg-k8s/providers/Microsoft.ContainerService/managedClusters/cluster-one"
        conn.execute(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, resource_group, name, type, location, sku, fqdn,
                is_public, status, pipeline_tag, first_detected, last_synced, raw_json,
                is_restricted, waf_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                aks_id,
                "sub-1",
                "rg-k8s",
                "cluster-one",
                "Microsoft.ContainerService/managedClusters",
                "ukwest",
                "Standard",
                None,
                0,
                "active",
                None,
                "2026-06-01T00:00:00Z",
                "2026-06-01T00:00:00Z",
                json.dumps({
                    "agentPoolProfiles": [
                        {"name": "nodepool1", "osType": "Linux", "count": 3},
                    ]
                }),
                0,
                None,
            ),
        )
        conn.commit()

        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
        client = app_module.app.test_client()
        resp = client.get("/api/cloud/resource-details", query_string={"id": aks_id})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        details = resp.get_json()
        assert details["configuration"]["operating_system"] == "Linux"

        os.unlink(tmp.name)

    def test_api_cloud_resource_details_lists_aks_services_with_ingress(self, monkeypatch):
        import json
        import os
        import sqlite3
        import sys
        import tempfile

        sys.path.insert(0, str(REPO_ROOT))
        os.environ.setdefault("FLASK_APP", "web/app.py")
        import web.app as app_module

        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp.close()
        conn = sqlite3.connect(tmp.name)
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE subscriptions (
                id TEXT PRIMARY KEY,
                display_name TEXT,
                environment TEXT,
                state TEXT
            );
            CREATE TABLE provisioned_assets (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                resource_group TEXT,
                name TEXT,
                type TEXT,
                location TEXT,
                sku TEXT,
                fqdn TEXT,
                is_public INTEGER DEFAULT 0,
                status TEXT,
                pipeline_tag TEXT,
                first_detected TEXT,
                last_synced TEXT,
                raw_json TEXT,
                is_restricted INTEGER DEFAULT 0,
                waf_mode TEXT
            );
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
                deployment_name TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state) VALUES (?, ?, ?, ?)",
            ("sub-1", "Test Subscription", "production", "Enabled"),
        )
        aks_id = "/subscriptions/sub-1/resourceGroups/rg-k8s/providers/Microsoft.ContainerService/managedClusters/cluster-one"
        conn.execute(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, resource_group, name, type, location, sku, fqdn,
                is_public, status, pipeline_tag, first_detected, last_synced, raw_json,
                is_restricted, waf_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                aks_id,
                "sub-1",
                "rg-k8s",
                "cluster-one",
                "Microsoft.ContainerService/managedClusters",
                "ukwest",
                "Standard",
                None,
                0,
                "active",
                None,
                "2026-06-01T00:00:00Z",
                "2026-06-01T00:00:00Z",
                json.dumps({}),
                0,
                None,
            ),
        )
        conn.executemany(
            """
            INSERT INTO aks_routes (
                subscription_id, cluster_name, resource_group, namespace, ingress_name,
                host, path, service_name, service_port, deployment_name
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("sub-1", "cluster-one", "rg-k8s", "storefront", "store-ingress", "store.example.com", "/*", "store-web", "80", "store-web"),
                ("sub-1", "cluster-one", "rg-k8s", "orders", "orders-ingress", "orders.example.com", "/api", "orders-api", "8080", "orders-api"),
                ("sub-1", "cluster-two", "rg-k8s", "other", "other-ingress", "other.example.com", "/*", "other-api", "80", "other-api"),
            ],
        )
        conn.commit()

        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
        client = app_module.app.test_client()
        resp = client.get("/api/cloud/resource-details", query_string={"id": aks_id})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        services = resp.get_json()["ingress_services"]
        assert [(service["namespace"], service["name"]) for service in services] == [
            ("orders", "orders-api"),
            ("storefront", "store-web"),
        ]

        os.unlink(tmp.name)

    def test_api_cloud_resource_details_surfaces_aks_network(self, monkeypatch):
        import json
        import os
        import sqlite3
        import sys
        import tempfile

        sys.path.insert(0, str(REPO_ROOT))
        os.environ.setdefault("FLASK_APP", "web/app.py")
        import web.app as app_module

        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp.close()
        conn = sqlite3.connect(tmp.name)
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE subscriptions (
                id TEXT PRIMARY KEY,
                display_name TEXT,
                environment TEXT,
                state TEXT
            );
            CREATE TABLE provisioned_assets (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                resource_group TEXT,
                name TEXT,
                type TEXT,
                location TEXT,
                sku TEXT,
                fqdn TEXT,
                is_public INTEGER DEFAULT 0,
                status TEXT,
                pipeline_tag TEXT,
                first_detected TEXT,
                last_synced TEXT,
                raw_json TEXT,
                is_restricted INTEGER DEFAULT 0,
                waf_mode TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state) VALUES (?, ?, ?, ?)",
            ("sub-1", "Test Subscription", "production", "Enabled"),
        )
        aks_id = "/subscriptions/sub-1/resourceGroups/rg-k8s/providers/Microsoft.ContainerService/managedClusters/cluster-one"
        conn.execute(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, resource_group, name, type, location, sku, fqdn,
                is_public, status, pipeline_tag, first_detected, last_synced, raw_json,
                is_restricted, waf_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                aks_id,
                "sub-1",
                "rg-k8s",
                "cluster-one",
                "Microsoft.ContainerService/managedClusters",
                "ukwest",
                "Standard",
                None,
                0,
                "active",
                None,
                "2026-06-01T00:00:00Z",
                "2026-06-01T00:00:00Z",
                json.dumps({
                    "agentPoolProfiles": [
                        {
                            "name": "nodepool1",
                            "osType": "Linux",
                            "vnetSubnetId": "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/virtualNetworks/rg-vnet/subnets/aks-subnet",
                        }
                    ]
                }),
                0,
                None,
            ),
        )
        conn.commit()

        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
        client = app_module.app.test_client()
        resp = client.get("/api/cloud/resource-details", query_string={"id": aks_id})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        details = resp.get_json()
        assert details["network"]["vnet"] == "rg-vnet"
        assert details["network"]["subnet"] == "aks-subnet"

        os.unlink(tmp.name)

    def test_api_cloud_architecture_surfaces_app_service_network(self, monkeypatch):
        import json
        import os
        import sqlite3
        import sys
        import tempfile

        sys.path.insert(0, str(REPO_ROOT))
        os.environ.setdefault("FLASK_APP", "web/app.py")
        import web.app as app_module

        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp.close()
        conn = sqlite3.connect(tmp.name)
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE subscriptions (
                id TEXT PRIMARY KEY,
                display_name TEXT,
                environment TEXT,
                state TEXT
            );
            CREATE TABLE provisioned_assets (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                resource_group TEXT,
                name TEXT,
                type TEXT,
                location TEXT,
                sku TEXT,
                fqdn TEXT,
                is_public INTEGER DEFAULT 0,
                status TEXT,
                pipeline_tag TEXT,
                first_detected TEXT,
                last_synced TEXT,
                raw_json TEXT,
                is_restricted INTEGER DEFAULT 0,
                waf_mode TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state) VALUES (?, ?, ?, ?)",
            ("sub-1", "Test Subscription", "production", "Enabled"),
        )
        ase_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/hostingEnvironments/ase-one"
        plan_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/serverfarms/api_windows"
        site_ase_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/sites/site-ase"
        site_vnet_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/sites/site-vnet"
        subnet_id = "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/virtualNetworks/rg-vnet/subnets/app-subnet"
        conn.executemany(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, resource_group, name, type, location, sku, fqdn,
                is_public, status, pipeline_tag, first_detected, last_synced, raw_json,
                is_restricted, waf_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    plan_id,
                    "sub-1",
                    "rg-app",
                    "api_windows",
                    "Microsoft.Web/serverFarms",
                    "P1v3",
                    None,
                    None,
                    0,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps({"hostingEnvironmentProfile": {"id": ase_id}}),
                    0,
                    None,
                ),
                (
                    ase_id,
                    "sub-1",
                    "rg-app",
                    "ase-one",
                    "Microsoft.Web/hostingEnvironments",
                    "uksouth",
                    "I1v2",
                    None,
                    0,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps({"virtualNetwork": {"id": subnet_id}}),
                    0,
                    None,
                ),
                (
                    site_ase_id,
                    "sub-1",
                    "rg-app",
                    "site-ase",
                    "Microsoft.Web/sites",
                    "uksouth",
                    "B1",
                    "site-ase.example.com",
                    0,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps({"hostingEnvironmentProfile": {"id": ase_id}}),
                    0,
                    None,
                ),
                (
                    site_vnet_id,
                    "sub-1",
                    "rg-app",
                    "site-vnet",
                    "Microsoft.Web/sites",
                    "uksouth",
                    "B1",
                    "site-vnet.example.com",
                    0,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps({"siteConfig": {"virtualNetworkSubnetId": subnet_id}}),
                    0,
                    None,
                ),
            ],
        )
        conn.commit()

        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
        client = app_module.app.test_client()
        resp = client.get("/api/cloud/architecture?sub=sub-1&view=reactflow")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        graph = resp.get_json()
        nodes = {n["id"]: n for n in graph["nodes"]}
        plan_node = nodes[plan_id]
        ase_node = nodes[ase_id]
        ase_net = ase_node["data"]["network"]
        assert ase_net["vnet"] == "rg-vnet"
        assert ase_net["subnet"] == "app-subnet"
        ase_parent_id = ase_node["data"]["parentNodeId"]
        assert ase_parent_id and str(ase_parent_id).startswith("synthetic-subnet::"), ase_parent_id
        assert nodes[ase_parent_id]["data"]["parentNodeId"] and str(nodes[ase_parent_id]["data"]["parentNodeId"]).startswith("synthetic-network::")
        assert plan_node["data"]["parentNodeId"] == ase_id

        site_ase = nodes[site_ase_id]["data"]["network"]
        site_vnet = nodes[site_vnet_id]["data"]["network"]
        assert site_ase["vnet"] == "rg-vnet"
        assert site_ase["subnet"] == "app-subnet"
        assert site_vnet["vnet"] == "rg-vnet"
        assert site_vnet["subnet"] == "app-subnet"

        os.unlink(tmp.name)

    def test_api_cloud_architecture_surfaces_aks_ingress_routes_in_reactflow(self, monkeypatch):
        import json
        import os
        import sqlite3
        import sys
        import tempfile

        sys.path.insert(0, str(REPO_ROOT))
        os.environ.setdefault("FLASK_APP", "web/app.py")
        import web.app as app_module

        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp.close()
        conn = sqlite3.connect(tmp.name)
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE subscriptions (
                id TEXT PRIMARY KEY,
                display_name TEXT,
                environment TEXT,
                state TEXT
            );
            CREATE TABLE provisioned_assets (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                resource_group TEXT,
                name TEXT,
                type TEXT,
                location TEXT,
                sku TEXT,
                fqdn TEXT,
                is_public INTEGER DEFAULT 0,
                status TEXT,
                pipeline_tag TEXT,
                first_detected TEXT,
                last_synced TEXT,
                raw_json TEXT,
                is_restricted INTEGER DEFAULT 0,
                waf_mode TEXT
            );
            CREATE TABLE aks_routes (
                cluster_name TEXT,
                namespace TEXT,
                ingress_name TEXT,
                host TEXT,
                path TEXT,
                service_name TEXT,
                service_port INTEGER,
                deployment_name TEXT,
                git_repository TEXT,
                resource_group TEXT,
                pod_template_labels TEXT,
                subscription_id TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state) VALUES (?, ?, ?, ?)",
            ("sub-1", "Test Subscription", "production", "Enabled"),
        )
        aks_id = "/subscriptions/sub-1/resourceGroups/rg-k8s/providers/Microsoft.ContainerService/managedClusters/cluster-one"
        conn.execute(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, resource_group, name, type, location, sku, fqdn,
                is_public, status, pipeline_tag, first_detected, last_synced, raw_json,
                is_restricted, waf_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                aks_id,
                "sub-1",
                "rg-k8s",
                "cluster-one",
                "Microsoft.ContainerService/managedClusters",
                "ukwest",
                "Standard",
                None,
                0,
                "active",
                None,
                "2026-06-01T00:00:00Z",
                "2026-06-01T00:00:00Z",
                json.dumps({}),
                0,
                None,
            ),
        )
        conn.execute(
            """
            INSERT INTO aks_routes (
                cluster_name, namespace, ingress_name, host, path, service_name,
                service_port, deployment_name, git_repository, resource_group,
                pod_template_labels, subscription_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "cluster-one",
                "default",
                "orders-ingress",
                "aks.example.com",
                "/*",
                "orders-api",
                80,
                "orders-api",
                "https://github.com/org/repo",
                "rg-k8s",
                json.dumps({"app.kubernetes.io/component": "api"}),
                "sub-1",
            ),
        )
        conn.commit()

        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
        client = app_module.app.test_client()
        resp = client.get("/api/cloud/architecture", query_string={"sub": "sub-1", "view": "reactflow"})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        labels = [str((node.get("data") or {}).get("label", "")) for node in data.get("nodes", [])]
        assert any(label == "orders-api" or label.startswith("orders-api ") for label in labels), labels
        assert any(edge.get("source", "").startswith("aks-ingress::") and edge.get("target") != aks_id for edge in data.get("edges", [])), data.get("edges", [])

        os.unlink(tmp.name)

    def test_api_cloud_resource_details_surfaces_ase_worker_os_type(self, monkeypatch):
        import json
        import os
        import sqlite3
        import sys
        import tempfile

        sys.path.insert(0, str(REPO_ROOT))
        os.environ.setdefault("FLASK_APP", "web/app.py")
        import web.app as app_module

        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp.close()
        conn = sqlite3.connect(tmp.name)
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE subscriptions (
                id TEXT PRIMARY KEY,
                display_name TEXT,
                environment TEXT,
                state TEXT
            );
            CREATE TABLE provisioned_assets (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                resource_group TEXT,
                name TEXT,
                type TEXT,
                location TEXT,
                sku TEXT,
                fqdn TEXT,
                is_public INTEGER DEFAULT 0,
                status TEXT,
                pipeline_tag TEXT,
                first_detected TEXT,
                last_synced TEXT,
                raw_json TEXT,
                is_restricted INTEGER DEFAULT 0,
                waf_mode TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state) VALUES (?, ?, ?, ?)",
            ("sub-1", "Test Subscription", "production", "Enabled"),
        )
        ase_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/hostingEnvironments/ase-one"
        conn.execute(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, resource_group, name, type, location, sku, fqdn,
                is_public, status, pipeline_tag, first_detected, last_synced, raw_json,
                is_restricted, waf_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ase_id,
                "sub-1",
                "rg-app",
                "ase-one",
                "Microsoft.Web/hostingEnvironments",
                "ukwest",
                "ASEv3",
                "ase-one.appserviceenvironment.net",
                1,
                "active",
                None,
                "2026-06-01T00:00:00Z",
                "2026-06-01T00:00:00Z",
                json.dumps({
                    "properties": {
                        "workerPools": [
                            {"osType": "Windows", "workerCount": 2},
                        ]
                    }
                }),
                0,
                None,
            ),
        )
        conn.commit()

        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
        client = app_module.app.test_client()
        resp = client.get("/api/cloud/resource-details", query_string={"id": ase_id})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        details = resp.get_json()
        assert details["configuration"]["operating_system"] == "Windows"

        os.unlink(tmp.name)

    def test_api_cloud_resource_details_inherits_ase_network_for_app_service_plan(self, monkeypatch):
        import json
        import os
        import sqlite3
        import sys
        import tempfile

        sys.path.insert(0, str(REPO_ROOT))
        os.environ.setdefault("FLASK_APP", "web/app.py")
        import web.app as app_module

        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp.close()
        conn = sqlite3.connect(tmp.name)
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE subscriptions (
                id TEXT PRIMARY KEY,
                display_name TEXT,
                environment TEXT,
                state TEXT
            );
            CREATE TABLE provisioned_assets (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                resource_group TEXT,
                name TEXT,
                type TEXT,
                location TEXT,
                sku TEXT,
                fqdn TEXT,
                is_public INTEGER DEFAULT 0,
                status TEXT,
                pipeline_tag TEXT,
                first_detected TEXT,
                last_synced TEXT,
                raw_json TEXT,
                is_restricted INTEGER DEFAULT 0,
                waf_mode TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state) VALUES (?, ?, ?, ?)",
            ("sub-1", "Test Subscription", "production", "Enabled"),
        )
        ase_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/hostingEnvironments/ase-one"
        plan_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/serverfarms/plan-one"
        conn.executemany(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, resource_group, name, type, location, sku, fqdn,
                is_public, status, pipeline_tag, first_detected, last_synced, raw_json,
                is_restricted, waf_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    ase_id,
                    "sub-1",
                    "rg-app",
                    "ase-one",
                    "Microsoft.Web/hostingEnvironments",
                    "ukwest",
                    "ASEv3",
                    "ase-one.appserviceenvironment.net",
                    0,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps({
                        "properties": {
                            "virtualNetwork": {
                                "id": "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/virtualNetworks/vnet-prod",
                            },
                            "subnet": {
                                "id": "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/virtualNetworks/vnet-prod/subnets/ase-subnet",
                            },
                        }
                    }),
                    0,
                    None,
                ),
                (
                    plan_id,
                    "sub-1",
                    "rg-app",
                    "plan-one",
                    "Microsoft.Web/serverfarms",
                    "ukwest",
                    "P1v3",
                    "",
                    0,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps({"hostingEnvironmentProfile": {"id": ase_id}}),
                    0,
                    None,
                ),
            ],
        )
        conn.commit()

        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
        client = app_module.app.test_client()
        resp = client.get("/api/cloud/resource-details", query_string={"id": plan_id})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        details = resp.get_json()
        assert details["parent_resource"]["name"] == "ase-one", details
        assert details["parent_resource"]["type_label"] == "App Service Environment", details
        assert details["parent_resource"]["network"]["vnet"] == "vnet-prod", details
        assert details["parent_resource"]["network"]["subnet"] == "ase-subnet", details
        assert details["network"]["vnet"] == "vnet-prod", details
        assert details["network"]["subnet"] == "ase-subnet", details

        os.unlink(tmp.name)

    def test_api_cloud_resource_details_surfaces_function_app_os_type(self, monkeypatch):
        import json
        import os
        import sqlite3
        import sys
        import tempfile

        sys.path.insert(0, str(REPO_ROOT))
        os.environ.setdefault("FLASK_APP", "web/app.py")
        import web.app as app_module

        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp.close()
        conn = sqlite3.connect(tmp.name)
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE subscriptions (
                id TEXT PRIMARY KEY,
                display_name TEXT,
                environment TEXT,
                state TEXT
            );
            CREATE TABLE provisioned_assets (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                resource_group TEXT,
                name TEXT,
                type TEXT,
                location TEXT,
                sku TEXT,
                fqdn TEXT,
                is_public INTEGER DEFAULT 0,
                status TEXT,
                pipeline_tag TEXT,
                first_detected TEXT,
                last_synced TEXT,
                raw_json TEXT,
                is_restricted INTEGER DEFAULT 0,
                waf_mode TEXT
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
            CREATE TABLE function_app_servicebus_triggers (
                id TEXT PRIMARY KEY,
                subscription_id TEXT NOT NULL,
                function_app_id TEXT NOT NULL,
                function_app_name TEXT NOT NULL,
                resource_group TEXT,
                function_name TEXT NOT NULL,
                trigger_type TEXT,
                entity_type TEXT,
                entity_name TEXT,
                subscription_name TEXT,
                connection TEXT,
                last_synced TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state) VALUES (?, ?, ?, ?)",
            ("sub-1", "Test Subscription", "production", "Enabled"),
        )
        plan_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/serverfarms/plan-one"
        fn_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/sites/fn-one"
        conn.executemany(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, resource_group, name, type, location, sku, fqdn,
                is_public, status, pipeline_tag, first_detected, last_synced, raw_json,
                is_restricted, waf_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    plan_id,
                    "sub-1",
                    "rg-app",
                    "plan-one",
                    "Microsoft.Web/serverfarms",
                    "ukwest",
                    "Y1",
                    None,
                    0,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps({"properties": {"reserved": True}}),
                    0,
                    None,
                ),
                (
                    fn_id,
                    "sub-1",
                    "rg-app",
                    "fn-one",
                    "Microsoft.Web/sites",
                    "ukwest",
                    "Y1",
                    "fn-one.azurewebsites.net",
                    1,
                    "active",
                    None,
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                    json.dumps({"kind": "functionapp,linux", "serverFarmId": plan_id}),
                    0,
                    None,
                ),
            ],
        )
        conn.execute(
            """
            INSERT INTO function_app_http_triggers (
                id, subscription_id, function_app_id, function_app_name, resource_group,
                function_name, route, auth_level, methods, fqdn, full_url, is_public, last_synced
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fn_id + "::HttpPing",
                "sub-1",
                fn_id,
                "fn-one",
                "rg-app",
                "HttpPing",
                "ping",
                "function",
                json.dumps(["GET"]),
                "fn-one.azurewebsites.net",
                "https://fn-one.azurewebsites.net/api/ping",
                1,
                "2026-06-01T00:00:00Z",
            ),
        )
        conn.execute(
            """
            INSERT INTO function_app_servicebus_triggers (
                id, subscription_id, function_app_id, function_app_name, resource_group,
                function_name, trigger_type, entity_type, entity_name, subscription_name,
                connection, last_synced
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fn_id + "::DirectCredit",
                "sub-1",
                fn_id,
                "fn-one",
                "rg-app",
                "DirectCredit",
                "servicebustrigger",
                "topic",
                "mydomain.service.events.payments.servicedirectcreditrecalledevent",
                "sb-subscription",
                "servicebus-connection",
                "2026-06-01T00:00:00Z",
            ),
        )
        conn.commit()

        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
        client = app_module.app.test_client()
        resp = client.get("/api/cloud/resource-details", query_string={"id": fn_id})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        details = resp.get_json()
        assert details["configuration"]["operating_system"] == "Linux"
        assert details["parent_resource"]["name"] == "plan-one", details
        assert details["parent_resource"]["type_label"] == "App Service Plan", details

        os.unlink(tmp.name)

    def test_api_cloud_resource_details_returns_appgw_routing_targets(self, monkeypatch):
        import json
        import os
        import sqlite3
        import sys
        import tempfile

        sys.path.insert(0, str(REPO_ROOT))
        os.environ.setdefault("FLASK_APP", "web/app.py")
        import web.app as app_module

        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp.close()
        conn = sqlite3.connect(tmp.name)
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
                resource_group TEXT,
                name TEXT,
                type TEXT,
                location TEXT,
                sku TEXT,
                fqdn TEXT,
                is_public INTEGER DEFAULT 0,
                status TEXT,
                pipeline_tag TEXT,
                first_detected TEXT,
                last_synced TEXT,
                raw_json TEXT,
                is_restricted INTEGER DEFAULT 0,
                waf_mode TEXT
            );
            CREATE TABLE appgw_routing_rules (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                gateway_name TEXT,
                backend_fqdns TEXT,
                backend_pool_name TEXT,
                listener_name TEXT,
                url_path TEXT,
                protocol TEXT,
                waf_policy_name TEXT,
                resource_group TEXT,
                hostname TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state, last_synced) VALUES (?, ?, ?, ?, ?)",
            ("sub-1", "Test Subscription", "production", "Enabled", "2026-06-01T00:00:00Z"),
        )
        gw_id = "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/applicationGateways/appgw-one"
        conn.execute(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, resource_group, name, type, location, sku, fqdn,
                is_public, status, pipeline_tag, first_detected, last_synced, raw_json,
                is_restricted, waf_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                gw_id,
                "sub-1",
                "rg-net",
                "appgw-one",
                "Microsoft.Network/applicationGateways",
                "uksouth",
                "WAF_v2",
                "appgw-one.example.com",
                1,
                "active",
                None,
                "2026-06-01T00:00:00Z",
                "2026-06-01T00:00:00Z",
                json.dumps({
                    "properties": {
                        "publicIpAddress": {"ipAddress": "20.30.40.50"},
                        "gatewayIPConfigurations": [
                            {
                                "properties": {
                                    "subnet": {
                                        "id": "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/virtualNetworks/vnet-net/subnets/snet-appgw",
                                    }
                                }
                            }
                        ],
                    }
                }),
                0,
                "WAF_v2",
            ),
        )
        conn.execute(
            """
            INSERT INTO appgw_routing_rules (
                id, subscription_id, gateway_name, backend_fqdns, backend_pool_name,
                listener_name, url_path, protocol, waf_policy_name, resource_group, hostname
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "appgw-one::route-1",
                "sub-1",
                "appgw-one",
                '["backend-one.internal", "backend-two.internal"]',
                "pool-a",
                "https-listener",
                "/*",
                "HTTPS",
                "policy-one",
                "rg-net",
                "appgw-one.example.com",
            ),
        )
        conn.commit()

        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
        client = app_module.app.test_client()
        resp = client.get("/api/cloud/resource-details", query_string={"id": gw_id})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data["name"] == "appgw-one"
        assert data["resource_group"] == "rg-net"
        assert data["sku"] == "WAF_v2"
        assert "20.30.40.50" in data["network"]["public_ips"]
        assert "appgw-one.example.com" in data["network"]["dns_names"]
        assert "snet-appgw" in str(data["network"]["subnet"])
        assert data["routing_targets"][0]["backend_pool_name"] == "pool-a"
        assert data["routing_targets"][0]["listener_name"] == "https-listener"
        assert data["routing_targets"][0]["waf_policy_name"] == "policy-one"

        os.unlink(tmp.name)

    def test_api_cloud_route_trace_returns_full_appgw_apim_aks_chain(self, monkeypatch):
        import json
        import os
        import sqlite3
        import sys
        import tempfile

        sys.path.insert(0, str(REPO_ROOT))
        os.environ.setdefault("FLASK_APP", "web/app.py")
        import web.app as app_module

        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp.close()
        conn = sqlite3.connect(tmp.name)
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
                resource_group TEXT,
                name TEXT,
                type TEXT,
                location TEXT,
                sku TEXT,
                fqdn TEXT,
                is_public INTEGER DEFAULT 0,
                status TEXT,
                pipeline_tag TEXT,
                first_detected TEXT,
                last_synced TEXT,
                raw_json TEXT,
                is_restricted INTEGER DEFAULT 0,
                waf_mode TEXT
            );
            CREATE TABLE appgw_routing_rules (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                gateway_name TEXT,
                gateway_resource_id TEXT,
                resource_group TEXT,
                rule_name TEXT,
                listener_name TEXT,
                hostname TEXT,
                protocol TEXT,
                url_path TEXT,
                backend_pool_name TEXT,
                backend_fqdns TEXT,
                http_settings_name TEXT,
                backend_port INTEGER,
                backend_protocol TEXT,
                host_override TEXT,
                waf_policy_name TEXT,
                exposure_level TEXT,
                last_synced TEXT
            );
            CREATE TABLE apim_api_routes (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                apim_name TEXT,
                apim_resource_id TEXT,
                api_name TEXT,
                api_display_name TEXT,
                api_path TEXT,
                api_protocols TEXT,
                backend_id TEXT,
                backend_url TEXT,
                service_url TEXT,
                requires_subscription INTEGER DEFAULT 1,
                gateway_hosts TEXT,
                exposure_level TEXT,
                last_synced TEXT
            );
            CREATE TABLE apim_backends (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                apim_name TEXT,
                backend_id TEXT,
                title TEXT,
                description TEXT,
                url TEXT,
                protocol TEXT,
                circuit_breaker TEXT,
                credentials TEXT,
                tls_validate_cert INTEGER DEFAULT 1,
                last_synced TEXT
            );
            CREATE TABLE aks_routes (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                cluster_name TEXT,
                cluster_resource_id TEXT,
                resource_group TEXT,
                namespace TEXT,
                ingress_name TEXT,
                host TEXT,
                host_aliases TEXT,
                path TEXT,
                is_default_backend INTEGER DEFAULT 0,
                service_name TEXT,
                service_port TEXT,
                service_ports TEXT,
                deployment_name TEXT,
                deployment_namespace TEXT,
                pod_template_labels TEXT,
                git_repository TEXT,
                team TEXT,
                exposure_level TEXT,
                last_synced TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state, last_synced) VALUES (?, ?, ?, ?, ?)",
            ("sub-1", "Test Subscription", "production", "Enabled", "2026-06-01T00:00:00Z"),
        )
        conn.execute(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, resource_group, name, type, location, sku, fqdn,
                is_public, status, pipeline_tag, first_detected, last_synced, raw_json,
                is_restricted, waf_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "/subscriptions/sub-1/resourceGroups/rg-api/providers/Microsoft.Network/applicationGateways/appgw-one",
                "sub-1",
                "rg-api",
                "appgw-one",
                "Microsoft.Network/applicationGateways",
                "uksouth",
                "WAF_v2",
                "napier-events.mydomain.co.uk",
                1,
                "active",
                None,
                "2026-06-01T00:00:00Z",
                "2026-06-01T00:00:00Z",
                json.dumps({"properties": {}}),
                0,
                "WAF_v2",
            ),
        )
        conn.execute(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, resource_group, name, type, location, sku, fqdn,
                is_public, status, pipeline_tag, first_detected, last_synced, raw_json,
                is_restricted, waf_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "/subscriptions/sub-1/resourceGroups/rg-api/providers/Microsoft.ApiManagement/service/production-api-uksouth",
                "sub-1",
                "rg-api",
                "production-api-uksouth",
                "Microsoft.ApiManagement/service",
                "uksouth",
                "Developer",
                "production-api-uksouth.azure-api.net",
                1,
                "active",
                None,
                "2026-06-01T00:00:00Z",
                "2026-06-01T00:00:00Z",
                json.dumps({"properties": {}}),
                0,
                None,
            ),
        )
        conn.execute(
            """
            INSERT INTO appgw_routing_rules (
                id, subscription_id, gateway_name, gateway_resource_id, resource_group,
                rule_name, listener_name, hostname, protocol, url_path, backend_pool_name,
                backend_fqdns, http_settings_name, backend_port, backend_protocol,
                host_override, waf_policy_name, exposure_level, last_synced
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "appgw-one::rule-1::/*",
                "sub-1",
                "appgw-one",
                "/subscriptions/sub-1/resourceGroups/rg-api/providers/Microsoft.Network/applicationGateways/appgw-one",
                "rg-api",
                "rule-1",
                "napier-events.mydomain.co.uk_public",
                "napier-events.mydomain.co.uk",
                "HTTPS",
                "/*",
                "apim-gateway",
                json.dumps(["production-api-uksouth.azure-api.net"]),
                "napier-events",
                443,
                "HTTPS",
                None,
                "waf-marketlane",
                "Public",
                "2026-06-01T00:00:00Z",
            ),
        )
        conn.execute(
            """
            INSERT INTO apim_api_routes (
                id, subscription_id, apim_name, apim_resource_id, api_name, api_display_name,
                api_path, api_protocols, backend_id, backend_url, service_url,
                requires_subscription, gateway_hosts, exposure_level, last_synced
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "production-api-uksouth::napier-api",
                "sub-1",
                "production-api-uksouth",
                "/subscriptions/sub-1/resourceGroups/rg-api/providers/Microsoft.ApiManagement/service/production-api-uksouth",
                "napier-api",
                "napier-api",
                "/napier-api",
                json.dumps(["https"]),
                "fincrime-napier-api",
                "https://production-fincrime-napier-api.internal.cbinnovation.uk",
                "https://production-fincrime-napier-api.internal.cbinnovation.uk",
                1,
                json.dumps(["production-api-uksouth.azure-api.net"]),
                "Public",
                "2026-06-01T00:00:00Z",
            ),
        )
        conn.execute(
            """
            INSERT INTO apim_backends (
                id, subscription_id, apim_name, backend_id, title, description, url,
                protocol, circuit_breaker, credentials, tls_validate_cert, last_synced
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "production-api-uksouth::fincrime-napier-api",
                "sub-1",
                "production-api-uksouth",
                "fincrime-napier-api",
                "fincrime-napier-api",
                "Napier backend",
                "https://production-fincrime-napier-api.internal.cbinnovation.uk",
                "http",
                None,
                None,
                1,
                "2026-06-01T00:00:00Z",
            ),
        )
        conn.execute(
            """
            INSERT INTO aks_routes (
                id, subscription_id, cluster_name, cluster_resource_id, resource_group,
                namespace, ingress_name, host, host_aliases, path, is_default_backend,
                service_name, service_port, service_ports, deployment_name, deployment_namespace,
                pod_template_labels, git_repository, team, exposure_level, last_synced
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "production-shared-aks-uksouth::finance::fincrime-napier-api-ingress::production-fincrime-napier-api.internal.cbinnovation.uk::/*::fincrime-napier-api::80::fincrime-napier-api::rule",
                "sub-1",
                "production-shared-aks-uksouth",
                "/subscriptions/sub-1/resourceGroups/rg-aks/providers/Microsoft.ContainerService/managedClusters/production-shared-aks-uksouth",
                "rg-aks",
                "finance",
                "fincrime-napier-api-ingress",
                "production-fincrime-napier-api.internal.cbinnovation.uk",
                json.dumps(["production-fincrime-napier-api.internal.cbinnovation.uk"]),
                "/*",
                0,
                "fincrime-napier-api",
                "80",
                json.dumps([80]),
                "fincrime-napier-api",
                "finance",
                json.dumps({"app": "napier"}),
                "git@example.com/napier",
                "platform",
                "Internal",
                "2026-06-01T00:00:00Z",
            ),
        )
        conn.commit()

        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
        client = app_module.app.test_client()
        resp = client.get(
            "/api/cloud/route-trace",
            query_string={"sub": "sub-1", "endpoint": "https://napier-events.mydomain.co.uk"},
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data["host"] == "napier-events.mydomain.co.uk", data
        assert data["path"] == "/", data
        kinds = [step["kind"] for step in data["resolved_chain"]]
        assert kinds[:6] == ["internet", "listener", "appgw", "backend_pool", "apim_api", "apim_service"], data
        assert any(step["kind"] == "appgw" and step.get("waf_policy_name") for step in data["resolved_chain"]), data
        assert "apim_backend" in kinds, data
        assert kinds[-4:] == ["aks_ingress", "aks_service", "aks_deployment", "aks_cluster"], data
        assert "classDef entryPointProtected stroke:#ea580c,stroke-width:2px,fill:#3d1c0d;" in data["mermaid"], data["mermaid"]
        assert "class appgw::appgw-one entryPointProtected;" in data["mermaid"], data["mermaid"]
        assert "class internet internet;" in data["mermaid"], data["mermaid"]
        assert "napier-events.mydomain.co.uk" in data["mermaid"], data["mermaid"]
        assert "production-api-uksouth" in data["mermaid"], data["mermaid"]
        assert "fincrime-napier-api-ingress" in data["mermaid"], data["mermaid"]

        os.unlink(tmp.name)

    def test_api_cloud_resource_details_returns_waf_policy_associations(self, monkeypatch):
        import os
        import sqlite3
        import sys
        import tempfile

        sys.path.insert(0, str(REPO_ROOT))
        os.environ.setdefault("FLASK_APP", "web/app.py")
        import web.app as app_module

        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp.close()
        conn = sqlite3.connect(tmp.name)
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
            CREATE TABLE appgw_waf_policies (
                name TEXT,
                subscription_id TEXT,
                resource_group TEXT,
                mode TEXT,
                state TEXT,
                managed_rule_sets TEXT,
                custom_rules_count INTEGER DEFAULT 0,
                exclusions_count INTEGER DEFAULT 0,
                request_body_check INTEGER DEFAULT 0,
                max_body_kb INTEGER,
                associated_gateways TEXT,
                last_synced TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state, last_synced) VALUES (?, ?, ?, ?, ?)",
            ("sub-1", "Test Subscription", "production", "Enabled", "2026-06-01T00:00:00Z"),
        )
        conn.execute(
            """
            INSERT INTO appgw_waf_policies (
                name, subscription_id, resource_group, mode, state, managed_rule_sets,
                custom_rules_count, exclusions_count, request_body_check, max_body_kb,
                associated_gateways, last_synced
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "policy-one",
                "sub-1",
                "rg-net",
                "Prevention",
                "Enabled",
                '[{"type":"OWASP","version":"3.2"}]',
                2,
                1,
                1,
                128,
                '["appgw-one","appgw-two"]',
                "2026-06-01T00:00:00Z",
            ),
        )
        conn.commit()

        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
        client = app_module.app.test_client()
        resp = client.get(
            "/api/cloud/resource-details",
            query_string={"id": "waf::policy-one", "type": "Microsoft.Network/applicationGatewayWebApplicationFirewallPolicies"},
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data["name"] == "policy-one"
        assert data["type_label"] == "WAF Policy"
        assert data["waf_policy"]["custom_rules_count"] == 2
        assert data["waf_policy"]["associated_gateways"] == ["appgw-one", "appgw-two"]

        os.unlink(tmp.name)

    def test_api_cloud_architecture_includes_icon_metadata_for_mermaid_nodes(self, monkeypatch):
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
            CREATE TABLE repositories (
                id INTEGER PRIMARY KEY,
                repo_name TEXT
            );
            CREATE TABLE resources (
                id INTEGER PRIMARY KEY,
                experiment_id TEXT,
                repo_id INTEGER,
                resource_name TEXT,
                resource_type TEXT,
                provider TEXT,
                parent_resource_id INTEGER,
                source_file TEXT,
                raw_json TEXT,
                discovered_by TEXT,
                discovery_method TEXT,
                status TEXT,
                first_seen TEXT,
                last_seen TEXT
            );
            """
        )
        conn.execute("INSERT INTO repositories (id, repo_name) VALUES (?, ?)", (1, "repo-a"))
        conn.execute(
            """
            INSERT INTO resources (
                id, experiment_id, repo_id, resource_name, resource_type, provider,
                parent_resource_id, source_file, raw_json, discovered_by, discovery_method, status,
                first_seen, last_seen
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                "exp-1",
                1,
                "appgw-one",
                "Microsoft.Network/applicationGateways",
                "Azure",
                None,
                "",
                "null",
                "scan",
                "scan",
                "active",
                "2026-06-01T00:00:00Z",
                "2026-06-01T00:00:00Z",
            ),
        )
        conn.commit()

        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
        client = app_module.app.test_client()
        resp = client.get("/api/cloud/architecture?experiment_id=exp-1")
        assert resp.status_code == 200
        data = resp.get_json()
        node = next(n for n in data["nodes"] if n["data"].get("label") == "appgw-one")
        assert node["data"].get("iconClass") == "icon-azurerm-app-gateway", node["data"]
        assert node["data"].get("iconPath", "").endswith("app-gateway.svg"), node["data"]

    def test_api_cloud_architecture_handles_null_raw_json(self, monkeypatch):
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
                state TEXT
            );
            CREATE TABLE provisioned_assets (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                resource_group TEXT,
                name TEXT,
                type TEXT,
                location TEXT,
                sku TEXT,
                fqdn TEXT,
                is_public INTEGER DEFAULT 0,
                status TEXT,
                pipeline_tag TEXT,
                first_detected TEXT,
                last_synced TEXT,
                raw_json TEXT,
                is_restricted INTEGER DEFAULT 0,
                waf_mode TEXT
            );
            """
        )
        conn.execute("INSERT INTO subscriptions (id, display_name, environment, state) VALUES (?, ?, ?, ?)", ("sub-1", "Test Subscription", "production", "Enabled"))
        conn.execute(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, resource_group, name, type, location, sku, fqdn,
                is_public, status, pipeline_tag, first_detected, last_synced, raw_json, is_restricted, waf_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "asset-1",
                "sub-1",
                "rg-net",
                "appgw-one",
                "Microsoft.Network/applicationGateways",
                "eastus",
                "Standard_v2",
                "appgw-one.example.com",
                1,
                "active",
                "",
                "2026-06-01T00:00:00Z",
                "2026-06-01T00:00:00Z",
                None,
                0,
                None,
            ),
        )
        conn.commit()

        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
        client = app_module.app.test_client()
        resp = client.get("/api/cloud/architecture?sub=sub-1&view=mermaid")
        assert resp.status_code == 200

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

    def test_subscription_diagram_promotes_data_and_service_platform_types(self, monkeypatch):
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
                location TEXT,
                sku TEXT,
                fqdn TEXT,
                is_public INTEGER DEFAULT 0,
                status TEXT,
                pipeline_tag TEXT,
                first_detected TEXT,
                last_synced TEXT,
                raw_json TEXT,
                is_restricted INTEGER DEFAULT 0,
                waf_mode TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO subscriptions (id, display_name, environment, state, last_synced) VALUES (?, ?, ?, ?, ?)",
            ("sub-1", "Demo Subscription", "production", "Enabled", "2026-06-09T10:00:00Z"),
        )

        entry_rows = [
            (
                f"gw-{idx}",
                "sub-1",
                f"gw-{idx}",
                "Microsoft.Network/applicationGateways",
                "rg-net",
                "westus",
                "WAF_v2",
                f"gw-{idx}.example.com",
                1,
                "active",
                None,
                "2026-06-09T10:00:00Z",
                "2026-06-09T10:00:00Z",
                json.dumps({}),
                0,
                "WAF_v2",
            )
            for idx in range(1, 121)
        ]
        special_rows = [
            (
                "sql-1",
                "sub-1",
                "sql-1",
                "Microsoft.Sql/servers",
                "rg-data",
                "westus",
                None,
                "sql-1.database.windows.net",
                1,
                "active",
                None,
                "2026-06-09T10:00:00Z",
                "2026-06-09T10:00:00Z",
                json.dumps({}),
                0,
                None,
            ),
            (
                "cosmos-1",
                "sub-1",
                "cosmos-1",
                "Microsoft.DocumentDB/databaseAccounts",
                "rg-data",
                "westus",
                "Standard",
                "cosmos-1.documents.azure.com",
                0,
                "active",
                None,
                "2026-06-09T10:00:00Z",
                "2026-06-09T10:00:00Z",
                json.dumps({}),
                0,
                None,
            ),
            (
                "sb-1",
                "sub-1",
                "sb-1",
                "Microsoft.ServiceBus/namespaces",
                "rg-msg",
                "westus",
                "Standard",
                "sb-1.servicebus.windows.net",
                0,
                "active",
                None,
                "2026-06-09T10:00:00Z",
                "2026-06-09T10:00:00Z",
                json.dumps({}),
                0,
                None,
            ),
            (
                "eh-1",
                "sub-1",
                "eh-1",
                "Microsoft.EventHub/namespaces",
                "rg-msg",
                "westus",
                "Standard",
                "eh-1.servicebus.windows.net",
                0,
                "active",
                None,
                "2026-06-09T10:00:00Z",
                "2026-06-09T10:00:00Z",
                json.dumps({}),
                0,
                None,
            ),
            (
                "sf-1",
                "sub-1",
                "sf-1",
                "Microsoft.ServiceFabric/clusters",
                "rg-back",
                "westus",
                "Standard",
                None,
                0,
                "active",
                None,
                "2026-06-09T10:00:00Z",
                "2026-06-09T10:00:00Z",
                json.dumps({}),
                0,
                None,
            ),
            (
                "plan-1",
                "sub-1",
                "plan-1",
                "Microsoft.Web/serverfarms",
                "rg-app",
                "westus",
                "P1v3",
                None,
                0,
                "active",
                None,
                "2026-06-09T10:00:00Z",
                "2026-06-09T10:00:00Z",
                json.dumps({"kind": "app"}),
                0,
                None,
            ),
        ]
        conn.executemany(
            """
            INSERT INTO provisioned_assets (
                id, subscription_id, name, type, resource_group, location, sku, fqdn,
                is_public, status, pipeline_tag, first_detected, last_synced, raw_json,
                is_restricted, waf_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [*entry_rows, *special_rows],
        )
        conn.commit()

        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)
        client = app_module.app.test_client()
        resp = client.get("/api/subscriptions/sub-1/diagram")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        mermaid = data["ingress_diagram"]["mermaid"]
        assert "rg_data_sql_1" in mermaid
        assert "rg_data_cosmos_1" in mermaid
        assert "rg_msg_sb_1" in mermaid
        assert "rg_msg_eh_1" in mermaid
        assert "rg_back_sf_1" in mermaid
        assert "rg_app_plan_1" in mermaid

    def test_settings_api_can_clear_cloud_model_cache(self, monkeypatch, tmp_path):
        import os
        import sqlite3
        import sys

        sys.path.insert(0, str(REPO_ROOT))
        os.environ.setdefault("FLASK_APP", "web/app.py")
        import web.app as app_module

        db_path = tmp_path / "cache.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(
            """
            CREATE TABLE subscription_diagram_cache (
                sub_id TEXT PRIMARY KEY,
                cache_signature TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO subscription_diagram_cache (sub_id, cache_signature, payload_json)
            VALUES ('sub-1', 'sig-1', '{"cached": true}');
            """
        )
        conn.commit()
        conn.close()

        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: sqlite3.connect(str(db_path)))
        app_module._SUBSCRIPTION_DIAGRAM_CACHE["sub-1"] = (0.0, "sig-1", {"cached": True})

        client = app_module.app.test_client()
        resp = client.post("/api/settings/cloud-cache/clear")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["cleared"]["db_rows"] == 1
        assert data["cleared"]["memory_entries"] == 1
        assert app_module._SUBSCRIPTION_DIAGRAM_CACHE == {}

        check = sqlite3.connect(str(db_path))
        try:
            remaining = check.execute("SELECT COUNT(*) FROM subscription_diagram_cache").fetchone()[0]
            assert remaining == 0
        finally:
            check.close()


def _cloud_assets_payload():
    plan_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/serverfarms/plan-one"
    app_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/sites/app-one"
    fn_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/sites/fn-one"
    ase_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/hostingEnvironments/ase-one"
    ase_app_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/sites/ase-app"
    ase_fn_id = "/subscriptions/sub-1/resourceGroups/rg-app/providers/Microsoft.Web/sites/ase-fn"
    gw_id = "/subscriptions/sub-1/resourceGroups/rg-net/providers/Microsoft.Network/applicationGateways/gw-one"
    apim_id = "/subscriptions/sub-1/resourceGroups/rg-api/providers/Microsoft.ApiManagement/service/production-api-uksouth"
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
        apim_id = "/subscriptions/sub-1/resourceGroups/rg-api/providers/Microsoft.ApiManagement/service/production-api-uksouth"
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
                (apim_id, "sub-1", "rg-api", "production-api-uksouth", "Microsoft.ApiManagement/service", "uksouth", "Premium",
                 None, 1, "production-api-uksouth.azure-api.net", None, json.dumps({}), now, now, "active"),
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
        apim = assets["/subscriptions/sub-1/resourceGroups/rg-api/providers/Microsoft.ApiManagement/service/production-api-uksouth"]
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
        assert apim["parent_id"] is None
        assert apim["parent_name"] is None
        assert apim["children_count"] == 0
        assert listener["parent_id"] == gw["id"]
        assert listener["is_child"] is True
        assert storage["display_type_label"] == "Storage Account"
        assert storage["children_count"] == 1
        assert acr["display_type_label"] == "ACR"
        assert container["parent_id"] == storage["id"]
        assert container["children_count"] == 1
        assert blob["parent_id"] == container["id"]
        assert blob["display_type_label"] == "Blob"
        assert sql["children_count"] == 1
        assert db["parent_id"] == sql["id"]
        assert db["display_type_label"] == "SQL Database"
        assert any(item["label"] == "App Service Environment" for item in data["type_summary"])
        assert any(item["label"] == "Function App" for item in data["type_summary"])
        assert any(item["label"] == "ACR" for item in data["type_summary"])

    def test_api_subscriptions_counts_distinct_assets(self, monkeypatch):
        import os
        import sys
        import sqlite3

        sys.path.insert(0, str(REPO_ROOT))
        os.environ.setdefault("FLASK_APP", "web/app.py")
        import web.app as app_module

        conn = sqlite3.connect(":memory:")
        conn.executescript(
            """
            CREATE TABLE subscriptions (
                id TEXT,
                display_name TEXT,
                environment TEXT,
                state TEXT,
                last_synced TEXT
            );
            CREATE TABLE provisioned_assets (
                id TEXT PRIMARY KEY,
                subscription_id TEXT,
                is_public INTEGER DEFAULT 0
            );
            """
        )
        conn.executemany(
            "INSERT INTO subscriptions (id, display_name, environment, state, last_synced) VALUES (?, ?, ?, ?, ?)",
            [
                ("sub-1", "Demo Subscription", "production", "Enabled", "2026-06-01T00:00:00Z"),
                ("sub-1", "Demo Subscription", "production", "Enabled", "2026-06-01T00:00:00Z"),
            ],
        )
        conn.executemany(
            "INSERT INTO provisioned_assets (id, subscription_id, is_public) VALUES (?, ?, ?)",
            [
                ("asset-1", "sub-1", 1),
                ("asset-2", "sub-1", 0),
            ],
        )
        monkeypatch.setattr(app_module, "_get_db_with_schema", lambda: conn)

        client = app_module.app.test_client()
        resp = client.get("/api/subscriptions")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["subscriptions"]) == 1
        subscription = data["subscriptions"][0]
        assert subscription["asset_count"] == 2
        assert subscription["public_count"] == 1
