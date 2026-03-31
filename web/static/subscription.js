// subscription.js - handles subscription-wide Q&A save
(function(){
  async function initSubscription(container, repoName, experimentId){
    if (!container) container = document;
    const btn = container.querySelector('#subscription-qa-save');
    if (!btn) return;

    const scopeEl = container.querySelector('#subscription-qa-scope');
    const dkEl = container.querySelector('#subscription-qa-dontknow');
    const qEl = container.querySelector('#subscription-qa-question');
    const aEl = container.querySelector('#subscription-qa-answer');
    const cEl = container.querySelector('#subscription-qa-confidence');
    const byEl = container.querySelector('#subscription-qa-by');
    const tEl = container.querySelector('#subscription-qa-tags');

    function syncDontKnow() {
      if (!dkEl || !aEl) return;
      aEl.disabled = !!dkEl.checked;
      if (dkEl.checked) aEl.value = '';
    }
    if (dkEl) dkEl.addEventListener('change', syncDontKnow);
    syncDontKnow();

    // Allow picking up an AI-suggested question into the form
    container.querySelectorAll('.qa-use').forEach((b) => {
      if (b.__qa_use_bound) return;
      b.__qa_use_bound = true;
      b.addEventListener('click', () => {
        const q = (b.dataset.question || '').trim();
        if (qEl) qEl.value = q;
        if (dkEl) dkEl.checked = false;
        if (aEl) aEl.value = '';
        syncDontKnow();
        try { qEl?.focus?.(); } catch(e) {}
      });
    });

    btn.addEventListener('click', async () => {
      const scope = (scopeEl?.value || 'repo').trim();
      const question = (qEl?.value || '').trim();
      let answer = (aEl?.value || '').trim();
      const dontKnow = !!(dkEl && dkEl.checked);
      if (dontKnow || !answer) answer = "Don't know";

      const confidence = (cEl?.value || '').trim();
      const answered_by = (byEl?.value || '').trim();
      const tags = (tEl?.value || '').trim();
      if (!question) {
        try { window._triage?.setStatus?.('Select a suggested question first', 'warn'); } catch(e) {}
        return;
      }
      btn.disabled = true;
      try {
        const resp = await fetch(`/api/subscription_context/${encodeURIComponent(experimentId)}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ scope, repo_name: repoName, question, answer, confidence, answered_by, tags }),
        });
        if (!resp.ok) throw new Error('save_failed');
        try { window._triage?.setStatus?.('Saved global knowledge Q&A', 'success'); } catch(e) {}
        try { await window._triage?.loadSectionContent?.('subscription', experimentId, repoName); } catch(e) {}
      } catch (e) {
        try { window._triage?.setStatus?.('Failed to save global knowledge Q&A', 'error'); } catch(e) {}
      } finally {
        btn.disabled = false;
      }
    });
  }

  window.initSubscription = initSubscription;
})();
