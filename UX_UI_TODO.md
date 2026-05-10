# Triage-Saurus — UX/UI Improvement Backlog

_Observed via headless browser testing (Playwright, 768×1024 / 1280×720 / 1440×900 / 1920×1080) across all 5 GOAT repo diagrams. No changes implemented — list only._

---

## 1. Framework: Migrate to Tailwind CSS

**Current state:** 2,433-line hand-rolled `app.css` + inline styles scattered across `index.html` and `diagram_viewer.html`. CSS variables are well-named but class names are inconsistent and specificity fights occur (e.g. `.scan-card .form-row .field.grow select` overrides).

**Recommended approach:**
- Install Tailwind CSS v4 (CDN play build for prototyping, or PostCSS build for production)
- Replace custom CSS class soup with Tailwind utilities; keep `--bg-*`, `--accent`, `--green`/`--red` tokens as CSS vars and expose them via `@theme` so Tailwind utilities reference them (`bg-[var(--bg-surface)]`)
- Use `@apply` sparingly — only for compound variants like `.provider-tab.active`
- Dark theme is already forced — no `dark:` variant needed, but keep it in mind for a future light-mode option
- Tailwind's JIT tree-shaking will cut CSS from ~2,433 lines to <200 lines for the pages actually used

**Tailwind migration priority targets:**

| Component | Current CSS | Tailwind equivalent |
|---|---|---|
| Page background | `background: #111827` | `bg-slate-900` (or `bg-[#0d1117]` to match app) |
| Header gradient | `linear-gradient(135deg, #0f172a, #1e3a5f)` | `bg-gradient-to-br from-slate-950 to-blue-950` |
| Provider tab active | `color: #60a5fa; border-bottom: 2px solid #3b82f6` | `text-blue-400 border-b-2 border-blue-500` |
| Provider dot colours | Inline CSS `.dot-aws { background: #f97316 }` | `bg-orange-500` (AWS), `bg-blue-500` (Azure), etc. |
| Zoom control buttons | Custom `.ctrl-btn` | `px-2 py-1 text-sm bg-slate-800 hover:bg-slate-700 border border-slate-600 rounded-md` |
| Legend swatches | Inline `.legend-swatch` | `inline-block h-1 w-7 rounded` with colour classes |
| Back button | Custom `.back-btn` | `text-sm text-slate-400 hover:text-slate-200 border border-slate-600 rounded-md px-3 py-1.5` |
| Error pre | Inline `color:#ef4444` | `text-red-400 p-4 whitespace-pre-wrap` |

---

## 2. Dashboard (`index.html`) — Layout & Information Architecture

### 2a. Empty state is opaque
- Immediately visible state: large empty "SCAN OUTPUT" pane + "Architecture diagram will appear after the scan completes." placeholder
- No list of previous experiments, no "Load a past scan" call-to-action visible until the user scrolls
- **Improvement:** Show an experiment list / recent scans table on initial load (or visible side panel). Each row → clickable link to `/diagrams/<id>?provider=<first_provider>`

### 2b. "Hide diagram" / "Hide scan" buttons are dead on load
- Both buttons appear at all times, toggling empty panels before a scan has run
- **Improvement:** Hide or disable these controls until a scan result is present; replace with a "View architecture" shortcut once a diagram exists

### 2c. Scan form cognitive load
- "Repository", "From scan", "To scan", "Run Compare" all in the same horizontal row — unclear hierarchy; "COMPARE SCANS" label is small and gold (inconsistent with green accent)
- **Improvement:** Separate "Start new scan" from "Compare scans" into distinct cards with clear headings; consider a two-step flow (pick repo → confirm scan)

### 2d. No experiment status indicator
- When a scan is running, the only feedback is streaming text in the SCAN OUTPUT pane. No progress bar, step counter, or estimated time
- **Improvement:** Add a step progress indicator (8 pipeline phases) with a spinner and elapsed time; show phase name ("Phase 3d — Generating diagram…")

### 2e. No keyboard navigation / accessibility
- No tab-order hints, no `aria-label` on buttons, no visible focus ring in the dark theme
- **Improvement:** Add visible focus rings (Tailwind `ring-2 ring-accent`) and correct ARIA roles

---

## 3. Diagram Viewer (`diagram_viewer.html`)

### 3a. SVG icons not rendering in node labels
- Mermaid code embeds `<img src="/static/assets/icons/...">` inside HTML labels. In the standalone viewer images don't appear reliably due to Mermaid's `foreignObject` rendering
- **Improvement:** Port the `mermaid-icon-injector.js` approach from the main dashboard to the standalone viewer. After Mermaid renders, walk all `foreignObject` elements and replace `<img>` placeholders with inline `<svg>` content fetched from the static path

### 3b. Large diagrams clip off screen (AWSGoat — 285 resources)
- AWSGoat renders with most nodes off-screen. "Fit" button exists but defaults to `scale(1)` not "auto-fit to viewport"
- **Improvement:** On first render, auto-calculate fit scale: `Math.min(container.width / svgWidth, container.height / svgHeight) * 0.9`. Add a "Mini-map" toggle for large diagrams (small overview panel in corner showing full graph with viewport rectangle)

### 3c. Internet node often partially off-screen
- In GCPGoat and AWSGoat the Internet node renders at the top-right and is cut off at 1440×900
- **Improvement:** After auto-fit, detect the Internet node bounding box and ensure it's visible within the viewport (critical security entry point should always be visible)

### 3d. Provider tabs cause full page reload
- Clicking a tab does a full page reload (`href="/diagrams/002?provider=aws"`); state and scroll position reset
- **Improvement:** Use client-side tab switching: store all provider Mermaid code in the page as `<script type="text/plain" data-provider="...">` blocks, re-render on tab click, use `history.pushState` to update URL for shareability

### 3e. Tab bar overflows on mobile (768px)
- The 5 terragoat tabs (ALICLOUD · AWS · AZURE · GCP · OCI) overflow horizontally without a visible scroll indicator
- **Improvement:** Add left/right fade-out gradient masks to hint scrollability. Abbreviate tab labels on narrow viewports (AL / AWS / AZ / GCP / OCI)

### 3f. Legend is too small and hard to scan
- Four small coloured lines with text wrap awkwardly at 768px. "Scroll to pan · Ctrl+scroll" hint disappears
- **Improvement:** Replace swatch lines with coloured pills/badges (`rounded-full px-2 py-0.5`). On narrow viewports move "Scroll to pan" into a tooltip on a `?` icon

### 3g. Zoom controls lack keyboard shortcuts
- `+`, `−`, `⊡ Fit` buttons have no tooltip text; `+`/`-` key bindings not implemented
- **Improvement:** Add `title` attributes and document keyboard shortcuts in a collapsed `?` help panel. Add `[+]`/`[-]` key bindings for zoom

### 3h. Header emoji is platform-dependent
- Provider emoji (☁️, 🔷, etc.) varies in colour/appearance across OS/browser rendering engines
- **Improvement:** Replace emoji with inline SVG provider logos (16×16) from `/static/assets/icons/<provider>/`; extend the coloured-dot pattern from the tab bar to the header

### 3i. "← Dashboard" link always goes to `/`
- If navigated from a bookmarked URL, the back link ignores browser history
- **Improvement:** Use `history.back()` with a fallback to `/` if `history.length <= 1`

### 3j. No finding/severity overlay on nodes
- Diagram shows exposure arrows and coloured borders but no per-node finding count badge
- **Improvement:** After render, fetch `/api/findings/<experiment_id>` and annotate each rendered SVG node with a severity badge (`🔴 3`) overlaid via `position: absolute`

---

## 4. Colour & Dark Theme Consistency

### 4a. Two different dark palettes in the same app
- Viewer uses `#111827` / `#0f172a` (Tailwind slate range); `app.css` uses `--bg-base: #0d1117` / `--bg-surface: #161b22` (GitHub dark)
- **Improvement:** Unify on the GitHub dark palette already in `app.css :root` — better contrast ratios (`--text: #c9d1d9`)

### 4b. Mermaid dark theme has poor node label contrast
- Mermaid's built-in `theme: 'dark'` uses `#1f2020` node backgrounds which clash with the app's `#111827` background
- **Improvement:** Override Mermaid theme variables with app CSS vars: `themeVariables: { primaryColor: 'var(--bg-subtle)', primaryTextColor: 'var(--text)', lineColor: 'var(--border)' }`

### 4c. Button hover states inconsistent
- Viewer ctrl-btn: `#1e293b → #334155`. App primary: `#238636 → #2ea043`. Compare-scan run: `transparent` on hover
- **Improvement:** Define 3 button variants — `btn-primary` (green), `btn-secondary` (slate), `btn-ghost` (transparent) — and apply consistently across all pages

---

## 5. Performance & Loading

### 5a. Mermaid loaded from CDN on every page load
- Both `index.html` and `diagram_viewer.html` load `mermaid@11` from `cdn.jsdelivr.net`. No `integrity` SRI hash
- **Improvement:** Vendor `mermaid.min.js` into `/static/vendor/mermaid.min.js` and serve with a long-lived cache header. Add SRI hash for integrity

### 5b. No loading skeleton while Mermaid renders
- Between page load and Mermaid finishing `render()`, the diagram area is blank or flashes raw Mermaid text
- **Improvement:** Show a pulsing skeleton card (Tailwind `animate-pulse bg-slate-700`) while rendering; swap out when SVG is injected

### 5c. Large diagrams block the main thread
- `mermaid.render()` for 285-node AWSGoat takes ~3-4 seconds and freezes the page (observed at 1440×900)
- **Improvement:** Move `mermaid.render()` into a Web Worker (Mermaid 11 supports `mermaid.renderAsync()`). Alternatively, split large diagrams into provider-filtered sub-diagrams with progressive expansion

---

## 6. Miscellaneous / Nice-to-Have

- **Share link:** "Copy link to this diagram + provider" button on the diagram viewer
- **Full-screen mode:** `⛶` button to expand diagram to 100vw/100vh and remove all chrome
- **Node click to detail:** Clicking a Mermaid node opens a slide-in panel showing the resource's DB properties and linked findings
- **Print/export:** SVG download already exists; add PNG export (`canvas.toDataURL`) and "Copy as PNG" button
- **Experiment name in browser title:** Tab shows "Alicloud Architecture" — add repo name for multi-tab usability (e.g. "terragoat — Alicloud Architecture — Triage-Saurus")
- **Favicon per provider:** Dynamically set `<link rel="icon">` to the provider icon so browser tabs are distinguishable
- **Accessibility:** Add `role="tablist"` / `role="tab"` / `aria-selected` to provider tabs; add `role="img"` and `aria-label` to the Mermaid SVG container
