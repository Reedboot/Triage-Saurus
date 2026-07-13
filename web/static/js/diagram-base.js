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
  container.dataset.diagramScale = String(scaledVal);
  const svgEl = container.querySelector('svg');
  if (svgEl) {
    const baseW = parseFloat(svgEl.dataset.baseWidth || '') ||
      parseFloat(svgEl.getAttribute('width') || '') ||
      svgEl.viewBox?.baseVal?.width ||
      svgEl.scrollWidth ||
      0;
    const baseH = parseFloat(svgEl.dataset.baseHeight || '') ||
      parseFloat(svgEl.getAttribute('height') || '') ||
      svgEl.viewBox?.baseVal?.height ||
      svgEl.scrollHeight ||
      0;
    if (baseW > 0 && baseH > 0) {
      svgEl.dataset.baseWidth = String(baseW);
      svgEl.dataset.baseHeight = String(baseH);
      svgEl.style.width = `${baseW * scaledVal}px`;
      svgEl.style.height = `${baseH * scaledVal}px`;
      svgEl.setAttribute('width', `${baseW * scaledVal}`);
      svgEl.setAttribute('height', `${baseH * scaledVal}`);
      container.style.transform = '';
      container.style.transformOrigin = '';
      return scaledVal;
    }
  }
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

  let sw = parseFloat(svgEl.dataset.baseWidth || '') || 0;
  let sh = parseFloat(svgEl.dataset.baseHeight || '') || 0;
  if (!sw || !sh) {
    sw = parseFloat(svgEl.getAttribute('width')) || 0;
    sh = parseFloat(svgEl.getAttribute('height')) || 0;
  }
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

function sanitizeFileName(name) {
  return String(name || 'diagram')
    .replace(/[^a-z0-9_-]+/gi, '_')
    .replace(/^_+|_+$/g, '') || 'diagram';
}

function getSvgExportBounds(svgEl) {
  const fallback = {
    x: 0,
    y: 0,
    width: parseFloat(svgEl?.getAttribute('width') || '') || svgEl?.clientWidth || svgEl?.scrollWidth || 0,
    height: parseFloat(svgEl?.getAttribute('height') || '') || svgEl?.clientHeight || svgEl?.scrollHeight || 0,
  };

  if (!svgEl) return fallback;

  let bounds = null;

  try {
    const vb = svgEl.viewBox?.baseVal;
    if (vb && vb.width > 0 && vb.height > 0) {
      bounds = { x: vb.x, y: vb.y, width: vb.width, height: vb.height };
    }
  } catch (_) {}

  try {
    const box = svgEl.getBBox?.();
    if (box && box.width > 0 && box.height > 0) {
      if (bounds) {
        const minX = Math.min(bounds.x, box.x);
        const minY = Math.min(bounds.y, box.y);
        const maxX = Math.max(bounds.x + bounds.width, box.x + box.width);
        const maxY = Math.max(bounds.y + bounds.height, box.y + box.height);
        bounds = {
          x: minX,
          y: minY,
          width: Math.max(1, maxX - minX),
          height: Math.max(1, maxY - minY),
        };
      } else {
        bounds = {
          x: box.x,
          y: box.y,
          width: box.width,
          height: box.height,
        };
      }
    }
  } catch (_) {}

  return bounds && bounds.width > 0 && bounds.height > 0 ? bounds : fallback;
}

function resolveExportScale(bounds) {
  const preferred = Math.max(2, Math.min(4, window.devicePixelRatio || 1));
  const maxSide = 16384;
  const maxArea = 268435456;
  const sideScale = Math.min(
    maxSide / Math.max(1, bounds.width),
    maxSide / Math.max(1, bounds.height),
  );
  const areaScale = Math.sqrt(maxArea / Math.max(1, bounds.width * bounds.height));
  return Math.max(1, Math.min(preferred, sideScale, areaScale));
}

/**
 * Collect icons rendered outside the SVG vector tree so they survive PNG export.
 *
 * Two sources are checked:
 *  1. <img class="ni"> elements inside Mermaid <foreignObject> labels – these are
 *     visible in the browser but silently dropped when the SVG is drawn to canvas
 *     (browsers refuse to render <foreignObject> for SVGs loaded as images).
 *  2. Any sibling overlay element marked [data-diagram-icon-overlay] (the Firefox
 *     HTML overlay that replaces foreignObject icons on that browser).
 *
 * For each icon we record its position in SVG coordinate space + a data-URL payload
 * so it can be injected as a native SVG <image> element in the export clone.
 */
async function _collectExportIcons(svgEl) {
  const icons = [];
  const ctm = svgEl.getScreenCTM?.();
  if (!ctm) return icons;

  const invCTM = ctm.inverse();
  const seenUrls = new Map();

  const fetchDataUrl = async (src) => {
    if (!src) return null;
    if (seenUrls.has(src)) return seenUrls.get(src);
    if (src.startsWith('data:')) { seenUrls.set(src, src); return src; }
    try {
      const resp = await fetch(src);
      if (!resp.ok) return null;
      const blob = await resp.blob();
      const dataUrl = await new Promise((resolve) => {
        const reader = new FileReader();
        reader.onload = () => resolve(/** @type {string} */ (reader.result));
        reader.onerror = () => resolve(null);
        reader.readAsDataURL(blob);
      });
      seenUrls.set(src, dataUrl);
      return dataUrl;
    } catch { return null; }
  };

  const toSvgRect = (domRect) => {
    if (!domRect || domRect.width <= 0 || domRect.height <= 0) return null;
    const pt = (x, y) => {
      const p = svgEl.createSVGPoint();
      p.x = x; p.y = y;
      return p.matrixTransform(invCTM);
    };
    const tl = pt(domRect.left, domRect.top);
    const br = pt(domRect.right, domRect.bottom);
    return { x: tl.x, y: tl.y, width: Math.max(1, br.x - tl.x), height: Math.max(1, br.y - tl.y) };
  };

  // Source 1: img.ni inside <foreignObject> (all browsers; visibility:hidden still
  // has valid layout, so getBoundingClientRect() returns the correct rect).
  for (const img of svgEl.querySelectorAll('img.ni')) {
    const src = img.getAttribute('src') || '';
    if (!src) continue;
    const rect = toSvgRect(img.getBoundingClientRect());
    if (!rect) continue;
    const href = await fetchDataUrl(src);
    if (href) icons.push({ href, ...rect });
  }

  // Source 2: Firefox HTML icon overlay (sibling div carrying visible <img> elements).
  const overlay = svgEl.parentElement?.querySelector('[data-diagram-icon-overlay]');
  if (overlay) {
    for (const img of overlay.querySelectorAll('img')) {
      const src = img.getAttribute('src') || '';
      if (!src) continue;
      const rect = toSvgRect(img.getBoundingClientRect());
      if (!rect) continue;
      const href = await fetchDataUrl(src);
      if (href) icons.push({ href, ...rect });
    }
  }

  return icons;
}

function _injectIconsIntoSvgClone(svgClone, icons) {
  for (const { href, x, y, width, height } of icons) {
    const el = document.createElementNS('http://www.w3.org/2000/svg', 'image');
    el.setAttribute('href', href);
    el.setAttribute('x', String(x));
    el.setAttribute('y', String(y));
    el.setAttribute('width', String(width));
    el.setAttribute('height', String(height));
    el.setAttribute('preserveAspectRatio', 'xMidYMid meet');
    svgClone.appendChild(el);
  }
}

export async function exportDiagramPNG(svgEl, diagramName = 'diagram') {
  if (!svgEl) return;

  try {
    // Collect icons from the live DOM before cloning – layout data is only
    // available while the element is attached and painted.
    const exportIcons = await _collectExportIcons(svgEl);

    const bounds = getSvgExportBounds(svgEl);
    const scale = resolveExportScale(bounds);
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d');
    if (!ctx) {
      console.error('[diagram-base] Could not get canvas 2D context');
      return;
    }

    canvas.width = Math.max(1, Math.ceil(bounds.width * scale));
    canvas.height = Math.max(1, Math.ceil(bounds.height * scale));
    ctx.setTransform(scale, 0, 0, scale, 0, 0);
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
    ctx.fillStyle = '#0d1117';
    ctx.fillRect(0, 0, bounds.width, bounds.height);

    const clone = svgEl.cloneNode(true);
    if (!clone.getAttribute('xmlns')) {
      clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
    }
    if (!clone.getAttribute('xmlns:xlink')) {
      clone.setAttribute('xmlns:xlink', 'http://www.w3.org/1999/xlink');
    }
    clone.setAttribute('viewBox', `${bounds.x} ${bounds.y} ${bounds.width} ${bounds.height}`);
    clone.setAttribute('width', `${bounds.width}`);
    clone.setAttribute('height', `${bounds.height}`);
    clone.style.removeProperty('max-width');
    clone.style.removeProperty('width');
    clone.style.removeProperty('height');

    const bg = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    bg.setAttribute('x', String(bounds.x));
    bg.setAttribute('y', String(bounds.y));
    bg.setAttribute('width', String(bounds.width));
    bg.setAttribute('height', String(bounds.height));
    bg.setAttribute('fill', '#0d1117');
    clone.insertBefore(bg, clone.firstChild);

    // Inject icons that were invisible to the SVG serialiser (foreignObject imgs
    // and Firefox HTML overlay imgs) as native <image> elements with data URLs.
    if (exportIcons.length) {
      _injectIconsIntoSvgClone(clone, exportIcons);
    }

    const serializer = new XMLSerializer();
    const svgString = serializer.serializeToString(clone);
    const blob = new Blob([svgString], { type: 'image/svg+xml;charset=utf-8' });
    const url = URL.createObjectURL(blob);

    const img = new Image();
    img.onload = () => {
      try {
        ctx.drawImage(img, 0, 0, bounds.width, bounds.height);
        const pngUrl = canvas.toDataURL('image/png');
        const link = document.createElement('a');
        link.href = pngUrl;
        link.download = `${sanitizeFileName(diagramName)}.png`;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
      } finally {
        URL.revokeObjectURL(url);
      }
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
