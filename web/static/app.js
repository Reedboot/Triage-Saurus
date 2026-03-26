(function(){
  // app.js - main UI logic extracted from index.html inline script
  // This file is loaded with `defer` so DOM elements are available.

  const MERMAID_BASE_CONFIG = {
    startOnLoad: false,
    theme: 'dark',
    securityLevel: 'loose',
    flowchart: { 
      curve: 'basis', 
      useMaxWidth: false,
      nodeSpacing: 80,
      rankSpacing: 60,
      padding: 30,
      htmlLabels: true
    },
    themeVariables: {
      fontSize: '14px',
      primaryTextColor: '#eee',
      lineColor: '#666'
    },
    maxTextSize: 90000
  };
  try { if (typeof mermaid !== 'undefined') mermaid.initialize(MERMAID_BASE_CONFIG); } catch(e){}
  if (typeof mermaid === 'undefined') {
    let mermaidInitAttempts = 0;
    const mermaidInitTimer = setInterval(() => {
      mermaidInitAttempts++;
      if (typeof mermaid !== 'undefined') {
        try { mermaid.initialize(MERMAID_BASE_CONFIG); } catch (e) {}
        clearInterval(mermaidInitTimer);
      } else if (mermaidInitAttempts >= 10) {
        clearInterval(mermaidInitTimer);
        try {
          const warn = document.createElement('div');
          warn.className = 'mermaid-error';
          warn.textContent = 'Mermaid library unavailable - diagrams may not render. To fix, place mermaid.min.js at /static/vendor/mermaid.min.js or enable CDN access.';
          warn.style.cssText = 'position:fixed;bottom:8px;right:8px;background:#ffcc00;color:#000;padding:8px;border-radius:6px;z-index:9999;font-size:12px';
          document.body.appendChild(warn);
        } catch (e) {}
      }
    }, 500);
  }

  const form            = document.getElementById('scan-form');
  const repoSelect      = document.getElementById('repo-select');
  const nameInput       = null; // scan name removed - server will assign a scan id
  const scanBtn         = document.getElementById('scan-btn');
  const resetBtn        = document.getElementById('reset-btn');
  const statusBar       = document.getElementById('status-bar');
  const statusText      = document.getElementById('status-text');
  const spinner         = document.getElementById('spinner');
  const logOutput       = document.getElementById('log-output');
  const tabsEl          = document.getElementById('diagram-tabs');
  const viewsEl         = document.getElementById('diagram-views');
  const placeholder     = document.getElementById('diagram-placeholder');
  const copyDiagramBtn  = document.getElementById('copy-diagram-btn');

  if (copyDiagramBtn) {
    copyDiagramBtn.addEventListener('click', async () => {
      // Copy the currently active mermaid source if available
      const activeView = viewsEl && viewsEl.querySelector('.diagram-view.active');
      if (!activeView) return;
      const mer = activeView.querySelector('.mermaid');
      if (!mer) return;
      const src = mer.textContent || mer.innerText || '';
      try {
        await navigator.clipboard.writeText(src);
        // small transient feedback
        const old = copyDiagramBtn.textContent;
        copyDiagramBtn.textContent = 'Copied';
        setTimeout(() => { copyDiagramBtn.textContent = old; }, 1200);
      } catch (e) {
        console.warn('Copy failed:', e);
      }
    });
  }
  const zoomInBtn       = document.getElementById('zoom-in-btn');
  const zoomOutBtn      = document.getElementById('zoom-out-btn');
  const zoomResetBtn    = document.getElementById('zoom-reset-btn');
  const toggleLogBtn    = document.getElementById('toggle-log-btn');
  const diagramWrap     = document.getElementById('diagram-zoom-wrap');
  const diagramInner    = document.getElementById('diagram-zoom-inner');
  const sectionTabBar   = document.getElementById('section-tab-bar');
  const sectionContent  = document.getElementById('section-panel-content');
  const tabBarPlaceholder = document.getElementById('tab-bar-placeholder');
  const toggleSectionsBtn = document.getElementById('toggle-sections-btn');
  const toggleDiagramBtnPrimary = document.getElementById('toggle-diagram-btn');
  const toggleDiagramBtnPersistent = document.getElementById('toggle-diagram-btn-persistent');
  const toggleDiagramBtns = [toggleDiagramBtnPrimary, toggleDiagramBtnPersistent].filter(Boolean);
  const diagramPanel = document.getElementById('diagram-panel');

  // Ensure diagramPanel exists before attempting to read/set styles
  if (diagramPanel && getComputedStyle(diagramPanel).display === 'none') {
    try { localStorage.setItem('diagramHidden', '1'); } catch (e) {}
  }

  // Sections should be visible by default after load so users immediately see tabbed content.
  let showingSections = true;

  // Zoom/pan state
  let zoomLevel = 1, panX = 0, panY = 0;
  let isPanning = false, panStartX = 0, panStartY = 0, panStartPX = 0, panStartPY = 0;
  const ZOOM_STEP = 0.1, ZOOM_MIN = 0.1, ZOOM_MAX = 20;
  let pendingFitHandle = null;
  let pendingFitIsRAF = false;

  const pastScansRow    = document.getElementById('past-scans-row');
  const pastScanSelect  = document.getElementById('past-scan-select');
  const loadScanBtn     = null; // load button removed - dropdown auto-loads
  // Compare UI temporarily removed
  const compareToggle   = null;
  const compareRow      = null;
  const compareFrom     = null;
  const compareTo       = null;
  const runCompareBtn   = null;

  // Diagram state
  let diagrams = [];
  let activeTab = 0;
  let currentRepoName = '';
  let currentExpId    = '';
  let scanInProgress  = false;

  function resolveSelectedRepoName() {
    if (currentRepoName) return currentRepoName;
    if (!repoSelect) return '';
    const selected = repoSelect.options[repoSelect.selectedIndex];
    if (!selected || !selected.value || selected.disabled) return '';
    const dataName = (selected.dataset && selected.dataset.name ? selected.dataset.name.trim() : '');
    if (dataName) {
      currentRepoName = dataName;
      return dataName;
    }
    const fallback = (selected.textContent || '').split('—')[0].trim();
    if (fallback) {
      currentRepoName = fallback;
      return fallback;
    }
    return '';
  }

  // -- Zoom + Pan (right panel) --
  function applyTransform() {
    if (diagramInner) {
      diagramInner.style.transform = `translate(${panX}px, ${panY}px) scale(${zoomLevel})`;
    }
  }

  function resetZoomPan() {
    zoomLevel = 1; panX = 0; panY = 0;
    applyTransform();
  }

  function scheduleFitDiagram(delay = 0) {
    if (pendingFitHandle !== null) {
      if (pendingFitIsRAF && typeof cancelAnimationFrame !== 'undefined') {
        cancelAnimationFrame(pendingFitHandle);
      } else {
        clearTimeout(pendingFitHandle);
      }
      pendingFitHandle = null;
    }
    const runner = () => {
      pendingFitHandle = null;
      fitDiagram();
    };
    if (delay > 0) {
      pendingFitIsRAF = false;
      pendingFitHandle = setTimeout(runner, delay);
    } else if (typeof requestAnimationFrame !== 'undefined') {
      pendingFitIsRAF = true;
      pendingFitHandle = requestAnimationFrame(runner);
    } else {
      pendingFitIsRAF = false;
      pendingFitHandle = setTimeout(runner, 0);
    }
  }

  function fitDiagram() {
    if (!diagramWrap || !diagramInner) return;
    const wrapW = diagramWrap.clientWidth;
    const wrapH = diagramWrap.clientHeight;
    if (!wrapW || !wrapH) {
      scheduleFitDiagram();
      return;
    }
    const svg = diagramInner.querySelector('svg');
    if (!svg) { resetZoomPan(); return; }
    const svgW  = svg.scrollWidth || svg.getBoundingClientRect().width || 400;
    const svgH  = svg.scrollHeight || svg.getBoundingClientRect().height || 300;
    if (!svgW || !svgH) { resetZoomPan(); return; }
    const scale = Math.min(wrapW / svgW, wrapH / svgH, 1) * 0.95;
    zoomLevel = Math.max(ZOOM_MIN, scale);
    panX = (wrapW - svgW * zoomLevel) / 2;
    panY = (wrapH - svgH * zoomLevel) / 2;
    applyTransform();
  }

  if (zoomInBtn)    zoomInBtn.addEventListener('click',    () => { zoomLevel = Math.min(ZOOM_MAX, +(zoomLevel + ZOOM_STEP).toFixed(3)); applyTransform(); });
  if (zoomOutBtn)   zoomOutBtn.addEventListener('click',   () => { zoomLevel = Math.max(ZOOM_MIN, +(zoomLevel - ZOOM_STEP).toFixed(3)); applyTransform(); });
  if (zoomResetBtn) zoomResetBtn.addEventListener('click', () => fitDiagram());

  // Wheel zoom on diagram
  if (diagramWrap) {
    diagramWrap.addEventListener('wheel', (e) => {
      e.preventDefault();
      const delta = e.deltaY < 0 ? ZOOM_STEP : -ZOOM_STEP;
      const newScale = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, +(zoomLevel + delta).toFixed(3)));
      // Zoom towards cursor
      const rect = diagramWrap.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      panX = mx - (mx - panX) * (newScale / zoomLevel);
      panY = my - (my - panY) * (newScale / zoomLevel);
      zoomLevel = newScale;
      applyTransform();
    }, { passive: false });

    diagramWrap.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      isPanning = true;
      panStartX = e.clientX; panStartY = e.clientY;
      panStartPX = panX;    panStartPY = panY;
      diagramWrap.setPointerCapture(e.pointerId);
    });
    diagramWrap.addEventListener('pointermove', (e) => {
      if (!isPanning) return;
      panX = panStartPX + (e.clientX - panStartX);
      panY = panStartPY + (e.clientY - panStartY);
      applyTransform();
    });
    diagramWrap.addEventListener('pointerup',   () => { isPanning = false; });
    diagramWrap.addEventListener('pointercancel', () => { isPanning = false; });
  }

  // Collapse/expand the scan (left) sidebar and resize diagram accordingly
  const workspaceEl = document.querySelector('.workspace');

  function setSidebarCollapsed(collapsed, { persist = true } = {}) {
    if (!workspaceEl) return;
    if (collapsed) workspaceEl.classList.add('collapsed'); else workspaceEl.classList.remove('collapsed');
    if (persist) {
      try { localStorage.setItem('scanCollapsed', collapsed ? '1' : '0'); } catch (e) {}
    }
    const lp = document.getElementById('log-panel');
    if (lp) lp.style.display = collapsed ? 'none' : '';
    if (toggleLogBtn) {
      toggleLogBtn.textContent = collapsed ? 'Expand scan' : 'Hide scan';
      toggleLogBtn.title = collapsed ? 'Expand/Show scan output' : 'Hide/Collapse scan output';
    }
    try { fitDiagram(); } catch (e) {}
    scheduleFitDiagram(260);
  }

  function setDiagramHidden(hidden, { persist = true } = {}) {
    if (!diagramPanel || !workspaceEl) return;
    if (persist) {
    try { localStorage.setItem('diagramHidden', hidden ? '1' : '0'); } catch (e) {}
    }
    diagramPanel.style.display = hidden ? 'none' : '';
    if (hidden) workspaceEl.classList.add('diagram-hidden'); else workspaceEl.classList.remove('diagram-hidden');
    toggleDiagramBtns.forEach(b => {
      try { b.textContent = hidden ? 'Show diagram' : 'Hide diagram'; b.title = hidden ? 'Show architecture diagram' : 'Hide architecture diagram'; } catch (e) {}
    });
    scheduleFitDiagram(200);
  }

  // Initialize diagram visibility from localStorage (collapsed => hidden)

  try {
    const savedDiag = localStorage.getItem('diagramHidden');
    if (savedDiag === '1') setDiagramHidden(true);
    else { toggleDiagramBtns.forEach(b => { try { b.textContent = 'Hide diagram'; b.title = 'Hide architecture diagram'; } catch (e) {} }); }
    const savedCollapsed = localStorage.getItem('scanCollapsed');
    if (savedCollapsed === '1') setSidebarCollapsed(true, { persist: false });
  } catch (e) {}

  // Attach click handler to all toggle buttons (persistent + status-bar)
  toggleDiagramBtns.forEach(b => {
    try {
      b.addEventListener('click', () => {
        const currentlyHidden = diagramPanel && diagramPanel.style.display === 'none';
        setDiagramHidden(!currentlyHidden);
      });
    } catch (e) {}
  });

  // -- Section tabs (left panel) --
  let activeSection = '';

  function initIngressApiDetails(sectionContent) {
    if (!sectionContent) return;
    const table = sectionContent.querySelector('#section-api-details-table');
    if (!table) return;

    // Initialize toggle glyph state
    table.querySelectorAll('.expand-toggle').forEach(toggle => {
      toggle.dataset.iconCollapsed = toggle.dataset.iconCollapsed || '▶';
      toggle.dataset.iconExpanded = toggle.dataset.iconExpanded || '▼';
      toggle.textContent = toggle.dataset.iconCollapsed;
    });

    table.addEventListener('click', (event) => {
      const toggle = event.target.closest('.expand-toggle');
      if (!toggle || !table.contains(toggle)) return;
      event.preventDefault();
      const apiId = toggle.dataset.apiId;
      if (!apiId) return;
      const expanded = toggle.classList.toggle('expanded');
      toggle.textContent = expanded ? (toggle.dataset.iconExpanded || '▼') : (toggle.dataset.iconCollapsed || '▶');
      const childRows = table.querySelectorAll(`tr[data-parent-api="${apiId.replace(/"/g, '\\"')}"]`);
      childRows.forEach(row => {
        row.style.display = expanded ? '' : 'none';
      });
    });
  }

  async function loadSectionTabs(expId, repoName) {
    const resolvedRepoName = repoName || resolveSelectedRepoName();
    if (!resolvedRepoName) return;
    const effectiveExpId = await resolveExperimentId(expId, resolvedRepoName);
    if (!effectiveExpId) {
      console.warn('Section tabs cannot load without an experiment id for', resolvedRepoName);
      sectionTabBar.innerHTML = '';
      tabBarPlaceholder && (tabBarPlaceholder.style.display = '');
      return;
    }
    currentExpId = effectiveExpId;
    currentRepoName = resolvedRepoName;
    try {
      const resp = await fetch(`/api/view/tabs/${encodeURIComponent(effectiveExpId)}/${encodeURIComponent(resolvedRepoName)}`);
      const text = await resp.text();
      try {
        const data = JSON.parse(text);
        if (data && data.error) {
          console.warn('Tabs endpoint returned error:', data.error);
          sectionTabBar.innerHTML = '';
          tabBarPlaceholder && (tabBarPlaceholder.style.display = '');
          return;
        }
        renderSectionTabs((data && data.tabs) || [], expId, resolvedRepoName);
      } catch (e) {
        console.warn('Failed to parse tabs response as JSON:', e, text.slice(0,200));
        sectionTabBar.innerHTML = '';
        tabBarPlaceholder && (tabBarPlaceholder.style.display = '');
      }
      // Intentionally do not auto-switch to a content tab here; renderSectionTabs will
      // show the Log tab by default so live logs remain visible when loading tabs.
    } catch (err) {
      console.warn('Failed to load section tabs:', err);
    }
  }

  // Helper to activate a section tab by key (or show raw log for __log__)
  function activateSectionKey(key, expId, repoName) {
    if (!sectionTabBar) return;
    const btn = sectionTabBar.querySelector(`.section-tab-btn[data-key="${key}"]`);
    if (!btn) return;
    // Toggle active class
    sectionTabBar.querySelectorAll('.section-tab-btn').forEach(b => b.classList.toggle('active', b === btn));
    if (key === '__log__') {
      // Show raw log but keep tab bar visible
      if (logOutput) logOutput.style.display = '';
      if (sectionContent) sectionContent.style.display = 'none';
      sectionTabBar.style.display = 'flex';
      showingSections = false;
      if (toggleSectionsBtn) toggleSectionsBtn.textContent = 'Sections';
      activeSection = '__log__';
    } else {
      // Load content for the requested key
      loadSectionContent(key, expId || currentExpId, repoName || currentRepoName);
      activeSection = key;
    }
  }

  function renderSectionTabs(tabs, expId, repoName) {
    sectionTabBar.innerHTML = '';
    if (!tabs.length) return;
    tabBarPlaceholder && (tabBarPlaceholder.style.display = 'none');

    // Build a map of available tabs for quick lookup
    const tabMap = new Map();
    for (const t of tabs) tabMap.set(t.key, t);

    // Desired order: Assets, TLDR, then the rest in server order (omit Log tab — use Show Log button)
    const ordered = ['assets', 'tldr'];
    for (const t of tabs) {
      if (!ordered.includes(t.key)) ordered.push(t.key);
    }

    for (const key of ordered) {
      const tab = tabMap.get(key);
      if (!tab) continue;
      const btn = document.createElement('button');
      btn.className = 'section-tab-btn';
      btn.textContent = tab.label;
      btn.dataset.key = tab.key;
      btn.addEventListener('click', () => { activateSectionKey(tab.key, expId, repoName); });
      sectionTabBar.appendChild(btn);
    }

    // Default selection: TL;DR if present, else Assets, else the first available tab
    let defaultKey = null;
    if (tabMap.has('tldr')) defaultKey = 'tldr';
    else if (tabMap.has('assets')) defaultKey = 'assets';
    else defaultKey = tabs[0].key;
    activateSectionKey(defaultKey);

    if (!showingSections && !scanInProgress) {
      showSectionsInLog();
    }
  }

  async function loadSectionContent(key, expId, repoName) {
    activeSection = key;
    // Mark active tab
    sectionTabBar.querySelectorAll('.section-tab-btn').forEach(b =>
      b.classList.toggle('active', b.dataset.key === key));

    sectionContent.innerHTML = '<div class="section-loading"><span>Loading…</span></div>';

    const structuredKeys = ['tldr', 'overview', 'risks', 'assets', 'findings', 'ingress', 'egress', 'ports', 'roles', 'containers'];

    try {
      const resolvedRepoName = repoName || currentRepoName || resolveSelectedRepoName();
      if (!resolvedRepoName) {
        sectionContent.innerHTML = '<div class="empty-state"><p class="s-inline-e09362">No repository selected yet.</p></div>';
        showSectionsInLog();
        return;
      }
      const targetExpId = await resolveExperimentId(expId, resolvedRepoName);
      if (!targetExpId) {
        sectionContent.innerHTML = '<div class="empty-state"><p class="s-inline-e09362">Experiment metadata is not available yet.</p></div>';
        showSectionsInLog();
        return;
      }
      if (!structuredKeys.includes(key)) {
        sectionContent.innerHTML = '<div class="empty-state"><p class="s-inline-e09362">Unsupported section key.</p></div>';
        return;
      }
      const url = `/api/view/${key}/${encodeURIComponent(targetExpId)}/${encodeURIComponent(resolvedRepoName)}`;
      const resp = await fetch(url);
      const html = await resp.text();
      sectionContent.innerHTML = html;

      // Re-run mermaid for any inline diagrams
      const merNodes = sectionContent.querySelectorAll('.mermaid:not([data-processed])');
      if (merNodes.length) await _runMermaid(Array.from(merNodes));

      // Initialize known section scripts (moved to static JS) so they run when a fragment is inserted.
      try {
        if (key === 'assets' && window.initAssets) {
          try { window.initAssets(sectionContent, resolvedRepoName, targetExpId); } catch (e) { console.warn('initAssets error:', e); }
        }
        if (key === 'roles' && window.initRoles) {
          try { window.initRoles(sectionContent, resolvedRepoName); } catch (e) { console.warn('initRoles error:', e); }
        }
        if (key === 'containers' && window.initContainers) {
          try { window.initContainers(sectionContent); } catch (e) { console.warn('initContainers error:', e); }
        }
        if (key === 'overview' && window.initOverview) {
          try { window.initOverview(sectionContent); } catch (e) { console.warn('initOverview error:', e); }
        }
        if (key === 'ingress') {
          initIngressApiDetails(sectionContent);
        }
        
        // Initialize column resizing for all section tables (including multiple tables per section)
        if (window.initTableColumnResize) {
          const sectionTables = sectionContent.querySelectorAll('.section-table');
          if (sectionTables.length > 0 && key) {
            sectionTables.forEach((sectionTable, idx) => {
              const tableId = sectionTables.length > 1 ? `${key}_table${idx}` : key;
              const storageKey = `${tableId}_col_widths_${resolvedRepoName}`;
              window.initTableColumnResize(sectionTable, storageKey);
            });
          }
        }
      } catch (e) {
        console.warn('Failed to run section initializer:', e);
      }
    } catch (err) {
      sectionContent.innerHTML = `<div class="empty-state"><p style="color:var(--red)">Failed to load section: ${err}</p></div>`;
    }
  }

  // -- Log helpers --
  function classifyLine(line) {
    if (/^-+$/.test(line) || /^={3,}/.test(line)) return 'line-sep';
    if (/\[ERROR\]|✗|error/i.test(line))           return 'line-error';
    if (/\[WARN\]|WARNING/i.test(line))             return 'line-warn';
    if (/^▶|Phase \d|Pipeline|Rendering|Experiment/i.test(line)) return 'line-phase';
    if (/✓|✅|complete|success/i.test(line))        return 'line-ok';
    if (/\[Web\]/.test(line))                       return 'line-done';
    return '';
  }

  function appendLog(line, opts = {}) {
    const force = !!(opts && opts.force);
    // If sections are currently visible in the log panel, don't append streaming logs
    // unless the caller explicitly forces it (used by AI analysis feedback).
    if (showingSections && !force) return;
    const wasEmpty = logOutput.querySelector('span[style]');
    if (wasEmpty) logOutput.innerHTML = '';
    const span = document.createElement('span');
    const cls = classifyLine(line);
    if (cls) span.className = cls;
    span.textContent = line + '\n';
    logOutput.appendChild(span);
    logOutput.scrollTop = logOutput.scrollHeight;
  }

  // -- Diagram rendering --
  async function _runMermaid(nodes) {
    // Render each node individually so one bad diagram can't block the others.
    async function tryRender(node, config) {
      try {
        mermaid.initialize(config);
        await mermaid.run({ nodes: [node] });
        // Apply current zoom level so rendered SVG scales appropriately
        try { node.style.transform = `scale(${zoomLevel})`; } catch (e) { /* ignore */ }
        return null;
      } catch (e) {
        return e;
      }
    }

    for (const node of nodes) {
      if (node.dataset.mermaidRendered === '1') continue;
      const origText = node.textContent || '';
      let lastErr = null;

      try {
        let err;

        // Attempt 1: base config
        err = await tryRender(node, MERMAID_BASE_CONFIG);
        if (!err) continue;
        lastErr = err;

        // Attempt 2: linear curve
        const cfgLinear = JSON.parse(JSON.stringify(MERMAID_BASE_CONFIG));
        cfgLinear.flowchart = Object.assign({}, cfgLinear.flowchart, { curve: 'linear' });
        err = await tryRender(node, cfgLinear);
        if (!err) continue;
        lastErr = err;

        // Attempt 3: useMaxWidth true
        const cfgMax = JSON.parse(JSON.stringify(MERMAID_BASE_CONFIG));
        cfgMax.flowchart = Object.assign({}, cfgMax.flowchart, { useMaxWidth: true });
        err = await tryRender(node, cfgMax);
        if (!err) continue;
        lastErr = err;

        // Attempt 4: remove edge labels and try base config again
        const stripped = sanitizeMermaid(origText.replace(/\|[^|]*\|/g, ''));
        node.textContent = stripped;
        err = await tryRender(node, MERMAID_BASE_CONFIG);
        if (!err) continue;
        lastErr = err;

        // Restore original sanitized content and show error
        node.textContent = sanitizeMermaid(origText);
        node.innerHTML = `<div style="color:#f85149;font-size:0.78rem;padding:12px;font-family:monospace;white-space:pre-wrap">⚠ Diagram parse error:\n${(lastErr && lastErr.message) || lastErr}</div>`;
        console.error('Mermaid render error:', lastErr);
      } finally {
        node.dataset.mermaidRendered = '1';
      }
    }

    // Restore base config after attempts
    try { mermaid.initialize(MERMAID_BASE_CONFIG); } catch (e) { /* ignore */ }
  }

  async function renderMermaidInView(view) {
    if (!view) return;
    const nodes = Array.from(view.querySelectorAll('.mermaid:not([data-mermaid-rendered])'));
    if (!nodes.length) return;
    await _runMermaid(nodes);
  }

  // Sanitize Mermaid source to fix common issues emitted by generator
  function sanitizeMermaid(src) {
    if (!src || typeof src !== 'string') return src;
    let s = src;
    // Hyphenate known underscored CSS props
    s = s.replace(/stroke_width/g, 'stroke-width')
         .replace(/stroke_dasharray/g, 'stroke-dasharray')
         .replace(/stroke_opacity/g, 'stroke-opacity')
         .replace(/fill_opacity/g, 'fill-opacity')
         .replace(/font_size/g, 'font-size')
         .replace(/font_weight/g, 'font-weight')
         .replace(/text_anchor/g, 'text-anchor')
         .replace(/line_height/g, 'line-height');
    // Remove invisible variation selectors
    s = s.replace(/\uFE0F/g, '');
    // Replace & inside quoted labels with 'and'
    s = s.replace(/"([^"\\]*(?:\\.[^"\\]*)*)"/g, (m, g1) => '"' + g1.replace(/&/g, 'and') + '"');
    // Convert space-separated stroke-dasharray values to comma-separated
    s = s.replace(/stroke-dasharray\s*:\s*(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)/g, (m, a, b) => `stroke-dasharray:${a},${b}`);
    // Remove px units from numeric stroke widths (Mermaid prefers numbers)
    s = s.replace(/stroke-width\s*:\s*(\d+)px/g, 'stroke-width:$1');
    // Remove linkStyle lines which can reference out-of-range indices and crash the renderer
    s = s.replace(/^\s*linkStyle\s+\d+[^\n]*\n/gm, '');

    // Remove explicit "contains" edges: containment should be represented via subgraphs
    s = s.split('\n').filter(line => !/\bcontains\b/i.test(line)).join('\n');

    // Collapse newlines inside bracketed labels: [a\n b] => [a b]
    s = s.replace(/\[([^\]]*\n[^\]]*)\]/g, (m, g1) => '[' + g1.replace(/\n|\r/g, ' ') + ']');

    // Remove self-edges (node linking to itself) which add clutter
    const lines = s.split('\n');
    const filtered = [];
    for (let line of lines) {
      if (/[-.]+>/.test(line)) {
        const parts = line.split(/[-.]+>/);
        if (parts.length >= 2) {
          const left = parts[0].replace(/["'`\[\]\(\)\{\}\s]/g, '').trim().toLowerCase();
          const right = parts[1].replace(/["'`\[\]\(\)\{\}\s:].*$/g, '').replace(/["'`\[\]\(\)\{\}\s]/g, '').trim().toLowerCase();
          if (left && right && left === right) {
            // skip self-edge
            continue;
          }
        }
      }
      filtered.push(line);
    }
    s = filtered.join('\n');

    // Deduplicate subgraph blocks, node definitions and style lines which often
    // occur repeatedly in generated output and can confuse the renderer.
    const linesArr = s.split('\n');
    const out = [];
    const seenSubgraphs = new Set();
    const seenNodes = new Set();
    const seenStyles = new Set();
    let skipSubgraph = 0;

    for (let i = 0; i < linesArr.length; i++) {
      const line = linesArr[i];
      const trimmed = line.trim();

      if (skipSubgraph > 0) {
        if (/^\s*end\s*$/i.test(trimmed)) {
          skipSubgraph -= 1;
        }
        continue;
      }

      // subgraph <id>[Label]
      const subMatch = trimmed.match(/^subgraph\s+([^\s\[]+)/i);
      if (subMatch) {
        const id = subMatch[1];
        if (seenSubgraphs.has(id)) {
          // skip until the matching 'end'
          skipSubgraph = 1;
          continue;
        }
        seenSubgraphs.add(id);
        out.push(line);
        continue;
      }

      // node definition like: nodeId[Label] or nodeId("Label")
      const nodeMatch = trimmed.match(/^([^\s\[]+)\s*(?:\[\[|\[\(|\[|\(\[|\("|\(\(|\{)/);
      if (nodeMatch) {
        const id = nodeMatch[1];
        if (seenNodes.has(id)) continue;
        seenNodes.add(id);
        out.push(line);
        continue;
      }

      // style <id> ...
      const styleMatch = trimmed.match(/^style\s+([^\s]+)/i);
      if (styleMatch) {
        const key = trimmed; // include full style string to detect duplicates
        if (seenStyles.has(key)) continue;
        seenStyles.add(key);
        out.push(line);
        continue;
      }

      out.push(line);
    }

    s = out.join('\n');

    // Normalize invalid Mermaid IDs (e.g. ${var.environment}, hyphens, dots)
    // to safe identifiers and rewrite all references consistently.
    const linesForIds = s.split('\n');
    const idCandidates = new Set();
    for (const line of linesForIds) {
      const trimmed = line.trim();
      const nodeMatch = trimmed.match(/^([^\s\[\(\{]+)\s*(?:\[\[|\[\(|\[|\(\[|\("|\(\(|\{)/);
      if (nodeMatch) idCandidates.add(nodeMatch[1]);
      const styleMatch = trimmed.match(/^style\s+([^\s]+)/i);
      if (styleMatch) idCandidates.add(styleMatch[1]);
    }

    const reserved = new Set(['flowchart', 'graph', 'subgraph', 'end', 'style', 'classDef', 'class', 'linkStyle']);
    const usedSafe = new Set();
    const idMap = new Map();

    const makeSafeId = (raw) => {
      let candidate = raw.replace(/[^A-Za-z0-9_]/g, '_').replace(/_+/g, '_').replace(/^_+|_+$/g, '');
      if (!candidate) candidate = 'node';
      if (/^\d/.test(candidate) || reserved.has(candidate.toLowerCase())) candidate = `n_${candidate}`;
      const base = candidate;
      let idx = 2;
      while (usedSafe.has(candidate)) {
        candidate = `${base}_${idx}`;
        idx += 1;
      }
      usedSafe.add(candidate);
      return candidate;
    };

    for (const original of Array.from(idCandidates).sort()) {
      if (/^[A-Za-z_][A-Za-z0-9_]*$/.test(original) && !reserved.has(original.toLowerCase())) continue;
      idMap.set(original, makeSafeId(original));
    }

    if (idMap.size) {
      const escapeRegExp = (str) => str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      for (const original of Array.from(idMap.keys()).sort((a, b) => b.length - a.length)) {
        const safe = idMap.get(original);
        const pattern = new RegExp(`(^|[^A-Za-z0-9_])${escapeRegExp(original)}(?=[^A-Za-z0-9_]|$)`, 'g');
        s = s.replace(pattern, `$1${safe}`);
      }
    }

    return s;
  }

  async function renderDiagrams(data) {
    diagrams = data;
    if (placeholder) placeholder.style.display = 'none';
    if (tabsEl) tabsEl.innerHTML = '';
    if (viewsEl) viewsEl.querySelectorAll('.diagram-view, .diff-view').forEach(el => el.remove());

    for (let i = 0; i < diagrams.length; i++) {
      const { title, code } = diagrams[i];
      const tab = document.createElement('button');
      tab.type = 'button';
      tab.className = 'tab-btn' + (i === 0 ? ' active' : '');
      tab.textContent = title;
      tab.dataset.idx = i;
      tab.addEventListener('click', () => switchTab(i));
      tabsEl.appendChild(tab);

      const view = document.createElement('div');
      view.className = 'diagram-view' + (i === 0 ? ' active' : '');
      view.dataset.idx = i;
      const mermaidEl = document.createElement('div');
      mermaidEl.className = 'mermaid';
      mermaidEl.textContent = sanitizeMermaid(code);
      view.appendChild(mermaidEl);
      viewsEl.appendChild(view);
    }

    if (diagrams.length > 1) tabsEl.classList.add('visible');
    const initialView = viewsEl.querySelector('.diagram-view.active');
    if (initialView) await renderMermaidInView(initialView);
    activeTab = 0;
    scheduleFitDiagram(200);
  }

  function switchTab(idx) {
    activeTab = idx;
    tabsEl.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', +b.dataset.idx === idx));
    viewsEl.querySelectorAll('.diagram-view, .diff-view').forEach(v => {
      v.classList.toggle('active', +v.dataset.idx === idx);
    });
    // Re-apply transform to the newly active diagram
    const activeView = viewsEl.querySelector('.diagram-view.active');
    if (activeView) void renderMermaidInView(activeView);
    scheduleFitDiagram(100);
  }

  // -- Diff rendering --
  async function renderDiff(data) {
    const { from: idFrom, to: idTo, diagrams_from, diagrams_to, timeline } = data;

    if (placeholder) placeholder.style.display = 'none';
    if (tabsEl) tabsEl.innerHTML = '';
    if (viewsEl) viewsEl.querySelectorAll('.diagram-view, .diff-view').forEach(el => el.remove());
    if (tabsEl) tabsEl.classList.add('visible');
    diagrams = [];
    activeTab = 0;

    // Build compare tab (index 0)
    const diffTab = document.createElement('button');
    diffTab.type = 'button';
    diffTab.className = 'tab-btn active';
    diffTab.textContent = `📊 Diff ${idFrom} → ${idTo}`;
    diffTab.dataset.idx = 0;
    diffTab.addEventListener('click', () => switchTab(0));
    tabsEl.appendChild(diffTab);

    const diffView = document.createElement('div');
    diffView.className = 'diff-view active';
    diffView.dataset.idx = 0;

    // Side-by-side diagrams
    const sbs = document.createElement('div');
    sbs.className = 'diff-side-by-side';

    async function buildSide(label, diags) {
      const side = document.createElement('div');
      side.className = 'diff-side';
      const lbl = document.createElement('div');
      lbl.className = 'diff-side-label';
      lbl.textContent = label;
      side.appendChild(lbl);
      if (diags && diags.length > 0) {
          const m = document.createElement('div');
          m.className = 'mermaid';
          m.textContent = sanitizeMermaid(diags[0].code);  // show first provider diagram (sanitized)
          side.appendChild(m);
        } else {
          side.innerHTML += '<p style="color:#484f58;font-size:0.8rem">No diagram available</p>';
        }
        return side;
    }

    const sideFrom = await buildSide(`Scan ${idFrom}`, diagrams_from);
    const sideTo   = await buildSide(`Scan ${idTo}`, diagrams_to);
    sbs.appendChild(sideFrom);
    sbs.appendChild(sideTo);
    diffView.appendChild(sbs);

    // Render mermaid in both sides
    const mermaidNodes = diffView.querySelectorAll('.mermaid');
    if (mermaidNodes.length) {
      await _runMermaid(mermaidNodes);
    }

    // Timeline
    const tl = document.createElement('div');
    tl.className = 'diff-timeline';
    const tlTitle = document.createElement('h3');
    tlTitle.textContent = `Change timeline — Scan ${idFrom} → ${idTo}`;
    tl.appendChild(tlTitle);

    if (!timeline || timeline.length === 0) {
      const none = document.createElement('div');
      none.className = 'no-changes';
      none.textContent = 'No changes detected between these scans.';
      tl.appendChild(none);
    } else {
      for (const entry of timeline) {
        const el = document.createElement('div');
        el.className = 'timeline-entry';
        const scanLabel = document.createElement('div');
        scanLabel.className = 'timeline-scan-id';
        scanLabel.textContent = `Scan ${entry.experiment_id}`;
        el.appendChild(scanLabel);

        for (const f of (entry.new_findings || [])) {
          const item = document.createElement('div');
          item.className = 'change-item change-new';
          item.textContent = `⚠ New finding: ${f.title || f.rule_id}${f.severity ? ' [' + f.severity + ']' : ''}`;
          el.appendChild(item);
        }
        for (const ruleId of (entry.resolved_findings || [])) {
          const item = document.createElement('div');
          item.className = 'change-item change-resolved';
          item.textContent = `✅ Resolved: ${ruleId}`;
          el.appendChild(item);
        }
        for (const r of (entry.resources_added || [])) {
          const item = document.createElement('div');
          item.className = 'change-item change-added';
          item.textContent = `➕ Resource added: ${r}`;
          el.appendChild(item);
        }
        for (const r of (entry.resources_removed || [])) {
          const item = document.createElement('div');
          item.className = 'change-item change-removed';
          item.textContent = `➖ Resource removed: ${r}`;
          el.appendChild(item);
        }
        tl.appendChild(el);
      }
    }

    diffView.appendChild(tl);
    viewsEl.appendChild(diffView);

    // Also add individual diagram tabs for "from" scan
    const allDiags = diagrams_from || [];
    diagrams = allDiags;
    for (let i = 0; i < allDiags.length; i++) {
      const tab = document.createElement('button');
      tab.type = 'button';
      tab.className = 'tab-btn';
      tab.textContent = `${idFrom}: ${allDiags[i].title}`;
      tab.dataset.idx = i + 1;
      tab.addEventListener('click', () => switchTab(i + 1));
      tabsEl.appendChild(tab);

      const view = document.createElement('div');
      view.className = 'diagram-view';
      view.dataset.idx = i + 1;
      const mermaidEl = document.createElement('div');
      mermaidEl.className = 'mermaid';
      mermaidEl.textContent = sanitizeMermaid(allDiags[i].code);
      view.appendChild(mermaidEl);
      viewsEl.appendChild(view);
    }

    // Render mermaid in the individual tabs too
    const remaining = viewsEl.querySelectorAll('.diagram-view .mermaid');
    if (remaining.length) {
      await _runMermaid(remaining);
    }
  }

  // -- Past scans --
  function _scanOptionLabel(scan) {
    const dt = scan.scanned_at ? scan.scanned_at.replace('T', ' ').slice(0, 19) : '';
    const flag = ''; // removed visual flag to avoid clutter
    return `Scan ${scan.experiment_id}${dt ? ' - ' + dt : ''}${flag}`;
  }

  function _populateScanSelects(scans) {
    if (pastScanSelect) {
      pastScanSelect.innerHTML = '<option value="" disabled selected>— select —</option>';
      for (const s of scans) {
        const opt = document.createElement('option');
        opt.value = s.experiment_id;
        opt.textContent = _scanOptionLabel(s);
        pastScanSelect.appendChild(opt);
      }
    }
  }

  async function loadPastScans(repoName) {
    if (!repoName) return [];
    try {
      const resp = await fetch(`/api/scans/${encodeURIComponent(repoName)}`);
      const data = await resp.json();
      const scans = data.scans || [];
      if (scans.length > 0) {
        _populateScanSelects(scans);
        // Select the most recent scan by default
        const last = scans[scans.length - 1];
        if (pastScanSelect && last && last.experiment_id) {
          pastScanSelect.value = last.experiment_id;
        }
        pastScansRow.classList.add('visible');
      } else {
        pastScansRow.classList.remove('visible');
        // compareRow removed
      }
      return scans;
    } catch {
      pastScansRow.classList.remove('visible');
      return [];
    }
  }

  let resolvingExperimentId = null;

  async function resolveExperimentId(expId, repoName) {
    const candidate = (expId || '').trim();
    if (candidate) {
      currentExpId = candidate;
      return candidate;
    }
    if (currentExpId) return currentExpId;
    if (!repoName) return '';
    if (resolvingExperimentId) return resolvingExperimentId;

    resolvingExperimentId = (async () => {
      try {
        const scans = await loadPastScans(repoName);
        if (scans.length) {
          const latest = scans[scans.length - 1].experiment_id;
          if (latest) {
            currentExpId = latest;
            return latest;
          }
        }
      } catch (err) {
        console.warn('Failed to resolve experiment id for sections:', err);
      } finally {
        resolvingExperimentId = null;
      }
      return '';
    })();

    return resolvingExperimentId;
  }

  async function _loadDiagrams(expId, { silent = false } = {}) {
    try {
      const resp = await fetch(`/api/diagrams/${encodeURIComponent(expId)}`);
      const data = await resp.json();
      if (data.diagrams && data.diagrams.length > 0) {
        await renderDiagrams(data.diagrams);
        if (!silent) setStatus(`Loaded scan ${expId}`, 'success');
        return true;
      }
      if (!silent) setStatus(`No diagrams found for scan ${expId}`, 'error');
      return false;
    } catch (err) {
      if (!silent) setStatus('Load failed: ' + err, 'error');
      return false;
    }
  }

  if (loadScanBtn) loadScanBtn.addEventListener('click', async () => {
    const expId = pastScanSelect.value;
    if (!expId) return;
    loadScanBtn.disabled = true;
    setStatus(`Loading scan ${expId}…`);
    spinner.style.display = '';
    statusBar.classList.add('visible');
    try {
      await _loadDiagrams(expId);
      // Load section tabs for the selected experiment and switch to Assets
      const targetRepoName = currentRepoName || resolveSelectedRepoName();
      if (targetRepoName) {
        currentRepoName = targetRepoName;
        await loadSectionTabs(expId, targetRepoName);
        try { activateSectionKey('assets', expId, targetRepoName); } catch (e) {}
      }
    } finally {
      spinner.style.display = 'none';
      loadScanBtn.disabled = false;
    }
  });

  // Auto-load sections and diagrams when a past-scan is selected (improves UX)
  if (pastScanSelect) pastScanSelect.addEventListener('change', async () => {
    const expId = pastScanSelect.value;
    if (!expId) return;
    // Mirror the Load button behavior
    if (loadScanBtn) loadScanBtn.disabled = true;
    setStatus(`Loading scan ${expId}…`);
    spinner.style.display = '';
    statusBar.classList.add('visible');
    try {
      await _loadDiagrams(expId);
      const targetRepoName = currentRepoName || resolveSelectedRepoName();
      if (targetRepoName) {
        currentRepoName = targetRepoName;
        await loadSectionTabs(expId, targetRepoName);
        try { activateSectionKey('assets', expId, targetRepoName); } catch (e) {}
      }
    } finally {
      spinner.style.display = 'none';
      if (loadScanBtn) loadScanBtn.disabled = false;
    }
  });

  // Compare UI removed
  // compareToggle event and runCompareBtn handler removed while the feature is hidden


  // -- SSE stream parsing --
  let buffer = '';

  function parseSSEChunk(chunk) {
    buffer += chunk;
    const messages = buffer.split('\n\n');
    buffer = messages.pop();
    for (const msg of messages) {
      if (!msg.trim()) continue;
      const eventMatch = msg.match(/^event:\s*(.+)$/m);
      const dataMatch  = msg.match(/^data:\s*(.+)$/m);
      if (!dataMatch) continue;
      const eventType = eventMatch ? eventMatch[1].trim() : 'message';
      let payload;
      try { payload = JSON.parse(dataMatch[1].trim()); } catch { payload = dataMatch[1].trim(); }
      handleEvent(eventType, payload);
    }
  }

  function handleEvent(type, payload) {
    if (type === 'experiment') {
      // Early experiment id emitted by server before pipeline output
      const expId = String(payload || '').trim();
      const repoName = resolveSelectedRepoName();
      if (expId) currentExpId = expId;
      if (expId && repoName) {
        setStatus(`Experiment ${expId} created`, 'info');
        // Preload diagrams and sections silently
        (async () => {
          await _loadDiagrams(expId, { silent: true });
          try { await loadSectionTabs(expId, repoName); } catch (e) {}
        })();
      }
      return;
    }
    if (type === 'log') {
      appendLog(payload);
    } else if (type === 'diagrams') {
      renderDiagrams(payload);
    } else if (type === 'error') {
      appendLog('[ERROR] ' + payload);
      setStatus('Error: ' + payload, 'error');
    } else if (type === 'done') {
      const code  = payload.exit_code;
      const expId = payload.experiment_id || currentExpId;
      const repoName = resolveSelectedRepoName();
      const effectiveRepoName = currentRepoName || repoName;
      if (code === 0) {
        setStatus(`✅ Scan complete — Experiment ${expId}`, 'success');
        // Refresh past scans list and section tabs
        if (effectiveRepoName) {
          currentRepoName = effectiveRepoName;
          loadPastScans(effectiveRepoName);
          (async () => {
            if (expId) {
              await loadSectionTabs(expId, effectiveRepoName);
              // After scan completes, switch to Assets tab
              try { activateSectionKey('assets', expId, effectiveRepoName); } catch (e) {}
              scheduleFitDiagram(500); // fit diagram after render
            }
          })();
        }
      } else {
        setStatus(`⚠ Pipeline exited with code ${code}`, 'error');
      }
      spinner.style.display = 'none';
      scanBtn.disabled = false;
      repoSelect.disabled = false;
      if (nameInput) nameInput.disabled = false;
      resetBtn.style.display = 'inline-block';
      scanInProgress = false;
    }
  }

  function showSectionsInLog() {
    if (!sectionTabBar || !sectionContent || !logOutput) return;
    logOutput.style.display = 'none';
    sectionTabBar.style.display = 'flex';
    sectionContent.style.display = 'flex';
    showingSections = true;
    if (toggleSectionsBtn) {
      toggleSectionsBtn.textContent = 'Show Log';
      toggleSectionsBtn.title = 'Show raw scan log';
    }
  }

  function showRawLog() {
    if (!sectionTabBar || !sectionContent || !logOutput) return;
    logOutput.style.display = '';
    sectionTabBar.style.display = 'none';
    sectionContent.style.display = 'none';
    showingSections = false;
    if (toggleSectionsBtn) {
      toggleSectionsBtn.textContent = 'Sections';
      toggleSectionsBtn.title = 'Show structured sections';
    }
  }

  if (toggleSectionsBtn) {
    toggleSectionsBtn.addEventListener('click', () => {
      if (showingSections) showRawLog(); else showSectionsInLog();
    });
  }

  function setStatus(text, cls) {
    statusText.textContent = text;
    statusText.className = 'status-text' + (cls ? ' ' + cls : '');
    statusBar.classList.add('visible');
  }

  // -- Form submit --
  if (form) form.addEventListener('submit', async (e) => {
    e.preventDefault();

    const repoPath = repoSelect.value;
    const scanName = ''; // scan name removed; server will generate experiment id
    currentRepoName = resolveSelectedRepoName() || currentRepoName;

    logOutput.innerHTML = '';
    viewsEl.querySelectorAll('.diagram-view, .diff-view').forEach(el => el.remove());
    tabsEl.innerHTML = '';
    tabsEl.classList.remove('visible');
    placeholder.style.display = '';
    buffer = '';
    diagrams = [];

    scanBtn.disabled = true;
    repoSelect.disabled = true;
    if (nameInput) nameInput.disabled = true;
    resetBtn.style.display = 'none';
    spinner.style.display = '';
    setStatus('Connecting…');

    // Keep the raw log visible during the scan
    scanInProgress = true;
    showRawLog();

    const formData = new FormData();
    formData.append('repo_path', repoPath);

    let response;
    try {
      response = await fetch('/scan', { method: 'POST', body: formData });
    } catch (err) {
      setStatus('Connection failed: ' + err, 'error');
      scanBtn.disabled = false; repoSelect.disabled = false; nameInput.disabled = false;
      spinner.style.display = 'none';
      return;
    }

    if (!response.ok) {
      const err = await response.text();
      setStatus('Server error: ' + err, 'error');
      scanBtn.disabled = false; repoSelect.disabled = false; nameInput.disabled = false;
      spinner.style.display = 'none';
      return;
    }

    setStatus('Scanning…');
    const reader  = response.body.getReader();
    const decoder = new TextDecoder();

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      parseSSEChunk(decoder.decode(value, { stream: true }));
    }
  });

  // -- Reset button --
  if (resetBtn) resetBtn.addEventListener('click', () => {
    repoSelect.disabled = false;
    nameInput.disabled = false;
    repoSelect.value = '';
    currentRepoName = '';
    currentExpId = '';
    activeSection = '';
    resetBtn.style.display = 'none';
    statusBar.classList.remove('visible');
    pastScansRow.classList.remove('visible');

    // Clear section tabs
    sectionTabBar.innerHTML = '';
    if (tabBarPlaceholder) {
      tabBarPlaceholder.style.display = '';
      sectionTabBar.appendChild(tabBarPlaceholder);
    }
    sectionContent.innerHTML = `<div class="empty-state" id="section-placeholder">
      <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
        <path d="M3 7h18M3 12h18M3 17h18"/>
      </svg>
      <p>Run or load a scan to view<br/>section details.</p>
    </div>`;
    // Reset diagram
    resetZoomPan();
  });

  // Auto-fill scan name from selected repo, load past scans, and silently auto-load latest diagram (no visual jump)
  if (repoSelect) repoSelect.addEventListener('change', async () => {
    const selected = repoSelect.options[repoSelect.selectedIndex];
    if (selected && selected.value) {
      const name = selected.dataset.name || selected.text.split(' — ')[0].trim();
      if (nameInput) nameInput.value = name;
      currentRepoName = name;

      const scans = await loadPastScans(name);

      // Auto-load the most recent scan that has a diagram (silently, no spinner/status in the scan card)
      if (scans && scans.length > 0) {
        let latest = null;
        for (let i = scans.length - 1; i >= 0; i--) {
          if (scans[i].has_diagrams) { latest = scans[i]; break; }
        }
        if (!latest) latest = scans[scans.length -1];
        if (latest && latest.experiment_id) {
          try {
            await _loadDiagrams(latest.experiment_id, { silent: true });
            // Also load section tabs (Assets, TLDR, etc.) and auto-activate Assets so sections populate
            try {
              await loadSectionTabs(latest.experiment_id, currentRepoName);
              try { activateSectionKey('assets', latest.experiment_id, currentRepoName); } catch (e) {}
            } catch (e) {
              console.warn('Auto-load sections failed:', e);
            }
            scheduleFitDiagram(500);
          } catch (err) {
            console.warn('Auto-load diagrams failed:', err);
          }
        }
      }
    }
  });

  document.getElementById('copy-log-btn')?.addEventListener('click', () => {
    navigator.clipboard.writeText(logOutput.innerText).catch(() => {});
  });

  document.getElementById('clear-log-btn')?.addEventListener('click', () => {
    logOutput.innerHTML = '<span style="color:#484f58">Scan output will appear here…</span>';
  });

  // Annotate repository options with a small icon if a past scan contains diagrams.
  // Runs in the background so the UI remains responsive.
  async function annotateRepoOptions() {
    const opts = Array.from(repoSelect.options || []);
    for (const opt of opts) {
      try {
        const name = opt.dataset.name;
        if (!name) continue;
        const resp = await fetch(`/api/scans/${encodeURIComponent(name)}`);
        if (!resp.ok) continue;
        const data = await resp.json();
        const scans = data.scans || [];
        if (scans.length) {
          let hasDiags = false;
          for (let i = scans.length - 1; i >= 0; i--) {
            if (scans[i].has_diagrams) { hasDiags = true; break; }
          }
          // Do not append symbols to option text — they caused visual clutter and overlap
          // If visual annotation is needed in future, prefer adding a CSS class or separate inline element.
        }
      } catch (err) {
        // Ignore per-repo errors
      }
    }
  }

  // Run annotation in background
  annotateRepoOptions();

  copyDiagramBtn?.addEventListener('click', () => {
    if (diagrams[activeTab]) {
      navigator.clipboard.writeText(diagrams[activeTab].code).catch(() => {});
    }
  });

  // Expose a few helpers for external modules/tests
  window._triage = {
    loadSectionTabs,
    loadSectionContent,
    renderDiagrams,
    renderDiff,
    fitDiagram,
    showRawLog,
    appendLog: (line) => appendLog(line, { force: true }),
    setStatus,
  };

})();
