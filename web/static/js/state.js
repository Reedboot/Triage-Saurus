/**
 * state.js — shared mutable application state for Triage-Saurus.
 *
 * All modules import this single object and mutate it directly.
 * DOM element refs (statusBar etc.) are populated by main.js init().
 */
export const state = {
  // ── EventSource / polling ─────────────────────────────────────────────────
  currentEventSource:    null,
  currentPollInterval:   null,

  // ── DOM element refs (set by main.js init) ────────────────────────────────
  statusBar:   null,
  statusText:  null,
  spinner:     null,
  logOutput:   null,
  scanBtn:     null,
  logAutoScrollBtn:   null,
  architectureAiBtn:  null,

  // ── Scan / experiment tracking ────────────────────────────────────────────
  currentExperimentId: null,
  currentRepoName:     null,

  // ── Architecture AI stream ────────────────────────────────────────────────
  architectureAiStream:        null,
  architectureAiStopInFlight:  false,

  // ── API-ops visibility mode ───────────────────────────────────────────────
  apiOpsMode:    'auto',   // 'auto' | 'all' | 'hide'
  storedDiagrams: [],

  // ── Zoom / pan state (current diagram) ───────────────────────────────────
  zoomState: {
    scale:    1.0,
    minScale: 0.05,
    maxScale: 8.0,
    panX: 0,
    panY: 0,
  },
  diagramStates:    {},    // keyed by diagram index
  currentDiagramIndex: 0,

  // ── Pan interaction ───────────────────────────────────────────────────────
  isPanning:          false,
  activePanPointerId: null,
  panStartX:  0,
  panStartY:  0,
  panOriginX: 0,
  panOriginY: 0,

  // ── Log auto-scroll ───────────────────────────────────────────────────────
  logAutoScrollEnabled:   true,
  logAutoScrollThreshold: 24,

  // ── Toolbar stop callback (registered by AI review) ──────────────────────
  toolbarStopCallback: null,
};
