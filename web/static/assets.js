// assets.js - initializes the assets partial when inserted into the DOM
// Usage: window.initAssets(containerElement, repoName)
(function(){
  function toLower(s){ return (s||'').toString().toLowerCase(); }

  function initAssets(container, repoName){
    if (!container) container = document;
    repoName = repoName || (container.querySelector('#assets-meta')?.dataset?.repo) || 'global';

    const searchInput = container.querySelector('#section-assets-search');
    const providerSelect = container.querySelector('#section-assets-provider');
    const table = container.querySelector('#section-assets-table');
    if (!table) return; // nothing to do
    const tbody = table.querySelector('tbody');
    let rows = Array.from(tbody.querySelectorAll('tr'));

    // Build data array for sorting/filtering
    let assetsData = rows.map(r => ({
      el: r,
      resource_name: toLower(r.dataset.resource_name || r.querySelector('.asset-name')?.textContent || ''),
      resource_type: toLower(r.dataset.resource_type || ''),
      render_category: toLower(r.dataset.renderCategory || r.dataset.render-category || ''),
      provider: toLower(r.dataset.provider || ''),
      provider_raw: r.dataset.provider_raw || '',
      region: toLower(r.dataset.region || ''),
      finding_count: parseInt(r.dataset.finding_count || '0', 10) || 0,
      display_on_diagram: (function(){
        const v = (r.dataset.displayOnDiagram || r.dataset.displayOnDiagram === '0' ? r.dataset.displayOnDiagram : r.getAttribute('data-display-on-diagram')) || r.dataset.display_on_diagram || '';
        if (!v) return false;
        const s = v.toString().toLowerCase();
        return ['1','true','yes','on'].includes(s);
      })(),
      source_file: r.dataset.source_file || '',
      discovered_by: toLower(r.dataset.discovered_by || ''),
      search: toLower(r.dataset.search || ''),
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

      // Apply multi-key sort
      if (sortStack.length) {
        filtered.sort((x, y) => {
          for (const s of sortStack) {
            const k = s.key;
            const dir = s.dir;
            const cmp = compare(x, y, k);
            if (cmp !== 0) return cmp * dir;
          }
          return 0;
        });
      }

      // Re-render tbody
      tbody.innerHTML = '';
      const emptyEl = container.querySelector('#section-assets-empty');
      if (filtered.length === 0) {
        if (emptyEl) emptyEl.style.display = '';
      } else {
        if (emptyEl) emptyEl.style.display = 'none';
        for (const a of filtered) tbody.appendChild(a.el);
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
  }

  // Expose globally
  window.initAssets = initAssets;
})();
