// findings.js - triage status UI (valid/false_positive/needs_context)
(function(){
  async function initFindings(container, repoName, experimentId){
    if (!container) container = document;
    const table = container.querySelector('#findings-table');
    if (!table) return;

    // Add small triage controls next to each triage badge
    const rows = Array.from(table.querySelectorAll('tbody tr'));
    rows.forEach((row) => {
      const fid = row.dataset.findingId || row.getAttribute('data-finding-id');
      if (!fid) return;
      if (row.__triageBound) return;
      row.__triageBound = true;

      const triageCell = row.querySelectorAll('td')[5];
      if (!triageCell) return;

      const wrap = document.createElement('div');
      wrap.style.display = 'flex';
      wrap.style.gap = '6px';
      wrap.style.alignItems = 'center';
      wrap.style.justifyContent = 'center';

      const current = triageCell.firstElementChild;
      if (current) wrap.appendChild(current);

      const sel = document.createElement('select');
      sel.className = 'assets-provider-filter';
      sel.style.padding = '2px 6px';
      sel.style.fontSize = '0.72rem';
      ['valid','false_positive','needs_context'].forEach((v) => {
        const opt = document.createElement('option');
        opt.value = v;
        opt.textContent = v;
        if (current && current.textContent.trim() === v) opt.selected = true;
        sel.appendChild(opt);
      });

      const btn = document.createElement('button');
      btn.className = 'btn-small';
      btn.textContent = 'Save';

      btn.addEventListener('click', async () => {
        btn.disabled = true;
        try {
          const reason = prompt('Reason (optional):') || '';
          const resp = await fetch(`/api/finding/triage/${encodeURIComponent(experimentId)}/${encodeURIComponent(fid)}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ triage_status: sel.value, triage_reason: reason }),
          });
          if (!resp.ok) throw new Error('save_failed');
          try { window._triage?.setStatus?.('Saved triage status', 'success'); } catch(e) {}
          // Refresh findings
          try { await window._triage?.loadSectionContent?.('findings', experimentId, repoName); } catch(e) {}
        } catch (e) {
          try { window._triage?.setStatus?.('Failed to save triage status', 'error'); } catch(e) {}
        } finally {
          btn.disabled = false;
        }
      });

      wrap.appendChild(sel);
      wrap.appendChild(btn);
      triageCell.innerHTML = '';
      triageCell.appendChild(wrap);
    });
  }

  window.initFindings = initFindings;
})();
