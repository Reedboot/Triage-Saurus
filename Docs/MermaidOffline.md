Mermaid offline / vendor guidance

If your environment blocks CDN access (or you prefer an offline copy), place a compatible mermaid.min.js file under:

  web/static/vendor/mermaid.min.js

Recommended steps:

1. Download mermaid v11.x minified build (e.g. from https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js) and save as the path above.
2. Restart the web server (if necessary).

The app attempts to load /static/vendor/mermaid.min.js first, and falls back to the CDN. If neither is available, a banner will appear in the UI with instructions.

Alternative: proxy the CDN through a trusted internal domain or adjust your Content-Security-Policy to allow cdn.jsdelivr.net.
