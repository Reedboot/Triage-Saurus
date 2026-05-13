(function () {
  var mermaidLoaded = false;
  function loadScript(src, onload, onerror) {
    var s = document.createElement('script');
    s.src = src; s.defer = true;
    s.onload = function () { mermaidLoaded = true; if (onload) onload(); };
    s.onerror = function () { if (onerror) onerror(); };
    document.head.appendChild(s);
  }
  // Load from CDN
  loadScript('https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js', function () { console.info('mermaid loaded from CDN'); }, function () {
    console.warn('Mermaid library failed to load from CDN. Diagrams will not render.');
    var message = 'Mermaid library unavailable — diagrams may not render. To fix: enable CDN access.';
    window.__triageMermaidLoadError = message;
    try {
      window.dispatchEvent(new CustomEvent('triage:mermaid-load-error', { detail: { message: message } }));
    } catch (e) { }
  });
})();
