(function() {
  console.log('[subscriptions] Script starting');
  const panel = document.getElementById('subscriptions-panel');
  if (!panel) { console.warn('[subscriptions] Panel not found'); return; }

  const listEl = document.getElementById('subscriptions-list');
  const tbodyEl = document.getElementById('subscriptions-tbody');
  const loadingEl = document.getElementById('subscriptions-loading');
  const emptyEl = document.getElementById('subscriptions-empty');
  const previewPanel = document.getElementById('subscription-preview-panel');
  const previewRoot = document.getElementById('ingress-diagram-div');
  const modeHost = document.getElementById('subscription-diagram-mode-host');
  const targetFilter = document.getElementById('ingress-diagram-div-target-filter');
  const drilldownModal = document.getElementById('drilldown-modal');
  const drilldownModalBody = document.getElementById('drilldown-modal-body');

  let currentSubscriptionId = '';
  let currentNodeDrilldownMap = {};

  function renderBadge(env, badge) {
    const colours = { danger:'#dc2626', warning:'#d97706', info:'#2563eb', secondary:'#6b7280' };
    const c = colours[badge] || colours.secondary;
    return `<span style="display:inline-block;padding:2px 8px;border-radius:10px;font-size:0.75rem;font-weight:600;background:${c};color:#fff;">${env}</span>`;
  }

  function formatDate(iso) {
    if (!iso) return '—';
    try { return new Date(iso).toLocaleString(); } catch (e) { return iso; }
  }

  function escapeHtml(str) {
    return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  function normalizeMermaidNodeId(rawId) {
    return String(rawId || '')
      .replace(/^.*?flowchart-/, '')
      .replace(/^mermaid-\d+-/, '')
      .replace(/-\d+$/, '');
  }

  function isHostname(value) {
    return /^[a-z0-9.-]+\.[a-z]{2,}$/i.test(String(value || '').trim());
  }

  function renderCellValue(value) {
    if (value === null || value === undefined || value === '') return '—';
    if (typeof value === 'object') {
      const label = String(value.label || value.name || value.text || value.title || '—');
      const href = String(value.href || value.url || value.link || '').trim();
      if (href) {
        return `<a href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)}</a>`;
      }
      return escapeHtml(label);
    }
    const text = String(value);
    if (/^https?:\/\//i.test(text)) {
      return `<a href="${escapeHtml(text)}" target="_blank" rel="noopener noreferrer">${escapeHtml(text)}</a>`;
    }
    if (isHostname(text)) {
      return `<a href="https://${escapeHtml(text)}" target="_blank" rel="noopener noreferrer">${escapeHtml(text)}</a>`;
    }
    return escapeHtml(text);
  }

  function renderTreeCell(value) {
    const cell = value && typeof value === 'object' ? value : { label: value };
    const label = String(cell.label || cell.name || cell.text || cell.title || '—');
    const style = String(cell.style || '').trim();
    return `<span${style ? ` style="${escapeHtml(style)}"` : ''}>${renderCellValue(label)}</span>`;
  }

  function renderAksIngressServices(response) {
    const services = Array.isArray(response?.ingress_services)
      ? response.ingress_services.filter((service) => service && (service.name || service.service_name))
      : [];
    if (!services.length) {
      return '<div style="color:var(--text-muted);">No drill-down details returned.</div>';
    }

    const grouped = new Map();
    for (const service of services) {
      const namespace = String(service.namespace || '—').trim() || '—';
      const key = namespace.toLowerCase();
      if (!grouped.has(key)) {
        grouped.set(key, { namespace, rows: [] });
      }
      grouped.get(key).rows.push(service);
    }

    const renderRow = (service) => {
      const name = String(service.name || service.service_name || '—').trim() || '—';
      const ingress = String(service.ingress_name || service.host || '—').trim() || '—';
      const host = String(service.host || '').trim();
      const ingressLabel = host && host !== ingress
        ? `${escapeHtml(ingress)}<br/><code>${escapeHtml(host)}</code>`
        : escapeHtml(ingress);
      const path = String(service.path || '—').trim() || '—';
      const port = String(service.port ?? '—').trim() || '—';
      const searchText = [service.namespace, name, ingress, host, path, port]
        .map((value) => String(value || '').toLowerCase())
        .join(' ');
      return `
        <tr data-aks-ingress-row data-search-text="${escapeHtml(searchText)}" style="border-bottom:1px solid var(--border);">
          <td style="padding:8px 10px;vertical-align:top;"><strong>${escapeHtml(name)}</strong></td>
          <td style="padding:8px 10px;vertical-align:top;">${ingressLabel}</td>
          <td style="padding:8px 10px;vertical-align:top;"><code>${escapeHtml(path)}</code></td>
          <td style="padding:8px 10px;vertical-align:top;">${escapeHtml(port)}</td>
        </tr>
      `;
    };

    const groups = Array.from(grouped.values()).sort((a, b) => a.namespace.localeCompare(b.namespace));
    return `
      <div style="margin-top:12px;">
        <div style="margin-bottom:10px;">
          <input
            type="search"
            data-aks-ingress-search
            placeholder="Filter namespaces, services, hosts, or paths…"
            style="width:100%;padding:8px 10px;border:1px solid var(--border);border-radius:8px;background:var(--bg-base);color:var(--text);"
          />
        </div>
        <div style="overflow:auto;border:1px solid var(--border);border-radius:8px;">
          ${groups.map((group, index) => `
            <details data-aks-ingress-group${index === 0 ? ' open' : ''} style="border-bottom:1px solid var(--border);">
              <summary style="cursor:pointer;list-style:none;padding:10px 12px;display:flex;align-items:center;justify-content:space-between;gap:12px;background:var(--bg-base);">
                <span><strong>${escapeHtml(group.namespace)}</strong></span>
                <span style="color:var(--text-muted);font-size:0.8rem;">${group.rows.length} service${group.rows.length === 1 ? '' : 's'}</span>
              </summary>
              <div style="overflow:auto;">
                <table style="width:100%;border-collapse:collapse;font-size:0.84rem;">
                  <thead>
                    <tr>
                      ${['Service', 'Ingress / Host', 'Path', 'Port'].map((column) => `<th style="padding:8px 10px;text-align:left;background:var(--bg-base);border-bottom:1px solid var(--border);font-size:0.75rem;text-transform:uppercase;letter-spacing:0.03em;color:var(--text-muted);">${escapeHtml(column)}</th>`).join('')}
                    </tr>
                  </thead>
                  <tbody>${group.rows.map(renderRow).join('')}</tbody>
                </table>
              </div>
            </details>
          `).join('')}
        </div>
      </div>
    `;
  }

  function buildTreeRows(rows, parentId = null, collapseChildren = false) {
    return rows
      .filter((row) => String(row?.parent_id ?? '') === String(parentId ?? ''))
      .map((row) => {
        const childCount = Number(row?.child_count || 0);
        const cells = Array.isArray(row?.cells) ? row.cells : [];
        const rowId = String(row?.id || '');
        const childMarkup = buildTreeRows(rows, rowId, collapseChildren);
        return `
          <tr data-row-id="${escapeHtml(rowId)}"${parentId && collapseChildren ? ` data-parent-id="${escapeHtml(String(parentId))}" hidden` : ''}${parentId && !collapseChildren ? ` data-parent-id="${escapeHtml(String(parentId))}"` : ''}>
            ${cells.map((cell, index) => {
              if (index === 0) {
                return `
                  <td>
                    ${childCount > 0 && collapseChildren ? '<img alt="" aria-hidden="true" src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==" style="width:12px;height:12px;margin-right:6px;vertical-align:middle;" />' : ''}
                    ${childCount > 0 && collapseChildren ? '<button type="button" class="expand-toggle" aria-expanded="false" style="margin-right:6px;">+</button>' : ''}
                    ${renderTreeCell(cell)}
                  </td>
                `;
              }
              return `<td>${renderTreeCell(cell)}</td>`;
            }).join('')}
          </tr>
          ${childMarkup}
        `;
      })
      .join('');
  }

  function syncModeButtons(mode) {
    if (!modeHost) return;
    modeHost.querySelectorAll('[data-cloud-arch-view]').forEach((btn) => {
      const isActive = (btn.getAttribute('data-cloud-arch-view') || '') === mode;
      btn.classList.toggle('is-active', isActive);
      btn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
    });
    if (targetFilter) {
      targetFilter.hidden = mode !== 'reactflow';
    }
  }

  function hideModal() {
    if (!drilldownModal) return;
    drilldownModal.hidden = true;
    if (drilldownModalBody) drilldownModalBody.innerHTML = '';
  }

  function renderDrilldownModal(nodeData, response) {
    if (!drilldownModal || !drilldownModalBody) return;
    const armType = String(nodeData?.arm_type || nodeData?.resourceType || nodeData?.type || '').trim();
    const title = String(response?.title || nodeData?.title || nodeData?.label || 'Resource details').trim();
    const columns = Array.isArray(response?.columns) ? response.columns : [];
    const rows = Array.isArray(response?.rows) ? response.rows : [];
    const sections = Array.isArray(response?.sections) ? response.sections : [];
    let table = '<div style="color:var(--text-muted);">No drill-down details returned.</div>';
    if (Array.isArray(response?.ingress_services) && response.ingress_services.length) {
      table = renderAksIngressServices(response);
    } else if (String(response?.view_type || '').toLowerCase() === 'tree_table' && sections.length) {
      table = sections.map((section) => `
        <div style="margin-top:16px;">
          <div style="font-weight:700;color:var(--text);margin-bottom:6px;">${escapeHtml(section?.title || '')}</div>
          ${section?.subtitle ? `<div style="color:var(--text-muted);font-size:0.8rem;margin-bottom:8px;">${escapeHtml(section.subtitle)}</div>` : ''}
          <table class="section-table" style="width:100%;">
            <thead><tr>${columns.map((col) => `<th>${escapeHtml(col)}</th>`).join('')}</tr></thead>
            <tbody>${buildTreeRows(Array.isArray(section?.rows) ? section.rows : [], null, Array.isArray(section?.rows) ? section.rows.some((row) => Boolean(row?.search_text)) : false)}</tbody>
          </table>
        </div>
      `).join('');
    } else if (columns.length || rows.length) {
      table = `<table class="section-table" style="width:100%;margin-top:12px;"><thead><tr>${columns.map((col) => `<th>${escapeHtml(col)}</th>`).join('')}</tr></thead><tbody>${rows.map((row) => `<tr>${row.map((cell) => `<td>${renderCellValue(cell)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
    }

    drilldownModalBody.innerHTML = `
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;">
        <div>
          <div style="font-size:1rem;font-weight:700;color:var(--text);">${escapeHtml(title)}</div>
          <div style="margin-top:6px;color:var(--text-muted);"><strong>Resource type:</strong> ${escapeHtml(armType || '—')}</div>
          ${(() => {
            const publicIps = Array.isArray(nodeData?.public_ips) ? nodeData.public_ips : [];
            const publicIp = nodeData?.public_ip || publicIps[0] || nodeData?.network?.public_ip || '';
            return publicIp ? `<div style="margin-top:4px;color:var(--text-muted);"><strong>Public IP:</strong> ${escapeHtml(publicIp)}</div>` : '';
          })()}
        </div>
        <button type="button" class="btn-diagram" id="drilldown-close-btn">Close</button>
      </div>
      ${table}
    `;
    drilldownModal.hidden = false;
    const closeBtn = document.getElementById('drilldown-close-btn');
    if (closeBtn) closeBtn.addEventListener('click', hideModal, { once: true });
    drilldownModalBody.querySelectorAll('.expand-toggle').forEach((btn) => {
      btn.addEventListener('click', () => {
        const row = btn.closest('tr');
        if (!row) return;
        const expanded = btn.getAttribute('aria-expanded') === 'true';
        btn.setAttribute('aria-expanded', expanded ? 'false' : 'true');
        btn.textContent = expanded ? '+' : '−';
        const rowId = row.getAttribute('data-row-id') || '';
        if (window.CSS && typeof CSS.escape === 'function') {
          const childRows = drilldownModalBody.querySelectorAll(`tr[data-parent-id="${CSS.escape(rowId)}"]`);
          childRows.forEach((child) => {
            child.hidden = expanded;
          });
        }
      });
    });
    const aksSearch = drilldownModalBody.querySelector('[data-aks-ingress-search]');
    const aksRows = Array.from(drilldownModalBody.querySelectorAll('[data-aks-ingress-row]'));
    const aksGroups = Array.from(drilldownModalBody.querySelectorAll('[data-aks-ingress-group]'));
    if (aksSearch && aksRows.length && aksGroups.length) {
      const updateAksSearch = () => {
        const query = String(aksSearch.value || '').trim().toLowerCase();
        for (const group of aksGroups) {
          let visible = 0;
          const groupRows = Array.from(group.querySelectorAll('[data-aks-ingress-row]'));
          for (const row of groupRows) {
            const searchText = String(row.dataset.searchText || '').toLowerCase();
            const show = !query || searchText.includes(query);
            row.hidden = !show;
            if (show) visible += 1;
          }
          group.hidden = visible === 0;
          if (query) {
            group.open = visible > 0;
          }
        }
      };
      aksSearch.addEventListener('input', updateAksSearch);
      updateAksSearch();
    }
  }

  async function openNodeDrilldown(nodeId, nodeData) {
    if (!currentSubscriptionId) return;
    const resp = await fetch(`/api/subscriptions/${encodeURIComponent(currentSubscriptionId)}/drilldown`, {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        arm_type: nodeData?.arm_type || nodeData?.resourceType || nodeData?.type || '',
        resources: Array.isArray(nodeData?.resources) ? nodeData.resources : [],
        node: nodeData || currentNodeDrilldownMap[nodeId] || {},
      }),
    });
    const data = await resp.json();
    renderDrilldownModal(nodeData || currentNodeDrilldownMap[nodeId] || {}, data);
  }

  function bindDrilldownHandlers(rootEl) {
    if (!rootEl) return;
    const nodes = rootEl.querySelectorAll('svg g.node[id]');
    nodes.forEach((nodeEl) => {
      const nodeId = normalizeMermaidNodeId(nodeEl.getAttribute('id') || '');
      const nodeData = currentNodeDrilldownMap[nodeId];
      if (!nodeData) return;
      nodeEl.classList.add('node-drillable');
      nodeEl.setAttribute('tabindex', '0');
      nodeEl.style.cursor = 'pointer';
      nodeEl.addEventListener('click', (evt) => {
        evt.preventDefault();
        evt.stopPropagation();
        openNodeDrilldown(nodeId, nodeData);
      });
      nodeEl.addEventListener('dblclick', (evt) => {
        evt.preventDefault();
        evt.stopPropagation();
        openNodeDrilldown(nodeId, nodeData);
      });
    });
  }

  async function renderPreview(subId, payload) {
    currentSubscriptionId = subId;
    const diagram = payload?.ingress_diagram || {};
    const source = String(diagram.mermaid || diagram.views?.connectivity?.mermaid || '').trim();
    currentNodeDrilldownMap = diagram.node_drilldown_map || diagram.views?.connectivity?.node_drilldown_map || {};

    if (!previewPanel || !previewRoot) return;
    previewPanel.hidden = false;
    previewRoot.innerHTML = '';
    syncModeButtons('mermaid');

    if (!window.TriageMermaid || typeof window.TriageMermaid.renderSource !== 'function') {
      previewRoot.textContent = 'Diagram renderer unavailable.';
      return;
    }

    await window.TriageMermaid.renderSource({
      source,
      rootEl: previewRoot,
      onRendered: async () => {
        if (typeof window.postProcessDiagramSvgs === 'function') {
          window.postProcessDiagramSvgs(previewRoot);
        }
        bindDrilldownHandlers(previewRoot);
      },
    });
  }

  async function loadPreview(subId) {
    const resp = await fetch(`/${encodeURIComponent(subId)}/diagram`, {
      headers: { Accept: 'application/json' },
    });
    const payload = await resp.json();
    await renderPreview(subId, payload);
  }

  async function loadSubscriptions() {
    try {
      const resp = await fetch('/api/subscriptions');
      const data = await resp.json();
      const subs = data.subscriptions || [];

      loadingEl.style.display = 'none';

      if (!subs.length) {
        emptyEl.style.display = 'block';
        return;
      }

      tbodyEl.innerHTML = subs.map(s => `
       <tr>
         <td class="subscription-name-cell">
           <a href="/cloud/architecture?sub=${encodeURIComponent(s.id)}" target="_blank" rel="noopener noreferrer" style="color:inherit;text-decoration:none;display:inline-block;">
             <strong style="color:#60a5fa; text-decoration:underline;">${escapeHtml(s.display_name || s.id)}</strong><br/>
             <small style="color:var(--text-muted);font-size:0.75rem;">${escapeHtml(s.id)}</small>
           </a>
         </td>
         <td><span style="display:inline-block;padding:2px 8px;border-radius:6px;font-size:0.75rem;font-weight:600;background:#0066cc;color:#fff;">${escapeHtml(s.provider || '—')}</span></td>
         <td>${renderBadge(s.environment, s.env_badge)}</td>
         <td>${escapeHtml(s.asset_count ?? '—')}</td>
         <td>${s.public_count > 0 ? `<span style="color:#f59e0b;font-weight:600;">⚠ ${escapeHtml(s.public_count)}</span>` : '0'}</td>
         <td>${escapeHtml(s.state || '—')}</td>
         <td style="font-size:0.8rem;">${formatDate(s.last_synced)}</td>
         <td>
           <div class="subscriptions-actions-cell">
             <a href="#" data-subscription-id="${escapeHtml(s.id)}" class="subscription-preview-btn btn btn-sm" style="padding:4px 12px; font-size:0.75rem; text-decoration:none;">👁 Preview</a>
             <a href="/cloud/assets?sub=${encodeURIComponent(s.id)}" class="btn btn-sm" style="padding:4px 12px; font-size:0.75rem; text-decoration:none;">📦 Show Assets</a>
           </div>
         </td>
       </tr>
      `).join('');

      tbodyEl.querySelectorAll('.subscription-preview-btn').forEach((btn) => {
        btn.addEventListener('click', async (evt) => {
          evt.preventDefault();
          const subId = btn.getAttribute('data-subscription-id') || '';
          if (!subId) return;
          await loadPreview(subId);
        });
      });

      if (modeHost) {
        modeHost.querySelectorAll('[data-cloud-arch-view]').forEach((btn) => {
          btn.addEventListener('click', () => {
            const mode = btn.getAttribute('data-cloud-arch-view') || 'mermaid';
            syncModeButtons(mode);
          });
        });
      }

      if (drilldownModal) {
        drilldownModal.addEventListener('click', (evt) => {
          if (evt.target === drilldownModal) hideModal();
        });
      }

      listEl.style.display = 'block';
    } catch (e) {
      loadingEl.textContent = 'Failed to load subscriptions.';
    }
  }

  loadSubscriptions();
})();
