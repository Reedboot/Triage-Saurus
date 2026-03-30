// assets.js - initializes the assets partial when inserted into the DOM
// Usage: window.initAssets(containerElement, repoName)
(function(){
  function toLower(s){ return (s||'').toString().toLowerCase(); }

  function initAssets(container, repoName, experimentId){
    if (!container) container = document;
    repoName = repoName || (container.querySelector('#assets-meta')?.dataset?.repo) || 'global';
    experimentId = experimentId || (container.querySelector('#assets-meta')?.dataset?.experiment) || '';

    const searchInput = container.querySelector('#section-assets-search');
    const providerSelect = container.querySelector('#section-assets-provider');
    const table = container.querySelector('#section-assets-table');
    if (!table) return; // nothing to do
    const tbody = table.querySelector('tbody');
    let rows = Array.from(tbody.querySelectorAll('tr'));

    // Build data array for sorting/filtering
    let assetsData = rows.map(r => ({
      el: r,
      id: r.dataset.resourceId || r.getAttribute('data-resource-id') || '',
      parent_id: r.dataset.parentId || r.getAttribute('data-parent-id') || '',
      resource_name: toLower(r.dataset.resource_name || r.querySelector('.asset-name')?.textContent || ''),
      resource_type: toLower(r.dataset.resource_type || ''),
      render_category: toLower(r.dataset.renderCategory || r.dataset['render-category'] || r.dataset.render_category || ''),
      provider: toLower(r.dataset.provider || ''),
      provider_raw: r.dataset.provider_raw || '',
      region: toLower(r.dataset.region || ''),
      finding_count: parseInt(r.dataset.finding_count || '0', 10) || 0,
      children_count: parseInt(r.dataset.childrenCount || r.getAttribute('data-children-count') || '0', 10) || 0,
      display_on_diagram: (function(){
        const v = r.dataset.displayOnDiagram || r.dataset.display_on_diagram || r.getAttribute('data-display-on-diagram') || '';
        if (!v) return false;
        const s = v.toString().toLowerCase();
        return ['1','true','yes','on'].includes(s);
      })(),
      source_file: r.dataset.source_file || '',
      discovered_by: toLower(r.dataset.discovered_by || ''),
      search: toLower(r.dataset.search || ''),
      depth: parseInt(r.dataset.depth || '0', 10) || 0,
      ancestors: r.dataset.ancestors || '',
    }));

    // Sorting state: array of {key, dir} (dir = 1 asc, -1 desc)
    let sortStack = [];
    const storageKey = `assets_sort_${repoName}`;
    const stackEl = container.querySelector('#assets-sort-stack');

    function compare(a, b, key) {
      const va = a[key];
      const vb = b[key];
      if (key === 'finding_count') return va - vb;
      return (va || '').localeCompare(vb || '');
    }

    function saveSortStack() {
      try { localStorage.setItem(storageKey, JSON.stringify(sortStack)); } catch(e){}
    }

    function renderSortStackUI() {
      if (!stackEl) return;
      if (!sortStack.length) { stackEl.textContent = '' ; return; }
      stackEl.innerHTML = '';
      sortStack.forEach((s, idx) => {
        const th = container.querySelector(`th[data-key="${s.key}"]`);
        const label = th ? th.textContent.replace(/\s*▲|\s*▼/g, '').trim() : s.key;
        const span = document.createElement('span');
        span.style.marginLeft = '8px';
        span.textContent = `${idx+1}: ${label} ${s.dir === 1 ? '▲' : '▼'}`;
        stackEl.appendChild(span);
      });
    }

    // Attempt to load saved sort state
    try {
      const stored = localStorage.getItem(storageKey);
      if (stored) {
        const parsed = JSON.parse(stored);
        if (Array.isArray(parsed)) sortStack = parsed;
      }
    } catch (e) { /* ignore */ }

    // Apply loaded sort indicators to headers (if present)
    (function initIndicators() {
      const headersInit = table.querySelectorAll('th.sortable');
      if (!headersInit) return;
      headersInit.forEach(h => {
        const si = h.querySelector('.sort-indicator');
        const k = h.dataset.key;
        const s = sortStack.find(s => s.key === k);
        if (s) si.textContent = s.dir === 1 ? '▲' : '▼'; else si.textContent = '';
      });
      renderSortStackUI();
    })();

    function applyFilterAndSort() {
      const q = toLower(searchInput?.value || '').trim();
      const provider = toLower(providerSelect?.value || '');

      // Filter threshold: start filtering text after 2+ chars, otherwise treat as empty
      const qEffective = q.length >= 2 ? q : '';

      let filtered = assetsData.filter(a => {
        const matchQ = !qEffective || a.search.includes(qEffective);
        const matchP = !provider || a.provider === provider;
        return matchQ && matchP;
      });

      // If a child matches filters, include its ancestors so hierarchy context remains visible.
      if (filtered.length) {
        const byIdAll = new Map(assetsData.map(a => [String(a.id || ''), a]));
        const includeIds = new Set(filtered.map(a => String(a.id || '')));
        filtered.forEach(a => {
          let parentId = String(a.parent_id || '');
          let safety = 0;
          while (parentId && safety < 100) {
            if (includeIds.has(parentId)) break;
            const parent = byIdAll.get(parentId);
            if (!parent) break;
            includeIds.add(parentId);
            parentId = String(parent.parent_id || '');
            safety += 1;
          }
        });
        filtered = assetsData.filter(a => includeIds.has(String(a.id || '')));
      }

      function sortItems(items) {
        if (!sortStack.length) return items.slice();
        const out = items.slice();
        out.sort((x, y) => {
          for (const s of sortStack) {
            const cmp = compare(x, y, s.key);
            if (cmp !== 0) return cmp * s.dir;
          }
          return 0;
        });
        return out;
      }

      function orderHierarchically(items) {
        const byId = new Map(items.map(a => [String(a.id || ''), a]));
        const childrenByParent = new Map();
        for (const a of items) {
          const pid = String(a.parent_id || '');
          if (!pid || !byId.has(pid)) continue;
          if (!childrenByParent.has(pid)) childrenByParent.set(pid, []);
          childrenByParent.get(pid).push(a);
        }

        const roots = items.filter(a => {
          const pid = String(a.parent_id || '');
          return !pid || !byId.has(pid);
        });

        const ordered = [];
        const seen = new Set();

        function appendNode(node, depth) {
          const nodeId = String(node.id || '');
          if (!nodeId || seen.has(nodeId) || depth > 100) return;
          seen.add(nodeId);
          ordered.push(node);
          const children = sortItems(childrenByParent.get(nodeId) || []);
          children.forEach(child => appendNode(child, depth + 1));
        }

        sortItems(roots).forEach(root => appendNode(root, 0));
        // Safety fallback for orphan/cycle rows.
        items.forEach(a => {
          const id = String(a.id || '');
          if (id && !seen.has(id)) ordered.push(a);
        });
        return ordered;
      }

      filtered = orderHierarchically(filtered);

      // Re-render tbody
      tbody.innerHTML = '';
      const emptyEl = container.querySelector('#section-assets-empty');
      if (filtered.length === 0) {
        if (emptyEl) emptyEl.style.display = '';
      } else {
        if (emptyEl) emptyEl.style.display = 'none';

        // Compute visible child counts among filtered rows
        const visibleChildCounts = {};
        for (const a of filtered) {
          if (a.parent_id) {
            visibleChildCounts[a.parent_id] = (visibleChildCounts[a.parent_id] || 0) + 1;
          }
        }

        for (const a of filtered) {
          // Update the visible child-count badge and expand toggle based on currently visible children
          const el = a.el;
          const id = a.id || (el.getAttribute('data-resource-id') || el.dataset.resourceId);
          const badge = el.querySelector('.child-count-badge');
          const toggle = el.querySelector('.expand-toggle');
          const c = visibleChildCounts[id] || 0;
          if (badge) {
            badge.textContent = c;
            badge.title = `${c} sub-asset(s)`;
            badge.style.display = c ? '' : 'none';
          }
          if (toggle) {
            toggle.style.display = c ? '' : 'none';
            // Keep hierarchy collapsed by default after sort/filter; user can expand explicitly.
            toggle.classList.remove('open');
          }

          // Ensure child rows are hidden by default after filtering
          if (a.parent_id) {
            el.style.display = 'none';
          }

          tbody.appendChild(el);
        }
      }
    }

    // Debounce helper
    let filterTimer = null;
    function scheduleFilter() {
      if (filterTimer) clearTimeout(filterTimer);
      filterTimer = setTimeout(() => { applyFilterAndSort(); filterTimer = null; }, 250);
    }

    // Hook inputs
    if (searchInput) searchInput.addEventListener('input', scheduleFilter);
    if (providerSelect) providerSelect.addEventListener('change', () => { applyFilterAndSort(); });

    // Show hidden assets toggle: when toggled, re-fetch the assets fragment including hidden items
    const showHiddenCheckbox = container.querySelector('#assets-show-hidden');
    if (showHiddenCheckbox) {
      showHiddenCheckbox.addEventListener('change', async (ev) => {
        const checked = !!ev.target.checked;
        if (!experimentId) {
          console.warn('No experiment id available for reloading assets fragment');
          return;
        }
        try {
          const url = `/api/view/assets/${encodeURIComponent(experimentId)}/${encodeURIComponent(repoName)}?include_hidden=${checked ? '1' : '0'}`;
          const resp = await fetch(url);
          if (!resp.ok) {
            console.warn('Failed to reload assets fragment:', resp.status, resp.statusText);
            return;
          }
          const html = await resp.text();
          container.innerHTML = html;
          // Re-run init on the replaced fragment
          try { window.initAssets(container, repoName, experimentId); } catch (e) { console.warn('initAssets error after reload:', e); }
        } catch (e) {
          console.warn('assets reload error:', e);
        }
      });
    }

    // Header click handlers for sorting (Shift+click to multi-sort)
    const headers = table.querySelectorAll('th.sortable');
    headers.forEach(h => {
      h.style.cursor = 'pointer';
      h.addEventListener('click', (ev) => {
        const key = h.dataset.key;
        const shift = ev.shiftKey;
        // Find existing
        const idx = sortStack.findIndex(s => s.key === key);
        if (!shift) {
          // Cycle: none -> asc -> desc -> none
          if (idx === -1) sortStack = [{key, dir: 1}];
          else if (sortStack[idx].dir === 1) sortStack = [{key, dir: -1}];
          else sortStack = [];
        } else {
          // Shift: toggle or add
          if (idx === -1) sortStack.push({key, dir: 1});
          else if (sortStack[idx].dir === 1) sortStack[idx].dir = -1;
          else sortStack.splice(idx, 1);
        }
        // Update indicators
        headers.forEach(h2 => {
          const si = h2.querySelector('.sort-indicator');
          const k = h2.dataset.key;
          const s = sortStack.find(s => s.key === k);
          if (s) si.textContent = s.dir === 1 ? '▲' : '▼'; else si.textContent = '';
        });
        // Persist and update UI
        saveSortStack();
        renderSortStackUI();
        applyFilterAndSort();
      });
    });

    // Initial render
    applyFilterAndSort();

    // Expand/collapse handler for parent/child rows (attach once per container)
    if (!container.__assets_expand_bound) {
      container.__assets_expand_bound = true;
      container.addEventListener('click', function(e) {
        const toggle = e.target.closest('.expand-toggle');
        if (!toggle) return;
        const parentId = toggle.getAttribute('data-parent-id');
        if (!parentId) return;
        const childRows = Array.from(container.querySelectorAll(`tr[data-parent-id="${parentId}"]`));
        const isOpen = toggle.classList.contains('open');
        toggle.classList.toggle('open', !isOpen);
        if (isOpen) {
          // Collapse: recursively hide all descendants (direct children and their descendants)
          const allRows = Array.from(container.querySelectorAll('tbody tr'));
          allRows.forEach(row => {
            const ancestors = (row.dataset.ancestors || '').split(',').filter(a => a);
            if (ancestors.includes(parentId)) {
              row.style.display = 'none';
            }
          });
        } else {
          // Expand: show only direct children, keep grandchildren hidden unless their parent is expanded
          const parentRow = toggle.closest('tr');
          let insertAfter = parentRow;
          childRows.forEach(row => {
            insertAfter.parentNode.insertBefore(row, insertAfter.nextSibling);
            insertAfter = row;
            row.style.display = 'table-row';
            // If this child also has children, ensure expand toggle is initially closed
            const childToggle = row.querySelector('.expand-toggle');
            if (childToggle) {
              childToggle.classList.remove('open');
            }
          });
        }
      });
    }
    // Column resizing functionality (using shared utility)
    if (window.initTableColumnResize) {
      window.initTableColumnResize(table, `assets_col_widths_${repoName}`);
    }
  }

  // Expose globally
  window.initAssets = initAssets;
})();
