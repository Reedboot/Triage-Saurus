/**
 * diagram-actions.js — export, copy, API-ops toggle, AI review, toast.
 */
import { state }          from './state.js';
import {
  applyTransform,
  initPanZoom,
  scheduleDiagramFit,
  saveDiagramState,
} from './diagram-zoom.js';
import {
  sanitizeMermaidSource,
  getActiveDiagramView,
  renderDiagrams,
} from './diagram-render.js';

// ── Toast ─────────────────────────────────────────────────────────────────────

export function showToast(message) {
  document.querySelector('.diagram-toast')?.remove();
  const toast = document.createElement('div');
  toast.className = 'diagram-toast';
  toast.textContent = message;
  toast.style.cssText = [
    'position:fixed', 'bottom:20px', 'right:20px',
    'background:#1e293b', 'color:#e2e8f0', 'padding:12px 16px',
    'border-radius:6px', 'border:1px solid #475569', 'z-index:1000',
    'font-size:14px', 'box-shadow:0 4px 12px rgba(0,0,0,0.3)',
    'animation:slideIn 0.3s ease-out',
  ].join(';');
  document.body.appendChild(toast);
  setTimeout(() => {
    toast.style.animation = 'fadeOut 0.3s ease-out';
    setTimeout(() => toast.remove(), 300);
  }, 2500);
}

// ── AI stream management ──────────────────────────────────────────────────────

export function updateArchitectureAiButton(isRunning) {
  if (!state.architectureAiBtn) return;
  state.architectureAiBtn.disabled = !!isRunning;
  state.architectureAiBtn.textContent = isRunning ? '⏳ Architecture AI…' : '🤖 Run AI (Architecture)';
  state.architectureAiBtn.title = isRunning
    ? 'Architecture AI is running'
    : 'Run AI against the architecture diagram and suggest code/rule changes';
}

export function closeArchitectureAiStream() {
  if (state.architectureAiStream) {
    try { state.architectureAiStream.close(); } catch (_) {}
    state.architectureAiStream = null;
  }
  state.architectureAiStopInFlight = false;
  updateArchitectureAiButton(false);
  if (typeof window._triage?.setToolbarStopState === 'function') {
    window._triage.setToolbarStopState({ enabled: false, visible: false, label: '⏹ Stop AI Scan' });
  }
  if (typeof window._triage?.setStatusBusy === 'function') {
    window._triage.setStatusBusy(false);
  }
}

// ── Diagram source helpers ────────────────────────────────────────────────────

export function getDiagramSource(diagramView) {
  if (!diagramView) return null;
  const preEl = diagramView.querySelector('pre.mermaid');
  if (!preEl) return null;
  return sanitizeMermaidSource(preEl.dataset.source || preEl.textContent || '');
}

// ── Refresh ───────────────────────────────────────────────────────────────────

export function refreshDiagram() {
  const activeDiagram = getActiveDiagramView();
  if (!activeDiagram) { showToast('No active diagram'); return; }
  const source = getDiagramSource(activeDiagram);
  if (!source) { showToast('No diagram source found'); return; }

  try {
    const preEl = activeDiagram.querySelector('pre.mermaid');
    if (!preEl) return;

    const savedZoomState = { ...state.zoomState };
    const zoomWrap = document.getElementById('diagram-zoom-wrap');
    const savedScroll = zoomWrap ? { left: zoomWrap.scrollLeft, top: zoomWrap.scrollTop } : null;

    const svg = preEl.nextElementSibling;
    if (svg && svg.tagName === 'svg') svg.remove();
    preEl.removeAttribute('data-processed');
    preEl.textContent = source;

    if (window.mermaid) {
      window.mermaid.init(undefined, preEl);
      if (savedScroll && zoomWrap) {
        setTimeout(() => {
          zoomWrap.scrollLeft = savedScroll.left;
          zoomWrap.scrollTop  = savedScroll.top;
          Object.assign(state.zoomState, savedZoomState);
          initPanZoom();
          applyTransform();
        }, 100);
      }
      showToast('Diagram refreshed');
    }
  } catch (err) {
    console.error('[Diagram] Refresh error:', err);
    showToast('Error refreshing diagram');
  }
}

// ── Copy source ───────────────────────────────────────────────────────────────

export function copyDiagramSource() {
  const activeDiagram = getActiveDiagramView();
  if (!activeDiagram) { showToast('No active diagram'); return; }
  const source = getDiagramSource(activeDiagram);
  if (!source) { showToast('No diagram source found'); return; }

  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(source)
      .then(() => showToast('Copied to clipboard'))
      .catch(() => showToast('Copy failed'));
  } else {
    try {
      const ta = document.createElement('textarea');
      ta.value = source;
      ta.style.cssText = 'position:fixed;opacity:0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      showToast('Copied to clipboard');
    } catch (_) { showToast('Copy failed'); }
  }
}

// ── Not-ready placeholder ─────────────────────────────────────────────────────

export function _showDiagramNotReady(message) {
  const placeholder  = document.getElementById('diagram-placeholder');
  const diagramViews = document.getElementById('diagram-views');
  if (placeholder) {
    placeholder.innerHTML = `
      <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
        <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
      </svg>
      <p style="color:var(--text-muted)">${message}</p>
      <button class="btn-small retry-diagram-btn" style="margin-top:8px">↺ Retry</button>`;
    placeholder.style.removeProperty('display');
  }
  if (diagramViews) Array.from(diagramViews.querySelectorAll('.diagram-view')).forEach(v => v.remove());
  const tabsEl = document.getElementById('diagram-tabs');
  if (tabsEl) tabsEl.innerHTML = '';
}

// ── API-ops mode ──────────────────────────────────────────────────────────────

export function updateApiOpsButtonText() {
  const btn = document.getElementById('toggle-api-ops-btn');
  if (!btn) return;
  const labels = { auto: 'Auto (<10)', all: 'All', hide: 'Hidden' };
  btn.textContent = `🧩 API ops: ${labels[state.apiOpsMode] || state.apiOpsMode}`;
}

export function handleToggleApiOps() {
  const cycle = { auto: 'all', all: 'hide', hide: 'auto' };
  state.apiOpsMode = cycle[state.apiOpsMode] || 'auto';
  updateApiOpsButtonText();
  refetchDiagramsWithApiOpsMode();
}

// ── Diagram fetch / render ────────────────────────────────────────────────────

export function refetchDiagramsWithApiOpsMode(forceRefresh = false) {
  const experimentId = state.currentExperimentId;
  const repoName     = state.currentRepoName || getCurrentRepoName();

  if (!experimentId || !repoName) {
    return;
  }

  const url = new URL(`/api/diagrams/${encodeURIComponent(experimentId)}`, window.location.origin);
  url.searchParams.set('repo_name', repoName);
  if (state.apiOpsMode === 'all')  url.searchParams.set('include_api_operations', '1');
  if (state.apiOpsMode === 'hide') url.searchParams.set('include_api_operations', '0');

  fetch(url.toString())
    .then(r => {
      if (!r.ok) throw Object.assign(new Error(`HTTP ${r.status}`), { status: r.status });
      return r.json();
    })
    .then(data => {
      if (data?.diagrams?.length > 0) {
        state.storedDiagrams = data.diagrams;
        renderDiagrams(data.diagrams);
      } else {
        _showDiagramNotReady('No diagram data was returned.');
      }
    })
    .catch(err => {
      console.warn('[Diagrams] Failed to fetch:', err);
      _showDiagramNotReady(
        err.status === 404
          ? 'Diagram not available yet — the scan may still be in progress.'
          : `Could not load diagram (${err.message}).`
      );
    });
}

// ── Export SVG ────────────────────────────────────────────────────────────────

export function exportDiagramSvg() {
  const activeDiagram = getActiveDiagramView();
  if (!activeDiagram) { showToast('No active diagram'); return; }
  const svgElement = activeDiagram.querySelector('svg');
  if (!svgElement) { showToast('No diagram SVG found'); return; }

  try {
    let svgString = new XMLSerializer().serializeToString(svgElement);
    svgString = svgString.replace('<svg', `<svg><rect width="100%" height="100%" fill="#0d1111"/>`);
    const blob = new Blob([svgString], { type: 'image/svg+xml' });
    const filename = `diagram_${state.currentExperimentId || 'unknown'}_${getCurrentRepoName() || 'diagram'}_${Date.now()}.svg`;
    _triggerDownload(URL.createObjectURL(blob), filename);
    URL.revokeObjectURL(URL.createObjectURL(blob));
    showToast('SVG downloaded');
  } catch (err) {
    console.error('[Diagram] SVG export error:', err);
    showToast('Error exporting SVG');
  }
}

// ── Export PNG ────────────────────────────────────────────────────────────────

export function exportDiagramPNG() {
  const activeDiagram = getActiveDiagramView();
  if (!activeDiagram) { showToast('No active diagram'); return; }
  const svg = activeDiagram.querySelector('svg');
  if (!svg) { showToast('No diagram SVG found'); return; }

  showToast('Converting to PNG...');

  try {
    const tempDiv = document.createElement('div');
    tempDiv.style.cssText = 'position:fixed;left:-9999px;top:-9999px;background:#0d1111;padding:20px';
    const clonedSvg = svg.cloneNode(true);
    tempDiv.appendChild(clonedSvg);
    document.body.appendChild(tempDiv);

    if (!window.html2canvas) {
      document.body.removeChild(tempDiv);
      showToast('html2canvas library not loaded');
      return;
    }

    window.html2canvas(tempDiv, { backgroundColor: '#0d1111', scale: 2, allowTaint: true, useCORS: true })
      .then(canvas => {
        canvas.toBlob((blob) => {
          if (!blob) { showToast('Failed to create PNG'); document.body.removeChild(tempDiv); return; }
          const filename = `diagram_${state.currentExperimentId || 'unknown'}_${getCurrentRepoName() || 'repo'}_${Date.now()}.png`;
          const url = URL.createObjectURL(blob);
          _triggerDownload(url, filename);
          URL.revokeObjectURL(url);
          document.body.removeChild(tempDiv);
          showToast('PNG downloaded: ' + filename);
        }, 'image/png');
      })
      .catch(err => {
        document.body.removeChild(tempDiv);
        console.error('[Diagram] PNG export error:', err);
        showToast('Failed to export PNG');
      });
  } catch (err) {
    console.error('[Diagram] PNG export error:', err);
    showToast('PNG export failed');
  }
}

// ── Architecture AI review ────────────────────────────────────────────────────

export function runArchitectureAiReview() {
  const experimentId = state.currentExperimentId;
  const repoName     = state.currentRepoName || getCurrentRepoName();

  if (!experimentId || !repoName || !document.querySelector('#diagram-views svg')) {
    showToast('Load a completed scan first');
    return;
  }

  closeArchitectureAiStream();
  state.architectureAiStopInFlight = false;
  updateArchitectureAiButton(true);

  window._triage?.showLog?.();
  window._triage?.setStatus?.('Running architecture AI…', 'info');
  window._triage?.setStatusBusy?.(true);
  window._triage?.appendLog?.(`[Architecture] Starting analysis for ${repoName} (experiment ${experimentId})`);
  window._triage?.setToolbarStopState?.({ enabled: true, visible: true, label: '⏹ Stop Architecture AI' });

  window._triage?.registerToolbarStop?.(async () => {
    if (state.architectureAiStopInFlight) return;
    state.architectureAiStopInFlight = true;
    window._triage?.setToolbarStopState?.({ enabled: false, visible: true, label: '⏳ Stopping Architecture AI…' });
    try {
      const resp = await fetch(
        `/api/analysis/stop/${encodeURIComponent(experimentId)}/${encodeURIComponent(repoName)}`,
        { method: 'POST' }
      );
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
      window._triage?.appendLog?.('[Architecture] Stop requested');
      streamDone = true;
      clearPolling();
      closeArchitectureAiStream();
      window._triage?.setStatus?.('Stopped', 'warn');
    } catch (err) {
      state.architectureAiStopInFlight = false;
      window._triage?.setToolbarStopState?.({ enabled: true, visible: true, label: '⏹ Stop Architecture AI' });
      window._triage?.setStatus?.('Stop failed', 'error');
      window._triage?.appendLog?.(`[Architecture] Stop failed: ${err.message}`);
    }
  });

  const streamUrl = `/api/analysis/copilot/stream/${encodeURIComponent(experimentId)}/${encodeURIComponent(repoName)}?mode=architecture`;
  window._triage?.appendLog?.('[Architecture] Connecting stream...');

  let streamDone = false;
  let pollTimer  = null;

  const clearPolling = () => { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } };

  const finishSuccess = () => {
    streamDone = true;
    clearPolling();
    closeArchitectureAiStream();
    window._triage?.setStatus?.('Completed', 'success');
    window._triage?.appendLog?.('[Architecture] Architecture review completed');
    refetchDiagramsWithApiOpsMode(true);
  };

  const finishFailure = (detail) => {
    streamDone = true;
    clearPolling();
    closeArchitectureAiStream();
    window._triage?.setStatus?.(detail || 'Architecture AI failed', 'error');
    if (detail) window._triage?.appendLog?.(`[Architecture] ${detail}`);
  };

  const pollStatus = async () => {
    try {
      const resp = await fetch(`/api/analysis/status/${encodeURIComponent(experimentId)}/${encodeURIComponent(repoName)}`);
      const data = await resp.json().catch(() => ({}));
      if (data.status === 'running') {
        const label = (data.active_agent_label || data.active_agent || 'Architecture validation').toString();
        window._triage?.setStatus?.(`Running: ${label}…`, 'info');
        window._triage?.setStatusBusy?.(true);
        window._triage?.setToolbarStopState?.({ enabled: true, visible: true, label: '⏹ Stop Architecture AI' });
        return;
      }
      if (data.status === 'completed') { finishSuccess(); return; }
      if (data.status === 'failed' || data.status === 'stopped') {
        finishFailure((data.error || (data.status === 'stopped' ? 'Stopped' : 'Failed')).toString());
      }
    } catch (_) { window._triage?.setStatus?.('Architecture status check failed', 'warn'); }
  };

  state.architectureAiStream = new EventSource(streamUrl);
  state.architectureAiStream.addEventListener('log', (evt) => {
    try { window._triage?.appendLog?.(`[Architecture] ${JSON.parse(evt.data)}`); } catch (_) {}
  });
  state.architectureAiStream.addEventListener('done', () => finishSuccess());
  state.architectureAiStream.addEventListener('error', async () => {
    if (streamDone) return;
    try {
      const resp = await fetch(`/api/analysis/status/${encodeURIComponent(experimentId)}/${encodeURIComponent(repoName)}`);
      const data = await resp.json().catch(() => ({}));
      if (data.status === 'completed') { finishSuccess(); return; }
      if (data.status === 'failed' || data.status === 'stopped') {
        finishFailure((data.error || (data.status === 'stopped' ? 'Stopped' : 'Failed')).toString());
        return;
      }
    } catch (_) {}
    if (state.architectureAiStream) {
      try { state.architectureAiStream.close(); } catch (_) {}
      state.architectureAiStream = null;
    }
    clearPolling();
    pollTimer = setInterval(pollStatus, 2000);
    window._triage?.setStatus?.('Streaming disconnected (polling...)', 'warn');
    window._triage?.setStatusBusy?.(true);
  });
  clearPolling();
  pollTimer = setInterval(pollStatus, 2000);
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function getCurrentRepoName() {
  const sel = document.getElementById('repo-select');
  if (!sel?.value) return '';
  const repoOpt = sel.querySelector('option:checked');
  return (repoOpt?.dataset?.name) || sel.value.split('/').pop();
}

function _triggerDownload(url, filename) {
  const link = document.createElement('a');
  link.href     = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}
