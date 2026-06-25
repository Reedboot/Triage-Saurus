/**
 * subscription-diagrams.js — Handles interactive diagram rendering for subscription page (/cloud).
 * 
 * Features:
 * - Uses diagram-base.js for core mermaid rendering and post-processing
 * - CSS styling injection for architectural colors
 * - Zoom and pan controls
 * - PNG export functionality
 */

import {
  applyDiagramScale,
  autoFitDiagram,
  patchForeignObjectLabels,
  enhancePlaceholderGlyphs,
  applyEmojiIconFallback,
  exportDiagramPNG,
} from './diagram-base.js';

import {
  stampSvgDimensions,
} from './diagram-shared.js';

import {
  renderMermaidSource,
  waitForCloudMermaid,
} from './cloud-mermaid-helper.js';

// ── Scale & transform helpers ─────────────────────────────────────────────────

export function postProcessSvg(svgEl) {
  if (!svgEl) return;
  patchForeignObjectLabels(svgEl);
  stampSvgDimensions(svgEl);
}

export async function renderMermaidDiagram({
  source,
  rootEl,
  onRendered,
}) {
  return renderMermaidSource({ source, rootEl, onRendered });
}

function getCurrentScale(container) {
  if (!container) return 1;
  const stored = parseFloat(container.dataset.diagramScale || '');
  if (Number.isFinite(stored) && stored > 0) return stored;
  const match = String(container.style.transform || '').match(/scale\(([^)]+)\)/);
  const parsed = match ? parseFloat(match[1]) : NaN;
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 1;
}

function setScale(container, scale) {
  const applied = applyDiagramScale(container, scale);
  if (container) container.dataset.diagramScale = String(applied || scale || 1);
  return applied;
}

function applyRelativeZoom(container, factor) {
  if (!container) return;
  const nextScale = getCurrentScale(container) * factor;
  setScale(container, nextScale);
}

let controlsBound = false;
function bindControlHandlers() {
  if (controlsBound) return;
  controlsBound = true;

  document.addEventListener('click', async (event) => {
    const target = event.target;
    if (!target || !target.closest) return;

    const zoomInBtn = target.closest('[data-diagram-zoom-in]');
    if (zoomInBtn) {
      const containerId = zoomInBtn.getAttribute('data-diagram-zoom-in');
      const container = containerId ? document.getElementById(containerId) : null;
      if (container) applyRelativeZoom(container, 1.2);
      return;
    }

    const zoomOutBtn = target.closest('[data-diagram-zoom-out]');
    if (zoomOutBtn) {
      const containerId = zoomOutBtn.getAttribute('data-diagram-zoom-out');
      const container = containerId ? document.getElementById(containerId) : null;
      if (container) applyRelativeZoom(container, 0.8);
      return;
    }

    const fitBtn = target.closest('[data-diagram-fit]');
    if (fitBtn) {
      const containerId = fitBtn.getAttribute('data-diagram-fit');
      const container = containerId ? document.getElementById(containerId) : null;
      const scrollEl = container?.parentElement;
      if (container && scrollEl) {
        const fitScale = autoFitDiagram(container, scrollEl);
        container.dataset.diagramScale = String(fitScale || 1);
      }
      return;
    }

    const exportBtn = target.closest('[data-diagram-export]');
    if (!exportBtn) return;
    const diagramId = exportBtn.getAttribute('data-diagram-export');
    const container = diagramId ? document.getElementById(diagramId) : null;
    if (!container) return;

    try {
      const svgEl = container.querySelector('svg');
      if (!svgEl) {
        console.warn(`[subscription-diagrams] No SVG found in ${diagramId}`);
        return;
      }

      const canvas = document.createElement('canvas');
      const ctx = canvas.getContext('2d');
      const rect = svgEl.getBBox?.() || { x: 0, y: 0, width: svgEl.clientWidth, height: svgEl.clientHeight };

      canvas.width = rect.width;
      canvas.height = rect.height;

      const serializer = new XMLSerializer();
      const svgStr = serializer.serializeToString(svgEl);
      const img = new Image();

      img.onload = () => {
        ctx.drawImage(img, 0, 0);
        const link = document.createElement('a');
        link.href = canvas.toDataURL('image/png');
        link.download = `diagram-${diagramId}.png`;
        link.click();
      };

      img.src = 'data:image/svg+xml;base64,' + btoa(svgStr);
    } catch (err) {
      console.error(`[subscription-diagrams] PNG export failed:`, err);
    }
  });
}

// ── Diagram rendering API ─────────────────────────────────────────────────────

export async function initSubscriptionDiagrams() {
  console.log('[subscription-diagrams] Initializing');
  
  // Wait for Mermaid
  try {
    await waitForCloudMermaid();
  } catch (err) {
    console.error('[subscription-diagrams] Mermaid init failed:', err);
    return;
  }
  bindControlHandlers();

  console.log('[subscription-diagrams] Ready');
}

// Auto-init if DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initSubscriptionDiagrams);
} else {
  initSubscriptionDiagrams();
}

// Expose to window (export is already done at function declaration)
window.initSubscriptionDiagrams = initSubscriptionDiagrams;
window.applyDiagramScale = applyDiagramScale;
window.autoFitDiagram = autoFitDiagram;

/** Post-process newly rendered SVGs inside a container (called after scoped mermaid.run). */
window.postProcessDiagramSvgs = (container) => {
  container.querySelectorAll('svg').forEach(postProcessSvg);
};
