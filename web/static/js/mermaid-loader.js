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
    try {
      var b = document.createElement('div');
      b.className = 'mermaid-error';
      b.textContent = 'Mermaid library unavailable — diagrams may not render. To fix: enable CDN access.';
      b.style.cssText = 'position:fixed;bottom:8px;right:8px;background:#f44336;color:white;padding:8px;border-radius:6px;z-index:9999;font-size:12px;max-width:400px';
      document.body.appendChild(b);
    } catch (e) { }
  });
})();
