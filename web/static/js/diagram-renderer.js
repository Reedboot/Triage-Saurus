// Shared Mermaid rendering utilities
(function() {
  // Wait for mermaid to load and initialize
  function waitForMermaid(callback, maxWait = 5000) {
    const start = Date.now();
    const check = () => {
      if (window.mermaid && typeof window.mermaid.contentLoaded === 'function') {
        console.log('[Diagram] Mermaid ready');
        if (callback) callback();
      } else if (Date.now() - start < maxWait) {
        setTimeout(check, 100);
      } else {
        console.warn('[Diagram] Mermaid did not load within timeout');
        if (callback) callback(); // Call anyway so things don't hang
      }
    };
    check();
  }

  // Simple render function that uses mermaid's contentLoaded
  async function renderDiagrams(container) {
    if (!window.mermaid) {
      console.warn('[Diagram] Mermaid not loaded');
      return false;
    }

    try {
      // Use mermaid's built-in content rendering
      console.log('[Diagram] Running mermaid.contentLoaded()');
      await window.mermaid.contentLoaded();
      console.log('[Diagram] ✓ Mermaid rendering complete');
      return true;
    } catch (err) {
      console.error('[Diagram] Error rendering:', err);
      return false;
    }
  }

  // Expose globally
  window.TriageDiagramRenderer = {
    render: renderDiagrams,
    waitForMermaid: waitForMermaid
  };

  // Also provide a compatibility wrapper for TriageMermaid
  Object.defineProperty(window, 'TriageMermaid', {
    get: function() {
      return window.TriageDiagramRenderer;
    }
  });
})();
