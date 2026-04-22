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
 *   2. Extract resource type from text (or via data attributes)
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
   *                          Example: { "azurerm_app_service": "/static/assets/icons/azure/app-service.svg" }
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
    // These typically contain a <rect> (the box) and <text> (the label)
    return Array.from(svgElement.querySelectorAll('g[class*="node"]'));
  }

  /**
   * Extract the resource type from a node
   * Tries multiple strategies:
   *   1. data-resource-type attribute (if present)
   *   2. Text content analysis (e.g., "azurerm_app_service: My App" → "azurerm_app_service")
   *   3. Node ID analysis
   */
  function extractResourceType(node, textContent) {
    // Strategy 1: data attribute
    const dataAttr = node.getAttribute('data-resource-type');
    if (dataAttr) return dataAttr;

    // Strategy 2: Parse from text content
    // Format can be: "resource_type: Label" or just "Label"
    if (textContent) {
      const match = textContent.match(/^([a-z_0-9]+)\s*:/i);
      if (match) return match[1].toLowerCase();
    }

    // Strategy 3: Derive from node ID if available
    const nodeId = node.id || '';
    if (nodeId) {
      // Mermaid node IDs might contain type info
      const idMatch = nodeId.match(/([a-z_0-9]+)/);
      if (idMatch) return idMatch[1].toLowerCase();
    }

    return null;
  }

  /**
   * Inject an icon for a specific node
   */
  function injectIconForNode(node, iconMap, svgElement) {
    // Find the text element (label) within this node
    const textElement = node.querySelector('text');
    if (!textElement) return;

    const textContent = textElement.textContent || '';
    if (!textContent) return;

    // Extract resource type from text or node attributes
    const resourceType = extractResourceType(node, textContent);
    if (!resourceType) return;

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
   * Process all Mermaid diagrams on the page and inject icons
   * Call this after Mermaid has finished rendering
   * 
   * @param {Object} options - Configuration options
   *   - containerSelector: CSS selector for diagram containers (default: '[id*="diagram"]')
   *   - iconDataUrl: URL to fetch icon mappings (default: '/api/icon-mappings')
   */
  async function processAllDiagrams(options = {}) {
    const {
      containerSelector = '[id*="diagram"]',
      iconDataUrl = '/api/icon-mappings'
    } = options;

    try {
      // Fetch icon mappings from backend
      const response = await fetch(iconDataUrl);
      if (!response.ok) {
        console.warn(`[MermaidIconInjector] Failed to fetch icon mappings: ${response.status}`);
        return;
      }

      const iconMap = await response.json();

      // Find all diagram containers
      const containers = document.querySelectorAll(containerSelector);
      containers.forEach(container => {
        const svgElement = container.querySelector('svg');
        if (svgElement) {
          injectIcons(svgElement, iconMap);
        }
      });
    } catch (err) {
      console.error('[MermaidIconInjector] Failed to process diagrams:', err.message);
    }
  }

  /**
   * Hook into Mermaid's rendering pipeline
   * Call this once to auto-inject icons whenever Mermaid renders
   */
  function autoInitialize() {
    if (!window.mermaid) {
      console.warn('[MermaidIconInjector] Mermaid library not found');
      return;
    }

    // Listen for Mermaid render events
    const originalInit = window.mermaid.init;
    window.mermaid.init = function(...args) {
      // Call original init
      const result = originalInit.apply(window.mermaid, args);

      // After a short delay to allow rendering, inject icons
      if (result && typeof result.then === 'function') {
        result.then(() => {
          setTimeout(() => {
            processAllDiagrams();
          }, 100);
        });
      } else {
        setTimeout(() => {
          processAllDiagrams();
        }, 100);
      }

      return result;
    };
  }

  // Public API
  return {
    injectIcons,
    processAllDiagrams,
    autoInitialize,
    CONFIG // Expose for customization if needed
  };
})();

// Auto-initialize when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    MermaidIconInjector.autoInitialize();
  });
} else {
  MermaidIconInjector.autoInitialize();
}
