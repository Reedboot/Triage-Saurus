/**
 * diagram-shared.js — Shared diagram utilities for both standalone and inline viewers.
 * This module contains common logic that should be identical across all diagram viewers.
 *
 * Also exposes window.DiagramShared for non-module contexts (e.g. diagram_viewer.html).
 */

// ── Mermaid Configuration ──────────────────────────────────────────────────────
// This configuration is used by both the inline viewer (diagram-render.js)
// and the standalone viewer (diagram_viewer.html).

export function getMermaidConfig() {
  return {
    startOnLoad: false,
    theme: 'dark',
    securityLevel: 'loose',
    maxTextSize: 500000,
    flowchart: { useMaxWidth: false, htmlLabels: true },
    onError: (err) => console.error('[Mermaid] Rendering error:', err.message),
  };
}

// ── SVG Utilities ──────────────────────────────────────────────────────────────

/**
 * Sanitize Mermaid source code by removing zero-width characters.
 * Used by both viewers to clean diagram source code.
 */
export function sanitizeMermaidSource(code) {
  return String(code || '').replace(/[\u200B\u200C\u200D\u2060\uFEFF]/g, '');
}

/**
 * Stamp SVG with explicit pixel dimensions based on viewBox.
 * This ensures the SVG has concrete dimensions for auto-fit calculations.
 * Used by both viewers after Mermaid rendering.
 */
export function stampSvgDimensions(svg) {
  const vb = svg.viewBox && svg.viewBox.baseVal;
  if (vb && vb.width > 0 && vb.height > 0) {
    svg.setAttribute('width',  `${vb.width}px`);
    svg.setAttribute('height', `${vb.height}px`);
    svg.style.setProperty('width',  `${vb.width}px`);
    svg.style.setProperty('height', `${vb.height}px`);
    svg.style.removeProperty('max-width');
  }
}

/**
 * Get the content bounds of a diagram SVG.
 * Tries viewBox first (most reliable), then getBBox, then HTML attributes.
 * Used for zoom/fit calculations.
 */
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

// ── Icon Injection Helper ──────────────────────────────────────────────────────

/**
 * Process Mermaid diagrams for icon injection using the MermaidIconInjector.
 * Called by both viewers after rendering to inject provider icons into diagrams.
 */
export function injectDiagramIcons(provider) {
  if (!window.MermaidIconInjector) return;

  const iconDataUrl = `/api/icon-mappings?provider=${encodeURIComponent(provider)}`;
  
  // Retry a few times to handle race between SVG insertion and icon map loading.
  [0, 250, 700].forEach(delay => {
    setTimeout(() => {
      window.MermaidIconInjector.processAllDiagrams({ iconDataUrl });
    }, delay);
  });

  // Self-heal for stale-cache/race cases where icons still don't appear.
  setTimeout(() => {
    const iconCount = document.querySelectorAll('g.mermaid-icon').length;
    if (iconCount === 0 && typeof window.MermaidIconInjector._clearCache === 'function') {
      window.MermaidIconInjector._clearCache();
      window.MermaidIconInjector.processAllDiagrams({ iconDataUrl });
    }
  }, 1400);
}

// ── Toast Notification (shared utility) ────────────────────────────────────────

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

// ── Navigation Helpers ─────────────────────────────────────────────────────────

/**
 * Extract the 'from' query parameter to determine where to navigate back.
 * Used by standalone viewer to return to the originating scan results.
 */
export function getNavigationOrigin() {
  const params = new URLSearchParams(window.location.search);
  return params.get('from') || null;
}

/**
 * Navigate back to the originating page, with a fallback to root.
 * Used by standalone viewer's back button.
 */
export function navigateBack() {
  const origin = getNavigationOrigin();
  if (origin) {
    window.location.href = origin;
  } else {
    window.location.href = '/';
  }
}

/**
 * Build a fullscreen diagram URL from the current experiment and provider.
 * Used by inline viewer to navigate to standalone fullscreen diagram.
 */
export function buildFullscreenDiagramUrl(experimentId, provider, originUrl) {
  const url = new URL(`/diagrams/${encodeURIComponent(experimentId)}`, window.location.origin);
  if (provider) {
    url.searchParams.set('provider', provider);
  }
  if (originUrl) {
    url.searchParams.set('from', originUrl);
  }
  return url.toString();
}

// ── Simple Inline Mermaid Renderer (for non-module contexts) ─────────────────────
// This function is designed for inline usage in HTML templates (e.g., subscriptions.html)
// and provides the same configuration and rendering logic as the module-based system.

export async function renderMermaidDiagram(container) {
  if (!window.mermaid) {
    console.warn('[Mermaid] Mermaid not loaded yet, retrying...');
    await new Promise(resolve => setTimeout(resolve, 300));
    if (!window.mermaid) {
      console.error('[Mermaid] Failed to load Mermaid library');
      return false;
    }
  }

  try {
    window.mermaid.initialize(getMermaidConfig());
    
    const mermaidBlocks = Array.from(container.querySelectorAll('.mermaid'));
    for (const block of mermaidBlocks) {
      const source = sanitizeMermaidSource(block.textContent || '');
      if (!source.trim()) continue;
      
      const renderId = `diag_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
      try {
        const rendered = await window.mermaid.render(renderId, source);
        block.innerHTML = rendered.svg || '';
        const svg = block.querySelector('svg');
        if (svg) stampSvgDimensions(svg);
        document.getElementById(renderId)?.remove();
      } catch (err) {
        console.error('[Mermaid] Rendering error:', err.message || err);
        document.getElementById(renderId)?.remove();
      }
    }
    return true;
  } catch (err) {
    console.error('[Mermaid] Error rendering diagram:', err);
    return false;
  }
}

// ── Global export for non-module contexts (e.g. diagram_viewer.html) ─────────
// Makes getMermaidConfig available as window.DiagramShared.getMermaidConfig()
// so the standalone viewer stays in sync with the inline viewer's config.
if (typeof window !== 'undefined') {
  window.DiagramShared = { getMermaidConfig };
}
