/**
 * diagram-render.js — Mermaid rendering, SVG stamping, and diagram tab building.
 */
import { state }         from './state.js';
import {
  initPanZoom,
  scheduleDiagramFit,
  saveDiagramState,
  loadDiagramState,
} from './diagram-zoom.js';
import {
  getMermaidConfig,
  sanitizeMermaidSource,
  stampSvgDimensions,
  injectDiagramIcons,
} from './diagram-shared.js';

// ── Helpers ───────────────────────────────────────────────────────────────────

export { sanitizeMermaidSource };

function sanitizeMermaidForParseFallback(source) {
  return String(source || '')
    // Terraform/resource labels sometimes include escaped control sequences
    // (e.g. format("%s-%s",\n...)) that Mermaid cannot parse reliably.
    .replace(/\\[nrt]/g, ' ')
    // Mermaid labels are more stable with apostrophes than escaped double-quotes.
    .replace(/\\"/g, "'")
    .replace(/\s+/g, ' ')
    .trim();
}

function shouldUseParseSafeSource(source) {
  return /\\[nrt]|\\\"/.test(String(source || ''));
}

function cleanupMermaidRenderArtifact(renderId) {
  if (!renderId) return;
  document.getElementById(`d${renderId}`)?.remove();
}

function cleanupStaleMermaidArtifacts() {
  document
    .querySelectorAll('div[id^="ddiag_"], div[id^="ddiagfb_"]')
    .forEach((el) => el.remove());
}

export function getActiveDiagramView() {
  const diagramViews = document.getElementById('diagram-views');
  if (!diagramViews) return null;
  return diagramViews.querySelector('.diagram-view.active');
}

export function getDiagramSource(diagramView) {
  if (!diagramView) return null;
  const preEl = diagramView.querySelector('pre.mermaid');
  if (!preEl) return null;
  return sanitizeMermaidSource(preEl.dataset.source || preEl.textContent || '');
}

export function setDiagramPlaceholderVisible(visible) {
  const diagramViews = document.getElementById('diagram-views');
  if (!diagramViews) return;
  diagramViews.classList.toggle('has-diagrams', !visible);
  const placeholder = diagramViews.querySelector(
    '#diagram-placeholder, .diagram-placeholder, .empty-state'
  );
  if (placeholder) {
    placeholder.hidden = !visible;
    if (visible) {
      placeholder.removeAttribute('aria-hidden');
    } else {
      placeholder.setAttribute('aria-hidden', 'true');
    }
  }
}

export function setDiagramLoadingVisible(visible, message = 'Loading architecture diagram…') {
  const overlay = document.getElementById('diagram-loading-overlay');
  if (!overlay) return;
  const text = document.getElementById('diagram-loading-text');
  if (text) text.textContent = message;
  overlay.hidden = !visible;
}

function createDiagramPlaceholder(message = 'Architecture diagram will appear after the scan completes.') {
  const placeholder = document.createElement('div');
  placeholder.className = 'diagram-placeholder';
  placeholder.id = 'diagram-placeholder';
  placeholder.innerHTML = `
    <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
      <rect x="3" y="3" width="7" height="7" />
      <rect x="14" y="3" width="7" height="8" />
      <rect x="3" y="14" width="7" height="7" />
      <rect x="14" y="14" width="7" height="7" />
    </svg>
    <p>${message}</p>
  `;
  return placeholder;
}

export function clearDiagrams(message = 'Architecture diagram will appear after the scan completes.') {
  const diagramViews = document.getElementById('diagram-views');
  const diagramTabs = document.getElementById('diagram-tabs');
  const diagramModeTabs = document.getElementById('diagram-mode-tabs');
  const diagramViewSummary = document.getElementById('diagram-view-summary');
  const zoomInner = document.getElementById('diagram-zoom-inner');

  state.storedDiagrams = [];
  state.currentDiagramIndex = 0;
  state.currentDiagramMode = 'connectivity';
  Object.keys(state.diagramStates).forEach(k => delete state.diagramStates[k]);

  if (zoomInner) zoomInner.classList.remove('is-rendering');
  if (diagramTabs) diagramTabs.innerHTML = '';
  if (diagramModeTabs) diagramModeTabs.innerHTML = '';
  if (diagramViewSummary) {
    diagramViewSummary.innerHTML = '';
    diagramViewSummary.style.display = 'none';
  }

  if (diagramViews) {
    diagramViews.replaceChildren(createDiagramPlaceholder(message));
  }
  cleanupStaleMermaidArtifacts();

  setDiagramPlaceholderVisible(true);
  setDiagramLoadingVisible(false);
}

function getDiagramViews(diagram) {
  return diagram && typeof diagram.views === 'object' && diagram.views ? diagram.views : {};
}

function getAvailableDiagramModes(diagrams) {
  const modes = new Set();
  (diagrams || []).forEach((diagram) => {
    Object.keys(getDiagramViews(diagram)).forEach((mode) => modes.add(mode));
  });
  return modes.size ? Array.from(modes) : ['connectivity'];
}

function getDiagramModePayload(diagram, mode) {
  const views = getDiagramViews(diagram);
  if (views[mode]) return views[mode];
  if (mode === 'connectivity') {
    return {
      code: diagram.code || '',
      css_code: diagram.css_code || '',
      attack_paths: diagram.attack_paths || [],
      asset_summary: diagram.asset_summary || {},
      description: '',
      legend: [],
    };
  }
  return null;
}

function isReactFlowMode() {
  return state.currentDiagramMode === 'react_flow';
}

function clearReactFlowRoots(container) {
  if (!container) return;
  container.querySelectorAll('.diagram-reactflow-host').forEach((host) => {
    try {
      host.__reactFlowRoot?.unmount?.();
    } catch (_) {}
    host.__reactFlowRoot = null;
  });
}

function setDiagramChromeForMode(mode) {
  const zoomWrap = document.getElementById('diagram-zoom-wrap');
  const controls = document.getElementById('inline-diagram-controls');
  const zoomInner = document.getElementById('diagram-zoom-inner');
  if (zoomWrap) zoomWrap.dataset.renderMode = mode;
  if (controls) controls.style.display = mode === 'react_flow' ? 'none' : '';
  if (mode === 'react_flow' && zoomInner) {
    zoomInner.style.transform = 'none';
    zoomInner.style.transition = '';
  }
}

function getReactFlowLibs() {
  const React = window.React;
  const ReactDOM = window.ReactDOM;
  const ReactFlowLib = window.ReactFlow;
  if (!React || !ReactDOM || !ReactFlowLib) return null;
  return {
    React,
    ReactDOM,
    ReactFlow: ReactFlowLib.ReactFlow,
    Background: ReactFlowLib.Background,
    Controls: ReactFlowLib.Controls,
    Handle: ReactFlowLib.Handle,
    MarkerType: ReactFlowLib.MarkerType,
    MiniMap: ReactFlowLib.MiniMap,
    Position: ReactFlowLib.Position,
  };
}

function normalizeReactFlowNode(node, libs) {
  const Position = libs?.Position || {};
  const sourcePosition = String(node.sourcePosition || 'right').toLowerCase() === 'left'
    ? Position.Left
    : Position.Right;
  const targetPosition = String(node.targetPosition || 'left').toLowerCase() === 'right'
    ? Position.Right
    : Position.Left;
  const style = Object.assign(
    {
      width: 260,
      borderRadius: 12,
      border: '1px solid #30363d',
      background: '#111827',
      color: '#e6edf3',
      boxShadow: '0 8px 20px rgba(0, 0, 0, 0.18)',
      padding: 0,
    },
    node.style || {}
  );
  return {
    id: String(node.id),
    type: node.type || 'repoNode',
    position: node.position || { x: 0, y: 0 },
    data: node.data || {},
    sourcePosition,
    targetPosition,
    draggable: false,
    selectable: false,
    style,
  };
}

function normalizeReactFlowEdge(edge, libs) {
  const MarkerType = libs?.MarkerType || {};
  const markerType = String(edge?.markerEnd?.type || '').toLowerCase();
  const fallbackColor = edge?.style?.stroke || '#94a3b8';
  return {
    id: String(edge.id),
    source: String(edge.source),
    target: String(edge.target),
    label: edge.label || '',
    type: edge.type || 'smoothstep',
    animated: !!edge.animated,
    data: edge.data || {},
    style: edge.style || {},
    markerEnd: {
      type: markerType.includes('arrow') ? (MarkerType.ArrowClosed || 'arrowclosed') : (MarkerType.ArrowClosed || 'arrowclosed'),
      color: edge?.markerEnd?.color || fallbackColor,
    },
  };
}

function renderReactFlowDiagram(container, payload) {
  const libs = getReactFlowLibs();
  if (!libs) {
    container.innerHTML = '<div class="diagram-reactflow-missing">React Flow libraries are not available.</div>';
    return;
  }

  const { React, ReactDOM, ReactFlow, Background, Controls, Handle, MiniMap, Position } = libs;
  const h = React.createElement;
  const nodes = (payload.nodes || []).map((node) => normalizeReactFlowNode(node, libs));
  const edges = (payload.edges || []).map((edge) => normalizeReactFlowEdge(edge, libs));

  if (!nodes.length) {
    container.innerHTML = '<div class="diagram-reactflow-empty">No graph data available for this view.</div>';
    return;
  }

  const nodeTypes = {
    repoNode: function RepoNode({ data }) {
      const tier = String(data?.tier || 'other');
      const tierLabel = {
        entry: 'Entry',
        api: 'API',
        backend: 'Backend',
        identity: 'Identity',
        data: 'Data',
        internet: 'Internet',
      }[tier] || 'Other';
      return h(
        'div',
        {
          className: `diagram-reactflow-node diagram-reactflow-node--${tier}`,
          style: {
            border: '1px solid currentColor',
            borderRadius: '12px',
            background: 'inherit',
            padding: '10px 12px',
            minHeight: '72px',
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'center',
            gap: '3px',
          },
          title: data?.resourceType || data?.typeLabel || data?.label,
        },
        h(Handle, { type: 'target', position: Position.Left, style: { background: 'currentColor', borderColor: 'currentColor' } }),
        h('div', { className: 'diagram-reactflow-node__tier' }, tierLabel),
        h('div', { className: 'diagram-reactflow-node__label' }, data?.label || 'Resource'),
        h('div', { className: 'diagram-reactflow-node__type' }, data?.typeLabel || data?.resourceType || ''),
        h(Handle, { type: 'source', position: Position.Right, style: { background: 'currentColor', borderColor: 'currentColor' } })
      );
    },
  };

  const ReactFlowDiagram = function ReactFlowDiagram() {
    return h(
      'div',
      { className: 'diagram-reactflow-shell' },
      h(
        ReactFlow,
        {
          nodes,
          edges,
          nodeTypes,
          fitView: true,
          fitViewOptions: { padding: 0.2 },
          minZoom: 0.15,
          maxZoom: 2.5,
          panOnDrag: true,
          zoomOnScroll: true,
          zoomOnPinch: true,
          nodesDraggable: false,
          nodesConnectable: false,
          elementsSelectable: false,
          proOptions: { hideAttribution: true },
          style: { width: '100%', height: '100%', background: '#0d1117' },
        },
        h(MiniMap, {
          nodeStrokeColor: (n) => n?.style?.borderColor || '#64748b',
          nodeColor: (n) => n?.style?.backgroundColor || '#111827',
          pannable: true,
          zoomable: true,
        }),
        h(Background, { gap: 16, size: 1, color: '#1f2937' }),
        h(Controls, { showInteractive: false })
      )
    );
  };

  const root = ReactDOM.createRoot(container);
  container.__reactFlowRoot = root;
  root.render(h(ReactFlowDiagram));
}

function renderDiagramModeTabs(diagrams) {
  const diagramModeTabs = document.getElementById('diagram-mode-tabs');
  if (!diagramModeTabs) return;

  const availableModes = getAvailableDiagramModes(diagrams);
  const defaultMode = diagrams.find((diagram) => diagram.default_view)?.default_view || 'connectivity';
  if (!availableModes.includes(state.currentDiagramMode)) {
    state.currentDiagramMode = availableModes.includes(defaultMode) ? defaultMode : availableModes[0];
  }

  diagramModeTabs.innerHTML = '';
  if (availableModes.length <= 1) return;

  const labels = {
    connectivity: 'Connectivity',
    attack_paths: 'Attack Paths',
    react_flow: 'React Flow',
  };

  availableModes.forEach((mode) => {
    const btn = document.createElement('button');
    btn.className = 'btn-small' + (mode === state.currentDiagramMode ? ' active' : '');
    btn.textContent = labels[mode] || mode;
    btn.dataset.mode = mode;
    btn.style.marginRight = '4px';
    btn.addEventListener('click', () => {
      if (state.currentDiagramMode === mode) return;
      state.currentDiagramMode = mode;
      renderDiagrams(state.storedDiagrams);
    });
    diagramModeTabs.appendChild(btn);
  });
}

function renderDiagramViewSummary(diagram) {
  const summaryEl = document.getElementById('diagram-view-summary');
  if (!summaryEl || !diagram) return;

  const payload = getDiagramModePayload(diagram, state.currentDiagramMode) || {};
  const attackPaths = payload.attack_paths || diagram.attack_paths || [];
  const assetSummary = payload.asset_summary || diagram.asset_summary || {};
  const legend = payload.legend || [];
  const counts = [
    ['Entry', assetSummary.entry_points],
    ['API', assetSummary.api_layer],
    ['Backends', assetSummary.backends],
    ['Data', assetSummary.data_stores],
    ['Public', assetSummary.public_assets],
  ].filter(([, value]) => Number.isFinite(value));

  const legendHtml = legend.length
    ? `<div style="margin-top:8px"><strong>Legend:</strong> ${legend.map((item) => String(item)).join(' | ')}</div>`
    : '';
  const countsHtml = counts.length
    ? `<div style="margin-top:8px"><strong>In view:</strong> ${counts.map(([label, value]) => `${label} ${value}`).join(' | ')}</div>`
    : '';
  const pathsHtml = attackPaths.length
    ? `<div style="margin-top:8px"><strong>Likely attack paths:</strong><ul style="margin:6px 0 0 18px;padding:0">${attackPaths.slice(0, 3).map((path) => `<li>${path.title || path.path || ''}</li>`).join('')}</ul></div>`
    : '';

  if (!payload.description && !legend.length && !counts.length && !attackPaths.length) {
    summaryEl.innerHTML = '';
    summaryEl.style.display = 'none';
    return;
  }

  summaryEl.innerHTML = `
    <div><strong>${payload.title || 'Architecture view'}</strong></div>
    ${payload.description ? `<div style="margin-top:6px">${payload.description}</div>` : ''}
    ${countsHtml}
    ${legendHtml}
    ${pathsHtml}
  `;
  summaryEl.style.display = 'block';
}

// ── Per-container Mermaid rendering ───────────────────────────────────────────

export async function renderMermaidInContainer(container) {
  cleanupStaleMermaidArtifacts();
  const mermaidBlocks = Array.from(container.querySelectorAll('.mermaid'));
  for (let idx = 0; idx < mermaidBlocks.length; idx++) {
    const block  = mermaidBlocks[idx];
    const source = sanitizeMermaidSource(block.dataset.source || block.textContent || '');
    if (!source.trim()) continue;
    const preferredSource = shouldUseParseSafeSource(source)
      ? sanitizeMermaidForParseFallback(source)
      : source;
    const renderId = `diag_${Date.now()}_${idx}_${Math.random().toString(36).slice(2, 8)}`;
    try {
      const rendered = await window.mermaid.render(renderId, preferredSource);
      block.innerHTML = rendered.svg || '';
      const svg = block.querySelector('svg');
      if (svg) stampSvgDimensions(svg);
      cleanupMermaidRenderArtifact(renderId);
    } catch (err) {
      cleanupMermaidRenderArtifact(renderId);
      const fallbackSource = sanitizeMermaidForParseFallback(source);
      if (fallbackSource && fallbackSource !== preferredSource) {
        const fallbackRenderId = `diagfb_${Date.now()}_${idx}_${Math.random().toString(36).slice(2, 8)}`;
        try {
          const rendered = await window.mermaid.render(fallbackRenderId, fallbackSource);
          block.innerHTML = rendered.svg || '';
          const svg = block.querySelector('svg');
          if (svg) stampSvgDimensions(svg);
          cleanupMermaidRenderArtifact(fallbackRenderId);
          continue;
        } catch (_) {
          cleanupMermaidRenderArtifact(fallbackRenderId);
          // Fall through to original error log below
        }
      }
      console.error('[Mermaid] Rendering error:', err.message || err);
    }
  }
}

// ── Main render entry point ───────────────────────────────────────────────────

export function renderDiagrams(diagrams) {
  if (!Array.isArray(diagrams) || !diagrams.length) {
    return;
  }

  state.storedDiagrams = diagrams;

  if (window.MermaidIconInjector && window.MermaidIconInjector._clearCache) {
    window.MermaidIconInjector._clearCache();
  }

  const diagramViews = document.getElementById('diagram-views');
  const diagramTabs  = document.getElementById('diagram-tabs');
  const zoomInner = document.getElementById('diagram-zoom-inner');

  if (!diagramViews || !diagramTabs) {
    console.error('[renderDiagrams] Missing required elements!');
    return;
  }

  setDiagramLoadingVisible(true, 'Rendering architecture diagram…');
  if (zoomInner) zoomInner.classList.add('is-rendering');
  cleanupStaleMermaidArtifacts();
  clearReactFlowRoots(diagramViews);
  setDiagramChromeForMode(state.currentDiagramMode);

  // Reset per-diagram zoom state
  Object.keys(state.diagramStates).forEach(k => delete state.diagramStates[k]);
  const initialIndex = Math.min(state.currentDiagramIndex || 0, diagrams.length - 1);

  setDiagramPlaceholderVisible(false);
  const placeholder = diagramViews.querySelector(
    '#diagram-placeholder, .diagram-placeholder, .empty-state'
  );
  if (placeholder) placeholder.remove();

  diagramTabs.innerHTML = '';
  diagramViews.querySelectorAll('.diagram-view').forEach(el => el.remove());
  renderDiagramModeTabs(diagrams);

  let addedCount = 0;
  const reactFlowMode = isReactFlowMode();
  diagrams.forEach((diag, idx) => {
    const title = diag.title || `Diagram ${idx + 1}`;
    const modePayload = getDiagramModePayload(diag, state.currentDiagramMode);
    const code  = sanitizeMermaidSource((modePayload && modePayload.code) || diag.code).trim();
    const hasReactFlow = reactFlowMode && modePayload && Array.isArray(modePayload.nodes) && modePayload.nodes.length;
    if (!code && !hasReactFlow) {
      console.warn('[renderDiagrams] Skipping diagram', idx, 'no code');
      return;
    }
    addedCount++;

    const tabBtn = document.createElement('button');
    tabBtn.className       = 'btn-small' + (idx === initialIndex ? ' active' : '');
    tabBtn.dataset.idx     = idx;
    tabBtn.textContent     = title;
    tabBtn.style.marginRight = '4px';
    diagramTabs.appendChild(tabBtn);

    const viewDiv = document.createElement('div');
    viewDiv.className   = 'diagram-view' + (idx === initialIndex ? ' active' : '');
    viewDiv.dataset.idx = idx;
    viewDiv.dataset.renderMode = hasReactFlow ? 'react_flow' : 'mermaid';

    if (hasReactFlow) {
      viewDiv.style.minHeight = '520px';
      viewDiv.style.height = '100%';
      const sourceCode = sanitizeMermaidSource(diag.code || '').trim();
      if (sourceCode) {
        const pre = document.createElement('pre');
        pre.className = 'mermaid';
        pre.style.display = 'none';
        pre.dataset.source = sourceCode;
        pre.textContent = sourceCode;
        viewDiv.appendChild(pre);
      }

      const host = document.createElement('div');
      host.className = 'diagram-reactflow-host';
      host.style.width = '100%';
      host.style.height = '520px';
      host.style.minHeight = '520px';
      host.style.background = '#0d1117';
      viewDiv.appendChild(host);
    } else {
      const pre = document.createElement('pre');
      pre.className       = 'mermaid';
      pre.style.background = 'transparent';
      pre.dataset.source  = code;
      pre.textContent     = code;
      viewDiv.appendChild(pre);
    }
    diagramViews.appendChild(viewDiv);

    tabBtn.addEventListener('click', () => {
      saveDiagramState(state.currentDiagramIndex);
      diagramTabs.querySelectorAll('button').forEach(b => b.classList.remove('active'));
      tabBtn.classList.add('active');
      diagramViews.querySelectorAll('.diagram-view').forEach(v => {
        v.classList.toggle('active', v.dataset.idx === String(idx));
      });
      const selectedView = diagramViews.querySelector(`.diagram-view[data-idx="${idx}"]`);
      const selectedMode = selectedView?.dataset.renderMode || 'mermaid';
      if (selectedMode === 'react_flow') {
        setDiagramLoadingVisible(false);
      } else if (selectedView && !selectedView.querySelector('svg')) {
        setDiagramLoadingVisible(true, 'Loading selected diagram…');
      } else {
        setDiagramLoadingVisible(false);
      }
      state.currentDiagramIndex = idx;
      renderDiagramViewSummary(diagrams[idx]);
      loadDiagramState(idx);
      if (window.MermaidIconInjector) {
        setTimeout(() => window.MermaidIconInjector.processAllDiagrams(), 100);
      }
    });
  });

  const doRender = () => {
    window.mermaid.initialize(getMermaidConfig());
    if (reactFlowMode) {
      try {
        diagramViews.querySelectorAll('.diagram-view').forEach((view, idx) => {
          const payload = getDiagramModePayload(diagrams[idx], state.currentDiagramMode) || {};
          const host = view.querySelector('.diagram-reactflow-host');
          if (host && payload.nodes && payload.edges) {
            renderReactFlowDiagram(host, payload);
          }
        });
        state.currentDiagramIndex = initialIndex;
        renderDiagramViewSummary(diagrams[initialIndex]);
        if (zoomInner) zoomInner.classList.remove('is-rendering');
        setDiagramLoadingVisible(false);
      } catch (e) {
        if (zoomInner) zoomInner.classList.remove('is-rendering');
        setDiagramLoadingVisible(false);
        console.error('[Diagrams] React Flow render error:', e);
      }
      return;
    }

    renderMermaidInContainer(diagramViews)
      .then(() => {
        setTimeout(() => {
          initPanZoom();
          state.currentDiagramIndex = initialIndex;
          renderDiagramViewSummary(diagrams[initialIndex]);
          scheduleDiagramFit();
          if (zoomInner) zoomInner.classList.remove('is-rendering');
          setDiagramLoadingVisible(false);
        }, 150);
        setTimeout(() => {
          if (window.MermaidIconInjector) window.MermaidIconInjector.processAllDiagrams();
        }, 400);
      })
      .catch(() => {
        if (zoomInner) zoomInner.classList.remove('is-rendering');
        setDiagramLoadingVisible(false);
      });
  };

  if (window.mermaid) {
    try { doRender(); } catch (e) { if (zoomInner) zoomInner.classList.remove('is-rendering'); console.error('[Diagrams] Mermaid render error:', e); }
  } else {
    const wait = setInterval(() => {
      if (window.mermaid) {
        clearInterval(wait);
        try { doRender(); } catch (e) { if (zoomInner) zoomInner.classList.remove('is-rendering'); console.error('[Diagrams] Mermaid render error:', e); }
      }
    }, 300);
    setTimeout(() => {
      clearInterval(wait);
      if (!window.mermaid) {
        // Never leave the loading overlay stuck if Mermaid fails to initialize.
        if (zoomInner) zoomInner.classList.remove('is-rendering');
        setDiagramLoadingVisible(false);
        const diagramViewsEl = document.getElementById('diagram-views');
        const placeholder = diagramViewsEl?.querySelector('#diagram-placeholder, .diagram-placeholder, .empty-state');
        if (placeholder) {
          const message = window.__triageMermaidLoadError || 'Mermaid failed to initialize, so diagrams could not render.';
          placeholder.innerHTML = `
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
              <circle cx="12" cy="12" r="10"></circle>
              <line x1="12" y1="8" x2="12" y2="12"></line>
              <line x1="12" y1="16" x2="12.01" y2="16"></line>
            </svg>
            <p>${message}</p>
          `;
        }
        setDiagramPlaceholderVisible(true);
      }
    }, 10000);
  }
}
