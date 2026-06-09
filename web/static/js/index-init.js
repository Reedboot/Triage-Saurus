/**
 * index-init.js
 * Initialization logic for index.html
 * 
 * Contains:
 * - Configuration object setup (moduleScanConcurrency, scanFeatureFlags)
 * - Smart button visibility handlers
 * - SSE pipeline hooks for scan control
 */

// ── Configuration Object ───────────────────────────────────────────────────
// These are set by Flask template variables in index.html
window._triage = window._triage || {};
// window._triage.moduleScanConcurrency and scanFeatureFlags are injected by template

// ── Smart hide/show buttons ────────────────────────────────────────────────
/**
 * Control visibility of scan-related buttons based on scan state
 * @param {boolean} visible - Whether to show the buttons
 */
function setScanButtonsVisible(visible) {
  var diagramBtn = document.getElementById('toggle-diagram-btn-persistent');
  var logBtn = document.getElementById('toggle-log-btn');
  if (diagramBtn) diagramBtn.style.visibility = visible ? 'visible' : 'hidden';
  if (logBtn) logBtn.style.visibility = visible ? 'visible' : 'hidden';
}

document.addEventListener('DOMContentLoaded', function () {
  setScanButtonsVisible(false);
});

// ── SSE Pipeline Hooks ─────────────────────────────────────────────────────
// Hook into SSE streaming — triagePipeline is populated by alpine-store.js
// Expose a stable reference so scan-control.js can call it before Alpine is ready
window.triagePipeline = window.triagePipeline || {
  onScanStart:    function() {},
  onScanLine:     function() {},
  onScanComplete: function() {},
  showModal:      function() {},
};

// Export for potential module usage
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { setScanButtonsVisible };
}
