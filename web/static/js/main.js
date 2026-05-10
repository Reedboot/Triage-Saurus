/**
 * main.js — app entry point. Imports all modules, wires events, populates window._triage.
 */
import { state }                   from './state.js';
import {
  applyTransform, zoomIn, zoomOut, scheduleDiagramFit,
} from './diagram-zoom.js';
import {
  setLogAutoScrollEnabled, updateLogAutoScrollButton, addLogLine,
  closeEventSource,
} from './scan-stream.js';
import {
  refreshDiagram, copyDiagramSource, exportDiagramSvg, exportDiagramPNG,
  handleToggleApiOps, updateApiOpsButtonText, refetchDiagramsWithApiOpsMode,
  runArchitectureAiReview, updateArchitectureAiButton, closeArchitectureAiStream,
  _showDiagramNotReady,
} from './diagram-actions.js';
import {
  showSectionsView, showLogView, getCurrentRepoName,
  buildSectionTabs, loadSectionContent, activateSectionKey,
} from './sections.js';
import {
  handleScanSubmit, handleReset, checkForRunningScan,
} from './scan-control.js';

// ── Populate window._triage bridge (used by findings.js, overview.js, etc.) ──

window._triage = window._triage || {};

window._triage.setStatus = (message, type) => {
  if (!state.statusText) return;
  state.statusText.textContent = message;
  state.statusText.className   = 'status-text';
  if (type === 'error')   state.statusText.classList.add('error');
  else if (type === 'warn')    state.statusText.classList.add('warn');
  else if (type === 'success') state.statusText.classList.add('success');
};

window._triage.appendLog = (text) => {
  if (state.logOutput) addLogLine(text, 'info');
};

window._triage.showLog = () => {
  showLogView();
  if (state.logOutput) state.logOutput.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
};

window._triage.showRawLog = () => window._triage.showLog();

window._triage.loadSectionContent = (key, experimentId, repoName) => {
  const tabBar = document.getElementById('section-tab-bar');
  if (tabBar) {
    tabBar.querySelectorAll('.section-tab-btn').forEach(b => {
      b.classList.toggle('active', b.dataset.key === key);
    });
  }
  return loadSectionContent(key, experimentId, repoName);
};

window._triage.activateSectionKey = (key, experimentId, repoName) =>
  activateSectionKey(key, experimentId, repoName);

window._triage.setStatusBusy = (isBusy) => {
  if (state.spinner) state.spinner.style.display = isBusy ? 'block' : 'none';
};

window._triage.setToolbarStopState = (opts) => {
  const btn = document.getElementById('stop-ai-toolbar-btn');
  if (!btn) return;
  if (opts?.label) btn.textContent = opts.label;
  btn.disabled = !(opts?.enabled);
  btn.hidden   = !(opts?.visible);
};

let _toolbarStopCallback = null;
window._triage.registerToolbarStop = (callback) => {
  _toolbarStopCallback = callback;
  const btn = document.getElementById('stop-ai-toolbar-btn');
  if (btn && !btn.__stopBound) {
    btn.__stopBound = true;
    btn.addEventListener('click', () => { if (_toolbarStopCallback) _toolbarStopCallback(); });
  }
};

// ── DOM ready init ────────────────────────────────────────────────────────────

function init() {
  // Grab shared DOM refs into state
  state.statusBar     = document.getElementById('status-bar');
  state.statusText    = document.getElementById('status-text');
  state.spinner       = document.getElementById('spinner');
  state.logOutput     = document.getElementById('log-output');
  state.scanBtn       = document.getElementById('scan-btn');
  state.logAutoScrollBtn = document.getElementById('toggle-log-autoscroll-btn');
  state.architectureAiBtn = document.getElementById('architecture-run-ai-btn');

  updateLogAutoScrollButton();
  updateArchitectureAiButton(false);

  // Scan form
  const scanForm = document.getElementById('scan-form');
  if (scanForm) scanForm.addEventListener('submit', handleScanSubmit);

  // Auto-scroll toggle
  if (state.logAutoScrollBtn) {
    state.logAutoScrollBtn.addEventListener('click', () => {
      const next = !state.logAutoScrollEnabled;
      setLogAutoScrollEnabled(next, next);
    });
  }

  // Sections ↔ log toggle
  const toggleSectionsBtn = document.getElementById('toggle-sections-btn');
  if (toggleSectionsBtn) {
    toggleSectionsBtn.addEventListener('click', () => {
      const tabBar      = document.getElementById('section-tab-bar');
      const panelContent = document.getElementById('section-panel-content');
      const logOut      = document.getElementById('log-output');
      if (!tabBar || !panelContent || !logOut) return;
      const sectionsVisible = window.getComputedStyle(tabBar).display !== 'none';
      if (sectionsVisible) {
        showLogView();
      } else {
        tabBar.style.display   = 'flex';
        panelContent.style.display = '';
        logOut.style.display   = 'none';
        toggleSectionsBtn.title       = 'Show log';
        toggleSectionsBtn.textContent = '📜 Log';
      }
    });
  }

  // Log panel hide/show
  const toggleLogBtn = document.getElementById('toggle-log-btn');
  if (toggleLogBtn) {
    toggleLogBtn.addEventListener('click', () => {
      const logPanel  = document.getElementById('log-panel');
      const workspace = document.querySelector('.workspace');
      if (!logPanel) return;
      const isHidden = logPanel.style.display === 'none';
      logPanel.style.display = isHidden ? '' : 'none';
      if (workspace) workspace.classList.toggle('collapsed', !isHidden);
      toggleLogBtn.textContent = isHidden ? '📜 Hide scan' : '📜 Show scan';
      toggleLogBtn.title       = isHidden ? 'Hide scan output' : 'Show scan output';
    });
  }

  // Diagram panel hide/show
  const toggleDiagramBtn = document.getElementById('toggle-diagram-btn-persistent');
  if (toggleDiagramBtn) {
    toggleDiagramBtn.addEventListener('click', () => {
      const diagramPanel = document.getElementById('diagram-panel');
      const workspace    = document.querySelector('.workspace');
      if (!diagramPanel || !workspace) return;
      const isHidden = diagramPanel.style.display === 'none';
      diagramPanel.style.display = isHidden ? '' : 'none';
      workspace.classList.toggle('diagram-hidden', !isHidden);
      toggleDiagramBtn.textContent = isHidden ? 'Hide diagram' : 'Show diagram';
      toggleDiagramBtn.title       = isHidden ? 'Hide/show architecture diagram' : 'Show architecture diagram';
    });
  }

  // Zoom controls
  document.getElementById('zoom-in-btn') ?.addEventListener('click', zoomIn);
  document.getElementById('zoom-out-btn')?.addEventListener('click', zoomOut);
  document.getElementById('zoom-reset-btn')?.addEventListener('click', () => scheduleDiagramFit());

  // Diagram toolbar buttons
  document.getElementById('refresh-diagram-btn')    ?.addEventListener('click', refreshDiagram);
  document.getElementById('copy-diagram-btn')        ?.addEventListener('click', copyDiagramSource);
  document.getElementById('export-diagram-svg-btn')  ?.addEventListener('click', exportDiagramSvg);
  document.getElementById('export-diagram-png-btn')  ?.addEventListener('click', exportDiagramPNG);
  document.getElementById('toggle-api-ops-btn')      ?.addEventListener('click', handleToggleApiOps);
  document.getElementById('architecture-run-ai-btn') ?.addEventListener('click', runArchitectureAiReview);

  // Retry button (delegated — injected dynamically by _showDiagramNotReady)
  document.getElementById('diagram-panel')?.addEventListener('click', e => {
    if (e.target.closest('.retry-diagram-btn')) refetchDiagramsWithApiOpsMode(true);
  });

  updateApiOpsButtonText();

  // Attach zoom-reset to dynamically-added diagram tab buttons
  const diagramTabs = document.getElementById('diagram-tabs');
  if (diagramTabs) {
    new MutationObserver(() => {
      diagramTabs.querySelectorAll('button').forEach(btn => {
        if (!btn.__zoomListenerAttached) {
          btn.__zoomListenerAttached = true;
          btn.addEventListener('click', () => scheduleDiagramFit());
        }
      });
    }).observe(diagramTabs, { childList: true });
  }

  // Repo select
  const repoSelect = document.getElementById('repo-select');
  if (repoSelect) {
    repoSelect.addEventListener('change', function () {
      if (!this.value) return;
      closeArchitectureAiStream();
      closeEventSource();
      if (state.currentPollInterval) { clearInterval(state.currentPollInterval); state.currentPollInterval = null; }
      if (state.logOutput) {
        state.logOutput.innerHTML = '<span class="s-inline-0e3800">Scan output will appear here…</span>';
        state.logOutput.scrollTop = 0;
      }
      setLogAutoScrollEnabled(true, false);
      if (state.statusBar) state.statusBar.style.display = 'none';
      if (state.spinner)   state.spinner.style.display   = 'none';
      window._triage.setStatus('Ready', '');

      const tabBar = document.getElementById('section-tab-bar');
      if (tabBar) tabBar.innerHTML = '<span id="tab-bar-placeholder" style="padding:8px 14px;font-size:0.75rem;color:var(--text-faint)">Run or load a scan to see sections</span>';
      const panelContent = document.getElementById('section-panel-content');
      if (panelContent) panelContent.innerHTML = '<div class="empty-state" id="section-placeholder"><svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 7h18M3 12h18M3 17h18"/></svg><p>Run or load a scan to view<br/>section details.</p></div>';

      const diagramViews = document.getElementById('diagram-views');
      if (diagramViews) {
        diagramViews.innerHTML = '<div class="empty-state"><svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="8"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg><p>Run or load a scan to view<br/>architecture diagrams.</p></div>';
        import('./diagram-render.js').then(m => m.setDiagramPlaceholderVisible(true));
      }
      const dt = document.getElementById('diagram-tabs');
      if (dt) dt.innerHTML = '';

      const repoOpt = this.querySelector('option:checked');
      const repoName = (repoOpt?.dataset?.name) || this.value.split('/').pop();
      fetch(`/api/scans/${encodeURIComponent(repoName)}`)
        .then(r => r.json())
        .then(data => {
          const scans = Array.isArray(data.scans) ? data.scans : [];

          const pastSelect = document.getElementById('past-scan-select');
          if (pastSelect) {
            pastSelect.innerHTML = '<option value="" disabled selected>— select a past scan —</option>';
            scans.forEach(s => {
              const opt = document.createElement('option');
              opt.value = s.experiment_id;
              opt.textContent = `#${s.experiment_id} — ${s.scanned_at ? new Date(s.scanned_at).toLocaleString() : s.experiment_id}`;
              pastSelect.appendChild(opt);
            });
          }

          ['compare-from-select', 'compare-to-select'].forEach(id => {
            const sel = document.getElementById(id);
            if (!sel) return;
            sel.innerHTML = '<option value="" disabled selected>— select a scan —</option>';
            scans.forEach(s => {
              const opt = document.createElement('option');
              opt.value = s.experiment_id;
              opt.textContent = `#${s.experiment_id} — ${s.scanned_at ? new Date(s.scanned_at).toLocaleString() : s.experiment_id}`;
              sel.appendChild(opt);
            });
          });

          document.getElementById('past-scans-row')?.classList.toggle('visible', scans.length > 0);

          if (pastSelect && scans.length > 0) {
            const targetExpId = window._urlParamTargetExp;
            let chosenScan = targetExpId ? scans.find(s => s.experiment_id === targetExpId) : null;
            if (targetExpId && !chosenScan) {
              buildSectionTabs(targetExpId, repoName);
              window._urlParamTargetExp = null;
              return;
            }
            window._urlParamTargetExp = null;
            if (!chosenScan) {
              chosenScan = scans.reduce((latest, cur) =>
                new Date(cur.scanned_at) > new Date(latest.scanned_at) ? cur : latest
              );
            }
            pastSelect.value = chosenScan.experiment_id;
            pastSelect.dispatchEvent(new Event('change'));
          }
        })
        .catch(() => {});

      checkForRunningScan(this.value);
    });

    if (repoSelect.value) checkForRunningScan(repoSelect.value);
  }

  // URL params (?experiment= & ?repo=)
  (function handleUrlParams() {
    const params = new URLSearchParams(window.location.search);
    const expId  = params.get('experiment');
    const repoP  = params.get('repo');
    if (!expId && !repoP) return;
    window.history.replaceState({}, '', window.location.pathname);
    if (expId) window._urlParamTargetExp = expId;

    const trySelectRepoOption = (repoPath, repoName) => {
      if (!repoSelect) return false;
      const normalizedPath = String(repoPath || '').trim();
      const pathBaseName = normalizedPath ? normalizedPath.split('/').filter(Boolean).pop() : '';
      const normalizedName = String(repoName || '').trim().toLowerCase();

      let targetOpt = null;
      if (normalizedPath) {
        targetOpt = Array.from(repoSelect.options).find(o => o.value === normalizedPath) || null;
      }
      if (!targetOpt && pathBaseName) {
        targetOpt = Array.from(repoSelect.options).find(o => {
          const optBaseName = String(o.value || '').split('/').filter(Boolean).pop();
          return optBaseName === pathBaseName;
        }) || null;
      }
      if (!targetOpt && normalizedName) {
        targetOpt = Array.from(repoSelect.options).find(o => ((o.dataset && o.dataset.name) || '').toLowerCase() === normalizedName) || null;
      }
      if (!targetOpt) return false;
      repoSelect.value = targetOpt.value;
      repoSelect.dispatchEvent(new Event('change'));
      return true;
    };

    const resolveExperimentRepoAndLoad = () => {
      if (!expId) return;
      fetch(`/api/experiment/${encodeURIComponent(expId)}/repo`)
        .then(r => r.json())
        .then(data => {
          const resolvedRepoPath = (data && data.repo_path) ? String(data.repo_path).trim() : '';
          const resolvedRepoName = (data && data.repo_name) ? String(data.repo_name).trim() : '';

          if (trySelectRepoOption(resolvedRepoPath, resolvedRepoName)) return;

          if (resolvedRepoName) {
            state.currentExperimentId = expId;
            buildSectionTabs(expId, resolvedRepoName);
            return;
          }

          setTimeout(() => {
            const ps = document.getElementById('past-scan-select');
            if (ps) {
              const opt = Array.from(ps.options).find(o => o.value === expId);
              if (opt) { ps.value = expId; ps.dispatchEvent(new Event('change')); }
            }
            window._urlParamTargetExp = null;
          }, 500);
        })
        .catch(() => {
          setTimeout(() => {
            const ps = document.getElementById('past-scan-select');
            if (ps) {
              const opt = Array.from(ps.options).find(o => o.value === expId);
              if (opt) { ps.value = expId; ps.dispatchEvent(new Event('change')); }
            }
            window._urlParamTargetExp = null;
          }, 500);
        });
    };

    if (repoP && repoSelect) {
      if (!trySelectRepoOption(repoP, '')) {
        resolveExperimentRepoAndLoad();
      }
    } else if (expId && !repoP) {
      resolveExperimentRepoAndLoad();
    }
  })();

  // Past-scan select
  const pastScanSelect = document.getElementById('past-scan-select');
  if (pastScanSelect) {
    pastScanSelect.addEventListener('change', function () {
      const experimentId = this.value;
      const repoName     = getCurrentRepoName();
      if (experimentId && repoName) {
        state.currentExperimentId = experimentId;
        buildSectionTabs(experimentId, repoName);
      }
    });
  }

  // Initial status + log view
  if (state.statusText) state.statusText.textContent = 'Ready';
  if (state.statusBar)  state.statusBar.style.display = 'none';
  showLogView();

  // Log auto-scroll on manual scroll
  if (state.logOutput && !state.logOutput.__autoScrollBound) {
    state.logOutput.__autoScrollBound = true;
    const logAutoScrollThreshold = 24;
    const updateLogAutoScroll = () => {
      const distFromBottom = state.logOutput.scrollHeight - state.logOutput.scrollTop - state.logOutput.clientHeight;
      const atBottom = distFromBottom <= logAutoScrollThreshold;
      if (atBottom !== state.logAutoScrollEnabled) setLogAutoScrollEnabled(atBottom, false);
    };
    state.logOutput.addEventListener('scroll', updateLogAutoScroll);
    updateLogAutoScroll();
  }
}

// Kick off when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
