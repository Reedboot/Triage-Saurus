/**
 * diagram-zoom.js — pan, zoom, fit, and transform for the inline diagram viewer.
 */
import { state } from './state.js';

// ── Transform ─────────────────────────────────────────────────────────────────

export function applyTransform() {
  const zoomInner = document.getElementById('diagram-zoom-inner');
  if (zoomInner) {
    requestAnimationFrame(() => {
      zoomInner.style.transition = state.isPanning ? 'none' : '';
      zoomInner.style.transform =
        `scale(${state.zoomState.scale}) translate(${state.zoomState.panX}px, ${state.zoomState.panY}px)`;
      zoomInner.style.transformOrigin = 'top left';
    });
  }
  updateZoomDisplay();
}

export function updateZoomDisplay() {
  const el = document.getElementById('zoom-level-display');
  if (el) el.textContent = Math.round(state.zoomState.scale * 100) + '%';
}

export function zoomIn() {
  state.zoomState.scale = Math.min(state.zoomState.maxScale, state.zoomState.scale * 1.2);
  applyTransform();
  saveDiagramState(state.currentDiagramIndex);
}

export function zoomOut() {
  state.zoomState.scale = Math.max(state.zoomState.minScale, state.zoomState.scale / 1.2);
  applyTransform();
  saveDiagramState(state.currentDiagramIndex);
}

export function zoomReset() {
  scheduleDiagramFit();
}

// ── Bounds detection ──────────────────────────────────────────────────────────

export function getDiagramContentBounds(svg) {
  // 1. viewBox (most reliable)
  const vb = svg.viewBox && svg.viewBox.baseVal;
  if (vb && vb.width > 0 && vb.height > 0) {
    return { width: vb.width, height: vb.height };
  }

  // 2. getBBox (actual rendered content)
  try {
    const bbox = svg.getBBox();
    if (bbox && bbox.width > 0 && bbox.height > 0) {
      return { width: bbox.width, height: bbox.height };
    }
  } catch (_) {}

  // 3. HTML attributes
  const w = parseFloat(svg.getAttribute('width'));
  const h = parseFloat(svg.getAttribute('height'));
  if (w > 0 && h > 0) return { width: w, height: h };

  return null;
}

// ── Fit ───────────────────────────────────────────────────────────────────────

export function fitActiveDiagram(mode = 'contain') {
  const activeDiagram = getActiveDiagramView();
  const svg = activeDiagram ? activeDiagram.querySelector('svg') : null;
  const zoomWrap = document.getElementById('diagram-zoom-wrap');

  if (!svg || !zoomWrap) {
    state.zoomState.scale = 1.0;
    state.zoomState.panX  = 0;
    state.zoomState.panY  = 0;
    applyTransform();
    return;
  }

  const bounds    = getDiagramContentBounds(svg);
  const wrapWidth  = zoomWrap.clientWidth  || zoomWrap.offsetWidth  || 0;
  const wrapHeight = zoomWrap.clientHeight || zoomWrap.offsetHeight || 0;

  if (!bounds || !wrapWidth || !wrapHeight) {
    console.warn('[fitActiveDiagram] Missing bounds or wrap dimensions, resetting');
    state.zoomState.scale = 1.0;
    state.zoomState.panX  = 0;
    state.zoomState.panY  = 0;
    applyTransform();
    return;
  }

  const padding       = 32;
  const availableWidth  = Math.max(1, wrapWidth  - padding);
  const availableHeight = Math.max(1, wrapHeight - padding);
  const containScale = Math.max(
    state.zoomState.minScale,
    Math.min(availableWidth / bounds.width, availableHeight / bounds.height)
  );
  const widthScale = Math.max(state.zoomState.minScale, availableWidth / bounds.width);
  const fitScale   = Math.min(
    state.zoomState.maxScale,
    mode === 'width' ? widthScale : containScale
  );

  state.zoomState.scale = fitScale;
  state.zoomState.panX  = wrapWidth  / (2 * fitScale) - bounds.width  / 2;
  state.zoomState.panY  = mode === 'width'
    ? 0
    : wrapHeight / (2 * fitScale) - bounds.height / 2;
  applyTransform();
  saveDiagramState(state.currentDiagramIndex);

  // Nudge so the Internet / 🌐 node stays visible after fit.
  // IMPORTANT: wrap in double-rAF so getBoundingClientRect reads happen AFTER
  // applyTransform's own requestAnimationFrame has flushed the CSS transform.
  // Without this, stale layout coords cause a massive erroneous panX offset.
  requestAnimationFrame(() => requestAnimationFrame(() => {
    try {
      const innerEl = document.getElementById('diagram-zoom-inner');
      if (!innerEl) return;
      const allLabels = activeDiagram.querySelectorAll('.nodeLabel, .node text, text');
      let internetEl = null;
      for (const el of allLabels) {
        if (/internet/i.test(el.textContent || '')) { internetEl = el; break; }
      }
      if (!internetEl) return;
      const innerRect = innerEl.getBoundingClientRect();
      const nodeRect  = internetEl.getBoundingClientRect();
      const margin = 24;
      const nodeScreenLeft   = nodeRect.left   - innerRect.left;
      const nodeScreenRight  = nodeRect.right  - innerRect.left;
      const nodeScreenTop    = nodeRect.top    - innerRect.top;
      const nodeScreenBottom = nodeRect.bottom - innerRect.top;
      let nudgeX = 0, nudgeY = 0;
      if (nodeScreenLeft < margin)                nudgeX = (margin - nodeScreenLeft)    / fitScale;
      if (nodeScreenRight > wrapWidth - margin)   nudgeX = (wrapWidth - margin - nodeScreenRight) / fitScale;
      if (nodeScreenTop < margin)                 nudgeY = (margin - nodeScreenTop)     / fitScale;
      if (nodeScreenBottom > wrapHeight - margin) nudgeY = (wrapHeight - margin - nodeScreenBottom) / fitScale;
      // Clamp nudge to prevent runaway offsets (should never exceed diagram dimensions)
      const maxNudge = Math.max(bounds.width, bounds.height);
      nudgeX = Math.max(-maxNudge, Math.min(maxNudge, nudgeX));
      nudgeY = Math.max(-maxNudge, Math.min(maxNudge, nudgeY));
      if (nudgeX !== 0 || nudgeY !== 0) {
        state.zoomState.panX += nudgeX;
        state.zoomState.panY += nudgeY;
        applyTransform();
        saveDiagramState(state.currentDiagramIndex);
      }
    } catch (_) { /* non-critical */ }
  }));
}

export function scheduleDiagramFit(attempt = 0) {
  const activeDiagram = getActiveDiagramView();
  const svg = activeDiagram ? activeDiagram.querySelector('svg') : null;
  const zoomWrap = document.getElementById('diagram-zoom-wrap');

  if (!svg || !zoomWrap || !zoomWrap.clientWidth) {
    if (attempt < 20) {
      setTimeout(() => scheduleDiagramFit(attempt + 1), 80);
    }
    return;
  }
  fitActiveDiagram('contain');
}

// ── State persistence ─────────────────────────────────────────────────────────

export function saveDiagramState(diagramIndex) {
  state.diagramStates[diagramIndex] = {
    scale: state.zoomState.scale,
    panX:  state.zoomState.panX,
    panY:  state.zoomState.panY,
  };
}

export function loadDiagramState(diagramIndex) {
  if (state.diagramStates[diagramIndex]) {
    const s = state.diagramStates[diagramIndex];
    state.zoomState.scale = s.scale;
    state.zoomState.panX  = s.panX;
    state.zoomState.panY  = s.panY;
  } else {
    scheduleDiagramFit();
    return;
  }
  applyTransform();
}

// ── Pan / zoom interactivity ──────────────────────────────────────────────────

export function initPanZoom() {
  const zoomWrap    = document.getElementById('diagram-zoom-wrap');
  const diagramViews = document.getElementById('diagram-views');
  if (!zoomWrap || !diagramViews) return;

  diagramViews.querySelectorAll('.mermaid svg').forEach(svg => {
    if (svg.__panZoomInitialized) return;
    svg.__panZoomInitialized = true;

    let panInputType = null;
    const onMouseWindowMove = (e) => updatePan('mouse', e.clientX, e.clientY);
    const onMouseWindowUp   = ()  => finishPan('mouse');

    const beginPan = (inputType, clientX, clientY, pointerId = null) => {
      if (state.isPanning) return;
      state.isPanning          = true;
      panInputType             = inputType;
      state.activePanPointerId = pointerId;
      state.panStartX = clientX;
      state.panStartY = clientY;
      state.panOriginX = state.zoomState.panX;
      state.panOriginY = state.zoomState.panY;
      svg.style.cursor = 'grabbing';
      if (inputType === 'mouse') {
        window.addEventListener('mousemove', onMouseWindowMove);
        window.addEventListener('mouseup',   onMouseWindowUp);
      }
    };

    const updatePan = (inputType, clientX, clientY, pointerId = null) => {
      if (!state.isPanning || panInputType !== inputType) return;
      if (inputType === 'pointer' && state.activePanPointerId !== pointerId) return;
      const scale = state.zoomState.scale || 1;
      state.zoomState.panX = state.panOriginX + ((clientX - state.panStartX) / scale);
      state.zoomState.panY = state.panOriginY + ((clientY - state.panStartY) / scale);
      applyTransform();
    };

    const finishPan = (inputType, pointerId = null) => {
      if (!state.isPanning || panInputType !== inputType) return;
      if (inputType === 'pointer' && state.activePanPointerId !== null && pointerId !== state.activePanPointerId) return;
      if (inputType === 'pointer' && svg.releasePointerCapture && state.activePanPointerId !== null) {
        try {
          if (svg.hasPointerCapture && svg.hasPointerCapture(state.activePanPointerId)) {
            svg.releasePointerCapture(state.activePanPointerId);
          }
        } catch (_) {}
      }
      state.isPanning          = false;
      panInputType             = null;
      state.activePanPointerId = null;
      if (inputType === 'mouse') {
        window.removeEventListener('mousemove', onMouseWindowMove);
        window.removeEventListener('mouseup',   onMouseWindowUp);
      }
      svg.style.cursor = 'grab';
      saveDiagramState(state.currentDiagramIndex);
      applyTransform();
    };

    svg.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      beginPan('pointer', e.clientX, e.clientY, e.pointerId);
      if (svg.setPointerCapture) {
        try { svg.setPointerCapture(e.pointerId); } catch (_) {}
      }
      e.preventDefault();
    });
    svg.addEventListener('pointermove',       (e) => updatePan('pointer', e.clientX, e.clientY, e.pointerId));
    svg.addEventListener('pointerup',         (e) => finishPan('pointer', e.pointerId));
    svg.addEventListener('pointercancel',     (e) => finishPan('pointer', e.pointerId));
    svg.addEventListener('lostpointercapture',(e) => finishPan('pointer', e.pointerId));
    svg.addEventListener('mousedown', (e) => {
      if (e.button !== 0) return;
      beginPan('mouse', e.clientX, e.clientY);
      e.preventDefault();
    });
    svg.addEventListener('mousemove', (e) => updatePan('mouse', e.clientX, e.clientY));
    svg.addEventListener('mouseup',   () => finishPan('mouse'));
    svg.addEventListener('mouseleave',() => finishPan('mouse'));

    // Mouse-wheel zoom
    svg.addEventListener('wheel', (e) => {
      e.preventDefault();
      const currentScale = state.zoomState.scale || 1;
      const wheelStep    = Math.max(0.004, Math.min(0.015, 0.02 / currentScale));
      const delta        = e.deltaY > 0 ? (1 - wheelStep) : (1 + wheelStep);
      const newScale     = currentScale * delta;
      if (newScale >= state.zoomState.minScale && newScale <= state.zoomState.maxScale) {
        const rect = svg.getBoundingClientRect();
        const useCenterAnchor = currentScale >= 2;
        const x = useCenterAnchor ? rect.width  / 2 : (e.clientX - rect.left);
        const y = useCenterAnchor ? rect.height / 2 : (e.clientY - rect.top);
        state.zoomState.panX += (x * (1 - delta)) / (currentScale * delta);
        state.zoomState.panY += (y * (1 - delta)) / (currentScale * delta);
        state.zoomState.scale = newScale;
        applyTransform();
        saveDiagramState(state.currentDiagramIndex);
      }
    });

    svg.style.cursor = 'grab';
  });
}

// ── Helpers used internally and by diagram-render ─────────────────────────────

function getActiveDiagramView() {
  const diagramViews = document.getElementById('diagram-views');
  if (!diagramViews) return null;
  return diagramViews.querySelector('.diagram-view.active');
}
