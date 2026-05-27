/**
 * diagram-viewer.js — Standalone fullscreen diagram viewer logic (/diagrams/<id>).
 *
 * Used exclusively by diagram_viewer.html. Imports base diagram utilities
 * and provider-specific rendering logic from diagram-base.js.
 */

import {
  PROVIDER_META,
  ensureMermaidInitialized,
  waitForMermaid,
  applyDiagramScale,
  autoFitDiagram,
  patchForeignObjectLabels,
  enhancePlaceholderGlyphs,
  applyEmojiIconFallback,
  exportDiagramPNG,
  renderDiagramCore,
} from './diagram-base.js';

import {
  sanitizeMermaidSource,
  stampSvgDimensions,
} from './diagram-shared.js';

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

// ── Scale helpers (wrap base module functions with local state) ───────────────

function applyScale(s) {
  currentScale = applyDiagramScale(container, s);
}

function autoFit() {
  fitScale = autoFitDiagram(container, scrollEl);
}

// ── SVG post-processing (viewer-specific emoji icon fallback) ────────────────

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

// ── Diagram autocomplete selector ────────────────────────────────────────────

(function initDiagramSelector() {
  const input   = document.getElementById('diagram-search-input');
  const results = document.getElementById('diagram-search-results');
  if (!input || !results) return;

  let allDiagrams = [];

  async function loadDiagrams() {
    try {
      const res = await fetch('/api/diagrams/list');
      const data = await res.json();
      allDiagrams = data.diagrams || [];
    } catch (_) {}
  }

  function formatDate(iso) {
    if (!iso) return '';
    try {
      return new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
    } catch (_) { return iso.slice(0, 10); }
  }

  function renderResults(query) {
    const q = query.trim().toLowerCase();
    const matches = q
      ? allDiagrams.filter(d =>
          d.experiment_id.toLowerCase().includes(q) ||
          d.repo_name.toLowerCase().includes(q) ||
          d.providers.join(' ').toLowerCase().includes(q)
        )
      : allDiagrams;

    results.innerHTML = '';
    if (!matches.length) {
      results.innerHTML = '<li class="px-3 py-2 text-[#7d8590]">No diagrams found</li>';
    } else {
      matches.forEach(d => {
        const li = document.createElement('li');
        li.className = 'px-3 py-2 cursor-pointer hover:bg-[#21262d] transition-colors border-b border-[#21262d] last:border-0';
        li.innerHTML = `
          <div class="flex items-center justify-between gap-2">
            <span class="font-mono text-[#388bfd] font-semibold">${d.experiment_id}</span>
            <span class="text-[#484f58]">${formatDate(d.created_at)}</span>
          </div>
          <div class="mt-0.5 flex items-center gap-2 text-[#7d8590]">
            ${d.repo_name ? `<span class="truncate">${d.repo_name}</span>` : ''}
            <span class="text-[#484f58]">${d.providers.join(', ')}</span>
          </div>`;
        li.addEventListener('click', () => {
          const url = new URL(`/diagrams/${encodeURIComponent(d.experiment_id)}`, window.location.origin);
          window.location.href = url.toString();
        });
        results.appendChild(li);
      });
    }
    results.classList.remove('hidden');
  }

  input.addEventListener('focus', async () => {
    if (!allDiagrams.length) await loadDiagrams();
    renderResults(input.value);
  });

  input.addEventListener('input', () => renderResults(input.value));

  // Keyboard navigation
  input.addEventListener('keydown', e => {
    const items = Array.from(results.querySelectorAll('li'));
    const active = results.querySelector('li.bg-\\[\\#21262d\\]') || results.querySelector('li[data-active]');
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const idx = items.indexOf(active);
      const next = e.key === 'ArrowDown'
        ? items[Math.min(idx + 1, items.length - 1)]
        : items[Math.max(idx - 1, 0)];
      items.forEach(li => li.removeAttribute('data-active') || li.classList.remove('!bg-[#21262d]'));
      if (next) { next.setAttribute('data-active', ''); next.scrollIntoView({ block: 'nearest' }); }
    } else if (e.key === 'Enter') {
      const activeItem = results.querySelector('li[data-active]');
      if (activeItem) activeItem.click();
    } else if (e.key === 'Escape') {
      results.classList.add('hidden');
      input.blur();
    }
  });

  // Close when clicking outside
  document.addEventListener('click', e => {
    if (!document.getElementById('diagram-selector-wrap')?.contains(e.target)) {
      results.classList.add('hidden');
    }
  });

  // Pre-load in background so first focus is instant
  loadDiagrams();
})();

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
