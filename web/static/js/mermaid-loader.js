(function () {
  var sources = [
    '/static/vendor/mermaid.min.js',
    '/static/js/mermaid.min.js'
  ];

  function loadScript(src, onload, onerror) {
    var s = document.createElement('script');
    s.src = src; s.defer = true;
    s.onload = function () { if (onload) onload(); };
    s.onerror = function () { if (onerror) onerror(); };
    document.head.appendChild(s);
  }

  function initializeMermaid(src) {
    console.info('✓ Mermaid loaded from ' + src);
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
  }

  function loadNext(index) {
    if (index >= sources.length) {
      console.error('✗ Failed to load Mermaid from local static bundles');
      var message = 'Mermaid library unavailable — diagrams will not render.';
      window.__triageMermaidLoadError = message;
      try {
        window.dispatchEvent(new CustomEvent('triage:mermaid-load-error', { detail: { message: message } }));
      } catch (e) { }
      return;
    }
    loadScript(sources[index], function () {
      initializeMermaid(sources[index]);
    }, function () {
      loadNext(index + 1);
    });
  }

  window.__triageMermaidLoadError = '';
  loadNext(0);
})();
