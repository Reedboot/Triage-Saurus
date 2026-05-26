/**
 * diagram-viewer.js — Standalone fullscreen diagram viewer logic (/diagrams/<id>).
 *
 * Used exclusively by diagram_viewer.html. Imports shared utilities from
 * diagram-shared.js so config, SVG helpers, and sanitisation stay in one place.
 */

import {
  getMermaidConfig,
  sanitizeMermaidSource,
  stampSvgDimensions,
} from './diagram-shared.js';

// ── Provider metadata ─────────────────────────────────────────────────────────

const PROVIDER_META = {
  aws:        { label: 'AWS',        color: '#f97316', icon: '/static/assets/icons/aws/account.svg' },
  azure:      { label: 'Azure',      color: '#3b82f6', icon: '/static/assets/icons/azure/compute/aks.svg' },
  gcp:        { label: 'GCP',        color: '#22c55e', icon: '/static/assets/icons/gcp/Cloud_Storage/SVG/cloud-storage.svg' },
  kubernetes: { label: 'Kubernetes', color: '#6366f1', icon: '/static/assets/icons/kubernetes/cluster.svg' },
  alicloud:   { label: 'AliCloud',   color: '#f59e0b', icon: null },
  oci:        { label: 'OCI',        color: '#e11d48', icon: '/static/assets/icons/oci/cloud.svg' },
};

// ── State ─────────────────────────────────────────────────────────────────────

let activeProvider  = window.__diagramViewerConfig?.activeProvider ?? 'azure';
let currentScale    = 1;
let fitScale        = 1;
let isFullscreen    = false;
let renderSeq       = 0;

// ── DOM refs ──────────────────────────────────────────────────────────────────

const scrollEl    = document.getElementById('mermaid-scroll');
const container   = document.getElementById('diagram-container');
const skeleton    = document.getElementById('skeleton');
const mainEl      = document.getElementById('diagram-main');
const headerTitle = document.getElementById('header-title');
const headerProv  = document.getElementById('header-provider');
const iconImg     = document.getElementById('provider-icon-img');
const iconDot     = document.getElementById('provider-icon-dot');

// ── Mermaid initialisation ────────────────────────────────────────────────────

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

// ── Read embedded diagram code ────────────────────────────────────────────────

function getDiagramCode(provider) {
  const el = document.querySelector(
    `script[type="application/json"][data-provider="${provider.replace(/"/g, '\\"')}"]`
  );
  if (!el) return null;
  const code = JSON.parse(el.textContent);
  const cssCode = el.getAttribute('data-css') || '';
  return cssCode ? (cssCode + '\n' + code) : code;
}

// ── Scale helpers ─────────────────────────────────────────────────────────────

function applyScale(s) {
  currentScale = Math.min(4, Math.max(0.1, s));
  container.style.transform = `scale(${currentScale})`;
}

function autoFit() {
  const svgEl = container.querySelector('svg');
  if (!svgEl) return;

  const cw = scrollEl.clientWidth  - 48;
  const ch = scrollEl.clientHeight - 48;
  if (cw <= 0 || ch <= 0) return;

  let sw = parseFloat(svgEl.getAttribute('width'))  || 0;
  let sh = parseFloat(svgEl.getAttribute('height')) || 0;
  if (!sw || !sh) {
    const vb = svgEl.viewBox.baseVal;
    sw = vb.width  || svgEl.scrollWidth;
    sh = vb.height || svgEl.scrollHeight;
  }

  if (sw > 0 && sh > 0) {
    fitScale = Math.min(cw / sw, ch / sh) * 0.90;
    fitScale = Math.min(4, Math.max(0.40, fitScale));
  } else {
    fitScale = 1;
  }
  applyScale(fitScale);
  scrollEl.scrollLeft = 0;
  scrollEl.scrollTop  = 0;
}

// ── SVG post-processing ───────────────────────────────────────────────────────

// Some browsers fail to paint foreignObject labels reliably — create SVG text fallbacks.
function patchForeignObjectLabels(svgEl) {
  if (!svgEl) return;
  const ns = 'http://www.w3.org/2000/svg';
  Array.from(svgEl.querySelectorAll('foreignObject')).forEach((fo, idx) => {
    if (fo.querySelector('img, image, svg')) return; // keep icon-bearing FOs intact
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

function iconEmojiForClassString(classString) {
  const cls = (classString || '').toLowerCase();
  if (!cls.includes('icon-') && !cls.includes('icon_')) return '';
  if (cls.includes('cluster'))   return '☸️';
  if (cls.includes('namespace')) return '🧭';
  if (cls.includes('deployment')) return '📦';
  if (cls.includes('service'))   return '🔌';
  if (cls.includes('pod'))       return '🧩';
  if (cls.includes('ingress'))   return '🌐';
  return '🔷';
}

function applyEmojiIconFallback(svgEl) {
  if (!svgEl) return;
  const PREFIXES = ['☸️','🧭','📦','🔌','🧩','🌐','🔷'];
  for (const node of svgEl.querySelectorAll('g.node, g.cluster')) {
    const classParts = [node.getAttribute('class') || ''];
    node.querySelectorAll('[class]').forEach(el => classParts.push(el.getAttribute('class') || ''));
    const emoji = iconEmojiForClassString(classParts.join(' '));
    if (!emoji) continue;
    const label = node.querySelector('text.fo-fallback-label, text.nodeLabel, text');
    if (!label) continue;
    const current = (label.textContent || '').trim();
    if (!current || PREFIXES.some(p => current.startsWith(p))) continue;
    label.textContent = `${emoji} ${current}`;
  }
}

function enhancePlaceholderGlyphs(svgEl) {
  if (!svgEl) return;
  for (const node of svgEl.querySelectorAll('g.node')) {
    node.querySelectorAll('text').forEach(textEl => {
      const val = (textEl.textContent || '').trim();
      if (val !== 'S' && val !== 'NS') return;
      const descriptor = (
        (node.querySelector('text.fo-fallback-label')?.textContent || '') + ' ' +
        (node.textContent || '')
      ).toLowerCase();
      let icon = '🔹';
      if (val === 'NS' || descriptor.includes('namespace')) icon = '🧭';
      else if (descriptor.includes('(service'))             icon = '🔌';
      else if (descriptor.includes('(deployment'))          icon = '📦';
      else if (descriptor.includes('(pod'))                 icon = '🧩';
      else if (descriptor.includes('internet'))             icon = '🌐';
      if (val !== icon) {
        textEl.textContent = icon;
        textEl.style.fontSize  = '16px';
        textEl.style.fontWeight = '600';
      }
    });
  }
}

// ── Header / provider icon ────────────────────────────────────────────────────

function updateHeaderIcon(provider) {
  const meta = PROVIDER_META[provider] || { label: provider.toUpperCase(), color: '#94a3b8', icon: null };
  headerProv.textContent = meta.label;

  if (meta.icon) {
    iconImg.alt   = meta.label + ' logo';
    iconImg.src   = meta.icon;
    iconImg.style.display = 'block';
    iconDot.style.display = 'none';
  } else {
    iconImg.style.display = 'none';
    iconDot.style.display = 'block';
    iconDot.style.background = meta.color;
  }

  const faviconLink = document.getElementById('favicon-link');
  if (faviconLink && meta.icon) faviconLink.href = meta.icon;
}

// ── Main render ───────────────────────────────────────────────────────────────

async function renderDiagram(provider) {
  const seq  = ++renderSeq;
  const code = getDiagramCode(provider);

  skeleton.classList.remove('hidden');
  container.innerHTML = '';

  if (!code || !code.trim()) {
    skeleton.classList.add('hidden');
    container.innerHTML = '<p class="text-[#7d8590] p-8 text-sm">No diagram available for this provider.</p>';
    return;
  }

  try {
    await waitForMermaid();
    const sanitizedCode = sanitizeMermaidSource(code);
    const renderId = `mermaid-render-${seq}`;
    const { svg } = await window.mermaid.render(renderId, sanitizedCode.trim());
    if (seq !== renderSeq) return;

    skeleton.classList.add('hidden');
    container.innerHTML = svg;

    const svgEl = container.querySelector('svg');
    if (svgEl) {
      stampSvgDimensions(svgEl);
      patchForeignObjectLabels(svgEl);
      enhancePlaceholderGlyphs(svgEl);
    }

    let _fitAttempts = 0;
    function _tryFit() {
      const svgEl = container.querySelector('svg');
      const sw = parseFloat(svgEl?.getAttribute('width'))  || svgEl?.viewBox?.baseVal?.width  || svgEl?.scrollWidth  || 0;
      const sh = parseFloat(svgEl?.getAttribute('height')) || svgEl?.viewBox?.baseVal?.height || svgEl?.scrollHeight || 0;
      if ((sw > 0 && sh > 0) || _fitAttempts++ >= 8) {
        autoFit();
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
        }
      } else {
        requestAnimationFrame(_tryFit);
      }
    }
    requestAnimationFrame(() => requestAnimationFrame(_tryFit));
  } catch (err) {
    if (seq !== renderSeq) return;
    skeleton.classList.add('hidden');
    container.innerHTML = `<pre class="text-red-400 p-4 text-xs whitespace-pre-wrap font-mono">${
      String(err.message).replace(/</g, '&lt;')
    }</pre>`;
  }
}

// ── Provider switching ────────────────────────────────────────────────────────

async function switchProvider(provider) {
  activeProvider = provider;

  document.querySelectorAll('[role="tab"]').forEach(tab => {
    const active = tab.dataset.provider === provider;
    tab.setAttribute('aria-selected', active ? 'true' : 'false');
    tab.classList.toggle('tab-active', active);
  });

  const url = new URL(window.location.href);
  url.searchParams.set('provider', provider);
  history.pushState({ provider }, '', url.toString());

  const tabEl = document.querySelector(`[role="tab"][data-provider="${provider}"]`);
  const diagramTitle = tabEl?.dataset.title
    || (PROVIDER_META[provider]?.label || provider.toUpperCase()) + ' Architecture';
  document.title = `${diagramTitle} | Triage-Saurus`;
  headerTitle.textContent = diagramTitle;

  updateHeaderIcon(provider);
  await renderDiagram(provider);
}

// ── Fullscreen ────────────────────────────────────────────────────────────────

function toggleFullscreen() {
  isFullscreen = !isFullscreen;
  mainEl.classList.toggle('is-fullscreen', isFullscreen);
  const btnFs = document.getElementById('btn-fullscreen');
  btnFs.textContent = isFullscreen ? '✕' : '⛶';
  btnFs.title       = isFullscreen ? 'Exit fullscreen (Esc)' : 'Fullscreen (F11)';
  setTimeout(autoFit, 60);
}

// ── Back button ───────────────────────────────────────────────────────────────

document.getElementById('btn-back')?.addEventListener('click', () => {
  const from = new URLSearchParams(window.location.search).get('from');
  window.location.href = from || '/';
});

// ── PNG export ────────────────────────────────────────────────────────────────

document.getElementById('btn-export-png').addEventListener('click', async () => {
  const svg = container.querySelector('svg');
  if (!svg) return;
  try {
    const clone = svg.cloneNode(true);
    clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
    const bg = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    bg.setAttribute('width',  clone.getAttribute('width')  || '1200');
    bg.setAttribute('height', clone.getAttribute('height') || '800');
    bg.setAttribute('fill', '#0d1117');
    clone.insertBefore(bg, clone.firstChild);
    const svgData = new XMLSerializer().serializeToString(clone);
    const blob    = new Blob([svgData], { type: 'image/svg+xml' });
    const url     = URL.createObjectURL(blob);
    const img     = new Image();
    img.onload = () => {
      const w = parseInt(svg.getAttribute('width'))  || 1200;
      const h = parseInt(svg.getAttribute('height')) || 800;
      const canvas = document.createElement('canvas');
      canvas.width  = w;
      canvas.height = h;
      const ctx = canvas.getContext('2d');
      ctx.fillStyle = '#0d1117';
      ctx.fillRect(0, 0, w, h);
      ctx.drawImage(img, 0, 0);
      URL.revokeObjectURL(url);
      const a = document.createElement('a');
      a.href     = canvas.toDataURL('image/png');
      a.download = (document.title || 'diagram').replace(/[^a-z0-9_-]/gi, '_') + '.png';
      a.click();
    };
    img.src = url;
  } catch (err) {
    console.error('[PNG export]', err);
  }
});

// ── Share ─────────────────────────────────────────────────────────────────────

document.getElementById('btn-share').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(window.location.href);
    const lbl = document.getElementById('share-label');
    lbl.textContent = 'Copied!';
    setTimeout(() => { lbl.textContent = 'Share'; }, 2000);
  } catch (_) { /* clipboard unavailable */ }
});

// ── Zoom / fit / fullscreen button listeners ──────────────────────────────────

document.getElementById('btn-zoom-in')  .addEventListener('click', () => applyScale(currentScale * 1.2));
document.getElementById('btn-zoom-out') .addEventListener('click', () => applyScale(currentScale / 1.2));
document.getElementById('btn-fit')      .addEventListener('click', () => { applyScale(fitScale); scrollEl.scrollLeft = 0; scrollEl.scrollTop = 0; });
document.getElementById('btn-fullscreen').addEventListener('click', toggleFullscreen);

// ── Keyboard shortcuts ────────────────────────────────────────────────────────

document.addEventListener('keydown', e => {
  if (e.target.matches('input,textarea,select,[contenteditable]')) return;
  if (e.key === '+' || e.key === '=') { e.preventDefault(); applyScale(currentScale * 1.2); }
  if (e.key === '-')                   { e.preventDefault(); applyScale(currentScale / 1.2); }
  if (e.key === 'f' || e.key === 'F')  { e.preventDefault(); applyScale(fitScale); scrollEl.scrollLeft = 0; scrollEl.scrollTop = 0; }
  if (e.key === 'Escape') {
    e.preventDefault();
    if (isFullscreen) {
      toggleFullscreen();
    } else {
      const from = new URLSearchParams(window.location.search).get('from');
      window.location.href = from || '/';
    }
  }
});

// ── Ctrl+scroll zoom ──────────────────────────────────────────────────────────

scrollEl.addEventListener('wheel', e => {
  if (e.ctrlKey || e.metaKey) {
    e.preventDefault();
    applyScale(currentScale * (e.deltaY > 0 ? 0.9 : 1.1));
  }
}, { passive: false });

// ── Drag-to-pan ───────────────────────────────────────────────────────────────

let panning = false, panX = 0, panY = 0, sl0 = 0, st0 = 0;
scrollEl.addEventListener('mousedown', e => {
  if (e.target.closest('a,button')) return;
  panning = true;
  panX = e.pageX; panY = e.pageY;
  sl0 = scrollEl.scrollLeft; st0 = scrollEl.scrollTop;
});
document.addEventListener('mousemove', e => {
  if (!panning) return;
  scrollEl.scrollLeft = sl0 - (e.pageX - panX);
  scrollEl.scrollTop  = st0 - (e.pageY - panY);
});
document.addEventListener('mouseup', () => { panning = false; });

// ── Browser back/forward ──────────────────────────────────────────────────────

window.addEventListener('popstate', e => {
  const p = e.state?.provider
         || new URLSearchParams(location.search).get('provider')
         || activeProvider;
  switchProvider(p);
});

// ── Provider tab click listeners ──────────────────────────────────────────────
// Tabs are rendered by Jinja, so we attach listeners after DOM is ready.

document.querySelectorAll('[role="tab"][data-provider]').forEach(tab => {
  tab.addEventListener('click', () => switchProvider(tab.dataset.provider));
});

// ── Initial render ────────────────────────────────────────────────────────────

updateHeaderIcon(activeProvider);
renderDiagram(activeProvider);
