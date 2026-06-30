(function() {
  console.log('[subscriptions] Script starting');
  const panel = document.getElementById('subscriptions-panel');
  if (!panel) { console.warn('[subscriptions] Panel not found'); return; }

  // ── State ─────────────────────────────────────────────────────────────────
  let _currentSubId = null;
  let _currentNodeMap = null;          // node_drilldown_map from ingress diagram
  const _breadcrumb = [];              // [{title, arm_type, resources}] stack
  let _loadDiagramController = null;  // AbortController for in-flight diagram fetch

  // ── Helpers ───────────────────────────────────────────────────────────────
  function renderBadge(env, badge) {
    const colours = { danger:'#dc2626', warning:'#d97706', info:'#2563eb', secondary:'#6b7280' };
    const c = colours[badge] || colours.secondary;
    return `<span style="display:inline-block;padding:2px 8px;border-radius:10px;font-size:0.75rem;font-weight:600;background:${c};color:#fff;">${env}</span>`;
  }

  function formatDate(iso) {
    if (!iso) return '—';
    try { return new Date(iso).toLocaleString(); } catch(e) { return iso; }
  }

  function architectureUrl(subId) {
    const url = new URL('/cloud/architecture', window.location.origin);
    url.searchParams.set('sub', subId);
    return url.toString();
  }

  async function waitForMermaid() {
    let attempts = 0;
    while (!window.mermaid && attempts < 100) {
      await new Promise(r => setTimeout(r, 50));
      attempts++;
    }
    return !!(window.mermaid && typeof window.mermaid.run === 'function');
  }

  function fitDiagramToViewport(transformTarget, viewport) {
    if (!transformTarget || !viewport) return 1;
    const svgEl = transformTarget.querySelector('svg');
    if (!svgEl) return 1;

    const cw = viewport.clientWidth - 48;
    const ch = viewport.clientHeight - 48;
    if (cw <= 0 || ch <= 0) return 1;

    let sw = parseFloat(svgEl.getAttribute('width')) || 0;
    let sh = parseFloat(svgEl.getAttribute('height')) || 0;
    if (!sw || !sh) {
      const vb = svgEl.viewBox?.baseVal;
      sw = vb?.width || svgEl.scrollWidth || 0;
      sh = vb?.height || svgEl.scrollHeight || 0;
    }
    if (!sw || !sh) return 1;

    let fitScale = Math.min(cw / sw, ch / sh) * 0.9;
    fitScale = Math.min(4, Math.max(0.05, fitScale));
    transformTarget.style.transformOrigin = '0 0';
    transformTarget.style.transform = `scale(${fitScale})`;
    viewport.scrollLeft = 0;
    viewport.scrollTop = 0;
    return fitScale;
  }

  function escapeHtml(str) {
    return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  /**
   * Inject a <style> tag scoped to the current subscription.
   * Deduplicates by subId + key so the same CSS is never added twice.
   * All injected tags carry data-sub-style so loadDiagram() can purge them on reload.
   */
  function injectSubStyle(subId, key, css) {
    if (!css) return;
    const attrVal = `${subId}:${key}`;
    if (document.querySelector(`style[data-sub-style="${CSS.escape(attrVal)}"]`)) return;
    const st = document.createElement('style');
    st.setAttribute('data-sub-style', attrVal);
    st.textContent = css;
    document.head.appendChild(st);
  }

  /** Build a zoom / fit / export controls bar for a diagram container. */
  function buildControlsBar(containerId) {
    const btnStyle = 'padding:4px 8px;font-size:0.75rem;background:var(--btn-bg,#333);border:1px solid var(--border-color,#555);border-radius:3px;cursor:pointer;color:var(--text-primary,#ccc);';
    const bar = document.createElement('div');
    bar.style.cssText = 'display:flex;gap:4px;margin-bottom:4px;';
    [['🔍+','data-diagram-zoom-in'],['🔍−','data-diagram-zoom-out'],['⊡ Fit','data-diagram-fit'],['💾 Export','data-diagram-export']].forEach(([txt, attr]) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.textContent = txt;
      btn.style.cssText = btnStyle;
      btn.setAttribute(attr, containerId);
      bar.appendChild(btn);
    });
    return bar;
  }

  function drilldownCellText(cell) {
    if (cell == null) return '';
    if (typeof cell === 'object') {
      if (cell.label != null) return String(cell.label);
      if (cell.text != null) return String(cell.text);
    }
    return String(cell);
  }

  function drilldownRowSearchText(row) {
    if (row && typeof row.search_text === 'string' && row.search_text.trim()) {
      return row.search_text.toLowerCase();
    }
    if (row && Array.isArray(row.cells)) {
      return row.cells.map(drilldownCellText).join(' ').toLowerCase();
    }
    return '';
  }

  function populateDrilldownCell(span, cell) {
    if (!span) return;
    if (cell && typeof cell === 'object' && cell.href && cell.label !== undefined) {
      if (cell.style) span.style.cssText = cell.style;
      const link = document.createElement('a');
      link.href = String(cell.href);
      link.textContent = String(cell.label);
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      if (cell.title) link.title = String(cell.title);
      link.style.cssText = 'color:#60a5fa;text-decoration:underline;word-break:break-all;';
      span.appendChild(link);
      return;
    }
    if (cell && typeof cell === 'object' && cell.label !== undefined) {
      if (cell.style) span.style.cssText = cell.style;
      appendDrilldownText(span, cell.label);
      return;
    }
    const value = cell == null || cell === '' ? '—' : String(cell);
    if (value === '—') {
      span.textContent = value;
      span.style.color = '#4b5563';
      return;
    }
    appendDrilldownText(span, value);
  }

  function drilldownExternalHref(value) {
    const text = String(value || '').trim();
    if (!text) return '';
    if (/^[a-z][a-z0-9+.-]*:\/\//i.test(text)) return text;
    return `https://${text}`;
  }

  function appendDrilldownText(container, value) {
    if (!container) return;
    const text = String(value ?? '');
    if (!text) return;

    // Linkify hostnames / URLs while preserving the surrounding text.
    const tokenRe = /\b(?:https?:\/\/|ftp:\/\/)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?::\d{2,5})?(?:\/[^\s<>()"'`]+)?/gi;
    let lastIndex = 0;
    let match;
    while ((match = tokenRe.exec(text)) !== null) {
      if (match.index > lastIndex) {
        container.appendChild(document.createTextNode(text.slice(lastIndex, match.index)));
      }

      const raw = match[0];
      const trimmed = raw.replace(/[.,;!?]+$/g, '');
      if (trimmed) {
        const link = document.createElement('a');
        link.href = drilldownExternalHref(trimmed);
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = trimmed;
        link.style.cssText = 'color:#60a5fa;text-decoration:underline;word-break:break-all;';
        container.appendChild(link);
        if (trimmed.length < raw.length) {
          container.appendChild(document.createTextNode(raw.slice(trimmed.length)));
        }
      } else {
        container.appendChild(document.createTextNode(raw));
      }
      lastIndex = match.index + raw.length;
    }

    if (lastIndex < text.length) {
      container.appendChild(document.createTextNode(text.slice(lastIndex)));
    }
  }

  function renderTreeDrilldown(content, data) {
    const iconPath = data.icon_path || '';
    const sections = Array.isArray(data.sections) && data.sections.length
      ? data.sections
      : [{ title: data.title || '', subtitle: data.subtitle || '', rows: data.rows || [] }];

    const filterWrap = document.createElement('div');
    filterWrap.style.cssText = 'margin-bottom:10px;display:flex;flex-direction:column;gap:6px;';

    const filterLabel = document.createElement('label');
    filterLabel.style.cssText = 'font-size:0.78rem;color:var(--text-muted);font-weight:600;';
    filterLabel.textContent = 'Filter';
    filterWrap.appendChild(filterLabel);

    const filterInput = document.createElement('input');
    filterInput.type = 'search';
    filterInput.placeholder = '🔎 Filter methods…';
    filterInput.style.cssText = 'width:100%;box-sizing:border-box;padding:6px 10px;background:#1e293b;border:1px solid #334155;border-radius:6px;color:#e2e8f0;font-size:0.85rem;outline:none;';
    filterWrap.appendChild(filterInput);
    content.appendChild(filterWrap);

    const summary = document.createElement('div');
    summary.style.cssText = 'font-size:0.76rem;color:var(--text-muted);margin-bottom:10px;';
    content.appendChild(summary);

    const host = document.createElement('div');
    host.className = 'tree-drilldown-host';
    content.appendChild(host);

    const state = { filter: '', expanded: new Set() };

    const render = () => {
      host.innerHTML = '';
      const q = state.filter.trim().toLowerCase();
      let totalApiRows = 0;
      let totalMethodRows = 0;
      let visibleApiRows = 0;
      let visibleMethodRows = 0;

      sections.forEach((section) => {
        const sectionRows = Array.isArray(section.rows) ? section.rows : [];
        if (!sectionRows.length) return;

        const rowMap = new Map();
        const childrenMap = new Map();
        sectionRows.forEach((row) => {
          const rowId = String(row.id || '').trim();
          if (rowId) rowMap.set(rowId, row);
        });
        sectionRows.forEach((row) => {
          const parentId = String(row.parent_id || '').trim();
          if (!parentId || !rowMap.has(parentId)) return;
          if (!childrenMap.has(parentId)) childrenMap.set(parentId, []);
          childrenMap.get(parentId).push(row);
        });

        const topRows = sectionRows.filter((row) => {
          const parentId = String(row.parent_id || '').trim();
          return !parentId || !rowMap.has(parentId);
        });

        const subtreeMatchCache = new Map();
        const rowMatches = (row) => drilldownRowSearchText(row).includes(q);
        const subtreeMatches = (row) => {
          const rowId = String(row.id || '').trim();
          if (!q) return true;
          if (subtreeMatchCache.has(rowId)) return subtreeMatchCache.get(rowId);
          let matched = rowMatches(row);
          for (const child of childrenMap.get(rowId) || []) {
            if (subtreeMatches(child)) {
              matched = true;
              break;
            }
          }
          subtreeMatchCache.set(rowId, matched);
          return matched;
        };

        if (q && !topRows.some((row) => subtreeMatches(row))) return;

        totalApiRows += topRows.length;
        totalMethodRows += sectionRows.filter((row) => String(row.parent_id || '').trim()).length;

        const sectionWrap = document.createElement('div');
        sectionWrap.className = 'tree-drilldown-section';

        const sectionHeader = document.createElement('div');
        sectionHeader.className = 'tree-drilldown-section-header';
        const sectionTitle = document.createElement('div');
        sectionTitle.className = 'tree-drilldown-section-title';
        sectionTitle.textContent = section.title || data.title || 'APIM';
        sectionHeader.appendChild(sectionTitle);
        if (section.subtitle) {
          const sectionSubtitle = document.createElement('div');
          sectionSubtitle.className = 'tree-drilldown-section-subtitle';
          sectionSubtitle.textContent = section.subtitle;
          sectionHeader.appendChild(sectionSubtitle);
        }
        sectionWrap.appendChild(sectionHeader);

        const table = document.createElement('table');
        table.className = 'tree-drilldown-table';
        const thead = document.createElement('thead');
        const hRow = document.createElement('tr');
        (data.columns || []).forEach((col) => {
          const th = document.createElement('th');
          th.textContent = col;
          hRow.appendChild(th);
        });
        thead.appendChild(hRow);
        table.appendChild(thead);

        const tbody = document.createElement('tbody');
        table.appendChild(tbody);
        sectionWrap.appendChild(table);
        host.appendChild(sectionWrap);

        const renderRow = (row, depth, ancestorOpen, ancestorForcedVisible) => {
          const rowId = String(row.id || '').trim();
          if (!rowId) return;

          const childRows = childrenMap.get(rowId) || [];
          const hasChildren = childRows.length > 0;
          const selfMatch = q ? rowMatches(row) : false;
          const visible = q ? (ancestorForcedVisible || subtreeMatches(row)) : (depth === 0 || ancestorOpen);

          const tr = document.createElement('tr');
          tr.className = `tree-drilldown-row${row.parent_id ? ' child-row' : ' parent-row'}`;
          tr.dataset.rowId = rowId;
          if (row.parent_id) tr.dataset.parentRowId = String(row.parent_id || '').trim();
          tr.dataset.childrenCount = String(row.child_count != null ? row.child_count : childRows.length);
          tr.dataset.search = drilldownRowSearchText(row);
          if (!visible) tr.style.display = 'none';

          (row.cells || []).forEach((cell, cellIndex) => {
            const td = document.createElement('td');
            td.style.cssText = 'padding:7px 12px;color:var(--text-primary,#e5e7eb);vertical-align:middle;';

            if (cellIndex === 0) {
              const wrap = document.createElement('div');
              wrap.style.cssText = 'display:flex;align-items:center;gap:6px;min-width:0;';

              if (depth > 0) {
                const indent = document.createElement('span');
                indent.style.cssText = `display:inline-block;width:${depth * 16}px;flex:0 0 auto;`;
                wrap.appendChild(indent);
              }

              if (hasChildren) {
                const toggle = document.createElement('button');
                toggle.type = 'button';
                toggle.className = `expand-toggle${state.expanded.has(rowId) ? ' open' : ''}`;
                toggle.textContent = '▶';
                toggle.title = state.expanded.has(rowId) ? 'Collapse methods' : 'Expand methods';
                toggle.addEventListener('click', (evt) => {
                  evt.stopPropagation();
                  if (state.expanded.has(rowId)) state.expanded.delete(rowId);
                  else state.expanded.add(rowId);
                  render();
                });
                wrap.appendChild(toggle);
              } else {
                const spacer = document.createElement('span');
                spacer.style.cssText = 'display:inline-block;width:16px;flex:0 0 auto;';
                wrap.appendChild(spacer);
              }

              if (iconPath) {
                const icon = document.createElement('img');
                icon.src = iconPath;
                icon.alt = '';
                icon.width = 14;
                icon.height = 14;
                icon.loading = 'lazy';
                icon.decoding = 'async';
                icon.style.cssText = 'flex:0 0 auto;object-fit:contain;';
                wrap.appendChild(icon);
              }

              const labelWrap = document.createElement('span');
              labelWrap.style.cssText = 'display:inline-flex;align-items:center;gap:6px;min-width:0;flex-wrap:wrap;';
              const valueSpan = document.createElement('span');
              populateDrilldownCell(valueSpan, cell);
              labelWrap.appendChild(valueSpan);
              if (hasChildren) {
                const badge = document.createElement('span');
                badge.className = 'child-count-badge';
                const count = Number(row.child_count != null ? row.child_count : childRows.length) || 0;
                badge.textContent = String(count);
                badge.title = `${count} method${count === 1 ? '' : 's'}`;
                labelWrap.appendChild(badge);
              }
              wrap.appendChild(labelWrap);
              td.appendChild(wrap);
            } else {
              const valueSpan = document.createElement('span');
              populateDrilldownCell(valueSpan, cell);
              td.appendChild(valueSpan);
            }

            tr.appendChild(td);
          });

          tbody.appendChild(tr);

          const nextAncestorOpen = q ? false : (ancestorOpen && state.expanded.has(rowId));
          const nextAncestorForcedVisible = q ? (ancestorForcedVisible || selfMatch) : false;
          childRows.forEach((childRow) => {
            renderRow(childRow, depth + 1, nextAncestorOpen, nextAncestorForcedVisible);
          });

          if (visible) {
            if (row.parent_id) visibleMethodRows += 1;
            else visibleApiRows += 1;
          }
        };

        topRows.forEach((row) => renderRow(row, 0, true, false));
      });

      if (!host.children.length) {
        const empty = document.createElement('div');
        empty.className = 'tree-drilldown-empty';
        empty.textContent = q
          ? `No APIM APIs or methods match "${state.filter.trim()}".`
          : (data.empty_message || 'No data available.');
        host.appendChild(empty);
      }

      if (q) {
        summary.textContent = `Showing APIM APIs and methods matching "${state.filter.trim()}".`;
      } else {
        summary.textContent = `Showing ${visibleApiRows || totalApiRows} API${(visibleApiRows || totalApiRows) === 1 ? '' : 's'} with ${visibleMethodRows || totalMethodRows} method${(visibleMethodRows || totalMethodRows) === 1 ? '' : 's'}.`;
      }
    };

    filterInput.addEventListener('input', () => {
      state.filter = filterInput.value;
      render();
    });

    render();
  }

  /**
   * After a Mermaid SVG renders, attach click handlers to drillable nodes.
   * Mermaid v11 flowchart nodes render as:  <g class="node" id="flowchart-{nodeId}-{seq}">
   * We recover nodeId by stripping prefix/suffix.
   *
   * Uses SVG-level event delegation so that clicks on HTML content inside
   * <foreignObject> (used for icon+label nodes) are correctly captured across
   * all browsers — foreignObject content can swallow per-element handlers.
   */
  function _attachDrilldownHandlers(svgEl, nodeMap, subId) {
    if (!svgEl || !nodeMap) return;

    // ── Visual affordances ──
    const nodeEls = svgEl.querySelectorAll('g.node[id]');
    nodeEls.forEach(el => {
      const rawId = el.getAttribute('id') || '';
      // Mermaid v10 IDs: "flowchart-{nodeId}-{seq}"
      // Mermaid v11 IDs: "mermaid-{timestamp}-flowchart-{nodeId}-{seq}"
      const nodeId = rawId
        .replace(/^mermaid-\d+-/, '')
        .replace(/^flowchart-/, '')
        .replace(/-\d+$/, '');
      const entry = nodeMap[nodeId];
      if (!entry || !entry.resources || !entry.resources.length) return;

      el.classList.add('node-drillable');
      el.setAttribute('title', `Click to explore ${entry.title}`);
      el.style.cursor = 'pointer';

      // Inject ⊕ badge near top-right of node bounding box
      const rect = el.querySelector('rect, polygon');
      if (rect) {
        try {
          const bbox = rect.getBBox();
          const badge = document.createElementNS('http://www.w3.org/2000/svg', 'text');
          badge.setAttribute('x', String(bbox.x + bbox.width - 4));
          badge.setAttribute('y', String(bbox.y + 10));
          badge.setAttribute('font-size', '10');
          badge.setAttribute('fill', '#60a5fa');
          badge.setAttribute('text-anchor', 'end');
          badge.setAttribute('pointer-events', 'none');
          badge.textContent = '⤵';
          el.appendChild(badge);
        } catch(_) { /* getBBox may fail in some browsers */ }
      }

      // Keyboard accessibility
      el.setAttribute('tabindex', '0');
      el.addEventListener('keydown', (evt) => {
        if (evt.key === 'Enter') openDrilldown(entry, subId);
      });
    });

    // ── Single delegated click handler on the SVG ──
    // This fires even when the user clicks HTML content inside a
    // <foreignObject> because the event reaches the SVG element via bubbling
    // regardless of which DOM context (HTML vs SVG) the target lives in.
    svgEl.addEventListener('click', (evt) => {
      // Walk up from evt.target to find the nearest SVG g.node with an id.
      // When the user clicks inside an HTML <foreignObject> label, evt.target is
      // an HTML element whose closest() searches the HTML sub-tree and can cross
      // back into the SVG parent chain.  We use composedPath() first for
      // reliability, then fall back to the manual parent walk.
      let nodeEl = null;

      // 1. composedPath() traverses the full event path including across
      //    shadow/namespace boundaries — most reliable.
      if (evt.composedPath) {
        for (const el of evt.composedPath()) {
          if (el === svgEl) break;
          if (el.tagName === 'g' && el.classList &&
              el.classList.contains('node') && el.id) {
            nodeEl = el;
            break;
          }
        }
      }

      // 2. closest() on the target as a fallback (works for plain SVG elements
      //    and for foreignObject content in modern browsers).
      if (!nodeEl && evt.target && evt.target.closest) {
        const candidate = evt.target.closest('g.node[id]');
        if (candidate && svgEl.contains(candidate)) nodeEl = candidate;
      }

      // 3. Manual parent-walk as a last resort.
      if (!nodeEl) {
        let el = evt.target;
        while (el && el !== svgEl) {
          if (el.tagName === 'g' && el.classList &&
              el.classList.contains('node') && el.id) {
            nodeEl = el;
            break;
          }
          el = el.parentElement || el.parentNode;
        }
      }

      if (!nodeEl) return;

      const rawId = nodeEl.getAttribute('id') || '';
      const nodeId = rawId
        .replace(/^mermaid-\d+-/, '')
        .replace(/^flowchart-/, '')
        .replace(/-\d+$/, '');
      const entry = nodeMap[nodeId];
      if (!entry || !entry.resources || !entry.resources.length) return;

      evt.stopPropagation();
      evt.preventDefault();
      openDrilldown(entry, subId);
    });
  }

  // ── Drill-down panel ──────────────────────────────────────────────────────
  function _renderBreadcrumb() {
    const bc = document.getElementById('drilldown-breadcrumb');
    if (!bc) return;
    bc.innerHTML = _breadcrumb
      .map((entry, i) => {
        const isLast = i === _breadcrumb.length - 1;
        if (isLast) return `<strong style="color:var(--text-primary,#e5e7eb);">${entry.title}</strong>`;
        return `<span style="color:#60a5fa; cursor:pointer; text-decoration:underline;"
                      data-bc-index="${i}">${entry.title}</span> ›`;
      })
      .join(' ');
    // Clicking a breadcrumb item navigates back to that level
    bc.querySelectorAll('[data-bc-index]').forEach(span => {
      span.addEventListener('click', () => {
        const idx = parseInt(span.getAttribute('data-bc-index'), 10);
        _breadcrumb.splice(idx + 1);
        const entry = _breadcrumb[idx];
        _loadDrilldownContent(entry, _currentSubId);
        _renderBreadcrumb();
      });
    });
  }

  async function openDrilldown(entry, subId) {
    _breadcrumb.push(entry);
    _renderBreadcrumb();
    _loadDrilldownContent(entry, subId);
  }

  async function _loadDrilldownContent(entry, subId) {
    const modal   = document.getElementById('drilldown-modal');
    const loading = document.getElementById('drilldown-loading');
    const content = document.getElementById('drilldown-content');
    const modalContent = modal?.querySelector('.modal-content');
    const isAppGateway = String(entry?.arm_type || '').toLowerCase().includes('applicationgateway');
    const isAksCluster = String(entry?.arm_type || '').toLowerCase().includes('managedcluster');

    if (modalContent) {
      if (isAppGateway || isAksCluster) {
        modalContent.style.maxWidth = 'min(99vw, 1800px)';
        modalContent.style.width = '99vw';
      } else {
        modalContent.style.maxWidth = 'min(96vw, 1400px)';
        modalContent.style.width = '96vw';
      }
    }

    modal.style.display   = 'flex';
    loading.style.display = 'block';
    content.style.display = 'none';
    content.innerHTML     = '';

    try {
      const resp = await fetch(`/api/subscriptions/${encodeURIComponent(subId)}/drilldown`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ arm_type: entry.arm_type, resources: entry.resources }),
      });
      const data = await resp.json();

      if (data.error) {
        loading.textContent = `⚠️ ${data.error}`;
        loading.style.display = 'flex';
        return;
      }

      loading.style.display = 'none';
      content.style.display = 'block';

      // ── Title ──────────────────────────────────────────────────────────────
      if (data.title) {
        const h = document.createElement('h4');
        h.textContent = data.title;
        h.style.cssText = 'margin:0 0 12px;color:var(--text-primary,#e5e7eb);font-size:1rem;';
        content.appendChild(h);
      }
      const resourceType = String(data.resource_type || entry?.arm_type || '').trim();
      if (resourceType) {
        const meta = document.createElement('div');
        meta.style.cssText = 'margin:-8px 0 12px;color:var(--text-muted,#94a3b8);font-size:0.8rem;';
        meta.appendChild(document.createTextNode('Resource type: '));
        const code = document.createElement('code');
        code.style.cssText = 'color:var(--text-primary,#e5e7eb);';
        code.textContent = resourceType;
        meta.appendChild(code);
        content.appendChild(meta);
      }

      // App Gateway drilldown: always offer manual routing refresh.
      if (isAppGateway && subId) {
        const actions = document.createElement('div');
        actions.style.cssText = 'display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:0 0 10px;';

        const btn = document.createElement('button');
        btn.textContent = '🔄 Refresh routing data';
        btn.style.cssText = 'padding:6px 14px;background:#1e40af;color:#e2e8f0;border:none;border-radius:6px;cursor:pointer;font-size:0.82rem;';

        const statusEl = document.createElement('p');
        statusEl.style.cssText = 'color:var(--text-muted);font-size:0.8rem;margin:0;';

        actions.appendChild(btn);
        actions.appendChild(statusEl);
        content.appendChild(actions);

        btn.addEventListener('click', async () => {
          btn.disabled = true;
          btn.textContent = '⏳ Harvesting…';
          statusEl.textContent = 'Running routing harvest — this may take 1–3 minutes…';
          try {
            const startResp = await fetch(`/api/subscriptions/${encodeURIComponent(subId)}/harvest-routing`, { method: 'POST' });
            const startData = await startResp.json();
            const taskId = startData.task_id;
            if (!taskId) throw new Error(startData.error || 'No task ID returned');

            // Poll for completion
            const pollInterval = setInterval(async () => {
              try {
                const pollResp = await fetch(`/api/subscriptions/${encodeURIComponent(subId)}/harvest-routing/${taskId}`);
                const pollData = await pollResp.json();
                if (pollData.status === 'done') {
                  clearInterval(pollInterval);
                  statusEl.textContent = '✅ Done — reloading…';
                  setTimeout(() => _loadDrilldownContent(entry, subId), 800);
                } else if (pollData.status === 'error') {
                  clearInterval(pollInterval);
                  statusEl.textContent = `❌ Harvest failed: ${(pollData.output || '').split('\n').pop()}`;
                  btn.disabled = false;
                  btn.textContent = '🔄 Retry';
                }
              } catch (_) { /* keep polling */ }
            }, 3000);
          } catch(e) {
            statusEl.textContent = `❌ ${e.message}`;
            btn.disabled = false;
            btn.textContent = '🔄 Retry';
          }
        });
      }

      if (data.view_type === 'tree_table') {
        const sections = Array.isArray(data.sections) ? data.sections : [];
        const hasTreeRows = sections.some(section => Array.isArray(section.rows) && section.rows.length > 0);
        if (!hasTreeRows) {
          const msg = document.createElement('p');
          msg.style.cssText = 'color:var(--text-muted);font-size:0.875rem;padding:8px 0;';
          msg.textContent = data.empty_message || 'No data available.';
          content.appendChild(msg);
          return;
        }
        renderTreeDrilldown(content, data);
        return;
      }

      // ── Empty state ────────────────────────────────────────────────────────
      if (!data.rows || data.rows.length === 0) {
        const msg = document.createElement('p');
        msg.style.cssText = 'color:var(--text-muted);font-size:0.875rem;padding:8px 0;';
        msg.textContent = data.empty_message || 'No data available.';
        content.appendChild(msg);
        return;
      }

        // ── Filter bar ────────────────────────────────────────────────────────
        const filterWrap = document.createElement('div');
        filterWrap.style.cssText = 'margin-bottom:10px;';
      const filterInput = document.createElement('input');
      filterInput.type        = 'text';
      filterInput.placeholder = '🔎 Filter…';
      filterInput.style.cssText = 'width:100%;box-sizing:border-box;padding:6px 10px;background:#1e293b;border:1px solid #334155;border-radius:6px;color:#e2e8f0;font-size:0.85rem;outline:none;';
      filterWrap.appendChild(filterInput);
      content.appendChild(filterWrap);

      // ── Table ──────────────────────────────────────────────────────────────
      const tableWrap = document.createElement('div');
      tableWrap.style.cssText = 'overflow:auto;flex:1;border-radius:6px;border:1px solid #1e293b;';

      const table = document.createElement('table');
      table.style.cssText = 'width:100%;border-collapse:collapse;font-size:0.82rem;';
      const iconPath = data.icon_path || '';

      // Header
      const thead = document.createElement('thead');
      const hRow  = document.createElement('tr');
      (data.columns || []).forEach(col => {
        const th = document.createElement('th');
        th.textContent = col;
        th.style.cssText = 'padding:8px 12px;text-align:left;background:#0f172a;color:#94a3b8;font-weight:600;font-size:0.78rem;text-transform:uppercase;letter-spacing:.04em;border-bottom:1px solid #1e293b;white-space:nowrap;';
        hRow.appendChild(th);
      });
      thead.appendChild(hRow);
      table.appendChild(thead);

      // Body
      const tbody = document.createElement('tbody');
      const allRows = [];

      data.rows.forEach((row, ri) => {
        const tr = document.createElement('tr');
        tr.style.cssText = `border-bottom:1px solid #1e293b;background:${ri % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.02)'};transition:background .15s;`;
        tr.addEventListener('mouseenter', () => { tr.style.background = 'rgba(96,165,250,0.07)'; });
        tr.addEventListener('mouseleave', () => { tr.style.background = ri % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.02)'; });

        row.forEach((cell, ci) => {
          const td = document.createElement('td');
          td.style.cssText = 'padding:7px 12px;color:var(--text-primary,#e5e7eb);vertical-align:middle;';
          const val = cell == null ? '—' : String(cell);
          if (ci === 0 && iconPath) {
            const wrap = document.createElement('span');
            wrap.style.cssText = 'display:inline-flex;align-items:center;gap:6px;min-width:0;';
            const icon = document.createElement('img');
            icon.src = iconPath;
            icon.alt = '';
            icon.width = 14;
            icon.height = 14;
            icon.loading = 'lazy';
            icon.decoding = 'async';
            icon.style.cssText = 'flex:0 0 auto;object-fit:contain;';
            wrap.appendChild(icon);
            if (cell && typeof cell === 'object' && cell.label) {
              const badge = document.createElement('span');
              badge.style.cssText = cell.style || '';
              badge.textContent = cell.label;
              wrap.appendChild(badge);
            } else {
              const text = document.createElement('span');
              if (val === '—') {
                text.textContent = val;
                text.style.color = '#4b5563';
              } else {
                appendDrilldownText(text, val);
              }
              wrap.appendChild(text);
            }
            td.appendChild(wrap);
          } else if (cell && typeof cell === 'object' && cell.href && cell.label) {
              const link = document.createElement('a');
              link.href = String(cell.href);
              link.textContent = String(cell.label);
              link.target = '_blank';
              link.rel = 'noopener noreferrer';
              if (cell.title) link.title = String(cell.title);
              link.style.cssText = 'color:#60a5fa;text-decoration:underline;word-break:break-all;';
              td.appendChild(link);
          } else if (cell && typeof cell === 'object' && cell.label) {
              // Exposure badge cell
              td.innerHTML = `<span style="${cell.style || ''}">${cell.label}</span>`;
          } else {
            if (val === '—') {
              td.textContent = val;
              td.style.color = '#4b5563';
            } else {
              appendDrilldownText(td, val);
            }
          }
          tr.appendChild(td);
        });

        tbody.appendChild(tr);
        allRows.push({ tr, searchText: row.map(c => (c && typeof c === 'object' ? c.label : c) || '').join(' ').toLowerCase() });
      });

      table.appendChild(tbody);
      tableWrap.appendChild(table);
      content.appendChild(tableWrap);

      // Wire filter
      filterInput.addEventListener('input', () => {
        const q = filterInput.value.toLowerCase();
        allRows.forEach(({ tr, searchText }) => {
          tr.style.display = searchText.includes(q) ? '' : 'none';
        });
      });

    } catch(e) {
      loading.style.display = 'flex';
      loading.textContent = `Failed: ${e.message}`;
    }
  }

  document.getElementById('drilldown-close').addEventListener('click', () => {
    _breadcrumb.length = 0;
    document.getElementById('drilldown-modal').style.display = 'none';
  });

  // ── Load subscriptions list ───────────────────────────────────────────────
  async function loadSubscriptions() {
    try {
      const resp = await fetch('/api/subscriptions');
      const data = await resp.json();
      const subs = data.subscriptions || [];

      document.getElementById('subscriptions-loading').style.display = 'none';

      if (!subs.length) {
        document.getElementById('subscriptions-empty').style.display = 'block';
        return;
      }

      const tbody = document.getElementById('subscriptions-tbody');
      tbody.innerHTML = subs.map(s => `
       <tr>
         <td class="subscription-name-cell">
           <a href="${architectureUrl(s.id)}" target="_blank" rel="noopener noreferrer" style="color:inherit;text-decoration:none;display:inline-block;">
             <strong style="color:#60a5fa; text-decoration:underline;">${escapeHtml(s.display_name || s.id)}</strong><br/>
             <small style="color:var(--text-muted);font-size:0.75rem;">${escapeHtml(s.id)}</small>
           </a>
         </td>
         <td><span style="display:inline-block;padding:2px 8px;border-radius:6px;font-size:0.75rem;font-weight:600;background:#0066cc;color:#fff;">${s.provider}</span></td>
         <td>${renderBadge(s.environment, s.env_badge)}</td>
         <td>${s.asset_count}</td>
         <td>${s.public_count > 0 ? `<span style="color:#f59e0b;font-weight:600;">⚠ ${s.public_count}</span>` : '0'}</td>
         <td>${s.state || '—'}</td>
         <td style="font-size:0.8rem;">${formatDate(s.last_synced)}</td>
         <td style="text-align:center;">
           <a href="/cloud/assets?sub=${encodeURIComponent(s.id)}" class="btn btn-sm" style="padding:4px 12px; font-size:0.75rem; text-decoration:none;">📦 Show Assets</a>
           <button type="button" class="btn btn-sm subscription-preview-btn" data-sub-id="${escapeHtml(s.id)}" data-sub-name="${escapeHtml(s.display_name || s.id)}" style="padding:4px 12px; font-size:0.75rem; margin-left:6px;">👁️ Preview</button>
           <a href="${architectureUrl(s.id)}" target="_blank" rel="noopener noreferrer" class="btn btn-sm" style="padding:4px 12px; font-size:0.75rem; text-decoration:none; margin-left:6px;">🧭 React Flow</a>
         </td>
       </tr>
      `).join('');

      document.getElementById('subscriptions-list').style.display = 'block';
      tbody.querySelectorAll('.subscription-preview-btn').forEach(button => {
        button.addEventListener('click', () => loadDiagram(button.dataset.subId, button.dataset.subName));
      });
    } catch(e) {
      document.getElementById('subscriptions-loading').textContent = 'Failed to load subscriptions.';
    }
  }

  function normalizeLegendItem(item) {
    return String(item || '')
      .replace(/\bApp Gating\b/g, 'App Gateway')
      .replace(/\bHTTP Listener\b/g, 'HTTP listener')
      .replace(/\bHTTPS Listener\b/g, 'HTTPS listener')
      .trim();
  }

  function normalizeMermaidText(mermaid) {
    if (!mermaid) return mermaid;
    return String(mermaid)
      .replace(/\bApp Gating\b/g, 'App Gateway')
      .replace(/\bHttp listener\b/g, 'HTTP listener')
      .replace(/\bHttps listener\b/g, 'HTTPS listener');
  }

  function withNormalizedView(view, fallback = {}) {
    const src = view || fallback || {};
    return {
      ...src,
      mermaid: normalizeMermaidText(src.mermaid || fallback.mermaid || ''),
      legend: (src.legend || fallback.legend || []).map(normalizeLegendItem).filter(Boolean),
    };
  }

  function buildOverviewView(diagram) {
    if (!diagram) return null;
    const views = diagram.views || {};
    const connectivity = views.connectivity || null;
    const base = connectivity || diagram;
    if (!base) return null;

    return withNormalizedView({
      ...base,
      node_drilldown_map: base.node_drilldown_map || connectivity?.node_drilldown_map || diagram.node_drilldown_map,
    }, diagram);
  }

  function getDiagramView(diagram, mode) {
    if (!diagram) return null;
    if (mode === 'overview') return buildOverviewView(diagram);
    const specific = (diagram.views && diagram.views[mode]) || null;
    if (specific) return withNormalizedView(specific, diagram);
    return mode === 'attack_paths' ? buildOverviewView(diagram) : withNormalizedView(diagram);
  }

  function getDiagramModes(diagram) {
    const views = diagram?.views || {};
    const modes = [];
    if (views.connectivity || diagram?.mermaid) modes.push('overview');
    if (views.attack_paths || (Array.isArray(diagram?.attack_paths) && diagram.attack_paths.length > 0)) {
      modes.push('attack_paths');
    }
    if (!modes.length) modes.push('overview');
    return modes;
  }

  function buildModeButtons(diagram, currentMode, onSelect) {
    const modes = getDiagramModes(diagram);
    const labels = {
      overview: 'Overview',
      attack_paths: 'Attack paths',
    };

    const wrap = document.createElement('div');
    wrap.style.cssText = 'display:flex;align-items:center;gap:6px;flex-wrap:wrap;';

    modes.forEach(mode => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = `diagram-mode-btn${mode === currentMode ? ' active' : ''}`;
      btn.textContent = labels[mode] || mode.replace(/_/g, ' ');
      btn.addEventListener('click', () => onSelect(mode));
      wrap.appendChild(btn);
    });

    return wrap;
  }

  function createCardShell() {
    const card = document.createElement('div');
      card.style.cssText = 'padding:6px;background:var(--bg-code,#1a1a1a);border-radius:8px;border:1px solid var(--border-color,#333);margin-bottom:4px;';
    return card;
  }

  function renderSummaryList(items, ordered = false) {
    if (!items || !items.length) return '';
    const tag = ordered ? 'ol' : 'ul';
    return `<${tag} style="margin:8px 0 0 18px;padding:0;display:grid;gap:6px;">${items.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</${tag}>`;
  }

  function collectDiagramLegend(diagram, view) {
    const candidates = [
      ...(view?.legend || []),
      ...(diagram?.legend || []),
      ...(diagram?.views?.overview?.legend || []),
      ...(diagram?.views?.connectivity?.legend || []),
      ...(diagram?.views?.attack_paths?.legend || []),
    ];
    const legend = candidates.map(normalizeLegendItem).filter(Boolean);
    if (legend.length) return Array.from(new Set(legend));
    return [
      'Orange edges: WAF-protected entry point (Prevention mode)',
      'Amber edges: WAF in Detection mode or IP-allowlisted access',
      'Red edges: directly public — no WAF or network restriction',
      'White edges: internal application or data flow',
    ];
  }

  function renderDiagramFooter(diagram, view) {
    const footer = document.getElementById('subscription-diagram-footer');
    if (!footer) return;

    const legend = collectDiagramLegend(diagram, view);
    if (!legend.length) {
      footer.style.display = 'none';
      footer.innerHTML = '';
      return;
    }

    footer.innerHTML = `
      <div><span class="footer-label">Arrow key</span></div>
      <div class="footer-items">
        ${legend.map((item) => `<span class="footer-item">${escapeHtml(item)}</span>`).join('')}
      </div>
    `;
    footer.style.display = 'block';
  }

  function parseAttackPathTarget(pathItem) {
    const rawPath = String(pathItem?.path || '').trim();
    if (!rawPath) return '';
    const parts = rawPath.split('->').map(part => part.trim()).filter(Boolean);
    return parts.length ? parts[parts.length - 1] : rawPath;
  }

  function normalizeAttackPathTargetKey(value) {
    return String(value || '').trim().toLowerCase();
  }

  function buildAttackPathMermaid(paths, attackTarget) {
    const lines = ['graph LR'];
    const nodeIds = new Map();
    const linkStyles = [];
    const targetKey = normalizeAttackPathTargetKey(attackTarget);
    let linkIndex = 0;

    const safeLabel = (value) => String(value || '').trim().replace(/"/g, '&quot;');
    const nodeIdFor = (label) => {
      const key = normalizeAttackPathTargetKey(label);
      if (nodeIds.has(key)) return nodeIds.get(key);
      const nodeId = `ap_${nodeIds.size}_${String(label || 'node').replace(/[^A-Za-z0-9_]/g, '_')}`;
      nodeIds.set(key, nodeId);
      const className = key === 'internet'
        ? 'internet'
        : (targetKey && key === targetKey ? 'attackTarget' : 'attackHop');
      lines.push(`    ${nodeId}["${safeLabel(label)}"]`);
      lines.push(`    class ${nodeId} ${className};`);
      return nodeId;
    };

    nodeIdFor('Internet');

    (paths || []).forEach((pathItem) => {
      const rawPath = String(pathItem?.path || '').trim();
      if (!rawPath) return;
      const segments = rawPath
        .split(/\s*(?:->|→)\s*/)
        .map(part => part.trim())
        .filter(Boolean);
      if (segments.length < 2) return;
      const firstNode = nodeIdFor(segments[0]);
      for (let i = 1; i < segments.length; i++) {
        const src = nodeIdFor(segments[i - 1]);
        const dst = nodeIdFor(segments[i]);
        lines.push(`    ${src} --> ${dst}`);
        linkStyles.push(i === segments.length - 1 ? 'stroke:#f59e0b,stroke-width:3px' : 'stroke:#fb923c,stroke-width:2px');
        linkIndex += 1;
      }
    });

    lines.push('');
    linkStyles.forEach((style, idx) => {
      lines.push(`    linkStyle ${idx} ${style}`);
    });
    lines.push('    classDef internet stroke:#d32f2f,stroke-width:2px,fill:#3b0a0a;');
    lines.push('    classDef attackHop stroke:#ea580c,stroke-width:2px,fill:#2b1707;');
    lines.push('    classDef attackTarget stroke:#8b5cf6,stroke-width:3px,fill:#2e1065;');
    return lines.join('\n');
  }

  async function renderDiagramSection(host, { subId, diagram, mode, heading, containerId, emptyText, minHeight = '360px', attackTarget = '', onAttackTargetChange = null }) {
    host.innerHTML = '';
    const view = getDiagramView(diagram, mode);
    if (!view || !view.mermaid) {
      const empty = createCardShell();
      empty.innerHTML = `<div style="color:var(--text-muted);">${escapeHtml(emptyText || 'No diagram available.')}</div>`;
      host.appendChild(empty);
      return;
    }

    const section = createCardShell();

    const titleRow = document.createElement('div');
    titleRow.style.cssText = 'display:flex;align-items:center;gap:8px;margin-bottom:4px;flex-wrap:wrap;';
    const titleEl = document.createElement('h4');
    titleEl.textContent = heading;
    titleEl.style.margin = '0';
    titleRow.appendChild(titleEl);
    titleRow.appendChild(buildControlsBar(containerId));
    section.appendChild(titleRow);

    if (view.description) {
      const desc = document.createElement('div');
      desc.style.cssText = 'font-size:0.82rem;color:var(--text-muted);margin-bottom:8px;line-height:1.45;';
      desc.textContent = view.description;
      section.appendChild(desc);
    }

    if (mode === 'attack_paths') {
      const attackPaths = (view.attack_paths || diagram.attack_paths || []);
      const targets = Array.from(
        new Set(
          attackPaths
            .map(parseAttackPathTarget)
            .map(target => target.trim())
            .filter(Boolean)
        )
      ).sort((a, b) => a.localeCompare(b));

      const filterWrap = document.createElement('div');
      filterWrap.style.cssText = 'display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:0 0 8px;';
      const filterLabel = document.createElement('label');
      filterLabel.textContent = 'Asset name';
      filterLabel.style.cssText = 'font-size:0.78rem;color:var(--text-muted);font-weight:600;';
      filterLabel.setAttribute('for', `${containerId}-target-filter`);
      filterWrap.appendChild(filterLabel);

      const filterInput = document.createElement('input');
      filterInput.type = 'search';
      filterInput.id = `${containerId}-target-filter`;
      filterInput.placeholder = 'Type asset name…';
      filterInput.value = attackTarget || '';
      filterInput.setAttribute('list', `${containerId}-target-options`);
      filterInput.style.cssText = 'min-width:260px;flex:1 1 380px;padding:5px 8px;border-radius:6px;border:1px solid var(--border-color,#444);background:var(--bg-base);color:var(--text-primary,#e5e7eb);';
      filterInput.addEventListener('input', () => {
        if (typeof onAttackTargetChange === 'function') onAttackTargetChange(filterInput.value);
      });
      filterWrap.appendChild(filterInput);

      const datalist = document.createElement('datalist');
      datalist.id = `${containerId}-target-options`;
      targets.forEach(target => {
        const option = document.createElement('option');
        option.value = target;
        datalist.appendChild(option);
      });
      filterWrap.appendChild(datalist);

      const clearBtn = document.createElement('button');
      clearBtn.type = 'button';
      clearBtn.className = 'btn btn-sm';
      clearBtn.textContent = 'Clear';
      clearBtn.style.padding = '4px 8px';
      clearBtn.addEventListener('click', () => {
        filterInput.value = '';
        if (typeof onAttackTargetChange === 'function') onAttackTargetChange('');
      });
      filterWrap.appendChild(clearBtn);

      section.appendChild(filterWrap);

      const normalizedTarget = normalizeAttackPathTargetKey(attackTarget);
      const filteredPaths = normalizedTarget
        ? attackPaths.filter(path => normalizeAttackPathTargetKey(parseAttackPathTarget(path)).includes(normalizedTarget))
        : attackPaths;

      const summary = document.createElement('div');
      summary.style.cssText = 'font-size:0.76rem;color:var(--text-muted);margin-bottom:8px;';
      summary.textContent = normalizedTarget
        ? `Showing ${filteredPaths.length} of ${attackPaths.length} attack paths for matching assets.`
        : `Showing all ${attackPaths.length} modeled attack paths.`;
      section.appendChild(summary);

      if (normalizedTarget && !filteredPaths.length) {
        const empty = document.createElement('div');
        empty.style.cssText = 'font-size:0.78rem;color:var(--text-muted);padding:6px;border:1px dashed var(--border-color,#444);border-radius:6px;margin-bottom:8px;';
        empty.textContent = 'No attack paths match that asset name.';
        section.appendChild(empty);
      }

      const focusedDiagram = buildAttackPathMermaid(filteredPaths, attackTarget);
      view.mermaid = focusedDiagram;
      view.css_code = [
        '/* Focused Attack Path Styling */',
        '.internet { stroke: #d32f2f; stroke-width: 2px; fill: #3b0a0a; }',
        '.attackHop { stroke: #ea580c; stroke-width: 2px; fill: #2b1707; }',
        '.attackTarget { stroke: #8b5cf6; stroke-width: 3px; fill: #2e1065; }',
      ].join('\n');
    }

    const viewport = document.createElement('div');
    viewport.style.cssText = `overflow:auto;min-height:${minHeight};max-height:calc(100vh - 140px);`;
    const transformTarget = document.createElement('div');
    transformTarget.id = containerId;
    const mermaidEl = document.createElement('div');
    mermaidEl.className = 'mermaid';
    mermaidEl.textContent = view.mermaid;
    transformTarget.appendChild(mermaidEl);
    viewport.appendChild(transformTarget);
    section.appendChild(viewport);
    host.appendChild(section);

    injectSubStyle(subId, `${containerId}-${mode}`, view.css_code);

    try {
      let svgEl = null;
      if (window.TriageMermaid && typeof window.TriageMermaid.render === 'function') {
        svgEl = await window.TriageMermaid.render(transformTarget, {
          onRendered: async () => {
            if (typeof window.postProcessDiagramSvgs === 'function') {
              window.postProcessDiagramSvgs(transformTarget);
            }
            if (view.icon_map && typeof MermaidIconInjector !== 'undefined') {
              const svgElements = transformTarget.querySelectorAll('svg');
              if (svgElements.length > 0 && typeof MermaidIconInjector.injectIcons === 'function') {
                await MermaidIconInjector.injectIcons(svgElements[svgElements.length - 1], view.icon_map);
              }
            }
          },
        });
      } else if (window.mermaid) {
        const renderId = `sub_${subId}_${containerId}_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
        const rendered = await window.mermaid.render(renderId, view.mermaid);
        mermaidEl.innerHTML = rendered.svg || '';
        svgEl = transformTarget.querySelector('svg');
        if (typeof window.postProcessDiagramSvgs === 'function') {
          window.postProcessDiagramSvgs(transformTarget);
        }
        if (view.icon_map && typeof MermaidIconInjector !== 'undefined') {
          const svgElements = transformTarget.querySelectorAll('svg');
          if (svgElements.length > 0 && typeof MermaidIconInjector.injectIcons === 'function') {
            await MermaidIconInjector.injectIcons(svgElements[svgElements.length - 1], view.icon_map);
          }
        }
      }

      const nodeMap = view.node_drilldown_map || diagram.node_drilldown_map || {};
      transformTarget.querySelectorAll('svg').forEach(svg => _attachDrilldownHandlers(svg, nodeMap, subId));

      // Keep 1:1 scale by default so users can zoom/pan naturally.
      transformTarget.style.transform = '';
      transformTarget.style.transformOrigin = '';
      viewport.scrollLeft = 0;
      viewport.scrollTop = 0;
      return svgEl;
    } catch (e) {
      const err = document.createElement('div');
      err.style.cssText = 'color:#fca5a5;font-size:0.82rem;margin-top:8px;';
      err.textContent = `Render error: ${e.message}`;
      section.appendChild(err);
    }
  }

  // ── Load ingress diagram ──────────────────────────────────────────────────
  async function loadDiagram(subId, subName) {
    // Cancel any in-flight request from a previous click
    if (_loadDiagramController) {
      _loadDiagramController.abort();
    }
    _loadDiagramController = new AbortController();
    const signal = _loadDiagramController.signal;

    _currentSubId = subId;
    _currentNodeMap = null;
    _breadcrumb.length = 0;
    document.getElementById('drilldown-modal').style.display = 'none';

    // Remove style tags injected by a previous subscription load
    document.querySelectorAll('style[data-sub-style]').forEach(el => el.remove());

    const wrap = document.getElementById('subscription-diagram-wrap');
    const container = document.getElementById('subscription-diagram-container');
    const title = document.getElementById('subscription-diagram-title');
    const footer = document.getElementById('subscription-diagram-footer');
    const modeHost = document.getElementById('subscription-diagram-mode-host');

    wrap.style.display = 'flex';
    wrap.scrollTop = 0;
    wrap.scrollLeft = 0;
    try { window.scrollTo({ top: 0, behavior: 'instant' }); } catch (_) { window.scrollTo(0, 0); }
    document.body.style.overflow = 'hidden';
    container.style.display = 'block';
    container.innerHTML = '';
    title.textContent = subName || subId;
    footer.style.display = 'none';
    footer.innerHTML = '';

    try {
      const resp = await fetch(`/api/subscriptions/${encodeURIComponent(subId)}/diagram`, { signal });
      const data = await resp.json();

      if (data.error) {
        const err = document.createElement('div');
        err.style.cssText = 'color:#fca5a5;font-size:0.9rem;padding:12px;';
        err.textContent = data.error;
        container.replaceChildren(err);
        return;
      }

      const ingressDiag = data.ingress_diagram;
      const modes = getDiagramModes(ingressDiag);
      const defaultMode = (ingressDiag?.default_view === 'attack_paths') ? 'attack_paths' : 'overview';
      let currentMode = modes.includes(defaultMode) ? defaultMode : (modes[0] || 'overview');
      let attackTarget = '';

      const renderCurrentMode = async () => {
        modeHost.innerHTML = '';
        modeHost.appendChild(buildModeButtons(ingressDiag, currentMode, async (mode) => {
          if (mode === currentMode) return;
          currentMode = mode;
          await renderCurrentMode();
        }));

        const view = getDiagramView(ingressDiag, currentMode) || ingressDiag || {};
        await renderDiagramSection(container, {
          subId,
          diagram: ingressDiag,
          mode: currentMode,
          heading: '🌐 Subscription ingress',
          containerId: 'ingress-diagram-div',
          emptyText: 'No subscription ingress diagram available.',
          minHeight: 'calc(100vh - 140px)',
          attackTarget,
          onAttackTargetChange: (nextValue) => {
            attackTarget = nextValue || '';
            renderCurrentMode();
          },
        });
        renderDiagramFooter(ingressDiag, view);
      };

      await renderCurrentMode();

      if (window.initSubscriptionDiagrams) await window.initSubscriptionDiagrams();
    } catch(e) {
      if (e.name === 'AbortError') return; // Superseded by a newer click — silently discard
      const err = document.createElement('div');
      err.style.cssText = 'color:#fca5a5;font-size:0.9rem;padding:12px;';
      err.textContent = `Failed to load diagram: ${e.message}`;
      container.replaceChildren(err);
    }
  }

  function closeDiagram() {
    document.getElementById('subscription-diagram-wrap').style.display = 'none';
    document.getElementById('drilldown-modal').style.display = 'none';
    const footer = document.getElementById('subscription-diagram-footer');
    if (footer) {
      footer.style.display = 'none';
      footer.innerHTML = '';
    }
    const modeHost = document.getElementById('subscription-diagram-mode-host');
    if (modeHost) modeHost.innerHTML = '';
    _breadcrumb.length = 0;
    document.body.style.overflow = '';
  }

  document.getElementById('subscription-diagram-close').addEventListener('click', () => {
    closeDiagram();
  });

  document.getElementById('subscription-diagram-wrap').addEventListener('click', (evt) => {
    if (evt.target === document.getElementById('subscription-diagram-wrap')) {
      closeDiagram();
    }
  });

  // Escape key closes the drilldown modal
  document.addEventListener('keydown', (evt) => {
    if (evt.key === 'Escape') {
      const modal = document.getElementById('drilldown-modal');
      if (modal && modal.style.display !== 'none') {
        closeDiagram();
        return;
      }
      const subModal = document.getElementById('subscription-diagram-wrap');
      if (subModal && subModal.style.display !== 'none') {
        closeDiagram();
      }
    }
  });

  loadSubscriptions();
})();
