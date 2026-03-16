// roles.js - initializes the roles & permissions partial with multi-filter support
// Usage: window.initRoles(containerElement, repoName)
(function(){
  function toLower(s){ return (s||'').toString().toLowerCase(); }

  function initRoles(container, repoName){
    if (!container) container = document;
    repoName = repoName || (container.querySelector('#assets-meta')?.dataset?.repo) || 'global';

    const searchInput = container.querySelector('#roles-search');
    // Create multi-select filters for Resource and Type
    let typeFilter = container.querySelector('#roles-type-filter');
    if (!typeFilter) {
      typeFilter = document.createElement('select');
      typeFilter.id = 'roles-type-filter';
      typeFilter.multiple = true;
      typeFilter.style.minWidth = '160px';
      typeFilter.title = 'Filter by type (multi-select)';
      container.querySelector('.assets-toolbar')?.insertBefore(typeFilter, container.querySelector('.assets-toolbar')?.children[1] || null);
    }

    const resourceFilter = container.querySelector('#roles-resource-filter');
    if (!resourceFilter) {
      // resourceFilter will be a text input for quick matching
      const rf = document.createElement('input');
      rf.id = 'roles-resource-filter';
      rf.placeholder = 'Resource name…';
      rf.className = 'assets-search';
      rf.style.maxWidth = '220px';
      container.querySelector('.assets-toolbar')?.insertBefore(rf, container.querySelector('.assets-toolbar')?.children[2] || null);
    }

    const table = container.querySelector('#roles-table');
    if (!table) return;
    const tbody = table.querySelector('tbody');
    let rows = Array.from(tbody.querySelectorAll('tr'));

    // Build type options
    const types = new Set(rows.map(r => toLower(r.querySelector('td:nth-child(2)')?.textContent || '').trim()).filter(Boolean));
    typeFilter.innerHTML = '';
    const optAll = document.createElement('option'); optAll.value = ''; optAll.textContent = 'All types'; typeFilter.appendChild(optAll);
    Array.from(types).sort().forEach(t => {
      const o = document.createElement('option'); o.value = t; o.textContent = t; typeFilter.appendChild(o);
    });

    // Build assetsData for filtering
    let data = rows.map(r => ({
      el: r,
      search: toLower(r.dataset.search || ''),
      type: toLower(r.querySelector('td:nth-child(2)')?.textContent || ''),
      resource: toLower(r.querySelector('td:nth-child(4)')?.textContent || ''),
    }));

    function applyRoleFilters() {
      const q = toLower(searchInput?.value || '').trim();
      const resourceQ = toLower(container.querySelector('#roles-resource-filter')?.value || '').trim();
      const selectedTypes = Array.from(typeFilter.selectedOptions || []).map(o => o.value).filter(Boolean);

      const qEffective = q.length >= 2 ? q : '';

      let shown = 0;
      for (const d of data) {
        let ok = true;
        if (qEffective && !d.search.includes(qEffective)) ok = false;
        if (resourceQ && !d.resource.includes(resourceQ)) ok = false;
        if (selectedTypes.length && !selectedTypes.includes(d.type)) ok = false;
        d.el.style.display = ok ? '' : 'none';
        if (ok) shown++;
      }
      const empty = container.querySelector('#roles-empty');
      if (empty) empty.style.display = shown === 0 ? '' : 'none';
    }

    // Debounce for search inputs
    let timer = null;
    function sched() { if (timer) clearTimeout(timer); timer = setTimeout(() => { applyRoleFilters(); timer = null; }, 250); }

    searchInput && searchInput.addEventListener('input', sched);
    container.querySelector('#roles-resource-filter')?.addEventListener('input', sched);
    typeFilter && typeFilter.addEventListener('change', applyRoleFilters);

    // Initial filter apply
    applyRoleFilters();
  }

  window.initRoles = initRoles;
})();
