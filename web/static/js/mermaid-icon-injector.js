/**
 * Mermaid Icon Injector
 * 
 * Post-processes rendered Mermaid diagrams to inject cloud provider icons
 * by inlining SVG/PNG content directly into the diagram (bypasses <image> sandbox restrictions).
 * 
 * Supports:
 * - SVG files: Inlined directly as SVG elements
 * - PNG files: Wrapped in SVG <image> elements to reference the PNG
 * 
 * Usage:
 *   - After Mermaid renders a diagram, call: MermaidIconInjector.injectIcons(svgElement, iconMap)
 *   - iconMap format: { resource_type: '/path/to/icon.svg' or '/path/to/icon.png', ... }
 * 
 * Architecture:
 *   1. Find all text elements in rendered Mermaid SVG (these are node labels)
 *   2. Extract resource type from CSS class (e.g., "icon-azurerm-app-service" → "azurerm_app_service")
 *   3. Look up icon path in iconMap
 *   4. For SVG: Fetch file as text and extract root SVG viewBox/dimensions
 *   5. For PNG: Create SVG <image> element pointing to PNG file
 *   6. Create <g> element with icon content, positioned left of text
 *   7. Insert into diagram using transform positioning
 */

const MermaidIconInjector = (() => {
  // Configuration for icon sizing and positioning
  const CONFIG = {
    ICON_SIZE: 24,           // 24x24px (optimal size - scales well as vectors, never pixelates)
    ICON_MARGIN_RIGHT: 4,    // Gap between icon and text
    PADDING_LEFT: 4,         // Left padding inside node
  };

  /**
   * Cache for fetched SVG content
   * Note: We DON'T persist this across page loads to ensure fresh icons on new scans
   */
  let _svgCache = {};

  function _sanitizeIconPath(iconPath) {
    let cleaned = String(iconPath || '')
      // Strip control chars + common zero-width chars that break static paths.
      .replace(/[\u0000-\u001f\u007f\u200b\u200c\u200d\ufeff]/g, '');

    // Normalize accidental duplicate static prefix.
    const dupPrefix = '/static/assets/icons/static/assets/icons/';
    if (cleaned.startsWith(dupPrefix)) {
      cleaned = '/static/assets/icons/' + cleaned.slice(dupPrefix.length);
    }

    return cleaned;
  }

  /**
   * Fetch and parse SVG or PNG file, returning an SVG element
   * For SVG: returns parsed SVG element
   * For PNG: returns an SVG element with embedded <image> referencing the PNG
   * @param {string} iconPath - Path to the SVG or PNG file
   * @returns {Promise<SVGElement|null>}
   */
  async function _fetchSvg(iconPath) {
    iconPath = _sanitizeIconPath(iconPath);
    if (_svgCache[iconPath]) {
      return _svgCache[iconPath];
    }

    // If this is a PNG path, try to use SVG version instead (better rendering)
    let pathToTry = iconPath;
    if (iconPath.toLowerCase().endsWith('.png')) {
      const svgPath = iconPath.replace(/\.png$/i, '.svg');
      try {
        const response = await fetch(svgPath);
        if (response.ok) {
          // SVG version exists, use it instead
          pathToTry = svgPath;
          iconPath = svgPath; // Update iconPath for cache key
        }
      } catch (e) {
        // SVG version doesn't exist, fall through to PNG
      }
    }

    try {
      const response = await fetch(pathToTry);
      if (!response.ok) return null;

      // Check if this is a PNG file
      if (pathToTry.toLowerCase().endsWith('.png')) {
        // For PNG files, fetch as blob and convert to data URL
        const blob = await response.blob();
        const reader = new FileReader();
        
        return new Promise((resolve) => {
          reader.onload = () => {
            const dataUrl = reader.result;
            
            // Create SVG wrapper for PNG image with high-quality rendering
            // Use larger viewBox to minimize pixelation when scaled
            const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
            svg.setAttribute('viewBox', '0 0 64 64');
            svg.setAttribute('width', '64');
            svg.setAttribute('height', '64');
            svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
            
            const image = document.createElementNS('http://www.w3.org/2000/svg', 'image');
            image.setAttribute('href', dataUrl);
            image.setAttribute('x', '0');
            image.setAttribute('y', '0');
            image.setAttribute('width', '64');
            image.setAttribute('height', '64');
            // Let browser choose best interpolation
            image.setAttribute('preserveAspectRatio', 'xMidYMid meet');
            
            svg.appendChild(image);
            _svgCache[iconPath] = svg;
            resolve(svg);
          };
          reader.readAsDataURL(blob);
        });
      }

      // Handle SVG files
      const svgText = await response.text();
      const parser = new DOMParser();
      const doc = parser.parseFromString(svgText, 'image/svg+xml');
      const svgElement = doc.documentElement;

      if (svgElement.tagName !== 'svg') {
        console.warn(`[MermaidIconInjector] Invalid SVG from ${pathToTry}`);
        return null;
      }

      _svgCache[iconPath] = svgElement;
      return svgElement;
    } catch (err) {
      console.warn(`[MermaidIconInjector] Failed to fetch icon ${pathToTry}: ${err.message}`);
      return null;
    }
  }

  /**
   * Main entry point: inject icons into a rendered Mermaid diagram
   * @param {SVGElement} svgElement - The rendered Mermaid diagram SVG
   * @param {Object} iconMap - Mapping of resource_type to icon file paths
   *                          Example: { "azurerm_app_service": "/static/assets/icons/azure/web/app-service.svg" }
   */
  async function injectIcons(svgElement, iconMap) {
    if (!svgElement || !iconMap) return;
    
    try {
      // Find all Mermaid nodes (they have class 'node' or similar identifiers)
      const nodes = findMermaidNodes(svgElement);
      
      let injected = 0;
      for (const node of nodes) {
        try {
          const success = await injectIconForNode(node, iconMap, svgElement);
          if (success) injected++;
        } catch (err) {
          console.warn('[MermaidIconInjector] Failed to inject icon for node:', err.message);
        }
      }
    } catch (err) {
      console.error('[MermaidIconInjector] Failed to inject icons:', err.message);
    }
  }

  /**
   * Find all nodes in the Mermaid SVG
   * Mermaid uses <g> elements with specific classes for nodes
   */
  function findMermaidNodes(svgElement) {
    // Strategy 1: Look for <g> with "node" or "cluster" in class (Mermaid v10 style)
    const seen = new Set();
    let results = [];
    for (const el of svgElement.querySelectorAll('g[class*="node"], g[class*="cluster"]')) {
      if (!seen.has(el)) { seen.add(el); results.push(el); }
    }
    
    // Strategy 2: If no nodes found, try ANY <g> element with an icon class marker
    if (results.length === 0) {
      // Look for <g> containing icon class markers (:::icon_*)
      const possibleNodes = svgElement.querySelectorAll('g[class*="icon_"]');
      for (const el of possibleNodes) {
        if (!seen.has(el)) { seen.add(el); results.push(el); }
      }
    }
    
    // Strategy 3: If still nothing, look at ALL g elements and report structure
    if (results.length === 0) {
      const allGs = svgElement.querySelectorAll('g');
      const allGsWithClass = svgElement.querySelectorAll('g[class]');
      const allTexts = svgElement.querySelectorAll('text');
      const allTextsWithClass = svgElement.querySelectorAll('text[class]');
      
      // Strategy 4: As last resort, try all g elements regardless of class
      results = Array.from(svgElement.querySelectorAll('g')).filter((el, idx) => idx < 50); // Limit to first 50
    }
    
    return results;
  }

  /**
   * Extract the resource type from a node's CSS class
   * Mermaid applies classes like "icon-azurerm-app-service" to nodes
   * We need to convert: "icon-azurerm-app-service" → "azurerm_app_service"
   */
  function extractResourceTypeFromClass(node) {
    const classCandidates = [];
    const pushClasses = (el) => {
      if (!el) return;
      let classList = el.className && (el.className.baseVal || el.className) || '';
      if (typeof classList !== 'string') classList = String(classList);
      if (classList) classCandidates.push(...classList.split(/\s+/));
      if (typeof el.getAttribute === 'function') {
        const attr = el.getAttribute('class');
        if (attr) classCandidates.push(...String(attr).split(/\s+/));
      }
    };

    // Check node first, then descendant elements (Mermaid may attach icon classes on inner groups/shapes).
    pushClasses(node);
    node.querySelectorAll?.('[class]').forEach(pushClasses);

    for (const cls of classCandidates) {
      if (!cls) continue;
      if (cls.startsWith('icon-')) {
        return cls.substring(5).replace(/-/g, '_');
      }
      if (cls.startsWith('icon_')) {
        return cls.substring(5);
      }
    }
    return null;
  }

  /**
   * Inject an inline SVG icon for a specific node
   * @returns {Promise<boolean>} - true if icon was injected, false otherwise
   */
  async function injectIconForNode(node, iconMap, svgElement) {
    // Skip nodes that already have an injected icon (idempotency guard)
    if (node.querySelector('.mermaid-icon')) return false;

    // Extract resource type from the CSS class applied to the node
    const resourceType = extractResourceTypeFromClass(node);
    if (!resourceType) return false;

    // Look up icon path in the provided map
    const iconPath = iconMap[resourceType];
    if (!iconPath) return false;

    // Find the label element — Mermaid uses either <text> (legacy) or
    // <foreignObject> (modern HTML-label mode) for node/cluster labels.
    const textElement = node.querySelector('text');
    const foreignObj  = !textElement ? node.querySelector('foreignObject') : null;
    if (!textElement && !foreignObj) return false;

    const labelContent = textElement
      ? textElement.textContent || ''
      : (foreignObj.textContent || '').trim();
    if (!labelContent) return false;

    // Fetch and parse the SVG icon
    const sourceSvg = await _fetchSvg(iconPath);
    if (!sourceSvg) return false;

    // Get the bounding box of the label element to position the icon.
    // For foreignObject we use getBoundingClientRect mapped to SVG coords.
    let textBBox;
    try {
      if (textElement) {
        textBBox = textElement.getBBox();
      } else {
        // Map foreignObject client rect into SVG coordinate space
        const foRect   = foreignObj.getBoundingClientRect();
        const svgRect  = svgElement.getBoundingClientRect();
        const ctm      = svgElement.getScreenCTM();
        if (!ctm) return false;
        const invCTM   = ctm.inverse();
        // Convert screen point (top-left of foreignObject) to SVG coords
        let pt = svgElement.createSVGPoint();
        pt.x = foRect.left - svgRect.left + svgRect.left;
        pt.y = foRect.top  - svgRect.top  + svgRect.top;
        // Use screen coordinates directly
        pt.x = foRect.left; pt.y = foRect.top;
        const svgPt = pt.matrixTransform(invCTM);
        textBBox = {
          x: svgPt.x,
          y: svgPt.y,
          width:  foRect.width  / Math.abs(ctm.a),
          height: foRect.height / Math.abs(ctm.d),
        };
      }
    } catch (err) {
      console.warn(`[MermaidIconInjector] Could not get text bounding box: ${err.message}`);
      return false;
    }

    // Calculate icon position (left of the text)
    const iconX = textBBox.x - CONFIG.ICON_SIZE - CONFIG.ICON_MARGIN_RIGHT - CONFIG.PADDING_LEFT;
    const iconY = textBBox.y + (textBBox.height - CONFIG.ICON_SIZE) / 2; // Vertically center with text

    // Create a <g> wrapper for the icon
    const groupElement = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    groupElement.setAttribute('class', 'mermaid-icon');
    groupElement.setAttribute('vector-effect', 'non-scaling-stroke');
    // Calculate scale based on actual viewBox dimensions so icons render at CONFIG.ICON_SIZE
    // regardless of whether the source SVG is 16×16, 32×32, 40×40, 80×80, etc.
    const viewBoxParts = (sourceSvg.getAttribute('viewBox') || '0 0 100 100').trim().split(/[\s,]+/).map(Number);
    const vbW = viewBoxParts[2] || 100;
    const vbH = viewBoxParts[3] || 100;
    const scale = CONFIG.ICON_SIZE / Math.max(vbW, vbH);
    groupElement.setAttribute('transform', `translate(${iconX},${iconY}) scale(${scale})`);

    // Clone all children from the source SVG into the group
    for (const child of sourceSvg.children) {
      const clonedChild = child.cloneNode(true);
      groupElement.appendChild(clonedChild);
    }

    // Add a title (tooltip) showing the resource type
    const titleElement = document.createElementNS('http://www.w3.org/2000/svg', 'title');
    titleElement.textContent = resourceType;
    groupElement.appendChild(titleElement);

    // Insert icon near the text label. In some Mermaid layouts the <text> element
    // is nested, so insert relative to its parent when needed.
    if (textElement && textElement.parentNode) {
      textElement.parentNode.insertBefore(groupElement, textElement);
    } else {
      node.insertBefore(groupElement, node.firstChild);
    }

    return true;
  }

  /**
   * Fetch icon map once and cache it. Returns a Promise<iconMap>.
   */
  let _iconMapCache = null;
  async function _getIconMap(iconDataUrl = '/api/icon-mappings') {
    if (_iconMapCache) return _iconMapCache;
    try {
      // Add cache-buster while preserving existing query params (e.g. provider=azure).
      const url = new URL(iconDataUrl, window.location.origin);
      url.searchParams.set('v', String(Date.now()));
      const response = await fetch(url.toString());
      if (!response.ok) {
        console.warn(`[MermaidIconInjector] Failed to fetch icon mappings: ${response.status}`);
        return {};
      }
      _iconMapCache = await response.json();
      // Sanitize all icon paths defensively so stale/bad cache entries
      // cannot produce broken static URLs.
      Object.keys(_iconMapCache).forEach((k) => {
        _iconMapCache[k] = _sanitizeIconPath(_iconMapCache[k]);
      });
      return _iconMapCache;
    } catch (err) {
      console.error('[MermaidIconInjector] Error fetching icon mappings:', err.message);
      return {};
    }
  }

  /**
   * Process all rendered Mermaid SVGs on the page and inject icons.
   * Targets `.mermaid svg` directly so it works regardless of wrapper IDs.
   *
   * @param {Object} options
   *   - iconDataUrl: URL for icon mappings (default '/api/icon-mappings')
   */
  async function processAllDiagrams(options = {}) {
    const { iconDataUrl = '/api/icon-mappings' } = options;
    try {
      const iconMap = await _getIconMap(iconDataUrl);
      if (!iconMap || !Object.keys(iconMap).length) {
        console.warn('[MermaidIconInjector] Icon map is empty');
        return;
      }

      // Find SVGs in both inline (.mermaid) and standalone (#diagram-container) viewers
      const svgElements = new Set([
        ...document.querySelectorAll('.mermaid svg'),
        ...document.querySelectorAll('#diagram-container svg')
      ]);

      for (const svgElement of svgElements) {
        await injectIcons(svgElement, iconMap);
      }

      // /diagrams/<id> view can auto-fit to an unreadably tiny scale for very wide graphs.
      // Clamp to a readable minimum after rendering/icon pass.
      const viewerContainer = document.getElementById('diagram-container');
      if (viewerContainer && viewerContainer.style && viewerContainer.style.transform) {
        const match = viewerContainer.style.transform.match(/scale\(([\d.]+)\)/);
        if (match) {
          const scale = parseFloat(match[1]);
          if (!Number.isNaN(scale) && scale > 0 && scale < 0.40) {
            viewerContainer.style.transform = 'scale(0.40)';
          }
        }
      }
    } catch (err) {
      console.error('[MermaidIconInjector] Failed to process diagrams:', err.message);
    }
  }

  /**
   * Hook into Mermaid's rendering pipeline.
   * Also installs a MutationObserver so icons are injected whenever new SVGs appear.
   */
  function autoInitialize() {
    if (!window.mermaid) {
      console.warn('[MermaidIconInjector] Mermaid library not found');
      return;
    }

    // Wrap mermaid.init so icons are injected after each render call
    const originalInit = window.mermaid.init;
    window.mermaid.init = function(...args) {
      const result = originalInit.apply(window.mermaid, args);

      const inject = () => setTimeout(() => processAllDiagrams(), 500);
      if (result && typeof result.then === 'function') {
        result.then(inject).catch(inject);
      } else {
        inject();
      }
      return result;
    };

    // Also wrap mermaid.run (used by Mermaid v10+)
    if (typeof window.mermaid.run === 'function') {
      const originalRun = window.mermaid.run;
      window.mermaid.run = function(...args) {
        const result = originalRun.apply(window.mermaid, args);
        const inject = () => setTimeout(() => processAllDiagrams(), 500);
        if (result && typeof result.then === 'function') {
          result.then(inject).catch(inject);
        } else {
          inject();
        }
        return result;
      };
    }

    // MutationObserver: catch any SVG that appears after initial load
    // (e.g., lazy-rendered tabs or dynamically injected diagrams)
    const observer = new MutationObserver((mutations) => {
      let hasSvg = false;
      for (const m of mutations) {
        for (const node of m.addedNodes) {
          if (node.nodeType === 1) {
            if (node.tagName === 'svg' || node.querySelector?.('svg')) {
              hasSvg = true;
              break;
            }
          }
        }
        if (hasSvg) break;
      }
      if (hasSvg) {
        setTimeout(() => processAllDiagrams(), 200);
      }
    });
    observer.observe(document.body, { childList: true, subtree: true });

    // Process diagrams that may have rendered before/around injector init.
    [0, 300, 1000, 2500].forEach((delay) => {
      setTimeout(() => processAllDiagrams(), delay);
    });
  }

  // Public API
  return {
    injectIcons,
    processAllDiagrams,
    autoInitialize,
    CONFIG, // Expose for customization if needed
    _clearCache: () => {
      _svgCache = {};
      _iconMapCache = null;
    }
  };
})();

// Expose on window so app.js and other scripts can reference it reliably
window.MermaidIconInjector = MermaidIconInjector;

// Auto-initialize: wrap mermaid.init/run if Mermaid is already loaded,
// otherwise poll until it appears (handles async mermaid-loader.js race).
function _initIconInjector() {
  if (window.mermaid) {
    MermaidIconInjector.autoInitialize();
  } else {
    const poll = setInterval(() => {
      if (window.mermaid) {
        clearInterval(poll);
        MermaidIconInjector.autoInitialize();
      }
    }, 100);
    // Give up after 15 s to avoid infinite polling
    setTimeout(() => clearInterval(poll), 15000);
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', _initIconInjector);
} else {
  _initIconInjector();
}
