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
    ICON_SIZE: 20,           // 20x20px icons
    ICON_MARGIN_RIGHT: 4,    // Gap between icon and text
    PADDING_LEFT: 4,         // Left padding inside node
  };

  /**
   * Cache for fetched SVG content
   */
  let _svgCache = {};

  /**
   * Fetch and parse SVG or PNG file, returning an SVG element
   * For SVG: returns parsed SVG element
   * For PNG: returns an SVG element with embedded <image> referencing the PNG
   * @param {string} iconPath - Path to the SVG or PNG file
   * @returns {Promise<SVGElement|null>}
   */
  async function _fetchSvg(iconPath) {
    if (_svgCache[iconPath]) {
      return _svgCache[iconPath];
    }

    try {
      const response = await fetch(iconPath);
      if (!response.ok) return null;

      // Check if this is a PNG file
      if (iconPath.toLowerCase().endsWith('.png')) {
        // For PNG files, fetch as blob and convert to data URL
        const blob = await response.blob();
        const reader = new FileReader();
        
        return new Promise((resolve) => {
          reader.onload = () => {
            const dataUrl = reader.result;
            
            // Create SVG wrapper for PNG image
            const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
            svg.setAttribute('viewBox', '0 0 18 18');
            svg.setAttribute('width', '18');
            svg.setAttribute('height', '18');
            
            const image = document.createElementNS('http://www.w3.org/2000/svg', 'image');
            image.setAttribute('href', dataUrl);
            image.setAttribute('x', '0');
            image.setAttribute('y', '0');
            image.setAttribute('width', '18');
            image.setAttribute('height', '18');
            
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
        console.warn(`[MermaidIconInjector] Invalid SVG from ${iconPath}`);
        return null;
      }

      _svgCache[iconPath] = svgElement;
      return svgElement;
    } catch (err) {
      console.warn(`[MermaidIconInjector] Failed to fetch icon ${iconPath}: ${err.message}`);
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
      console.log(`[MermaidIconInjector] Found ${nodes.length} nodes in diagram`);
      
      let injected = 0;
      for (const node of nodes) {
        try {
          const success = await injectIconForNode(node, iconMap, svgElement);
          if (success) injected++;
        } catch (err) {
          console.warn('[MermaidIconInjector] Failed to inject icon for node:', err.message);
        }
      }
      console.log(`[MermaidIconInjector] Injected ${injected} inline SVG icons`);
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
      console.log(`[MermaidIconInjector] Strategy 1 found 0 nodes, trying fallback strategies...`);
      
      // Look for <g> containing icon class markers (:::icon_*)
      const possibleNodes = svgElement.querySelectorAll('g[class*="icon_"]');
      for (const el of possibleNodes) {
        if (!seen.has(el)) { seen.add(el); results.push(el); }
      }
      console.log(`[MermaidIconInjector] Strategy 2 (g[class*="icon_"]): found ${results.length}`);
    }
    
    // Strategy 3: If still nothing, look at ALL g elements and report structure
    if (results.length === 0) {
      console.log(`[MermaidIconInjector] Strategies 1-2 failed, analyzing SVG structure...`);
      
      const allGs = svgElement.querySelectorAll('g');
      const allGsWithClass = svgElement.querySelectorAll('g[class]');
      const allTexts = svgElement.querySelectorAll('text');
      const allTextsWithClass = svgElement.querySelectorAll('text[class]');
      
      console.log(`[MermaidIconInjector DEBUG] SVG Structure:`);
      console.log(`[MermaidIconInjector DEBUG]  Total <g> elements: ${allGs.length}`);
      console.log(`[MermaidIconInjector DEBUG]  <g> with class attr: ${allGsWithClass.length}`);
      console.log(`[MermaidIconInjector DEBUG]  Total <text> elements: ${allTexts.length}`);
      console.log(`[MermaidIconInjector DEBUG]  <text> with class attr: ${allTextsWithClass.length}`);
      
      if (allGsWithClass.length > 0) {
        const classes = [];
        for (let i = 0; i < Math.min(5, allGsWithClass.length); i++) {
          const cls = allGsWithClass[i].className.baseVal || allGsWithClass[i].className || 'no-class';
          classes.push(cls.substring(0, 60));
        }
        console.log(`[MermaidIconInjector DEBUG]  Sample <g> classes: ${JSON.stringify(classes)}`);
      }
      
      if (allTextsWithClass.length > 0) {
        const classes = [];
        for (let i = 0; i < Math.min(3, allTextsWithClass.length); i++) {
          const cls = allTextsWithClass[i].className.baseVal || allTextsWithClass[i].className || 'no-class';
          classes.push(cls.substring(0, 60));
        }
        console.log(`[MermaidIconInjector DEBUG]  Sample <text> classes: ${JSON.stringify(classes)}`);
      }
      
      // Strategy 4: As last resort, try all g elements regardless of class
      results = Array.from(svgElement.querySelectorAll('g')).filter((el, idx) => idx < 50); // Limit to first 50
      console.log(`[MermaidIconInjector DEBUG]  Fallback to all <g> elements (first 50): ${results.length}`);
    }
    
    return results;
  }

  /**
   * Extract the resource type from a node's CSS class
   * Mermaid applies classes like "icon-azurerm-app-service" to nodes
   * We need to convert: "icon-azurerm-app-service" → "azurerm_app_service"
   */
  function extractResourceTypeFromClass(node) {
    let classList = node.className.baseVal || node.className || '';
    
    // Handle DOMTokenList or other non-string types
    if (typeof classList !== 'string') {
      classList = String(classList);
    }
    
    // Look for classes starting with "icon-" (from node markers) or "icon_" (from classDef)
    // Mermaid may apply either form depending on how classes are applied
    const classes = classList.split(/\s+/);
    for (const cls of classes) {
      if (cls.startsWith('icon-')) {
        // From hyphenated class marker :::icon-azurerm-app-service
        // Remove "icon-" prefix and convert hyphens to underscores
        const resourceType = cls.substring(5).replace(/-/g, '_');
        return resourceType;
      } else if (cls.startsWith('icon_')) {
        // From underscore classDef (Mermaid applies underscored names)
        // Remove "icon_" prefix, already has underscores
        const resourceType = cls.substring(5);
        return resourceType;
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

    // Find the text element (label) within this node
    const textElement = node.querySelector('text');
    if (!textElement) return false;

    const textContent = textElement.textContent || '';
    if (!textContent) return false;

    // Extract resource type from the CSS class applied to the node
    const resourceType = extractResourceTypeFromClass(node);
    if (!resourceType) {
      console.debug('[MermaidIconInjector] No icon class found on node');
      return false;
    }

    // Look up icon path in the provided map
    const iconPath = iconMap[resourceType];
    if (!iconPath) {
      console.debug(`[MermaidIconInjector] No icon mapping for: ${resourceType}`);
      return false;
    }

    // Fetch and parse the SVG file
    const sourceSvg = await _fetchSvg(iconPath);
    if (!sourceSvg) return false;

    // Get the bounding box of the text element to position the icon
    let textBBox;
    try {
      textBBox = textElement.getBBox();
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
    // Calculate scale based on actual viewBox dimensions so icons render at CONFIG.ICON_SIZE
    // regardless of whether the source SVG is 16×16, 32×32, 40×40, 80×80, etc.
    const viewBoxParts = (sourceSvg.getAttribute('viewBox') || '0 0 100 100').trim().split(/[\s,]+/).map(Number);
    const vbW = viewBoxParts[2] || 100;
    const vbH = viewBoxParts[3] || 100;
    const scale = CONFIG.ICON_SIZE / Math.max(vbW, vbH);
    groupElement.setAttribute('transform', `translate(${iconX},${iconY}) scale(${scale})`);

    // Clone the source SVG content into the group

    // Clone all children from the source SVG into the group
    for (const child of sourceSvg.children) {
      const clonedChild = child.cloneNode(true);
      groupElement.appendChild(clonedChild);
    }

    // Add a title (tooltip) showing the resource type
    const titleElement = document.createElementNS('http://www.w3.org/2000/svg', 'title');
    titleElement.textContent = resourceType;
    groupElement.appendChild(titleElement);

    // Insert icon into the SVG, positioned before the text (so it renders underneath)
    node.insertBefore(groupElement, textElement);

    return true;
  }

  /**
   * Fetch icon map once and cache it. Returns a Promise<iconMap>.
   */
  let _iconMapCache = null;
  async function _getIconMap(iconDataUrl = '/api/icon-mappings') {
    if (_iconMapCache) return _iconMapCache;
    try {
      const response = await fetch(iconDataUrl);
      if (!response.ok) {
        console.warn(`[MermaidIconInjector] Failed to fetch icon mappings: ${response.status}`);
        return {};
      }
      _iconMapCache = await response.json();
      console.log(`[MermaidIconInjector] Loaded ${Object.keys(_iconMapCache).length} icon mappings`);
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

      const svgElements = document.querySelectorAll('.mermaid svg');
      console.log(`[MermaidIconInjector] Found ${svgElements.length} Mermaid SVG(s) with ${Object.keys(iconMap).length} icons`);

      for (const svgElement of svgElements) {
        await injectIcons(svgElement, iconMap);
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
  }

  // Public API
  return {
    injectIcons,
    processAllDiagrams,
    autoInitialize,
    CONFIG // Expose for customization if needed
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
