/**
 * Mermaid Icon Injector
 * 
 * Post-processes rendered Mermaid diagrams to inject cloud provider icons
 * alongside node labels (icon on left, text on right).
 * 
 * Usage:
 *   - After Mermaid renders a diagram, call: MermaidIconInjector.injectIcons(svgElement, iconMap)
 *   - iconMap format: { resource_type: '/path/to/icon.svg', ... }
 * 
 * Architecture:
 *   1. Find all text elements in rendered Mermaid SVG (these are node labels)
 *   2. Extract resource type from CSS class (e.g., "icon-azurerm-app-service" → "azurerm_app_service")
 *   3. Look up icon path in iconMap
 *   4. Inject <image> SVG element to the left of the text
 *   5. Position and size consistently across all nodes
 */

const MermaidIconInjector = (() => {
  // Configuration for icon sizing and positioning
  const CONFIG = {
    ICON_SIZE: 24,           // 24x24px icons
    ICON_MARGIN_RIGHT: 6,    // Gap between icon and text
    PADDING_LEFT: 4,         // Left padding inside node
    ICON_COLOR_OPACITY: 0.85 // Slight transparency for visual depth
  };

  /**
   * Main entry point: inject icons into a rendered Mermaid diagram
   * @param {SVGElement} svgElement - The rendered Mermaid diagram SVG
   * @param {Object} iconMap - Mapping of resource_type to icon file paths
   *                          Example: { "azurerm_app_service": "/static/assets/icons/azure/web/app-service.svg" }
   */
  function injectIcons(svgElement, iconMap) {
    if (!svgElement || !iconMap) return;
    
    try {
      // Find all Mermaid nodes (they have class 'node' or similar identifiers)
      const nodes = findMermaidNodes(svgElement);
      
      nodes.forEach(node => {
        try {
          injectIconForNode(node, iconMap, svgElement);
        } catch (err) {
          console.warn('[MermaidIconInjector] Failed to inject icon for node:', err.message);
        }
      });
    } catch (err) {
      console.error('[MermaidIconInjector] Failed to inject icons:', err.message);
    }
  }

  /**
   * Find all nodes in the Mermaid SVG
   * Mermaid uses <g> elements with specific classes for nodes
   */
  function findMermaidNodes(svgElement) {
    // Look for: <g class="node ..." or class="...node..."
    // Also include <g class="...cluster..."> which is how Mermaid renders subgraphs
    // (subgraphs can have :::icon-xxx classes too, e.g. K8s cluster/namespace/deployment)
    const seen = new Set();
    const results = [];
    for (const el of svgElement.querySelectorAll('g[class*="node"], g[class*="cluster"]')) {
      if (!seen.has(el)) { seen.add(el); results.push(el); }
    }
    return results;
  }

  /**
   * Extract the resource type from a node's CSS class
   * Mermaid applies classes like "icon-azurerm-app-service" to nodes
   * We need to convert: "icon-azurerm-app-service" → "azurerm_app_service"
   */
  function extractResourceTypeFromClass(node) {
    const classList = node.className.baseVal || node.className || '';
    
    // Look for class starting with "icon-"
    const classes = classList.split(/\s+/);
    for (const cls of classes) {
      if (cls.startsWith('icon-')) {
        // Remove "icon-" prefix and convert hyphens to underscores
        const resourceType = cls.substring(5).replace(/-/g, '_');
        return resourceType;
      }
    }
    return null;
  }

  /**
   * Inject an icon for a specific node
   */
  function injectIconForNode(node, iconMap, svgElement) {
    // Skip nodes that already have an injected icon (idempotency guard)
    if (node.querySelector('.mermaid-icon')) return;

    // Find the text element (label) within this node
    const textElement = node.querySelector('text');
    if (!textElement) return;

    const textContent = textElement.textContent || '';
    if (!textContent) return;

    // Extract resource type from the CSS class applied to the node
    const resourceType = extractResourceTypeFromClass(node);
    if (!resourceType) {
      console.debug('[MermaidIconInjector] No icon class found on node');
      return;
    }

    // Look up icon path in the provided map
    const iconPath = iconMap[resourceType];
    if (!iconPath) {
      console.debug(`[MermaidIconInjector] No icon mapping for: ${resourceType}`);
      return;
    }

    // Get the bounding box of the text element to position the icon
    let textBBox;
    try {
      textBBox = textElement.getBBox();
    } catch (err) {
      console.warn(`[MermaidIconInjector] Could not get text bounding box: ${err.message}`);
      return;
    }

    // Calculate icon position (left of the text)
    const iconX = textBBox.x - CONFIG.ICON_SIZE - CONFIG.ICON_MARGIN_RIGHT - CONFIG.PADDING_LEFT;
    const iconY = textBBox.y + (textBBox.height - CONFIG.ICON_SIZE) / 2; // Vertically center with text

    // Create SVG image element for the icon
    const imageElement = document.createElementNS('http://www.w3.org/2000/svg', 'image');
    imageElement.setAttribute('href', iconPath);
    imageElement.setAttribute('x', iconX);
    imageElement.setAttribute('y', iconY);
    imageElement.setAttribute('width', CONFIG.ICON_SIZE);
    imageElement.setAttribute('height', CONFIG.ICON_SIZE);
    imageElement.setAttribute('preserveAspectRatio', 'xMidYMid meet');
    imageElement.setAttribute('opacity', CONFIG.ICON_COLOR_OPACITY);
    imageElement.classList.add('mermaid-icon');

    // Insert icon into the SVG, positioned under the node group so it renders behind
    node.appendChild(imageElement);

    // Optionally add a title (tooltip) showing the resource type
    const titleElement = document.createElementNS('http://www.w3.org/2000/svg', 'title');
    titleElement.textContent = resourceType;
    imageElement.appendChild(titleElement);
  }

  /**
   * Fetch icon map once and cache it.  Returns a Promise<iconMap>.
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
      if (!iconMap || !Object.keys(iconMap).length) return;

      // Target every rendered Mermaid SVG directly — works for all tabs, visible or not
      document.querySelectorAll('.mermaid svg').forEach(svgElement => {
        injectIcons(svgElement, iconMap);
      });
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

      const inject = () => setTimeout(() => processAllDiagrams(), 300);
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
        const inject = () => setTimeout(() => processAllDiagrams(), 300);
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
        setTimeout(() => processAllDiagrams(), 150);
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
