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

function handleScanStageEvent(message) {
  if (!message || typeof message !== 'object') return;
  window.triagePipeline?.onScanStage?.(message);
  if (message.state === 'failed') {
    window._triage.setStatus(message.label || 'Scan failed', 'error');
  }
}

// ── Form submit ───────────────────────────────────────────────────────────────

export function handleScanSubmit(e) {
  e.preventDefault();
  const form = document.getElementById('scan-form');
  if (!form) return;

  const repoPath = (document.getElementById('repo-select')?.value || '').trim();
  if (!repoPath) { window._triage.setStatus('Choose a repo to run a scan', 'error'); return; }

  const repoSelect = document.getElementById('repo-select');
  const repoOpt    = repoSelect?.querySelector('option:checked');
  const repoName   = (repoOpt?.dataset?.name) || repoPath.split('/').pop();

  fetch(`/api/scans/${repoName}`)
    .then(r => r.json())
    .then(data => {
      if (data.running_experiment) {
        showScanModal(repoName, data.running_experiment, repoPath);
      } else {
        // Check for modules before starting scan
        detectAndPromptForModules(repoPath);
      }
    })
    .catch(() => detectAndPromptForModules(repoPath));
}

// ── Module detection ───────────────────────────────────────────────────────────

export function detectAndPromptForModules(repoPath) {
  // Show status while detecting modules
  window._triage.setStatus('Detecting external modules…', '');

  fetch('/api/detect-modules', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ repo_path: repoPath })
  })
    .then(r => r.json())
    .then(data => {
      if (data.error) {
        window._triage.setStatus(`Module detection failed: ${data.error}`, 'error');
        // Continue with scan anyway
        startScan(repoPath);
        return;
      }

      // If modules detected, only prompt for ones that are not already scanned.
      // Re-prompting with "already scanned" modules is redundant and interrupts flow.
      if (data.modules && data.modules.length > 0) {
        const modulesNeedingScan = data.modules.filter(m => !m.already_scanned);
        if (modulesNeedingScan.length === 0) {
          window._triage.setStatus('All external modules already scanned', '');
          startScan(repoPath);
          return;
        }
        showModuleModal(repoPath, modulesNeedingScan);
      } else {
        window._triage.setStatus('No external modules detected', '');
        startScan(repoPath);
      }
    })
    .catch(err => {
      console.warn('Module detection error:', err);
      // On error, continue with scan anyway
      window._triage.setStatus('Proceeding without module detection', '');
      startScan(repoPath);
    });
}

export function showModuleModal(repoPath, modules, onScanSelected = null, onSkip = null) {
  // Use Alpine store if available
  if (window.triagePipeline?.showModuleModal) {
    window.triagePipeline.showModuleModal(
      modules,
      (selectedModules) => {
        if (selectedModules.length > 0) {
          if (onScanSelected) {
            onScanSelected(selectedModules);
          } else {
            scanModulesThenProceed(repoPath, selectedModules);
          }
        } else {
          if (onSkip) onSkip();
          else startScan(repoPath);
        }
      },
      () => {
        if (onSkip) onSkip();
        else startScan(repoPath);
      } // Skip modules
    );
    return;
  }

  // Fallback (Alpine not yet loaded)
  if (onSkip) onSkip();
  else startScan(repoPath);
}

export function scanModulesThenProceed(repoPath, selectedModules) {
  // Validate user-provided paths
  const modulesToScan = [];
  const missingPaths = [];
  
  for (const mod of selectedModules) {
    const resolvedPath = mod.module_repo_path || (mod.userProvidedPath && mod.userProvidedPath.trim());
    
    if (!resolvedPath) {
      missingPaths.push(mod.name);
    } else {
      modulesToScan.push({
        name: mod.name,
        module_repo_name: mod.module_repo_name,
        module_repo_path: resolvedPath,
        source: mod.source,
        inferred_type: mod.inferred_type
      });
    }
  }
  
  if (missingPaths.length > 0) {
    window._triage.setStatus(`Error: Missing paths for modules: ${missingPaths.join(', ')}`, 'error');
    return;
  }
  
  console.log('Selected modules to scan:', modulesToScan);
  
  // Scan each selected module first, then the original repo
  scanModulesSequentially(modulesToScan, repoPath);
}

function registerModuleScan(module, experimentId) {
  if (!module?.module_repo_path || !module?.source || !experimentId) {
    return Promise.resolve(false);
  }

  return fetch('/api/modules/register-scan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      module_repo_path: module.module_repo_path,
      module_source: module.source,
      experiment_id: String(experimentId),
    }),
  })
    .then(r => r.json().catch(() => ({})).then(payload => ({ ok: r.ok, payload })))
    .then(({ ok, payload }) => {
      if (!ok || !payload?.ok) {
        const msg = payload?.error || 'Module registry update failed';
        addLogLine(`[Warn] ⚠️ ${msg}`, 'warn');
        return false;
      }
      addLogLine(`[Info] 📦 Registered module in registry: ${module.name}`, 'info');
      return true;
    })
    .catch(err => {
      addLogLine(`[Warn] ⚠️ Failed to register module scan: ${err.message}`, 'warn');
      return false;
    });
}

export function scanModulesSequentially(modulesToScan, originalRepoPath, index = 0, onComplete = null) {
  if (index >= modulesToScan.length) {
    // All modules in this sequence scanned
    if (onComplete) {
      // If there's a callback (nested module scenario), call it
      onComplete();
    } else {
      // Otherwise scan the original repo
      window._triage.setStatus(`All modules scanned. Now scanning original repo…`, '');
      setTimeout(() => {
        startScan(originalRepoPath);
      }, 1000);
    }
    return;
  }
  
  const module = modulesToScan[index];
  const nextIndex = index + 1;
  const totalModules = modulesToScan.length;
  
  window._triage.setStatus(`Scanning module ${nextIndex} of ${totalModules}: ${module.name}…`, '');
  console.log(`Starting scan for module: ${module.name} at ${module.module_repo_path}`);
  
  closeArchitectureAiStream();
  closeEventSource();
  clearLog();
  showLogView();

  if (state.statusBar) state.statusBar.style.display = 'flex';
  if (state.spinner)   state.spinner.style.display   = 'block';
  if (state.scanBtn) state.scanBtn.disabled = true;

  window.triagePipeline?.onScanStart?.();
  setScanButtonsVisible(false);

  const moduleBaseName = module.module_repo_path.split('/').filter(Boolean).pop() || module.name;
  const scanName = moduleBaseName.toLowerCase().replace(/[^a-z0-9]+/g, '_') + '_module_scan';
  const formData = new FormData();
  formData.append('repo_path', module.module_repo_path);
  formData.append('scan_name', scanName);

  fetch('/scan', { method: 'POST', body: formData })
    .then(response => {
      if (!response.ok) {
        addLogLine(`Error: HTTP ${response.status}`, 'error');
        window._triage.setStatus(`Failed to scan module: ${module.name}`, 'error');
        if (state.scanBtn) state.scanBtn.disabled = false;
        if (state.statusBar) state.statusBar.style.display = 'none';
        if (state.spinner)   state.spinner.style.display   = 'none';
        // Continue with next module anyway
        setTimeout(() => scanModulesSequentially(modulesToScan, originalRepoPath, nextIndex), 1000);
        return;
      }

      addLogLine('[Info] 🔌 Module scan pipeline connected', 'info');

      if (!response.body) {
        addLogLine('Error: Response body not available', 'error');
        if (state.scanBtn) state.scanBtn.disabled = false;
        setTimeout(() => scanModulesSequentially(modulesToScan, originalRepoPath, nextIndex), 1000);
        return;
      }

      const reader  = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer       = '';
      let currentEvent = null;
      let chunkCount   = 0;
      let sawDoneEvent = false;

      function processChunk() {
        reader.read().then(({ done, value }) => {
          if (done) {
            addLogLine(`[Info] 🔒 Module scan pipeline closed`, 'info');
            setScanButtonsVisible(true);
            if (state.scanBtn) state.scanBtn.disabled = false;
            if (state.spinner) state.spinner.style.display = 'none';
            if (!sawDoneEvent) {
              window._triage.setStatus(`Module scan for ${module.name} closed before completion`, 'warn');
            }
            // Check if this module has nested modules before proceeding to next module
            checkAndScanNestedModules(module.module_repo_path, modulesToScan, originalRepoPath, nextIndex);
            return;
          }

          chunkCount++;
          if (chunkCount === 1) addLogLine('[Info] 📡 Receiving module scan data...', 'info');

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
                    sawDoneEvent = true;
                    const isFailed = Number(message.exit_code || 0) !== 0 || message.status === 'failed';
                    if (isFailed) {
                      addLogLine(`Module scan failed with exit code: ${message.exit_code}`, 'error');
                    } else {
                      addLogLine(`Module scan completed successfully`, 'success');
                      const expId = message.experiment_id || state.currentExperimentId;
                      // Persist module-level knowledge for reuse by future repo scans.
                      registerModuleScan(module, expId);
                    }
                  }
                }
              } catch (e) {
                console.warn('Failed to parse module scan message:', e);
              }
            }
          });

          processChunk();
        });
      }

      processChunk();
    })
    .catch(err => {
      console.warn('Module scan error:', err);
      addLogLine(`Error scanning module: ${err.message}`, 'error');
      window._triage.setStatus(`Module scan failed: ${err.message}`, 'error');
      if (state.scanBtn) state.scanBtn.disabled = false;
      if (state.statusBar) state.statusBar.style.display = 'none';
      if (state.spinner) state.spinner.style.display = 'none';
      // Continue with next module anyway
      setTimeout(() => scanModulesSequentially(modulesToScan, originalRepoPath, nextIndex), 1000);
    });
}

export function checkAndScanNestedModules(modulePath, parentModules, originalRepoPath, nextIndex) {
  // Check if the scanned module has nested modules before proceeding to next module
  window._triage.setStatus('Checking for nested modules…', '');
  
  fetch('/api/detect-modules', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ repo_path: modulePath })
  })
    .then(r => r.json())
    .then(data => {
      if (data.error) {
        console.log('No nested modules detected or detection error:', data.error);
        // Continue to next module
        setTimeout(() => scanModulesSequentially(parentModules, originalRepoPath, nextIndex), 1000);
        return;
      }
      
      // If nested modules found, show modal for user to select which to scan
      if (data.modules && data.modules.length > 0) {
        const nestedModulesNeedingScan = data.modules.filter(m => !m.already_scanned);
        if (nestedModulesNeedingScan.length === 0) {
          addLogLine('[Info] All nested modules already scanned — continuing', 'info');
          setTimeout(() => scanModulesSequentially(parentModules, originalRepoPath, nextIndex), 1000);
          return;
        }
        addLogLine(`[Info] Found ${nestedModulesNeedingScan.length} nested module(s) to scan`, 'info');
        showModuleModal(modulePath, nestedModulesNeedingScan,
          (selectedNested) => {
            if (selectedNested.length > 0) {
              // Scan nested modules, then continue with parent module sequence
              scanNestedModulesThenContinue(selectedNested, modulePath, parentModules, originalRepoPath, nextIndex);
            } else {
              // Skip nested modules, continue with next parent module
              setTimeout(() => scanModulesSequentially(parentModules, originalRepoPath, nextIndex), 1000);
            }
          },
          () => {
            // User skipped nested modules, continue to next parent module
            setTimeout(() => scanModulesSequentially(parentModules, originalRepoPath, nextIndex), 1000);
          }
        );
      } else {
        // No nested modules, continue to next module in sequence
        setTimeout(() => scanModulesSequentially(parentModules, originalRepoPath, nextIndex), 1000);
      }
    })
    .catch(err => {
      console.log('Nested module detection error:', err);
      // Continue to next module anyway
      setTimeout(() => scanModulesSequentially(parentModules, originalRepoPath, nextIndex), 1000);
    });
}

export function scanNestedModulesThenContinue(nestedModules, parentModulePath, parentModules, originalRepoPath, nextParentIndex) {
  // Recursively scan nested modules, then continue with parent module sequence
  scanModulesSequentially(nestedModules, originalRepoPath, 0, () => {
    // Callback: when all nested modules done, continue with next parent module
    setTimeout(() => scanModulesSequentially(parentModules, originalRepoPath, nextParentIndex), 1000);
  });
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
      () => detectAndPromptForModules(repoPath)
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
  document.getElementById('modal-new').onclick    = () => { modal.style.display = 'none'; detectAndPromptForModules(repoPath); };
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
  window._triage.setStatus('Scan pipeline…', '');
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

      addLogLine('[Info] 🔌 Scan pipeline connected', 'info');

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
      let sawDoneEvent = false;

      function processChunk() {
        reader.read().then(({ done, value }) => {
          if (done) {
            addLogLine('[Info] 🔒 Scan pipeline closed', 'info');
            setScanButtonsVisible(true);
            if (state.scanBtn) state.scanBtn.disabled = false;
            if (state.spinner) state.spinner.style.display = 'none';
            if (!sawDoneEvent) {
              window._triage.setStatus('Scan pipeline closed before completion', 'warn');
            }
            return;
          }

          chunkCount++;
          if (chunkCount === 1) addLogLine('[Info] 📡 Receiving pipeline data...', 'info');

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
                    sawDoneEvent = true;
                    const isFailed = Number(message.exit_code || 0) !== 0 || message.status === 'failed';
                    if (isFailed) {
                      window._triage.setStatus('Scan failed', 'error');
                    } else {
                      window._triage.setStatus('Scan ready', 'success');
                      window.triagePipeline?.onScanComplete?.();
                    }
                    setScanButtonsVisible(true);
                    const expId = message.experiment_id || state.currentExperimentId;
                    if (!isFailed && expId) buildSectionTabs(expId, getCurrentRepoName());
                  }
                }
                if (currentEvent === 'experiment' && typeof message === 'string' && message) {
                  state.currentExperimentId = message;
                }
                if (currentEvent === 'scan_stage' && message && typeof message === 'object') {
                  handleScanStageEvent(message);
                }
                if (currentEvent === 'diagrams' && Array.isArray(message)) {
                  renderDiagrams(message);
                }
                if (message?.status && currentEvent !== 'scan_stage' && currentEvent !== 'done') {
                  window._triage.setStatus(message.status, '');
                }
              } catch (_) {
                addLogLine(dataStr, currentEvent || '');
              }
              currentEvent = null;
            }
          });

          processChunk();
        });
      }

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
      addLogLine('[Info] ⏳ Waiting for scan to be ready...', 'info');
      addLogLine('[Info] 📡 Polling server every 5 seconds...', 'info');
      _startReconnectPoll(repoName, experimentId, createdAt);
    });

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
          addLogLine('[Info] ✅ Scan ready!', 'info');
          window._triage.setStatus('Scan ready', 'success');
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
