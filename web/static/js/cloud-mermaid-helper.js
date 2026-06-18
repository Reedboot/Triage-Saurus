/**
 * cloud-mermaid-helper.js
 * Mermaid diagram rendering helper for cloud.html
 * 
 * Exports window.TriageMermaid.render() for rendering diagrams
 */

(function() {
  // Expose render function that works with mermaid.run()
  window.TriageMermaid = {
    /**
     * Render mermaid diagrams in a container
     * Waits for mermaid library to load before rendering
     * @param {HTMLElement} container - Container element for diagrams
     */
    render: async function(container) {
      console.log('[TriageMermaid] render() called');
      
      // Wait for mermaid to be ready
      let attempts = 0;
      while (!window.mermaid && attempts < 50) {
        console.log('[TriageMermaid] waiting for mermaid...');
        await new Promise(r => setTimeout(r, 100));
        attempts++;
      }
      
      if (!window.mermaid) {
        console.error('[TriageMermaid] Mermaid failed to load after 5s');
        return;
      }
      
      try {
        console.log('[TriageMermaid] Calling mermaid.run()');
        const nodes = container ? Array.from(container.querySelectorAll('.mermaid')) : [];
        const result = nodes.length && window.mermaid.run
          ? await window.mermaid.run({ nodes })
          : await window.mermaid.run();
        console.log('[TriageMermaid] mermaid.run() completed, result:', result);
      } catch (e) {
        console.error('[TriageMermaid] Error calling mermaid.run():', e);
      }
    }
  };
})();
