/**
 * diagram-render.js — Mermaid rendering, SVG stamping, and diagram tab building.
 */
import { state }         from './state.js';
import {
  initPanZoom,
  scheduleDiagramFit,
  saveDiagramState,
  loadDiagramState,
} from './diagram-zoom.js';

// ── Helpers ───────────────────────────────────────────────────────────────────

export function sanitizeMermaidSource(code) {
  return String(code || '').replace(/[\u200B\u200C\u200D\u2060\uFEFF]/g, '');
}

export function getActiveDiagramView() {
  const diagramViews = document.getElementById('diagram-views');
  if (!diagramViews) return null;
  return diagramViews.querySelector('.diagram-view.active');
}

export function getDiagramSource(diagramView) {
  if (!diagramView) return null;
  const preEl = diagramView.querySelector('pre.mermaid');
  if (!preEl) return null;
  return sanitizeMermaidSource(preEl.dataset.source || preEl.textContent || '');
}

export function setDiagramPlaceholderVisible(visible) {
  const diagramViews = document.getElementById('diagram-views');
  if (!diagramViews) return;
  diagramViews.classList.toggle('has-diagrams', !visible);
  const placeholder = diagramViews.querySelector(
    '#diagram-placeholder, .diagram-placeholder, .empty-state'
  );
  if (placeholder) {
    placeholder.hidden = !visible;
    if (visible) {
      placeholder.removeAttribute('aria-hidden');
    } else {
      placeholder.setAttribute('aria-hidden', 'true');
    }
  }
}

export function setDiagramLoadingVisible(visible, message = 'Loading architecture diagram…') {
  const overlay = document.getElementById('diagram-loading-overlay');
  if (!overlay) return;
  const text = document.getElementById('diagram-loading-text');
  if (text) text.textContent = message;
  overlay.hidden = !visible;
}

// ── Mermaid init config ───────────────────────────────────────────────────────

function getMermaidConfig() {
  return {
    startOnLoad: false,
    theme: 'dark',
    securityLevel: 'loose',
    flowchart: { useMaxWidth: false, htmlLabels: true },
    onError: (err) => console.error('[Mermaid] Rendering error:', err.message),
  };
}

// ── SVG stamping (gives explicit px dimensions so auto-fit works) ─────────────

function stampSvgDimensions(svg) {
  const vb = svg.viewBox && svg.viewBox.baseVal;
  if (vb && vb.width > 0 && vb.height > 0) {
    svg.setAttribute('width',  `${vb.width}px`);
    svg.setAttribute('height', `${vb.height}px`);
    svg.style.setProperty('width',  `${vb.width}px`);
    svg.style.setProperty('height', `${vb.height}px`);
    svg.style.removeProperty('max-width');
  }
}

// ── Per-container Mermaid rendering ───────────────────────────────────────────

export async function renderMermaidInContainer(container) {
  const mermaidBlocks = Array.from(container.querySelectorAll('.mermaid'));
  for (let idx = 0; idx < mermaidBlocks.length; idx++) {
    const block  = mermaidBlocks[idx];
    const source = block.dataset.source || block.textContent || '';
    if (!source.trim()) continue;
    try {
      const renderId = `diag_${Date.now()}_${idx}_${Math.random().toString(36).slice(2, 8)}`;
      const rendered = await window.mermaid.render(renderId, source);
      block.innerHTML = rendered.svg || '';
      const svg = block.querySelector('svg');
      if (svg) stampSvgDimensions(svg);
    } catch (err) {
      console.error('[Mermaid] Rendering error:', err.message || err);
    }
  }
}

// ── Main render entry point ───────────────────────────────────────────────────

export function renderDiagrams(diagrams) {
  if (!Array.isArray(diagrams) || !diagrams.length) {
    return;
  }

  if (window.MermaidIconInjector && window.MermaidIconInjector._clearCache) {
    window.MermaidIconInjector._clearCache();
  }

  const diagramViews = document.getElementById('diagram-views');
  const diagramTabs  = document.getElementById('diagram-tabs');
  const zoomInner = document.getElementById('diagram-zoom-inner');

  if (!diagramViews || !diagramTabs) {
    console.error('[renderDiagrams] Missing required elements!');
    return;
  }

  setDiagramLoadingVisible(true, 'Rendering architecture diagram…');
  if (zoomInner) zoomInner.classList.add('is-rendering');

  // Reset per-diagram zoom state
  Object.keys(state.diagramStates).forEach(k => delete state.diagramStates[k]);
  state.currentDiagramIndex = 0;

  setDiagramPlaceholderVisible(false);
  const placeholder = diagramViews.querySelector(
    '#diagram-placeholder, .diagram-placeholder, .empty-state'
  );
  if (placeholder) placeholder.remove();

  diagramTabs.innerHTML = '';
  diagramViews.querySelectorAll('.diagram-view').forEach(el => el.remove());

  let addedCount = 0;
  diagrams.forEach((diag, idx) => {
    const title = diag.title || `Diagram ${idx + 1}`;
    const code  = sanitizeMermaidSource(diag.code).trim();
    if (!code) {
      console.warn('[renderDiagrams] Skipping diagram', idx, 'no code');
      return;
    }
    addedCount++;

    const tabBtn = document.createElement('button');
    tabBtn.className       = 'btn-small' + (idx === 0 ? ' active' : '');
    tabBtn.dataset.idx     = idx;
    tabBtn.textContent     = title;
    tabBtn.style.marginRight = '4px';
    diagramTabs.appendChild(tabBtn);

    const viewDiv = document.createElement('div');
    viewDiv.className   = 'diagram-view' + (idx === 0 ? ' active' : '');
    viewDiv.dataset.idx = idx;

    const pre = document.createElement('pre');
    pre.className       = 'mermaid';
    pre.style.background = 'transparent';
    pre.dataset.source  = code;
    pre.textContent     = code;
    viewDiv.appendChild(pre);
    diagramViews.appendChild(viewDiv);

    tabBtn.addEventListener('click', () => {
      saveDiagramState(state.currentDiagramIndex);
      diagramTabs.querySelectorAll('button').forEach(b => b.classList.remove('active'));
      tabBtn.classList.add('active');
      diagramViews.querySelectorAll('.diagram-view').forEach(v => {
        v.classList.toggle('active', v.dataset.idx === String(idx));
      });
      const selectedView = diagramViews.querySelector(`.diagram-view[data-idx="${idx}"]`);
      if (selectedView && !selectedView.querySelector('svg')) {
        setDiagramLoadingVisible(true, 'Loading selected diagram…');
      } else {
        setDiagramLoadingVisible(false);
      }
      state.currentDiagramIndex = idx;
      loadDiagramState(idx);
      if (window.MermaidIconInjector) {
        setTimeout(() => window.MermaidIconInjector.processAllDiagrams(), 100);
      }
    });
  });

  const doRender = () => {
    window.mermaid.initialize(getMermaidConfig());
    renderMermaidInContainer(diagramViews)
      .then(() => {
        setTimeout(() => {
          initPanZoom();
          state.currentDiagramIndex = 0;
          scheduleDiagramFit();
          if (zoomInner) zoomInner.classList.remove('is-rendering');
          setDiagramLoadingVisible(false);
        }, 150);
        setTimeout(() => {
          if (window.MermaidIconInjector) window.MermaidIconInjector.processAllDiagrams();
        }, 400);
      })
      .catch(() => {
        if (zoomInner) zoomInner.classList.remove('is-rendering');
        setDiagramLoadingVisible(false);
      });
  };

  if (window.mermaid) {
    try { doRender(); } catch (e) { if (zoomInner) zoomInner.classList.remove('is-rendering'); console.error('[Diagrams] Mermaid render error:', e); }
  } else {
    const wait = setInterval(() => {
      if (window.mermaid) {
        clearInterval(wait);
        try { doRender(); } catch (e) { if (zoomInner) zoomInner.classList.remove('is-rendering'); console.error('[Diagrams] Mermaid render error:', e); }
      }
    }, 300);
    setTimeout(() => {
      clearInterval(wait);
      if (!window.mermaid) {
        // Never leave the loading overlay stuck if Mermaid fails to initialize.
        if (zoomInner) zoomInner.classList.remove('is-rendering');
        setDiagramLoadingVisible(false);
      }
    }, 10000);
  }
}
