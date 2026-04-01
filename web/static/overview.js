// overview.js - handles AI analysis controls in Overview and TLDR tabs
(function () {
  function initOverview(container) {
    if (!container) container = document;

    const meta = container.querySelector('.overview-actions');
    const runBtn = container.querySelector('#overview-run-ai-btn, #tldr-run-ai-btn');
    const statusEl = container.querySelector('#overview-ai-status, #tldr-ai-status');
    const genRulesBtn = container.querySelector('#overview-gen-rules-btn');
    if (!meta || !statusEl) return;

    const experimentId = (meta.dataset.experimentId || '').trim();
    const repoName = (meta.dataset.repoName || '').trim();
    const refreshSection = (meta.dataset.refreshSection || 'overview').trim() || 'overview';
    if (!experimentId || !repoName) {
      statusEl.textContent = 'Missing experiment context';
      if (runBtn) runBtn.disabled = true;
      return;
    }
    if (!runBtn) return;

    const triage = window._triage || {};
    let pollTimer = null;
    let seenAiLogCount = 0;
    let stopInFlight = false;
    let activeStream = null;
    let observedActiveRun = false;
    let completionRefreshTriggered = false;

    function setStatus(text, isError) {
      statusEl.textContent = text;
      statusEl.classList.toggle('error', !!isError);
    }

    function updateGlobalStatus(message, tone) {
      if (triage && typeof triage.setStatus === 'function' && message) {
        triage.setStatus(message, tone || 'info');
      }
    }

    function updateGlobalBusy(isBusy) {
      if (triage && typeof triage.setStatusBusy === 'function') {
        triage.setStatusBusy(!!isBusy);
      }
    }

    function updateToolbarStop(enabled) {
      if (triage && typeof triage.setToolbarStopState === 'function') {
        triage.setToolbarStopState({
          enabled: !!enabled,
          visible: !!enabled,
          label: '⏹ Stop AI Scan',
        });
      }
    }
    updateToolbarStop(false);

    function closeActiveStream(reason) {
      if (!activeStream) return;
      try {
        activeStream.close();
      } catch (err) {
        // ignore
      }
      if (triage && typeof triage.appendLog === 'function' && reason) {
        triage.appendLog(`[Copilot] Stream closed (${reason})`);
      }
      activeStream = null;
    }

    async function requestStop(source) {
      if (stopInFlight) return;
      stopInFlight = true;
      if (triage && typeof triage.setToolbarStopState === 'function') {
        triage.setToolbarStopState({
          enabled: false,
          visible: true,
          label: '⏹ Stopping AI Scan...',
        });
      }
      try {
        const resp = await fetch(`/api/analysis/stop/${encodeURIComponent(experimentId)}/${encodeURIComponent(repoName)}`, {
          method: 'POST',
        });
        if (!resp.ok) {
          throw new Error('stop_failed');
        }
        const payload = await resp.json().catch(() => ({}));
        if (triage && typeof triage.appendLog === 'function') {
          triage.appendLog(source === 'toolbar' ? '[Copilot] Stop requested from toolbar' : '[Copilot] Stop requested');
        }
        if (payload && payload.error) {
          throw new Error(payload.error || 'stop_failed');
        }
        setStatus('Stopping…', false);
        if (runBtn) runBtn.disabled = false;
        closeActiveStream('stopped by user');
        updateGlobalStatus('Stopping AI analysis…', 'warn');
        updateGlobalBusy(true);
        if (pollTimer) clearInterval(pollTimer);
        pollTimer = setInterval(pollStatus, 2000);
      } catch (err) {
        setStatus('Stop failed', true);
        if (triage && typeof triage.appendLog === 'function') {
          triage.appendLog('[Copilot] Stop threw error');
        }
        updateGlobalStatus('AI analysis stop failed', 'error');
        updateGlobalBusy(false);
      } finally {
        stopInFlight = false;
      }
    }

    if (triage && typeof triage.registerToolbarStop === 'function') {
      triage.registerToolbarStop(() => requestStop('toolbar'));
    }

    function clearPolling() {
      if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    }

    async function maybeRefreshSectionAfterCompletion() {
      if (completionRefreshTriggered) return;
      if (!observedActiveRun && !activeStream) return;
      completionRefreshTriggered = true;
      observedActiveRun = false;

      // Refresh the section that initiated the run (overview/tldr)
      if (window._triage && typeof window._triage.loadSectionContent === 'function') {
        window._triage.loadSectionContent(refreshSection, experimentId, repoName).catch(() => {});
      }

      // If there are open questions, take the user to Global Knowledge Q&A; else to Overview.
      try {
        const resp = await fetch(`/api/analysis/status/${encodeURIComponent(experimentId)}/${encodeURIComponent(repoName)}`);
        const status = await resp.json().catch(() => ({}));
        const openQ = Array.isArray(status.open_questions) ? status.open_questions : [];
        const unanswered = openQ.filter((q) => (typeof q === 'string' && q.trim()) || (q && typeof q === 'object' && String(q.question || '').trim())).length;
        if (window._triage && typeof window._triage.activateSectionKey === 'function') {
          window._triage.activateSectionKey(unanswered > 0 ? 'subscription' : 'overview', experimentId, repoName);
        }
      } catch (e) {
        if (window._triage && typeof window._triage.activateSectionKey === 'function') {
          window._triage.activateSectionKey('overview', experimentId, repoName);
        }
      }
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
          observedActiveRun = true;
          completionRefreshTriggered = false;
          const label = (data.active_step_label || data.active_step || '').toString();
          const skepticsRunning = !!data.skeptics_running;
          const statusText = label ? `Running: ${label}${skepticsRunning ? ' (skeptics)' : ''}…` : 'Running...';
          setStatus(statusText, false);
          if (runBtn) runBtn.disabled = true;
          updateGlobalBusy(true);
          if (!stopInFlight) {
            if (triage && typeof triage.setToolbarStopState === 'function') {
              triage.setToolbarStopState({
                enabled: true,
                visible: true,
                label: '⏹ Stop AI Scan',
              });
            }
          }
          updateGlobalStatus(statusText, skepticsRunning ? 'warn' : 'info');
          return;
        }
        if (data.status === 'completed') {
          closeActiveStream('completed via status');
          setStatus('Completed', false);
          if (runBtn) runBtn.disabled = false;
          updateToolbarStop(false);
          updateGlobalBusy(false);
          maybeRefreshSectionAfterCompletion();
          clearPolling();
          updateGlobalStatus('AI analysis completed', 'success');
          return;
        }
        if (data.status === 'failed' || data.status === 'stopped') {
          observedActiveRun = false;
          closeActiveStream(`${data.status} via status`);
          const detail = (data.error || (data.status === 'stopped' ? 'Stopped' : 'Failed')).toString();
          setStatus(detail, true);
          if (runBtn) runBtn.disabled = false;
          updateToolbarStop(false);
          updateGlobalBusy(false);
          clearPolling();
          if (data.status === 'failed' && triage && typeof triage.appendLog === 'function' && detail) {
            triage.appendLog(`[Copilot] ${detail}`);
          }
          updateGlobalStatus(data.status === 'stopped' ? 'AI analysis stopped' : 'AI analysis failed', data.status === 'stopped' ? 'warn' : 'error');
          return;
        }

        if (data.status === 'idle' || !data.status) {
          observedActiveRun = false;
          closeActiveStream('idle via status');
          setStatus('Idle', false);
          if (runBtn) runBtn.disabled = false;
          updateToolbarStop(false);
          updateGlobalBusy(false);
          clearPolling();
          updateGlobalStatus('AI analysis idle', 'info');
        }
      } catch (err) {
        setStatus('Status check failed', true);
        clearPolling();
        if (runBtn) runBtn.disabled = false;
        updateToolbarStop(false);
        updateGlobalBusy(false);
        updateGlobalStatus('AI status check failed', 'error');
      }
    }

    if (runBtn && !runBtn.__overview_ai_bound) {
      runBtn.__overview_ai_bound = true;
      runBtn.addEventListener('click', async () => {
        runBtn.disabled = true;
        observedActiveRun = true;
        completionRefreshTriggered = false;
        if (triage && typeof triage.setToolbarStopState === 'function') {
          triage.setToolbarStopState({
            enabled: true,
            visible: true,
            label: '⏹ Stop AI Scan',
          });
        }
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
        updateGlobalBusy(true);

        try {
          // Start a Copilot streaming job; stream output into the Log panel via SSE.
          const streamUrl = `/api/analysis/copilot/stream/${encodeURIComponent(experimentId)}/${encodeURIComponent(repoName)}`;

          if (window._triage && typeof window._triage.appendLog === 'function') {
            window._triage.appendLog('[Copilot] Connecting stream...');
          }

          closeActiveStream('restarting');
          const es = new EventSource(streamUrl);
          activeStream = es;

           es.addEventListener('log', (evt) => {
            try {
              const line = JSON.parse(evt.data);
              if (window._triage && typeof window._triage.appendLog === 'function') {
                window._triage.appendLog(`[Copilot] ${line}`);
              }
            } catch (e) {
              // ignore
            }
          });

           let streamDone = false;

           es.addEventListener('done', () => {
              streamDone = true;
              closeActiveStream('completed');
              setStatus('Completed', false);
               runBtn.disabled = false;
               updateToolbarStop(false);
               updateGlobalBusy(false);
             clearPolling();
            maybeRefreshSectionAfterCompletion();
          });

           es.addEventListener('error', async () => {
              // EventSource fires 'error' for disconnects, and may also fire when the server closes the stream.
              // If we've already seen a 'done' event, ignore this.
              if (streamDone) {
                closeActiveStream('completed');
                return;
              }

              // Check status first; only treat as a real error if the backend reports failed.
              try {
                const resp = await fetch(`/api/analysis/status/${encodeURIComponent(experimentId)}/${encodeURIComponent(repoName)}`);
                const st = await resp.json().catch(() => ({}));
                if (st && st.status === 'completed') {
                  streamDone = true;
                  closeActiveStream('completed');
                  setStatus('Completed', false);
                  runBtn.disabled = false;
                  updateToolbarStop(false);
                  updateGlobalBusy(false);
                  clearPolling();
                  maybeRefreshSectionAfterCompletion();
                  return;
                }
                if (st && st.status === 'failed') {
                  const detail = (st.error || 'Failed').toString();
                  if (triage && typeof triage.appendLog === 'function' && detail) {
                    triage.appendLog(`[Copilot] ${detail}`);
                  }
                  closeActiveStream('failed');
                  setStatus(detail, true);
                  runBtn.disabled = false;
                  updateToolbarStop(false);
                  updateGlobalBusy(false);
                  clearPolling();
                  return;
                }
              } catch (e) {}

              // Otherwise, fall back to polling and let the status endpoint decide whether we actually finished.
              closeActiveStream('disconnected');
              clearPolling();
              pollTimer = setInterval(pollStatus, 2000);

              setStatus('Streaming disconnected (polling...)', true);
              updateGlobalBusy(true);
            });

           setStatus('Running Copilot...', false);
           if (pollTimer) clearInterval(pollTimer);
           pollTimer = setInterval(pollStatus, 2000);
        } catch (err) {
           setStatus('Start failed', true);
           if (window._triage && typeof window._triage.appendLog === 'function') {
             window._triage.appendLog('[Copilot] Start failed due to network/runtime error');
           }
            runBtn.disabled = false;
            updateToolbarStop(false);
            updateGlobalBusy(false);
        }
      });
    }

    if (genRulesBtn) {
      genRulesBtn.disabled = false;
    }

    pollStatus();
  }

  window.initOverview = initOverview;
})();
