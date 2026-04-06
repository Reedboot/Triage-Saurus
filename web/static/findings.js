// findings.js - initializes the findings partial when inserted into the DOM
(function () {
  function toLower(value) {
    return (value || '').toString().toLowerCase();
  }

  function getExperimentId(container, fallbackExperimentId) {
    if (fallbackExperimentId) return fallbackExperimentId;
    const scoped = container && container.querySelector('[data-experiment-id]');
    if (scoped && scoped.dataset && scoped.dataset.experimentId) return scoped.dataset.experimentId;
    const globalScoped = document.querySelector('[data-experiment-id]');
    if (globalScoped && globalScoped.dataset && globalScoped.dataset.experimentId) return globalScoped.dataset.experimentId;
    const metaExp = document.querySelector('meta[name="experiment-id"]');
    return metaExp ? metaExp.content : '';
  }

  function copySnippetFromWrap(wrap, button) {
    if (!wrap || !button) return;
    const table = wrap.querySelector('.code-table');
    const pre = wrap.querySelector('pre');
    let text = '';

    if (table) {
      table.querySelectorAll('tr').forEach((tr) => {
        const cells = tr.querySelectorAll('td');
        if (cells.length >= 2) text += cells[1].textContent + '\n';
      });
    } else if (pre) {
      text = pre.textContent || '';
    }

    navigator.clipboard.writeText(text.trimEnd()).then(() => {
      button.textContent = '✅ Copied';
      setTimeout(() => {
        button.textContent = '📋 Copy';
      }, 1500);
    }).catch(() => {
      button.textContent = '❌ Failed';
    });
  }

  function showBlastRadius(container, resourceName, experimentId) {
    if (!resourceName || !experimentId) return;

    let modal = document.getElementById('blast-radius-modal');
    if (!modal) {
      modal = document.createElement('div');
      modal.id = 'blast-radius-modal';
      modal.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.7);z-index:9999;display:flex;align-items:center;justify-content:center;';
      modal.innerHTML = '<div style="background:var(--surface-1,#1e1e2e);border-radius:12px;padding:24px;max-width:700px;width:90%;max-height:80vh;overflow-y:auto;position:relative;">' +
        '<button id="blast-radius-close" style="position:absolute;top:12px;right:12px;background:none;border:none;font-size:1.4rem;cursor:pointer;color:var(--text-primary);">✕</button>' +
        '<h3 id="blast-radius-title" style="margin:0 0 16px;font-size:1.1rem;">🎯 Blast Radius</h3>' +
        '<div id="blast-radius-content">Loading...</div></div>';
      document.body.appendChild(modal);
      document.getElementById('blast-radius-close').addEventListener('click', function () {
        modal.remove();
      });
      modal.addEventListener('click', function (event) {
        if (event.target === modal) modal.remove();
      });
    }

    document.getElementById('blast-radius-title').textContent = '🎯 Blast Radius: ' + resourceName;
    document.getElementById('blast-radius-content').innerHTML = '<div style="text-align:center;padding:20px;">Loading diagram...</div>';

    fetch('/api/diagrams/blast_radius/' + encodeURIComponent(experimentId) + '/' + encodeURIComponent(resourceName))
      .then(function (resp) { return resp.json(); })
      .then(function (data) {
        const content = document.getElementById('blast-radius-content');
        if (data.error) {
          content.innerHTML = '<p style="color:var(--severity-high);">' + data.error + '</p>';
          return;
        }
        content.innerHTML = '<pre class="mermaid" style="background:transparent;">' + data.code + '</pre>';
        if (window.mermaid) {
          try { window.mermaid.init(undefined, content.querySelectorAll('.mermaid')); } catch (e) {}
        }
      })
      .catch(function (err) {
        document.getElementById('blast-radius-content').innerHTML = '<p style="color:var(--severity-high);">Failed to load: ' + err.message + '</p>';
      });
  }

  function initFindings(container, repoName, experimentId) {
    if (!container) container = document;
    container.dataset.findingsRepoName = repoName || container.dataset.findingsRepoName || '';
    container.dataset.findingsExperimentId = experimentId || container.dataset.findingsExperimentId || '';

    const searchInput = container.querySelector('#findings-search');
    const providerSelect = container.querySelector('#findings-provider-filter');
    const sevSelect = container.querySelector('#findings-sev-filter');
    const emptyEl = container.querySelector('#findings-empty');
    const cards = Array.from(container.querySelectorAll('.finding-card'));
    const groups = Array.from(container.querySelectorAll('.provider-group'));

    function getCurrentRepoName() {
      return container.dataset.findingsRepoName || repoName || '';
    }

    function getCurrentExperimentId() {
      return getExperimentId(container, container.dataset.findingsExperimentId || experimentId);
    }

    function toggleProviderGroup(provider) {
      if (!provider) return;
      const group = container.querySelector('.provider-group[data-provider="' + provider.replace(/"/g, '\\"') + '"]');
      if (!group) return;
      group.classList.toggle('collapsed');
      const icon = group.querySelector('[data-toggle-icon]');
      if (icon) icon.textContent = group.classList.contains('collapsed') ? '▶' : '▼';
    }

    function filterFindings() {
      const q = toLower(searchInput && searchInput.value);
      const p = toLower(providerSelect && providerSelect.value);
      const s = (sevSelect && sevSelect.value || '').toUpperCase();

      let anyVisible = false;

      cards.forEach((card) => {
        const matchQ = !q || toLower(card.dataset.search).includes(q);
        const matchP = !p || toLower(card.dataset.provider) === p;
        const matchS = !s || card.dataset.sev === s;
        const show = matchQ && matchP && matchS;
        card.style.display = show ? '' : 'none';
        if (show) anyVisible = true;
      });

      groups.forEach((group) => {
        const visibleCards = Array.from(group.querySelectorAll('.finding-card')).filter((card) => card.style.display !== 'none').length;
        const providerEmpty = group.querySelector('.provider-empty');
        if (providerEmpty) providerEmpty.style.display = visibleCards === 0 ? '' : 'none';
      });

      if (emptyEl) emptyEl.style.display = anyVisible ? 'none' : '';
    }

    function saveTriage(button, findingId, status) {
      const resolvedExperimentId = getCurrentExperimentId();
      if (!resolvedExperimentId || !findingId) {
        try { window._triage && window._triage.setStatus && window._triage.setStatus('Cannot triage: missing experiment or finding ID', 'error'); } catch (e) {}
        return;
      }

      const reason = prompt('Reason (optional, press Cancel to abort):');
      if (reason === null) return;

      button.disabled = true;
      fetch('/api/finding/triage/' + encodeURIComponent(resolvedExperimentId) + '/' + encodeURIComponent(findingId), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ triage_status: status, triage_reason: reason || '' })
      }).then(function (resp) {
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        const row = button.closest('.finding-card');
        if (row) {
          row.querySelectorAll('.btn-triage').forEach(function (b) {
            b.style.fontWeight = b.dataset.status === status ? '700' : '';
          });
        }
        try { window._triage && window._triage.setStatus && window._triage.setStatus('Triage status saved', 'success'); } catch (e) {}
        if (window._triage && window._triage.loadSectionContent) {
          try { window._triage.loadSectionContent('findings', resolvedExperimentId, getCurrentRepoName()); } catch (e) {}
        }
      }).catch(function (err) {
        try { window._triage && window._triage.setStatus && window._triage.setStatus('Failed to save triage: ' + err.message, 'error'); } catch (e) {}
      }).finally(function () {
        button.disabled = false;
      });
    }

    if (!container.__findingsClickBound) {
      container.addEventListener('click', function (event) {
        const toggle = event.target.closest('[data-provider-toggle]');
        if (toggle && container.contains(toggle)) {
          toggleProviderGroup(toggle.getAttribute('data-provider-toggle'));
          return;
        }

        const triageBtn = event.target.closest('.btn-triage');
        if (triageBtn && container.contains(triageBtn)) {
          saveTriage(triageBtn, triageBtn.dataset.findingId, triageBtn.dataset.status);
          return;
        }

        const copyBtn = event.target.closest('.btn-copy-snippet');
        if (copyBtn && container.contains(copyBtn)) {
          copySnippetFromWrap(copyBtn.closest('.code-snippet-wrap'), copyBtn);
          return;
        }

        const blastBtn = event.target.closest('.btn-blast-radius');
        if (blastBtn && container.contains(blastBtn)) {
          showBlastRadius(container, blastBtn.dataset.resource, getCurrentExperimentId());
        }
      });
      container.__findingsClickBound = true;
    }

    if (searchInput) searchInput.oninput = filterFindings;
    if (providerSelect) providerSelect.onchange = filterFindings;
    if (sevSelect) sevSelect.onchange = filterFindings;

    filterFindings();
  }

  window.initFindings = initFindings;
  window.toggleProviderGroup = function (provider) {
    const container = document.getElementById('section-panel-content') || document;
    const group = container.querySelector('.provider-group[data-provider="' + String(provider || '').replace(/"/g, '\\"') + '"]');
    if (!group) return;
    group.classList.toggle('collapsed');
    const icon = group.querySelector('[data-toggle-icon]');
    if (icon) icon.textContent = group.classList.contains('collapsed') ? '▶' : '▼';
  };
  window.filterFindings = function () {
    const container = document.getElementById('section-panel-content') || document;
    const searchInput = container.querySelector('#findings-search');
    const providerSelect = container.querySelector('#findings-provider-filter');
    const sevSelect = container.querySelector('#findings-sev-filter');
    const emptyEl = container.querySelector('#findings-empty');
    const cards = Array.from(container.querySelectorAll('.finding-card'));
    const groups = Array.from(container.querySelectorAll('.provider-group'));
    const q = toLower(searchInput && searchInput.value);
    const p = toLower(providerSelect && providerSelect.value);
    const s = (sevSelect && sevSelect.value || '').toUpperCase();
    let anyVisible = false;
    cards.forEach((card) => {
      const show = (!q || toLower(card.dataset.search).includes(q)) &&
        (!p || toLower(card.dataset.provider) === p) &&
        (!s || card.dataset.sev === s);
      card.style.display = show ? '' : 'none';
      if (show) anyVisible = true;
    });
    groups.forEach((group) => {
      const visibleCards = Array.from(group.querySelectorAll('.finding-card')).filter((card) => card.style.display !== 'none').length;
      const providerEmpty = group.querySelector('.provider-empty');
      if (providerEmpty) providerEmpty.style.display = visibleCards === 0 ? '' : 'none';
    });
    if (emptyEl) emptyEl.style.display = anyVisible ? 'none' : '';
  };
})();
