/**
 * subscription-diagrams.js — Handles interactive diagram rendering for subscription page (/cloud).
 * 
 * Features:
 * - Mermaid diagram rendering with proper initialization
 * - CSS styling injection for architectural colors
 * - SVG post-processing (icon injection, label fallbacks)
 * - Zoom and pan controls
 * - PNG export functionality
 * - Provider icon injection
 */

import {
  getMermaidConfig,
  sanitizeMermaidSource,
  stampSvgDimensions,
} from './diagram-shared.js';

// ── Mermaid initialization ────────────────────────────────────────────────────

let mermaidInitialized = false;
function ensureMermaidInitialized() {
  if (mermaidInitialized) return true;
  if (!window.mermaid) return false;
  window.mermaid.initialize(getMermaidConfig());
  mermaidInitialized = true;
  return true;
}

async function waitForMermaid(timeoutMs = 10000) {
  const started = Date.now();
  while (!ensureMermaidInitialized()) {
    if (Date.now() - started > timeoutMs) {
      throw new Error(window.__triageMermaidLoadError || 'Mermaid failed to initialize.');
    }
    await new Promise(resolve => setTimeout(resolve, 200));
  }
}

// ── Scale & transform helpers ─────────────────────────────────────────────────

function applyDiagramScale(container, scale) {
  if (!container) return;
  container.style.transform = `scale(${Math.min(4, Math.max(0.1, scale))})`;
}

function autoFitDiagram(container, scrollEl) {
  if (!container || !scrollEl) return;
  
  const svgEl = container.querySelector('svg');
  if (!svgEl) return;

  const cw = scrollEl.clientWidth - 48;
  const ch = scrollEl.clientHeight - 48;
  if (cw <= 0 || ch <= 0) return;

  let sw = parseFloat(svgEl.getAttribute('width')) || 0;
  let sh = parseFloat(svgEl.getAttribute('height')) || 0;
  if (!sw || !sh) {
    const vb = svgEl.viewBox.baseVal;
    sw = vb.width || svgEl.scrollWidth;
    sh = vb.height || svgEl.scrollHeight;
  }

  if (sw > 0 && sh > 0) {
    const fitScale = Math.min(cw / sw, ch / sh) * 0.90;
    applyDiagramScale(container, fitScale);
  }
  scrollEl.scrollLeft = 0;
  scrollEl.scrollTop = 0;
}

// ── SVG post-processing ───────────────────────────────────────────────────────

function patchForeignObjectLabels(svgEl) {
  if (!svgEl) return;
  const ns = 'http://www.w3.org/2000/svg';
  Array.from(svgEl.querySelectorAll('foreignObject')).forEach((fo, idx) => {
    if (fo.querySelector('img, image, svg')) return;
    const text = (fo.textContent || '').trim();
    if (!text) return;

    const x = parseFloat(fo.getAttribute('x') || '0');
    const y = parseFloat(fo.getAttribute('y') || '0');
    const w = parseFloat(fo.getAttribute('width') || '0');
    const hAttr = parseFloat(fo.getAttribute('height') || '0');
    const h = Number.isFinite(hAttr) && hAttr > 0 ? hAttr : 18;

    fo.setAttribute('height', String(h));
    fo.style.height = `${h}px`;

    if (fo.parentNode?.querySelector(`.fo-fallback-label[data-fo-fallback="${idx}"]`)) return;

    const fallback = document.createElementNS(ns, 'text');
    fallback.setAttribute('class', 'fo-fallback-label');
    fallback.setAttribute('data-fo-fallback', String(idx));
    fallback.setAttribute('x', String(x + (w > 0 ? (w / 2) : 0)));
    fallback.setAttribute('y', String(y + 10));
    fallback.setAttribute('text-anchor', 'middle');
    fallback.setAttribute('dominant-baseline', 'middle');
    fallback.textContent = text;
    fo.parentNode?.appendChild(fallback);

    fo.style.opacity = '0';
    fo.style.pointerEvents = 'none';
  });
}

// ── SVG dimension stamping ────────────────────────────────────────────────────

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

  // Hook all collapsible diagram headers (with deduplication)
  const headers = document.querySelectorAll('[data-diagram-header]');
  console.log(`[subscription-diagrams] Found ${headers.length} diagram headers`);
  
  headers.forEach(header => {
    // Check if already hooked
    if (header._diagramHookAttached) {
      console.log('[subscription-diagrams] Header already hooked, skipping');
      return;
    }
    header._diagramHookAttached = true;
    
    header.addEventListener('click', async (evt) => {
      const diagramId = header.getAttribute('data-diagram-header');
      const diagramDiv = document.getElementById(diagramId);
      const scrollEl = diagramDiv?.parentElement;
      const toggle = header.querySelector('span');
      
      console.log(`[subscription-diagrams] Clicked header for ${diagramId}`);
      
      if (!diagramDiv || !scrollEl) {
        console.warn(`[subscription-diagrams] Could not find diagram or parent for ${diagramId}`);
        return;
      }

      const isVisible = diagramDiv.style.display !== 'none';
      console.log(`[subscription-diagrams] Before toggle: display=${diagramDiv.style.display}`);
      diagramDiv.style.display = isVisible ? 'none' : 'block';
      console.log(`[subscription-diagrams] After toggle: display=${diagramDiv.style.display}`);
      
      // Update toggle icon
      if (toggle) {
        toggle.textContent = isVisible ? '▶' : '▼';
      }

      // Render on expand
      if (!isVisible) {
        try {
          await window.mermaid.run();
          
          // Post-process any new SVGs
          const newSvgs = diagramDiv.querySelectorAll('svg');
          newSvgs.forEach(postProcessSvg);
          
          console.log(`[subscription-diagrams] Rendered diagram ${diagramId}`);
        } catch (err) {
          console.warn(`[subscription-diagrams] Render error for ${diagramId}:`, err);
        }
      }
    });
  });

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
