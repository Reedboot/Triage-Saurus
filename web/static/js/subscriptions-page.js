(function() {
  console.log('[subscriptions] Script starting');
  const panel = document.getElementById('subscriptions-panel');
  if (!panel) { console.warn('[subscriptions] Panel not found'); return; }

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

  function escapeHtml(str) {
    return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

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
         <td>
           <div class="subscriptions-actions-cell">
             <a href="/cloud/assets?sub=${encodeURIComponent(s.id)}" class="btn btn-sm" style="padding:4px 12px; font-size:0.75rem; text-decoration:none;">📦 Show Assets</a>
           </div>
         </td>
       </tr>
      `).join('');

      document.getElementById('subscriptions-list').style.display = 'block';
    } catch(e) {
      document.getElementById('subscriptions-loading').textContent = 'Failed to load subscriptions.';
    }
  }

  loadSubscriptions();
})();
