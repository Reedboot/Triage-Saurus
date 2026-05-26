// Shared Mermaid rendering utilities
(function() {
  // Wait for mermaid to load
  function waitForMermaid(callback, maxWait = 5000) {
    const start = Date.now();
    const check = () => {
      if (window.mermaid && typeof window.mermaid.run === 'function') {
        console.log('[Diagram] Mermaid ready for rendering');
        if (callback) callback();
      } else if (Date.now() - start < maxWait) {
        setTimeout(check, 100);
      } else {
        console.warn('[Diagram] Mermaid did not load within timeout');
        if (callback) callback(); // Call anyway
      }
    };
    check();
  }

  // Render diagrams using mermaid.run()
  async function renderDiagrams(container) {
    if (!window.mermaid) {
      console.warn('[Diagram] Mermaid not loaded');
      return false;
    }

    try {
      console.log('[Diagram] Starting render with mermaid.run()');
      // mermaid.run() will find all .mermaid divs and render them
      await window.mermaid.run();
      console.log('[Diagram] ✓ All diagrams rendered');
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
