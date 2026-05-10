/**
 * scan-control.js — scan form submit, modal, start/stop/reconnect, poll.
 */
import { state }                   from './state.js';
import { closeEventSource, addLogLine, clearLog, setLogAutoScrollEnabled } from './scan-stream.js';
import { closeArchitectureAiStream }                                       from './diagram-actions.js';
import { buildSectionTabs, getCurrentRepoName, showLogView }               from './sections.js';
import { renderDiagrams }                                                   from './diagram-render.js';

// ── Helpers ───────────────────────────────────────────────────────────────────

function setScanButtonsVisible(visible) {
  const diagramBtn = document.getElementById('toggle-diagram-btn-persistent');
  const logBtn     = document.getElementById('toggle-log-btn');
  if (diagramBtn) diagramBtn.style.visibility = visible ? 'visible' : 'hidden';
  if (logBtn)     logBtn.style.visibility     = visible ? 'visible' : 'hidden';
}

// ── Form submit ───────────────────────────────────────────────────────────────

export function handleScanSubmit(e) {
  e.preventDefault();
  const form = document.getElementById('scan-form');
  if (!form) return;

  const repoPath = (document.getElementById('repo-select')?.value || '').trim();
  if (!repoPath) { window._triage.setStatus('Please select a repository', 'error'); return; }

  const repoSelect = document.getElementById('repo-select');
  const repoOpt    = repoSelect?.querySelector('option:checked');
  const repoName   = (repoOpt?.dataset?.name) || repoPath.split('/').pop();

  fetch(`/api/scans/${repoName}`)
    .then(r => r.json())
    .then(data => {
      if (data.running_experiment) {
        showScanModal(repoName, data.running_experiment, repoPath);
      } else {
        startScan(repoPath);
      }
    })
    .catch(() => startScan(repoPath));
}

// ── Modal ─────────────────────────────────────────────────────────────────────

export function showScanModal(repoName, experimentId, repoPath) {
  const message = `Experiment ${experimentId} is currently running for ${repoName}.`;

  // Use Alpine store if available (preferred), fall back to direct DOM
  if (window.triagePipeline?.showModal) {
    window.triagePipeline.showModal(
      message,
      () => {
        closeEventSource();
        if (state.logOutput?.innerHTML.includes('Scan output will appear here')) state.logOutput.innerHTML = '';
        checkForRunningScan(repoPath);
      },
      () => startScan(repoPath)
    );
    return;
  }

  // Fallback (Alpine not yet loaded)
  const modal    = document.getElementById('scan-modal');
  const modalMsg = document.getElementById('modal-message');
  if (!modal) return;
  modalMsg.textContent = message;
  modal.style.display  = 'flex';
  document.getElementById('modal-watch').onclick  = () => {
    modal.style.display = 'none';
    closeEventSource();
    if (state.logOutput?.innerHTML.includes('Scan output will appear here')) state.logOutput.innerHTML = '';
    checkForRunningScan(repoPath);
  };
  document.getElementById('modal-new').onclick    = () => { modal.style.display = 'none'; startScan(repoPath); };
  document.getElementById('modal-cancel').onclick = () => { modal.style.display = 'none'; };
}

// ── Start scan ────────────────────────────────────────────────────────────────

export function startScan(repoPath) {
  closeArchitectureAiStream();
  closeEventSource();
  clearLog();
  showLogView();

  if (state.statusBar) state.statusBar.style.display = 'flex';
  if (state.spinner)   state.spinner.style.display   = 'block';
  window._triage.setStatus('Connecting to scan stream…', '');
  if (state.scanBtn) state.scanBtn.disabled = true;

  // Notify Alpine pipeline component
  window.triagePipeline?.onScanStart?.();
  setScanButtonsVisible(false);

  const repoBaseName = repoPath.split('/').filter(Boolean).pop() || 'repo';
  const scanName = repoBaseName.toLowerCase().replace(/[^a-z0-9]+/g, '_') + '_scan';
  const formData = new FormData();
  formData.append('repo_path', repoPath);
  formData.append('scan_name', scanName);

  fetch('/scan', { method: 'POST', body: formData })
    .then(response => {
      if (!response.ok) {
        addLogLine(`Error: HTTP ${response.status}`, 'error');
        window._triage.setStatus('Scan failed', 'error');
        if (state.scanBtn) state.scanBtn.disabled = false;
        if (state.statusBar) state.statusBar.style.display = 'none';
        if (state.spinner)   state.spinner.style.display   = 'none';
        return;
      }

      addLogLine('[Info] 🔌 Connected to scan stream', 'info');

      if (!response.body) {
        addLogLine('Error: Response body not available', 'error');
        if (state.scanBtn) state.scanBtn.disabled = false;
        return;
      }

      const reader  = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer       = '';
      let currentEvent = null;
      let chunkCount   = 0;

      function processChunk() {
        reader.read().then(({ done, value }) => {
          if (done) {
            addLogLine('[Info] 🔒 Stream closed', 'info');
            window._triage.setStatus('Scan complete', 'success');
            window.triagePipeline?.onScanComplete?.();
            setScanButtonsVisible(true);
            if (state.scanBtn) state.scanBtn.disabled = false;
            if (state.spinner) state.spinner.style.display = 'none';
            return;
          }

          chunkCount++;
          if (chunkCount === 1) addLogLine('[Info] 📡 Receiving data...', 'info');

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop();

          lines.forEach(line => {
            if (line === '') { currentEvent = null; return; }
            if (line.startsWith('event: ')) { currentEvent = line.slice(7).trim(); return; }
            if (line.startsWith('data: ')) {
              const dataStr = line.slice(6);
              try {
                const message = JSON.parse(dataStr);
                if (typeof message === 'string') {
                  addLogLine(message, currentEvent || '');
                } else if (message && typeof message === 'object') {
                  if (message.message) addLogLine(message.message, message.level || currentEvent || '');
                  if (currentEvent === 'done') {
                    window._triage.setStatus('Scan complete', 'success');
                    window.triagePipeline?.onScanComplete?.();
                    setScanButtonsVisible(true);
                    const expId = message.experiment_id || state.currentExperimentId;
                    if (expId) buildSectionTabs(expId, getCurrentRepoName());
                  }
                }
                if (currentEvent === 'experiment' && typeof message === 'string' && message) {
                  state.currentExperimentId = message;
                }
                if (currentEvent === 'diagrams' && Array.isArray(message)) {
                  renderDiagrams(message);
                }
                if (message?.status) window._triage.setStatus(message.status, '');
              } catch (_) {
                addLogLine(dataStr, currentEvent || '');
              }
              currentEvent = null;
            }
          });

          processChunk();
        });
      }

      window._triage.setStatus('Scan running…', '');
      processChunk();
    })
    .catch(error => {
      addLogLine(`Connection error: ${error.message}`, 'error');
      window._triage.setStatus('Connection failed', 'error');
      if (state.scanBtn) state.scanBtn.disabled = false;
      if (state.statusBar) state.statusBar.style.display = 'none';
      if (state.spinner)   state.spinner.style.display   = 'none';
    });
}

// ── Reset ─────────────────────────────────────────────────────────────────────

export function handleReset() {
  closeArchitectureAiStream();
  closeEventSource();
  if (state.currentPollInterval) { clearInterval(state.currentPollInterval); state.currentPollInterval = null; }
  clearLog();
  state.currentExperimentId = null;
  if (state.statusBar) state.statusBar.style.display = 'none';
  if (state.spinner)   state.spinner.style.display   = 'none';
  if (state.logOutput) {
    state.logOutput.innerHTML = '<span class="s-inline-0e3800">Scan output will appear here…</span>';
    state.logOutput.scrollTop = 0;
  }
  setLogAutoScrollEnabled(true, false);
  const tabBar = document.getElementById('section-tab-bar');
  if (tabBar) tabBar.innerHTML = '<span id="tab-bar-placeholder" style="padding:8px 14px;font-size:0.75rem;color:var(--text-faint)">Run or load a scan to see sections</span>';
  const panelContent = document.getElementById('section-panel-content');
  if (panelContent) panelContent.innerHTML = '<div class="empty-state" id="section-placeholder"><svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 7h18M3 12h18M3 17h18"/></svg><p>Run or load a scan to view<br/>section details.</p></div>';
  showLogView();
  window._triage.setStatus('Ready', '');
}

// ── Check for running scan ────────────────────────────────────────────────────

export function checkForRunningScan(repoPath) {
  if (!repoPath) return;
  const repoSelect = document.getElementById('repo-select');
  const repoOpt    = repoSelect?.querySelector('option:checked');
  const repoName   = (repoOpt?.dataset?.name) || repoPath.split('/').pop();

  fetch(`/api/scans/${repoName}`)
    .then(r => r.json())
    .then(data => {
      if (data.running_experiment) {
        closeEventSource();
        if (state.logOutput?.innerHTML.includes('Scan output will appear here')) {
          state.logOutput.innerHTML = '';
          state.logOutput.scrollTop = 0;
        }
        setLogAutoScrollEnabled(true, false);
        document.getElementById('section-placeholder')?.style.setProperty('display', 'none');
        addLogLine(`[Info] 🔄 Reconnecting to running experiment ${data.running_experiment}...`, 'info');
        if (state.statusBar) state.statusBar.style.display = 'flex';
        if (state.spinner)   state.spinner.style.display   = 'block';
        window._triage.setStatus(`Reconnecting to experiment ${data.running_experiment}...`, '');
        reconnectToRunningExperiment(repoPath, data.running_experiment, data.running_experiment_created_at);
      }
    })
    .catch(() => {});
}

// ── Reconnect ─────────────────────────────────────────────────────────────────

export function reconnectToRunningExperiment(repoPath, experimentId, createdAt) {
  const repoSelect = document.getElementById('repo-select');
  const repoOpt    = repoSelect?.querySelector('option:checked');
  const repoName   = (repoOpt?.dataset?.name) || repoPath.split('/').pop();

  if (state.currentPollInterval) { clearInterval(state.currentPollInterval); state.currentPollInterval = null; }

  addLogLine('[Info] ⏳ Scan already in progress on server', 'info');
  addLogLine(`[Info] 🧪 Experiment ID: ${experimentId}`, 'info');

  fetch(`/api/scan_log/${encodeURIComponent(repoName)}`)
    .then(r => r.json())
    .then(data => {
      const lines = Array.isArray(data.lines) ? data.lines : [];
      if (lines.length > 0) {
        addLogLine('[Info] 📜 --- Log from scan start ---', 'info');
        lines.forEach(line => addLogLine(line, ''));
        addLogLine('[Info] 📜 --- End of historical log ---', 'info');
      }
    })
    .catch(() => {})
    .finally(() => {
      addLogLine('[Info] ⏳ Waiting for scan to complete...', 'info');
      addLogLine('[Info] 📡 Polling server every 5 seconds...', 'info');
      _startReconnectPoll(repoName, experimentId, createdAt);
    });

  window._triage.setStatus('Scan in progress…', '');
  if (state.statusBar) state.statusBar.style.display = 'flex';
}

export function _startReconnectPoll(repoName, experimentId, createdAt) {
  let pollCount = 0;
  const startTime = createdAt ? new Date(createdAt) : new Date();

  if (createdAt) {
    const elapsed = Math.floor((Date.now() - startTime) / 1000);
    const m = Math.floor(elapsed / 60), s = elapsed % 60;
    addLogLine(
      m > 0
        ? `[Web] Scan in progress — elapsed ${m}m ${s}s (est. 4-5 min remaining)`
        : `[Web] Scan in progress — elapsed ${elapsed}s (est. 4-5 min remaining)`,
      'info'
    );
  }

  state.currentPollInterval = setInterval(() => {
    pollCount++;
    const elapsed = Math.floor((Date.now() - startTime) / 1000);
    const m = Math.floor(elapsed / 60), s = elapsed % 60;

    if (elapsed > 420) {
      clearInterval(state.currentPollInterval);
      state.currentPollInterval = null;
      addLogLine('[Error] ❌ Scan appears to have stalled or crashed (no completion after 7+ minutes)', 'error');
      addLogLine('[Error] ⚠️ Lock file may be stale. You can try starting a new scan.', 'error');
      window._triage.setStatus('Scan timeout', 'error');
      if (state.spinner) state.spinner.style.display = 'none';
      if (state.scanBtn) state.scanBtn.disabled = false;
      return;
    }

    fetch(`/api/scans/${repoName}`)
      .then(r => r.json())
      .then(data => {
        if (!data.running_experiment) {
          clearInterval(state.currentPollInterval);
          state.currentPollInterval = null;
          addLogLine('[Info] ✅ Scan complete!', 'info');
          window._triage.setStatus('Scan complete', 'success');
          if (state.spinner) state.spinner.style.display = 'none';
          if (state.scanBtn) state.scanBtn.disabled = false;
          addLogLine(m > 0 ? `[Info] ⏱️ Total time: ${m}m ${s}s` : `[Info] ⏱️ Total time: ${elapsed}s`, 'info');
          const expId = state.currentExperimentId || experimentId;
          if (expId) buildSectionTabs(expId, repoName);
        } else if (pollCount % 6 === 0) {
          addLogLine(
            m > 0
              ? `[Web] Scan in progress — elapsed ${m}m ${s}s (est. 4-5 min remaining)`
              : `[Web] Scan in progress — elapsed ${elapsed}s (est. 4-5 min remaining)`,
            'info'
          );
        }
      })
      .catch(() => {});
  }, 5000);

  if (state.spinner) state.spinner.style.display = 'none';
}
