document.addEventListener('DOMContentLoaded', function () {
  const btn = document.getElementById('run-compare-btn');
  if (!btn) return;
  btn.addEventListener('click', async function () {
    const from = (document.getElementById('compare-from-select') || {}).value || '';
    const to = (document.getElementById('compare-to-select') || {}).value || '';
    const repoOpt = document.querySelector('#repo-select option:checked');
    const repo = (repoOpt && repoOpt.dataset && repoOpt.dataset.name) ? repoOpt.dataset.name : (document.getElementById('repo-select') || {}).value || '';
    if (!from || !to || !repo) { alert('Please select both scans to compare and select a repository.'); return; }
    try {
      const resp = await fetch(`/api/diff?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}&repo=${encodeURIComponent(repo)}`);
      const data = await resp.json();
      if (window.renderDiff) { window.renderDiff(data); }
      else if (window._triage && window._triage.renderDiff) { window._triage.renderDiff(data); }
      else {
        let tries = 0;
        const waiter = setInterval(() => {
          if (window.renderDiff) { clearInterval(waiter); window.renderDiff(data); }
          else if (window._triage && window._triage.renderDiff) { clearInterval(waiter); window._triage.renderDiff(data); }
          else if (++tries > 40) { clearInterval(waiter); alert('Compare not available.'); }
        }, 200);
      }
      const dp = document.getElementById('diagram-panel'); if (dp) dp.scrollIntoView({ behavior: 'smooth' });
    } catch (e) { alert('Compare failed: ' + e); }
  });
});
