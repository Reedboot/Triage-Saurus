(function () {
  var mermaidLoaded = false;
  function loadScript(src, onload, onerror) {
    var s = document.createElement('script');
    s.src = src; s.defer = true;
    s.onload = function () { mermaidLoaded = true; if (onload) onload(); };
    s.onerror = function () { if (onerror) onerror(); };
    document.head.appendChild(s);
  }
  // Load from local static directory
  loadScript('/static/js/mermaid.min.js', function () {
    console.info('✓ Mermaid loaded from local static directory');
    // Initialize mermaid
    if (window.mermaid && typeof window.mermaid.initialize === 'function') {
      window.mermaid.initialize({
        startOnLoad: false,
        theme: 'dark',
        securityLevel: 'loose',
        maxTextSize: 2000000,
        maxEdges: 2000,
        flowchart: {
          useMaxWidth: false,
          htmlLabels: true,
        },
      });
      console.info('✓ Mermaid initialized');
    }
  }, function () {
    console.error('✗ Failed to load mermaid library from /static/js/mermaid.min.js');
    var message = 'Mermaid library unavailable — diagrams will not render.';
    window.__triageMermaidLoadError = message;
    try {
      window.dispatchEvent(new CustomEvent('triage:mermaid-load-error', { detail: { message: message } }));
    } catch (e) { }
  });
})();
