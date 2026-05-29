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
  waitForMermaid,
  applyDiagramScale,
  autoFitDiagram,
  patchForeignObjectLabels,
  enhancePlaceholderGlyphs,
  applyEmojiIconFallback,
  exportDiagramPNG,
} from './diagram-base.js';

import {
  sanitizeMermaidSource,
  stampSvgDimensions,
} from './diagram-shared.js';

// ── Scale & transform helpers ─────────────────────────────────────────────────

function postProcessSvg(svgEl) {
  if (!svgEl) return;
  patchForeignObjectLabels(svgEl);
  stampSvgDimensions(svgEl);
}

// ── Diagram rendering API ─────────────────────────────────────────────────────

export async function initSubscriptionDiagrams() {
  console.log('[subscription-diagrams] Initializing');
  
  // Wait for Mermaid
  try {
    await waitForMermaid();
  } catch (err) {
    console.error('[subscription-diagrams] Mermaid init failed:', err);
    return;
  }

  // Hook zoom buttons
  document.querySelectorAll('[data-diagram-zoom-in]').forEach(btn => {
    btn.addEventListener('click', () => {
      const containerId = btn.getAttribute('data-diagram-zoom-in');
      const container = document.getElementById(containerId);
      if (container) applyDiagramScale(container, 1.2);
    });
  });

  document.querySelectorAll('[data-diagram-zoom-out]').forEach(btn => {
    btn.addEventListener('click', () => {
      const containerId = btn.getAttribute('data-diagram-zoom-out');
      const container = document.getElementById(containerId);
      if (container) applyDiagramScale(container, 0.8);
    });
  });

  document.querySelectorAll('[data-diagram-fit]').forEach(btn => {
    btn.addEventListener('click', () => {
      const containerId = btn.getAttribute('data-diagram-fit');
      const container = document.getElementById(containerId);
      const scrollEl = container?.parentElement;
      if (container && scrollEl) autoFitDiagram(container, scrollEl);
    });
  });

  // Hook PNG export buttons
  document.querySelectorAll('[data-diagram-export]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const diagramId = btn.getAttribute('data-diagram-export');
      const container = document.getElementById(diagramId);
      if (!container) return;

      try {
        const svgEl = container.querySelector('svg');
        if (!svgEl) {
          console.warn(`[subscription-diagrams] No SVG found in ${diagramId}`);
          return;
        }

        // Create canvas and render
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
  });

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
