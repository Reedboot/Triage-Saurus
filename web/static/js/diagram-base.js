/**
 * diagram-base.js — Shared diagram rendering utilities used by all diagram modules.
 * 
 * Provides:
 * - Mermaid initialization and async waiting
 * - SVG post-processing (dimension stamping, label patching)
 * - Scale and zoom helpers
 * - PNG export functionality
 * - Common constants (provider metadata, etc.)
 * 
 * Used by: diagram-viewer.js, subscription-diagrams.js, diagram-render.js
 */

import {
  getMermaidConfig,
  sanitizeMermaidSource,
  stampSvgDimensions,
} from './diagram-shared.js';

// ── Provider metadata ─────────────────────────────────────────────────────────

export const PROVIDER_META = {
  aws:        { label: 'AWS',        color: '#f97316', icon: '/static/assets/icons/aws/account.svg' },
  azure:      { label: 'Azure',      color: '#3b82f6', icon: '/static/assets/icons/azure/compute/aks.svg' },
  gcp:        { label: 'GCP',        color: '#22c55e', icon: '/static/assets/icons/gcp/Cloud_Storage/SVG/cloud-storage.svg' },
  kubernetes: { label: 'Kubernetes', color: '#6366f1', icon: '/static/assets/icons/kubernetes/cluster.svg' },
  alicloud:   { label: 'AliCloud',   color: '#f59e0b', icon: null },
  oci:        { label: 'OCI',        color: '#e11d48', icon: '/static/assets/icons/oci/cloud.svg' },
};

// ── Mermaid initialization ────────────────────────────────────────────────────

let mermaidInitialized = false;

export function ensureMermaidInitialized() {
  if (mermaidInitialized) return true;
  if (!window.mermaid) return false;
  window.mermaid.initialize(getMermaidConfig());
  mermaidInitialized = true;
  return true;
}

export async function waitForMermaid(timeoutMs = 10000) {
  const started = Date.now();
  while (!ensureMermaidInitialized()) {
    if (Date.now() - started > timeoutMs) {
      throw new Error(window.__triageMermaidLoadError || 'Mermaid failed to initialize.');
    }
    await new Promise(resolve => setTimeout(resolve, 200));
  }
}

// ── Scale helpers ─────────────────────────────────────────────────────────────

export function applyDiagramScale(container, scale) {
  if (!container) return;
  const scaledVal = Math.min(4, Math.max(0.1, scale));
  container.style.transformOrigin = '0 0';
  container.style.transform = `scale(${scaledVal})`;
  return scaledVal;
}

export function autoFitDiagram(container, scrollEl) {
  if (!container || !scrollEl) return 1;
  
  const svgEl = container.querySelector('svg');
  if (!svgEl) return 1;

  const cw = scrollEl.clientWidth - 48;
  const ch = scrollEl.clientHeight - 48;
  if (cw <= 0 || ch <= 0) return 1;

  let sw = parseFloat(svgEl.getAttribute('width')) || 0;
  let sh = parseFloat(svgEl.getAttribute('height')) || 0;
  if (!sw || !sh) {
    const vb = svgEl.viewBox.baseVal;
    sw = vb.width || svgEl.scrollWidth;
    sh = vb.height || svgEl.scrollHeight;
  }

  let fitScale = 1;
  if (sw > 0 && sh > 0) {
    fitScale = Math.min(cw / sw, ch / sh) * 0.90;
    fitScale = Math.min(4, Math.max(0.05, fitScale));
  }
  
  applyDiagramScale(container, fitScale);
  scrollEl.scrollLeft = 0;
  scrollEl.scrollTop = 0;
  return fitScale;
}

// ── SVG post-processing ───────────────────────────────────────────────────────

/**
 * Fix rendering issues with foreignObject labels that browsers fail to paint reliably.
 * Creates SVG text fallbacks for text-only foreign objects.
 *
 * Note: modern browsers (Chromium, Firefox, Safari) render <foreignObject> correctly
 * so we only add a fallback when the element has zero rendered height, which indicates
 * the browser skipped it.  This prevents the Internet node (and other plain-text
 * Mermaid labels) from showing twice — once from the foreignObject and once from the
 * injected <text> element.
 */
export function patchForeignObjectLabels(svgEl) {
  if (!svgEl) return;
  const ns = 'http://www.w3.org/2000/svg';
  Array.from(svgEl.querySelectorAll('foreignObject')).forEach((fo, idx) => {
    if (fo.querySelector('img, image, svg')) return; // keep icon-bearing FOs intact
    const text = (fo.textContent || '').trim();
    if (!text) return;

    // Skip if the browser is already rendering this foreignObject (height > 0).
    // Adding a fallback <text> on top of a rendered foreignObject creates a
    // duplicate label at a different font size.
    try {
      const rect = fo.getBoundingClientRect();
      if (rect.height > 0) return;
    } catch (_) {
      // getBoundingClientRect may throw if the element is detached; fall through
      // to the legacy fallback path.
    }

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
    fallback.setAttribute('font-size', '12');
    fallback.setAttribute('font-family', 'sans-serif');
    fallback.setAttribute('fill', '#000');
    fallback.setAttribute('data-fo-fallback', String(idx));
    fallback.textContent = text.replace(/\s+/g, ' ');

    fo.parentNode.insertBefore(fallback, fo.nextSibling);
  });
}

/**
 * Apply placeholder glyph enhancements for better visibility.
 */
export function enhancePlaceholderGlyphs(svgEl) {
  if (!svgEl) return;
  const placeholders = svgEl.querySelectorAll('[data-placeholder-type]');
  placeholders.forEach(el => {
    const type = el.getAttribute('data-placeholder-type');
    const icon = {
      'icon-missing': '❌',
      'icon-generic': '📌',
      'icon-default': '⚙️',
    }[type] || '•';

    const text = el.querySelector('text');
    if (text) text.textContent = `${icon} ${text.textContent || type}`;
  });
}

/**
 * Apply emoji icon fallback when icon injection fails.
 */
export function applyEmojiIconFallback(svgEl) {
  if (!svgEl) return;
  const iconPlaceholders = svgEl.querySelectorAll('[class*="icon-"]:not(.mermaid-icon)');
  iconPlaceholders.forEach(el => {
    const className = el.getAttribute('class') || '';
    const emoji = {
      'icon-cloud': '☁️',
      'icon-database': '🗄️',
      'icon-server': '🖥️',
      'icon-lock': '🔒',
      'icon-network': '🌐',
      'icon-storage': '💾',
      'icon-cpu': '⚙️',
      'icon-memory': '💾',
      'icon-network-interface': '🔌',
      'icon-firewall': '🔥',
    }[className] || '▪';
    const text = el.querySelector('text');
    if (text && text.textContent.trim() === '') {
      text.textContent = emoji;
    }
  });
}

// ── PNG export functionality ───────────────────────────────────────────────────

export async function exportDiagramPNG(svgEl, diagramName = 'diagram') {
  if (!svgEl) return;

  try {
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d');
    if (!ctx) {
      console.error('[diagram-base] Could not get canvas 2D context');
      return;
    }

    const rect = svgEl.getBBox?.() || {
      x: 0,
      y: 0,
      width: svgEl.clientWidth,
      height: svgEl.clientHeight,
    };
    
    canvas.width = rect.width;
    canvas.height = rect.height;

    const serializer = new XMLSerializer();
    const svgString = serializer.serializeToString(svgEl);
    const blob = new Blob([svgString], { type: 'image/svg+xml' });
    const url = URL.createObjectURL(blob);

    const img = new Image();
    img.onload = () => {
      ctx.drawImage(img, 0, 0);
      URL.revokeObjectURL(url);

      const pngUrl = canvas.toDataURL('image/png');
      const link = document.createElement('a');
      link.href = pngUrl;
      link.download = `${diagramName}.png`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
    };
    img.onerror = () => {
      console.error('[diagram-base] Failed to load SVG image');
      URL.revokeObjectURL(url);
    };
    img.src = url;
  } catch (error) {
    console.error('[diagram-base] PNG export failed:', error);
  }
}

// ── Diagram rendering helper ───────────────────────────────────────────────────

/**
 * Core diagram rendering pipeline.
 * Handles: mermaid render, sanitization, SVG post-processing, icon injection
 */
export async function renderDiagramCore(
  mermaidCode,
  containerId,
  options = {}
) {
  const {
    provider = 'azure',
    renderId = `diagram-render-${Date.now()}-${Math.random()}`,
    skeletonEl = null,
    onProgress = null,
  } = options;

  const container = document.getElementById(containerId);
  if (!container) {
    console.error(`[diagram-base] Container ${containerId} not found`);
    return null;
  }

  if (skeletonEl) skeletonEl.classList.remove('hidden');
  container.innerHTML = '';

  if (!mermaidCode || !mermaidCode.trim()) {
    if (skeletonEl) skeletonEl.classList.add('hidden');
    container.innerHTML = '<p class="text-[#7d8590] p-8 text-sm">No diagram available.</p>';
    return null;
  }

  try {
    await waitForMermaid();
    
    const sanitizedCode = sanitizeMermaidSource(mermaidCode);
    const { svg } = await window.mermaid.render(renderId, sanitizedCode.trim());
    
    if (skeletonEl) skeletonEl.classList.add('hidden');
    container.innerHTML = svg;

    const svgEl = container.querySelector('svg');
    if (svgEl) {
      stampSvgDimensions(svgEl);
      patchForeignObjectLabels(svgEl);
      enhancePlaceholderGlyphs(svgEl);
      
      // Attempt icon injection
      if (window.MermaidIconInjector) {
        const iconDataUrl = `/api/icon-mappings?provider=${encodeURIComponent(provider)}`;
        [0, 250, 700].forEach(delay => {
          setTimeout(() => window.MermaidIconInjector.processAllDiagrams({ iconDataUrl }), delay);
        });
        setTimeout(() => {
          const iconCount = container.querySelectorAll('g.mermaid-icon').length;
          if (iconCount === 0 && typeof window.MermaidIconInjector._clearCache === 'function') {
            window.MermaidIconInjector._clearCache();
            window.MermaidIconInjector.processAllDiagrams({ iconDataUrl });
          }
          if (container.querySelectorAll('g.mermaid-icon').length === 0) {
            applyEmojiIconFallback(container.querySelector('svg'));
          }
          enhancePlaceholderGlyphs(container.querySelector('svg'));
        }, 1400);
      } else {
        applyEmojiIconFallback(svgEl);
      }
      
      if (onProgress) onProgress('rendered');
    }

    return svgEl;
  } catch (error) {
    if (skeletonEl) skeletonEl.classList.add('hidden');
    console.error('[diagram-base] Render failed:', error);
    container.innerHTML = `<p class="text-red-500 p-8 text-sm">Diagram render failed: ${error.message}</p>`;
    return null;
  }
}
