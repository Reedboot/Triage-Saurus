// overview.js - handles AI analysis button in the Overview tab
(function () {
  function initOverview(container) {
    if (!container) container = document;

    const meta = container.querySelector('.overview-actions');
    const runBtn = container.querySelector('#overview-run-ai-btn');
    const statusEl = container.querySelector('#overview-ai-status');
    if (!meta || !runBtn || !statusEl) return;

    const experimentId = (meta.dataset.experimentId || '').trim();
    const repoName = (meta.dataset.repoName || '').trim();
    if (!experimentId || !repoName) {
      statusEl.textContent = 'Missing experiment context';
      runBtn.disabled = true;
      return;
    }

    let pollTimer = null;
    let seenAiLogCount = 0;

    function setStatus(text, isError) {
      statusEl.textContent = text;
      statusEl.classList.toggle('error', !!isError);
    }

    async function pollStatus() {
      try {
        const resp = await fetch(`/api/analysis/status/${encodeURIComponent(experimentId)}/${encodeURIComponent(repoName)}`);
        const data = await resp.json();

        const logs = Array.isArray(data.logs) ? data.logs : [];
        if (logs.length > seenAiLogCount) {
          const newLines = logs.slice(seenAiLogCount);
          seenAiLogCount = logs.length;
          if (window._triage && typeof window._triage.appendLog === 'function') {
            newLines.forEach((line) => window._triage.appendLog(`[AI] ${line}`));
          }
        }

        if (data.status === 'running') {
          setStatus('Running AI analysis...', false);
          runBtn.disabled = true;
          return;
        }
        if (data.status === 'completed') {
          setStatus('Completed', false);
          runBtn.disabled = false;
          if (window._triage && typeof window._triage.loadSectionContent === 'function') {
            window._triage.loadSectionContent('overview', experimentId, repoName).catch(() => {});
          }
          if (pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
          }
          return;
        }
        if (data.status === 'failed') {
          setStatus(data.error || 'Failed', true);
          runBtn.disabled = false;
          if (pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
          }
          return;
        }

        setStatus('Idle', false);
        runBtn.disabled = false;
      } catch (err) {
        setStatus('Status check failed', true);
      }
    }

    if (!runBtn.__overview_ai_bound) {
      runBtn.__overview_ai_bound = true;
      runBtn.addEventListener('click', async () => {
        runBtn.disabled = true;
        setStatus('Starting...', false);
        seenAiLogCount = 0;

        if (window._triage && typeof window._triage.showRawLog === 'function') {
          window._triage.showRawLog();
        }
        if (window._triage && typeof window._triage.appendLog === 'function') {
          window._triage.appendLog(`[AI] Starting analysis for ${repoName} (experiment ${experimentId})`);
        }
        if (window._triage && typeof window._triage.setStatus === 'function') {
          window._triage.setStatus('Running AI analysis…', 'info');
        }

        try {
          const resp = await fetch(`/api/analysis/start/${encodeURIComponent(experimentId)}/${encodeURIComponent(repoName)}`, {
            method: 'POST',
          });
          const data = await resp.json();

          if (!resp.ok) {
            setStatus(data.error || 'Start failed', true);
            if (window._triage && typeof window._triage.appendLog === 'function') {
              window._triage.appendLog(`[AI] Start failed: ${data.error || 'unknown error'}`);
            }
            runBtn.disabled = false;
            return;
          }

          setStatus(data.status === 'running' ? 'Already running...' : 'Running AI analysis...', false);
          if (pollTimer) clearInterval(pollTimer);
          pollTimer = setInterval(pollStatus, 2000);
        } catch (err) {
          setStatus('Start failed', true);
          if (window._triage && typeof window._triage.appendLog === 'function') {
            window._triage.appendLog('[AI] Start failed due to network/runtime error');
          }
          runBtn.disabled = false;
        }
      });
    }

    pollStatus();
  }

  window.initOverview = initOverview;
})();
