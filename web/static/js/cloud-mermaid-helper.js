import {
  patchForeignObjectLabels,
} from './diagram-base.js';
import {
  sanitizeMermaidSource,
  stampSvgDimensions,
} from './diagram-shared.js';

const CLOUD_MERMAID_CONFIG = {
  startOnLoad: false,
  theme: 'dark',
  securityLevel: 'loose',
  maxTextSize: 500000,
  flowchart: {
    useMaxWidth: false,
    htmlLabels: false,
  },
  onError: (err) => console.error('[Mermaid] Rendering error:', err?.message || err),
};

let mermaidInitialized = false;

export function ensureCloudMermaidInitialized() {
  if (mermaidInitialized) return true;
  if (!window.mermaid) return false;
  window.mermaid.initialize(CLOUD_MERMAID_CONFIG);
  mermaidInitialized = true;
  return true;
}

export async function waitForCloudMermaid(timeoutMs = 10000) {
  const started = Date.now();
  while (!ensureCloudMermaidInitialized()) {
    if (Date.now() - started > timeoutMs) {
      throw new Error(window.__triageMermaidLoadError || 'Mermaid failed to initialize.');
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
}

function postProcessSvg(svgEl) {
  if (!svgEl) return;
  patchForeignObjectLabels(svgEl);
  stampSvgDimensions(svgEl);
}

export async function renderMermaidSource({ source, rootEl, onRendered } = {}) {
  if (!rootEl) return null;
  const mermaidSource = sanitizeMermaidSource(source);
  if (!String(mermaidSource || '').trim()) return null;

  await waitForCloudMermaid();

  const renderId = `cloud_mermaid_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  const rendered = await window.mermaid.render(renderId, mermaidSource);
  rootEl.innerHTML = rendered.svg || '';
  const svg = rootEl.querySelector('svg');
  if (svg) {
    postProcessSvg(svg);
    if (typeof onRendered === 'function') {
      await onRendered(svg);
    }
  }
  return svg;
}

export async function renderMermaidContainer(container, options = {}) {
  if (!container) return null;
  const blocks = Array.from(container.querySelectorAll('.mermaid'));
  let lastSvg = null;
  for (const block of blocks) {
    const svg = await renderMermaidSource({
      source: block.textContent || '',
      rootEl: block,
      onRendered: options?.onRendered,
    });
    lastSvg = svg || lastSvg;
  }
  return lastSvg;
}

window.TriageMermaid = {
  render: renderMermaidContainer,
  renderContainer: renderMermaidContainer,
  renderSource: renderMermaidSource,
  run: renderMermaidContainer,
  ensureReady: waitForCloudMermaid,
};
