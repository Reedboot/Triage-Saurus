/**
 * diagram-edge-handler.js — Handle double-click on Mermaid diagram edges to show connection metadata.
 * 
 * Features:
 * - Detects double-click on diagram edges (arrows)
 * - Extracts metadata from Mermaid comments
 * - Displays connection properties in a modal: HTTPS/HTTP, listeners, WAF, authentication, exposure
 */

(function() {
  'use strict';

  /**
   * Parse metadata from Mermaid diagram source comments.
   * Format: "source --> target  %% {json metadata}"
   * @param {string} diagramSource - Full Mermaid diagram source
   * @param {string} sourceId - Source node ID
   * @param {string} targetId - Target node ID
   * @returns {object|null} Parsed metadata or null
   */
  function extractEdgeMetadata(diagramSource, sourceId, targetId) {
    if (!diagramSource) return null;
    
    // Match lines with arrows between source and target nodes
    // Pattern: sourceId -->|"label"| targetId  %% {metadata}
    const edgePattern = new RegExp(
      `${escapeRegex(sourceId)}\\s+-->\\|[^|]+\\|\\s+${escapeRegex(targetId)}\\s+%%\\s+(.+)$`,
      'gm'
    );
    
    const match = edgePattern.exec(diagramSource);
    if (!match) return null;
    
    try {
      // Extract JSON metadata from comment
      const metadataStr = match[1].trim();
      return JSON.parse(metadataStr);
    } catch (e) {
      console.warn('[diagram-edge-handler] Failed to parse edge metadata:', e);
      return null;
    }
  }

  function escapeRegex(str) {
    return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }

  /**
   * Show edge connection details modal.
   * @param {object} metadata - Edge metadata object
   */
  function showEdgeDetailsModal(metadata) {
    if (!metadata) return;
    
    // Build modal content
    const protocols = metadata.protocols || [];
    const listeners = metadata.listeners || [];
    const wafPolicies = metadata.waf_policies || [];
    const hostnames = metadata.hostnames || [];
    const fqdn = metadata.fqdn || '';
    const exposure = metadata.exposure || 'unknown';
    
    const hasHTTPS = protocols.some(p => p.toUpperCase() === 'HTTPS');
    const hasHTTP = protocols.some(p => p.toUpperCase() === 'HTTP');
    const hasWAF = wafPolicies.length > 0;
    
    // Security indicators
    const protocolBadge = hasHTTPS 
      ? '<span class="badge badge-success">🔒 HTTPS</span>'
      : hasHTTP 
        ? '<span class="badge badge-warning">⚠️ HTTP</span>'
        : '<span class="badge badge-neutral">Protocol: ' + protocols.join(', ') + '</span>';
    
    const wafBadge = hasWAF
      ? '<span class="badge badge-success">🛡️ WAF Protected</span>'
      : '<span class="badge badge-danger">❌ No WAF</span>';
    
    const exposureBadge = exposure === 'public'
      ? '<span class="badge badge-danger">🌐 Public Internet</span>'
      : '<span class="badge badge-success">🔒 Private/Internal</span>';
    
    const modalHtml = `
      <div class="modal" id="edge-details-modal" style="display:flex;">
        <div class="modal-content" style="max-width:600px;">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
            <h3 style="margin:0;">🔗 Connection Details</h3>
            <button class="modal-btn cancel" id="edge-modal-close" style="padding:4px 12px;">✕</button>
          </div>
          
          <div style="margin-bottom:16px;">
            <h4 style="margin:0 0 8px 0;color:var(--text-muted);">Target FQDN</h4>
            <code style="display:block;padding:8px;background:var(--bg-code,#1e1e1e);border-radius:4px;word-break:break-all;">
              ${fqdn || 'Unknown'}
            </code>
          </div>
          
          <div style="margin-bottom:16px;">
            <h4 style="margin:0 0 8px 0;color:var(--text-muted);">Security Posture</h4>
            <div style="display:flex;gap:8px;flex-wrap:wrap;">
              ${protocolBadge}
              ${wafBadge}
              ${exposureBadge}
            </div>
          </div>
          
          ${listeners.length > 0 ? `
            <div style="margin-bottom:16px;">
              <h4 style="margin:0 0 8px 0;color:var(--text-muted);">Listeners (${listeners.length})</h4>
              <ul style="margin:0;padding-left:20px;font-size:0.9rem;">
                ${listeners.map(l => `<li>${l}</li>`).join('')}
              </ul>
            </div>
          ` : ''}
          
          ${hostnames.length > 0 ? `
            <div style="margin-bottom:16px;">
              <h4 style="margin:0 0 8px 0;color:var(--text-muted);">Hostnames</h4>
              <ul style="margin:0;padding-left:20px;font-size:0.9rem;">
                ${hostnames.map(h => `<li><code>${h}</code></li>`).join('')}
              </ul>
            </div>
          ` : ''}
          
          ${wafPolicies.length > 0 ? `
            <div style="margin-bottom:16px;">
              <h4 style="margin:0 0 8px 0;color:var(--text-muted);">WAF Policies</h4>
              <ul style="margin:0;padding-left:20px;font-size:0.9rem;">
                ${wafPolicies.map(w => `<li>${w}</li>`).join('')}
              </ul>
            </div>
          ` : ''}
          
          <div style="margin-top:24px;text-align:right;">
            <button class="modal-btn primary" id="edge-modal-ok">OK</button>
          </div>
        </div>
      </div>
    `;
    
    // Remove existing modal if any
    const existing = document.getElementById('edge-details-modal');
    if (existing) existing.remove();
    
    // Insert modal
    document.body.insertAdjacentHTML('beforeend', modalHtml);
    
    // Bind close handlers
    const modal = document.getElementById('edge-details-modal');
    const closeBtn = document.getElementById('edge-modal-close');
    const okBtn = document.getElementById('edge-modal-ok');
    
    const closeModal = () => {
      if (modal) modal.remove();
    };
    
    closeBtn?.addEventListener('click', closeModal);
    okBtn?.addEventListener('click', closeModal);
    modal?.addEventListener('click', (e) => {
      if (e.target === modal) closeModal();
    });
  }

  /**
   * Initialize edge click handling on a diagram container.
   * @param {HTMLElement} container - Diagram container element
   */
  function initEdgeClickHandling(container) {
    if (!container) return;
    
    const svgEl = container.querySelector('svg');
    if (!svgEl) return;
    
    // Get diagram source from pre.mermaid element
    const preEl = container.querySelector('pre.mermaid');
    if (!preEl) return;
    
    const diagramSource = preEl.dataset.source || preEl.textContent || '';
    
    // Attach double-click handler to SVG
    svgEl.addEventListener('dblclick', (event) => {
      // Check if click target is an edge (path element within .edgePath)
      let target = event.target;
      let edgePath = null;
      
      // Walk up to find .edgePath container
      while (target && target !== svgEl) {
        if (target.classList && target.classList.contains('edgePath')) {
          edgePath = target;
          break;
        }
        target = target.parentElement;
      }
      
      if (!edgePath) return;  // Not an edge click
      
      // Extract source and target node IDs from edge class or data attributes
      // Mermaid typically adds classes like "flowchart-link" and markers
      // We need to parse the edge to find source/target
      
      // Try to extract from id attribute (Mermaid uses format like "L-sourceId-targetId")
      const edgeId = edgePath.id || '';
      const idMatch = edgeId.match(/L-(.+?)-(.+?)$/);
      
      if (idMatch) {
        const sourceId = idMatch[1];
        const targetId = idMatch[2];
        
        const metadata = extractEdgeMetadata(diagramSource, sourceId, targetId);
        if (metadata) {
          event.stopPropagation();
          showEdgeDetailsModal(metadata);
        }
      }
    });
  }

  /**
   * Initialize edge handling for all diagram containers on the page.
   */
  function initAllDiagrams() {
    // Target subscription diagrams
    const subDiagram = document.getElementById('subscription-diagram-container');
    if (subDiagram) {
      initEdgeClickHandling(subDiagram);
    }
    
    // Target main repo diagram
    const mainDiagram = document.getElementById('diagram-zoom-wrap');
    if (mainDiagram) {
      initEdgeClickHandling(mainDiagram);
    }
    
    // Observer to handle dynamically loaded diagrams
    const observer = new MutationObserver((mutations) => {
      mutations.forEach((mutation) => {
        mutation.addedNodes.forEach((node) => {
          if (node.nodeType === 1) {  // Element node
            if (node.id === 'subscription-diagram-container' || node.id === 'diagram-zoom-wrap') {
              initEdgeClickHandling(node);
            }
            // Check children
            const diagrams = node.querySelectorAll('#subscription-diagram-container, #diagram-zoom-wrap');
            diagrams.forEach(initEdgeClickHandling);
          }
        });
      });
    });
    
    observer.observe(document.body, { childList: true, subtree: true });
  }

  // Auto-initialize when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAllDiagrams);
  } else {
    initAllDiagrams();
  }

  // Expose globally for manual initialization
  window.DiagramEdgeHandler = {
    initEdgeClickHandling,
    initAllDiagrams,
  };

})();
