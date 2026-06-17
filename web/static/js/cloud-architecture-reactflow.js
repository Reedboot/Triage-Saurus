const React = window.React;
const { createRoot } = window.ReactDOM;
const { Background, Controls, Handle, MarkerType, MiniMap, Position, ReactFlow } = window.ReactFlow;
const { useCallback, useEffect, useState } = React;

const h = React.createElement;
const CONFIG = window.__TRIAGE_CLOUD_ARCH__ || {};
const rootEl = document.getElementById("cloud-arch-root");
const emptyEl = document.getElementById("cloud-arch-empty");
const summaryLineEl = document.getElementById("cloud-arch-summary-line");
const legendEl = document.getElementById("cloud-arch-provider-legend");
const errorCardEl = document.getElementById("cloud-arch-error-card");
const errorEl = document.getElementById("cloud-arch-error");
const formEl = document.getElementById("cloud-arch-form");
const subscriptionInput = document.getElementById("subscription-input");
const viewButtons = Array.from(document.querySelectorAll("[data-cloud-arch-view]"));
const INITIAL_VIEW_MODE = (CONFIG.initialViewMode || "overview").toLowerCase();
let activeViewMode = INITIAL_VIEW_MODE === "full" ? "full" : "overview";

const PROVIDER_THEMES = {
  azure: { label: "Azure", iconPath: "/static/assets/icons/azure/compute/aks.svg", border: "#0078d4", background: "rgba(0, 120, 212, 0.14)" },
  aws: { label: "AWS", abbr: "AWS", border: "#ff9900", background: "rgba(255, 153, 0, 0.14)" },
  gcp: { label: "Google Cloud", iconPath: "/static/vendor/cloud-icons/gcp.svg", border: "#4285f4", background: "rgba(66, 133, 244, 0.14)" },
  oci: { label: "Oracle Cloud", abbr: "OCI", border: "#f80000", background: "rgba(248, 0, 0, 0.14)" },
  alicloud: { label: "Alibaba Cloud", iconPath: "/static/vendor/cloud-icons/alibaba.svg", border: "#ff6a00", background: "rgba(255, 106, 0, 0.14)" },
  tencentcloud: { label: "Tencent Cloud", abbr: "TC", border: "#0052d9", background: "rgba(0, 82, 217, 0.14)" },
  huaweicloud: { label: "Huawei Cloud", iconPath: "/static/vendor/cloud-icons/huawei.svg", border: "#ff3b30", background: "rgba(255, 59, 48, 0.14)" },
  digitalocean: { label: "DigitalOcean", iconPath: "/static/vendor/cloud-icons/digitalocean.svg", border: "#0080ff", background: "rgba(0, 128, 255, 0.14)" },
  openstack: { label: "OpenStack", iconPath: "/static/vendor/cloud-icons/openstack.svg", border: "#ed1944", background: "rgba(237, 25, 68, 0.14)" },
  unknown: { label: "Unknown", abbr: "?", border: "#64748b", background: "rgba(100, 116, 139, 0.14)" },
  external: { label: "External", iconPath: "/static/vendor/cloud-icons/external.svg", border: "#475569", background: "rgba(71, 85, 105, 0.14)" },
};

function themeFor(key) {
  return PROVIDER_THEMES[key] || PROVIDER_THEMES.unknown;
}

function syncViewButtons() {
  for (const button of viewButtons) {
    const mode = (button.dataset.cloudArchView || "").toLowerCase();
    const isActive = mode === activeViewMode;
    button.classList.toggle("is-active", isActive);
    button.setAttribute("aria-pressed", isActive ? "true" : "false");
  }
}

function ProviderIcon({ providerKey, iconPath }) {
  const theme = themeFor(providerKey);
  return h(
    "span",
    { className: "cloud-node__icon", style: { background: theme.background, color: theme.border } },
    iconPath
      ? h("img", { src: iconPath, alt: theme.label, style: { width: "20px", height: "20px", objectFit: "contain" } })
      : theme.iconPath
      ? h("img", { src: theme.iconPath, alt: theme.label, style: { width: "18px", height: "18px", objectFit: "contain" } })
      : h("span", { style: { fontSize: "0.7rem", fontWeight: 800, letterSpacing: "0.02em" } }, theme.abbr || "☁"),
  );
}

function CloudNode({ data }) {
  const theme = themeFor(data.providerKey);
  
  // Build security icons array
  const securityIcons = [];
  if (data.wafMode) {
    securityIcons.push(h("span", { 
      className: "cloud-node__security-icon cloud-node__security-icon--waf",
      title: `WAF ${data.wafMode}` 
    }, "🛡️"));
  }
  if (data.hasManagedIdentity) {
    securityIcons.push(h("span", { 
      className: "cloud-node__security-icon cloud-node__security-icon--auth",
      title: "Managed Identity" 
    }, "🔑"));
  }
  if (data.loggingEnabled) {
    securityIcons.push(h("span", { 
      className: "cloud-node__security-icon cloud-node__security-icon--logging",
      title: "Diagnostic Logging Enabled" 
    }, "📊"));
  }
  
  // Check if this node is a group node
  const isGroupNode = data.isGroupNode || false;
  
  // Check if this node is expandable (has children)
  const resourceType = (data.resourceType || "").toLowerCase();
  const isExpandable = isGroupNode || 
    resourceType.includes('applicationgateways') || 
    resourceType.includes('applicationgateway') ||
    (resourceType.includes('sql/servers') && !resourceType.includes('/databases')) ||
    resourceType.includes('apimanagement/service') ||
    resourceType.includes('kubernetes') ||
    resourceType.includes('containerservice');
  
  const isExpanded = data.expanded || false;
  const isChildNode = data.isChildNode || false;
  
  return h(
    "div",
    {
      className: `cloud-node${data.synthetic ? " cloud-node--synthetic" : ""}${data.summaryNode ? " cloud-node--summary" : ""}${isChildNode ? " cloud-node--child" : ""}${isGroupNode ? " cloud-node--group" : ""}`,
      style: { 
        borderColor: theme.border,
        cursor: data.summaryNode ? "default" : "pointer",
      },
      title: data.resourceType || data.typeLabel || data.label,
    },
    h(Handle, { type: "target", position: Position.Left, style: { background: theme.border, borderColor: theme.border } }),
    h(
      "div",
      { className: "cloud-node__header" },
      isChildNode && data.childIcon ? 
        h("span", { style: { fontSize: "1.5rem", marginRight: "8px" } }, data.childIcon) :
        h(ProviderIcon, { providerKey: data.providerKey, iconPath: data.iconPath }),
      h(
        "div",
        { className: "cloud-node__title-wrap" },
        h("div", { className: "cloud-node__title" }, data.label),
        h("div", { className: "cloud-node__type" }, data.typeLabel),
      ),
      h("span", { className: "cloud-node__provider" }, theme.label),
      isExpandable && !isChildNode ? h(
        "button",
        {
          className: "cloud-node__expand-btn",
          title: isExpanded ? "Collapse" : (isGroupNode ? "Expand to show all resources" : "Expand to show children"),
          onClick: (e) => {
            e.stopPropagation();
            if (window.toggleNodeExpansion) {
              window.toggleNodeExpansion(data.nodeId || data.id);
            }
          }
        },
        isExpanded ? "−" : "+"
      ) : null,
    ),
    !isChildNode ? h(
      "div",
      { className: "cloud-node__meta" },
      h("span", null, data.repoName || "—"),
      h(
        "span",
        {
          className: `cloud-node__badge ${
            data.public ? "cloud-node__badge--public" : 
            data.isRestricted ? "cloud-node__badge--restricted" : 
            "cloud-node__badge--private"
          }`,
        },
        data.public ? "Public" : data.isRestricted ? "IP Restricted" : "Private",
      ),
    ) : null,
    securityIcons.length > 0 ? h("div", { className: "cloud-node__security-icons" }, ...securityIcons) : null,
    data.sourceFile ? h("div", { className: "cloud-node__source" }, data.sourceFile) : null,
    h(Handle, { type: "source", position: Position.Right, style: { background: theme.border, borderColor: theme.border } }),
  );
}

const nodeTypes = { cloudNode: CloudNode };

// Modal management
const modalOverlay = document.getElementById("cloud-arch-modal-overlay");
const modalTitle = document.getElementById("modal-title");
const modalSubtitle = document.getElementById("modal-subtitle");
const modalBody = document.getElementById("modal-body");
const modalIcon = document.getElementById("modal-icon");
const modalCloseBtn = document.getElementById("modal-close-btn");

function closeModal() {
  if (modalOverlay) {
    modalOverlay.hidden = true;
  }
}

function openModal(resourceId, nodeData) {
  if (!modalOverlay) return;
  
  modalOverlay.hidden = false;
  modalTitle.textContent = "Loading...";
  modalSubtitle.textContent = "";
  modalBody.innerHTML = '<div class="cloud-arch-modal-loading">Loading resource details...</div>';
  
  // Set icon
  const theme = themeFor(nodeData.providerKey);
  modalIcon.style.background = theme.background;
  modalIcon.style.color = theme.border;
  modalIcon.textContent = "☁";
  
  // Fetch resource details
  const url = new URL("/api/cloud/resource-details", window.location.origin);
  url.searchParams.set("id", resourceId);
  
  // Add subscription for Internet node
  if (resourceId.toLowerCase() === "internet") {
    const subInput = document.getElementById("subscription-input");
    if (subInput && subInput.value) {
      url.searchParams.set("sub", subInput.value);
    }
  }
  
  fetch(url.toString(), { headers: { Accept: "application/json" } })
    .then(resp => resp.json())
    .then(data => {
      if (data.error) {
        throw new Error(data.error);
      }
      renderModalContent(data);
    })
    .catch(err => {
      modalBody.innerHTML = `<div class="cloud-arch-modal-empty">❌ Error loading details: ${err.message}</div>`;
    });
}

function renderModalContent(data) {
  modalTitle.textContent = data.name;
  modalSubtitle.textContent = data.type_label ? `${data.type_label}${data.resource_group ? " • " + data.resource_group : ""}` : (data.resource_group || "");
  
  const sections = [];
  
  // Special handling for Internet node (attack surface)
  if (data.attack_surface) {
    const surface = data.attack_surface;
    
    // Attack Surface Overview
    const overviewFields = [
      { label: "Total Public Assets", value: `<strong style="font-size: 1.2em; color: var(--accent-warning);">${surface.total_public_assets}</strong>`, isHtml: true },
      { label: "WAF Protected", value: `<span class="cloud-arch-modal-badge cloud-arch-modal-badge--success">${surface.waf_protected_count} / ${surface.total_public_assets}</span>`, isHtml: true },
      { label: "Unrestricted Access", value: `<span class="cloud-arch-modal-badge cloud-arch-modal-badge--danger">${surface.unrestricted_count}</span>`, isHtml: true },
    ];
    sections.push({ title: "🎯 Attack Surface Overview", icon: "🎯", fields: overviewFields });
    
    // Protocols & Ports
    if (surface.protocols && surface.protocols.length > 0) {
      const protocolsHtml = '<div style="display: flex; flex-wrap: wrap; gap: 8px;">' +
        surface.protocols.map(proto => 
          `<span class="cloud-arch-modal-badge cloud-arch-modal-badge--info">${proto}</span>`
        ).join('') + '</div>';
      sections.push({ 
        title: "Exposed Protocols & Ports", 
        icon: "🔌", 
        fields: [{ label: "", value: protocolsHtml, isHtml: true, fullWidth: true }] 
      });
    }
    
    // DNS Names
    if (surface.dns_names && surface.dns_names.length > 0) {
      const dnsHtml = '<ul class="cloud-arch-modal-list">' +
        surface.dns_names.map(dns => `<li class="cloud-arch-modal-list-item">${escapeHtml(dns)}</li>`).join('') +
        '</ul>';
      sections.push({ 
        title: "Public DNS Names", 
        icon: "🌍", 
        fields: [{ label: "", value: dnsHtml, isHtml: true, fullWidth: true }] 
      });
    }
    
    // Public IPs
    if (surface.public_ips && surface.public_ips.length > 0) {
      const ipsHtml = '<div style="display: flex; flex-wrap: wrap; gap: 8px;">' +
        surface.public_ips.map(ip => 
          `<code style="padding: 4px 8px; background: var(--bg-base); border-radius: 4px;">${escapeHtml(ip)}</code>`
        ).join('') + '</div>';
      sections.push({ 
        title: "Public IP Addresses", 
        icon: "🔢", 
        fields: [{ label: "", value: ipsHtml, isHtml: true, fullWidth: true }] 
      });
    }
    
    // Entry Points
    if (surface.entry_points && surface.entry_points.length > 0) {
      const entryPointsHtml = '<ul class="cloud-arch-modal-list">' +
        surface.entry_points.map(ep => {
          const badges = [];
          if (ep.waf_protected) badges.push('<span class="cloud-arch-modal-badge cloud-arch-modal-badge--success">🛡️ WAF</span>');
          if (!ep.is_restricted) badges.push('<span class="cloud-arch-modal-badge cloud-arch-modal-badge--warning">⚠️ Unrestricted</span>');
          
          return `<li class="cloud-arch-modal-list-item">
            <strong>${escapeHtml(ep.name)}</strong> <em style="color: var(--text-muted); font-size: 0.85rem;">(${escapeHtml(ep.type)})</em><br/>
            ${ep.fqdn ? `<code>${escapeHtml(ep.fqdn)}</code> ` : ""}
            ${badges.join(' ')}
          </li>`;
        }).join('') +
        '</ul>';
      sections.push({ 
        title: "Public Entry Points", 
        icon: "🚪", 
        fields: [{ label: "", value: entryPointsHtml, isHtml: true, fullWidth: true }] 
      });
    }
    
    // Render Internet node sections
    modalBody.innerHTML = sections.map(section => {
      const fieldsHtml = section.fields.map(field => {
        if (field.fullWidth) {
          return `
            <div class="cloud-arch-modal-field" style="grid-column: 1 / -1;">
              ${field.label ? `<div class="cloud-arch-modal-field-label">${field.label}</div>` : ""}
              <div class="cloud-arch-modal-field-value">${field.isHtml ? field.value : escapeHtml(field.value)}</div>
            </div>
          `;
        }
        return `
          <div class="cloud-arch-modal-field">
            <div class="cloud-arch-modal-field-label">${field.label}</div>
            <div class="cloud-arch-modal-field-value">${field.isHtml ? field.value : escapeHtml(field.value)}</div>
          </div>
        `;
      }).join('');
      
      return `
        <div class="cloud-arch-modal-section">
          <div class="cloud-arch-modal-section-title">
            <span class="cloud-arch-modal-section-icon">${section.icon}</span>
            ${section.title}
          </div>
          <div class="cloud-arch-modal-grid">
            ${fieldsHtml}
          </div>
        </div>
      `;
    }).join('');
    
    return;
  }
  
  // Configuration Section (regular resources)
  if (data.configuration) {
    const configFields = [];
    if (data.configuration.sku_name) configFields.push({ label: "SKU", value: data.configuration.sku_name });
    if (data.configuration.sku_tier) configFields.push({ label: "Tier", value: data.configuration.sku_tier });
    if (data.location) configFields.push({ label: "Location", value: data.location });
    if (data.subscription) configFields.push({ label: "Subscription", value: data.subscription });
    if (data.environment) configFields.push({ label: "Environment", value: data.environment });
    if (data.configuration.kind) configFields.push({ label: "Kind", value: data.configuration.kind });
    
    if (data.configuration.hostname) configFields.push({ label: "Hostname", value: data.configuration.hostname });
    if (data.configuration.protocol) configFields.push({ label: "Protocol", value: data.configuration.protocol });
    if (data.configuration.port) configFields.push({ label: "Port", value: data.configuration.port });
    if (data.configuration.backend_address) configFields.push({ label: "Backend", value: data.configuration.backend_address });
    
    if (configFields.length > 0) {
      sections.push({ title: "Configuration", icon: "⚙️", fields: configFields });
    }
  }
  
  // Security Section
  if (data.security) {
    const sec = data.security;
    const securityFields = [];
    
    // Public exposure badge
    const exposureBadge = sec.is_public 
      ? '<span class="cloud-arch-modal-badge cloud-arch-modal-badge--danger">🌐 Public</span>'
      : '<span class="cloud-arch-modal-badge cloud-arch-modal-badge--success">🔒 Private</span>';
    securityFields.push({ label: "Exposure", value: exposureBadge, isHtml: true });
    
    // WAF status
    if (sec.waf_enabled !== undefined) {
      const wafBadge = sec.waf_enabled
        ? `<span class="cloud-arch-modal-badge cloud-arch-modal-badge--success">🛡️ WAF ${sec.waf_mode || "Enabled"}</span>`
        : '<span class="cloud-arch-modal-badge cloud-arch-modal-badge--warning">⚠️ No WAF</span>';
      securityFields.push({ label: "WAF Protection", value: wafBadge, isHtml: true });
    }
    
    // Restricted access
    if (sec.is_restricted !== undefined) {
      const restrictedBadge = sec.is_restricted
        ? '<span class="cloud-arch-modal-badge cloud-arch-modal-badge--success">✓ IP Restricted</span>'
        : '<span class="cloud-arch-modal-badge cloud-arch-modal-badge--warning">⚠️ Unrestricted</span>';
      securityFields.push({ label: "Access Control", value: restrictedBadge, isHtml: true });
    }
    
    // TLS version
    if (sec.tls_version) {
      const tlsBadge = sec.tls_version >= "1.2" || sec.tls_version.includes("1.2")
        ? '<span class="cloud-arch-modal-badge cloud-arch-modal-badge--success">🔐 ' + sec.tls_version + '</span>'
        : '<span class="cloud-arch-modal-badge cloud-arch-modal-badge--warning">⚠️ ' + sec.tls_version + ' (Weak)</span>';
      securityFields.push({ label: "TLS Version", value: tlsBadge, isHtml: true });
    }
    
    // Public network access
    if (sec.public_network_access) {
      securityFields.push({ label: "Public Network Access", value: sec.public_network_access });
    }
    
    // Encryption
    if (sec.encryption_at_rest !== undefined) {
      const encBadge = sec.encryption_at_rest
        ? '<span class="cloud-arch-modal-badge cloud-arch-modal-badge--success">🔐 Encrypted</span>'
        : '<span class="cloud-arch-modal-badge cloud-arch-modal-badge--warning">⚠️ Not Encrypted</span>';
      securityFields.push({ label: "Encryption at Rest", value: encBadge, isHtml: true });
    }
    
    // Firewall rules
    if (sec.firewall_rules && sec.firewall_rules.length > 0) {
      const rulesHtml = '<ul class="cloud-arch-modal-list">' + 
        sec.firewall_rules.map(rule => 
          `<li class="cloud-arch-modal-list-item">${rule.name}: ${rule.start_ip} - ${rule.end_ip}</li>`
        ).join('') + '</ul>';
      securityFields.push({ label: "Firewall Rules", value: rulesHtml, isHtml: true, fullWidth: true });
    }
    
    // Allowed IPs
    if (sec.allowed_ips && sec.allowed_ips.length > 0) {
      securityFields.push({ label: "Allowed IPs", value: sec.allowed_ips.join(", ") });
    }
    
    sections.push({ title: "Security", icon: "🔒", fields: securityFields });
  }
  
  // Network Section
  if (data.network) {
    const net = data.network;
    const networkFields = [];
    
    if (net.public_ips && net.public_ips.length > 0) {
      const ipsHtml = net.public_ips.map(ip => 
        `<span class="cloud-arch-modal-badge cloud-arch-modal-badge--info">🌐 ${ip}</span>`
      ).join(' ');
      networkFields.push({ label: "Public IPs", value: ipsHtml, isHtml: true, fullWidth: true });
    }
    
    if (net.dns_names && net.dns_names.length > 0) {
      const dnsHtml = net.dns_names.map(dns => 
        `<span class="cloud-arch-modal-badge cloud-arch-modal-badge--info">🔗 ${dns}</span>`
      ).join(' ');
      networkFields.push({ label: "DNS Names", value: dnsHtml, isHtml: true, fullWidth: true });
    }
    
    // Add port information if available
    if (data.configuration && data.configuration.port) {
      const portBadge = `<span class="cloud-arch-modal-badge cloud-arch-modal-badge--info">${data.configuration.protocol || 'TCP'}:${data.configuration.port}</span>`;
      networkFields.push({ label: "Exposed Port", value: portBadge, isHtml: true });
    }
    
    if (net.vnet) networkFields.push({ label: "Virtual Network", value: net.vnet });
    if (net.subnet) networkFields.push({ label: "Subnet", value: net.subnet });
    
    if (net.private_endpoints && net.private_endpoints.length > 0) {
      const peHtml = '<ul class="cloud-arch-modal-list">' +
        net.private_endpoints.map(pe => `<li class="cloud-arch-modal-list-item">${pe}</li>`).join('') +
        '</ul>';
      networkFields.push({ label: "Private Endpoints", value: peHtml, isHtml: true, fullWidth: true });
    }
    
    if (networkFields.length > 0) {
      sections.push({ title: "Network", icon: "🌐", fields: networkFields });
    }
  }
  
  // Identity & Access Section
  if (data.identity) {
    const ident = data.identity;
    const identityFields = [];
    
    if (ident.type) {
      identityFields.push({ label: "Identity Type", value: ident.type });
    }
    
    if (ident.managed_identity !== undefined) {
      const miBadge = ident.managed_identity
        ? '<span class="cloud-arch-modal-badge cloud-arch-modal-badge--success">✓ Managed Identity</span>'
        : '<span class="cloud-arch-modal-badge cloud-arch-modal-badge--warning">🔑 Keys/Passwords</span>';
      identityFields.push({ label: "Authentication", value: miBadge, isHtml: true });
    }
    
    if (ident.user_assigned_identities && ident.user_assigned_identities.length > 0) {
      const uaiHtml = '<ul class="cloud-arch-modal-list">' +
        ident.user_assigned_identities.map(uai => `<li class="cloud-arch-modal-list-item">${uai}</li>`).join('') +
        '</ul>';
      identityFields.push({ label: "User-Assigned Identities", value: uaiHtml, isHtml: true, fullWidth: true });
    }
    
    if (identityFields.length > 0) {
      sections.push({ title: "Identity & Access", icon: "🔑", fields: identityFields });
    }
  }
  
  // Logging Section - Removed per user request
  
  // Key Vault Section
  if (data.keyvault && data.keyvault.references && data.keyvault.references.length > 0) {
    const kvHtml = '<ul class="cloud-arch-modal-list">' +
      data.keyvault.references.map(ref => 
        `<li class="cloud-arch-modal-list-item"><strong>${ref.path}:</strong> <code>${ref.reference}</code></li>`
      ).join('') +
      '</ul>';
    sections.push({ 
      title: "Key Vault References", 
      icon: "🔐", 
      fields: [{ label: "", value: kvHtml, isHtml: true, fullWidth: true }] 
    });
  }
  
  // Render all sections
  modalBody.innerHTML = sections.map(section => {
    const fieldsHtml = section.fields.map(field => {
      if (field.fullWidth) {
        return `
          <div class="cloud-arch-modal-field" style="grid-column: 1 / -1;">
            ${field.label ? `<div class="cloud-arch-modal-field-label">${field.label}</div>` : ""}
            <div class="cloud-arch-modal-field-value">${field.isHtml ? field.value : escapeHtml(field.value)}</div>
          </div>
        `;
      }
      return `
        <div class="cloud-arch-modal-field">
          <div class="cloud-arch-modal-field-label">${field.label}</div>
          <div class="cloud-arch-modal-field-value">${field.isHtml ? field.value : escapeHtml(field.value)}</div>
        </div>
      `;
    }).join('');
    
    return `
      <div class="cloud-arch-modal-section">
        <div class="cloud-arch-modal-section-title">
          <span class="cloud-arch-modal-section-icon">${section.icon}</span>
          ${section.title}
        </div>
        <div class="cloud-arch-modal-grid">
          ${fieldsHtml}
        </div>
      </div>
    `;
  }).join('');
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

if (modalCloseBtn) {
  modalCloseBtn.addEventListener("click", closeModal);
}

if (modalOverlay) {
  modalOverlay.addEventListener("click", (e) => {
    if (e.target === modalOverlay) {
      closeModal();
    }
  });
}

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && modalOverlay && !modalOverlay.hidden) {
    closeModal();
  }
});

function App() {
  const [nodes, setNodes] = useState([]);
  const [edges, setEdges] = useState([]);
  const [graphKey, setGraphKey] = useState("initial");
  const [expandedNodes, setExpandedNodes] = useState(new Set());

  const onNodeClick = useCallback((event, node) => {
    console.log("Node clicked:", node);
    if (node.data && !node.data.summaryNode) {
      openModal(node.id, node.data);
    }
  }, []);
  
  const onEdgeClick = useCallback((event, edge) => {
    // Build edge details for modal
    const sourceNode = nodes.find(n => n.id === edge.source);
    const targetNode = nodes.find(n => n.id === edge.target);
    
    if (!sourceNode || !targetNode) return;
    
    const connectionType = edge.data?.connection_type || "unknown";
    const protocol = edge.label || "N/A";
    
    // Determine auth type based on source/target data
    let authType = "Unknown";
    if (sourceNode.data?.hasManagedIdentity || targetNode.data?.hasManagedIdentity) {
      authType = "Managed Identity";
    } else if (connectionType === "public") {
      authType = "Public (No Authentication)";
    } else if (connectionType === "private" || connectionType === "internal") {
      authType = "Private Network / VNet";
    }
    
    const edgeData = {
      label: `${sourceNode.data.label} → ${targetNode.data.label}`,
      typeLabel: "Network Connection",
      repoName: sourceNode.data.repoName,
      providerKey: sourceNode.data.providerKey,
      sections: [
        {
          icon: "🔗",
          title: "Connection Details",
          fields: [
            { label: "Source", value: sourceNode.data.label },
            { label: "Destination", value: targetNode.data.label },
            { label: "Protocol", value: protocol },
            { label: "Connection Type", value: connectionType.charAt(0).toUpperCase() + connectionType.slice(1) },
            { label: "Authentication", value: authType },
          ]
        }
      ]
    };
    
    openModal(edge.id, edgeData);
  }, [nodes]);
  
  // Toggle node expansion - show/hide existing child nodes
  const toggleNodeExpansion = useCallback((nodeId) => {
    const isCurrentlyExpanded = expandedNodes.has(nodeId);
    
    if (isCurrentlyExpanded) {
      // Collapse: hide child nodes and edges
      setNodes(prevNodes => prevNodes.map(n => {
        if (n.data?.parentNodeId === nodeId || (n.parent === nodeId && n.data?.isChildNode)) {
          return { ...n, hidden: true };
        }
        if (n.id === nodeId) {
          return { ...n, data: { ...n.data, expanded: false } };
        }
        return n;
      }));
      
      setEdges(prevEdges => prevEdges.map(e => {
        // Hide edges connected to child nodes
        const sourceNode = nodes.find(n => n.id === e.source);
        const targetNode = nodes.find(n => n.id === e.target);
        if ((sourceNode?.data?.parentNodeId === nodeId) || (targetNode?.data?.parentNodeId === nodeId) ||
            (e.source === nodeId && targetNode?.data?.isChildNode) || (e.target === nodeId && sourceNode?.data?.isChildNode)) {
          return { ...e, hidden: true };
        }
        return e;
      }));
      
      setExpandedNodes(prev => {
        const next = new Set(prev);
        next.delete(nodeId);
        return next;
      });
    } else {
      // Expand: show child nodes and edges
      setNodes(prevNodes => prevNodes.map(n => {
        if (n.data?.parentNodeId === nodeId || (n.parent === nodeId && n.data?.isChildNode)) {
          return { ...n, hidden: false };
        }
        if (n.id === nodeId) {
          return { ...n, data: { ...n.data, expanded: true } };
        }
        return n;
      }));
      
      setEdges(prevEdges => prevEdges.map(e => {
        // Show edges connected to child nodes
        const sourceNode = nodes.find(n => n.id === e.source);
        const targetNode = nodes.find(n => n.id === e.target);
        if ((sourceNode?.data?.parentNodeId === nodeId) || (targetNode?.data?.parentNodeId === nodeId) ||
            (e.source === nodeId && targetNode?.data?.isChildNode) || (e.target === nodeId && sourceNode?.data?.isChildNode)) {
          return { ...e, hidden: false };
        }
        return e;
      }));
      
      setExpandedNodes(prev => new Set(prev).add(nodeId));
    }
  }, [expandedNodes, nodes]);
  
  // Expose toggle function globally for CloudNode component
  useEffect(() => {
    window.toggleNodeExpansion = toggleNodeExpansion;
    return () => { delete window.toggleNodeExpansion; };
  }, [toggleNodeExpansion]);

  const showError = useCallback((message) => {
    errorCardEl.hidden = false;
    errorEl.textContent = message;
  }, []);

  const hideError = useCallback(() => {
    errorCardEl.hidden = true;
    errorEl.textContent = "";
  }, []);

  const renderSummary = useCallback((payload, subscriptionName) => {
    const providers = payload?.summary?.provider_counts || [];
    const resourceCount = payload?.summary?.resource_count ?? 0;
    const displayedCount = payload?.summary?.displayed_resource_count ?? resourceCount;
    const omittedCount = payload?.summary?.omitted_resource_count ?? 0;
    const connectionCount = payload?.summary?.connection_count ?? 0;
    summaryLineEl.innerHTML = [
      `<span><strong>${resourceCount}</strong> resources</span>`,
      omittedCount > 0 ? `<span><strong>${displayedCount}</strong> shown</span>` : null,
      `<span><strong>${connectionCount}</strong> connections</span>`,
      payload?.summary?.layout_mode ? `<span><strong>${payload.summary.layout_mode}</strong> mode</span>` : null,
      `<span><strong>${subscriptionName || "subscription-production"}</strong></span>`,
    ]
      .filter(Boolean)
      .join(" ");

    legendEl.innerHTML = providers
      .map((provider) => {
        const theme = themeFor(provider.key);
        const marker = provider.key === "unknown" || provider.key === "external" ? "☁" : "■";
        return `
          <span class="cloud-arch-pill" style="border-color:${theme.border};">
            <span style="display:inline-flex;align-items:center;justify-content:center;width:14px;height:14px;color:${theme.border};">${marker}</span>
            <span>${provider.label}</span>
            <span class="count">${provider.count}</span>
          </span>
        `;
      })
      .join("");
    
    // Show connection legend
    const connectionLegendEl = document.getElementById("cloud-arch-connection-legend");
    if (connectionLegendEl && connectionCount > 0) {
      connectionLegendEl.style.display = "block";
      connectionLegendEl.innerHTML = `
        <div style="font-weight: 700; margin-bottom: 8px; color: var(--text);">Connection Legend</div>
        <div style="display: flex; flex-direction: column; gap: 6px;">
          <div style="display: flex; align-items: center; gap: 8px;">
            <svg width="32" height="3" style="flex-shrink: 0;"><line x1="0" y1="1.5" x2="32" y2="1.5" stroke="#f97316" stroke-width="3" /></svg>
            <span style="color: var(--text-muted);">WAF-protected ingress (Prevention mode)</span>
          </div>
          <div style="display: flex; align-items: center; gap: 8px;">
            <svg width="32" height="3" style="flex-shrink: 0;"><line x1="0" y1="1.5" x2="32" y2="1.5" stroke="#f59e0b" stroke-width="2" /></svg>
            <span style="color: var(--text-muted);">WAF Detection mode or IP-restricted access</span>
          </div>
          <div style="display: flex; align-items: center; gap: 8px;">
            <svg width="32" height="3" style="flex-shrink: 0;"><line x1="0" y1="1.5" x2="32" y2="1.5" stroke="#ef4444" stroke-width="3" /></svg>
            <span style="color: var(--text-muted);">Directly public — no WAF or network restriction</span>
          </div>
          <div style="display: flex; align-items: center; gap: 8px;">
            <svg width="32" height="3" style="flex-shrink: 0;"><line x1="0" y1="1.5" x2="32" y2="1.5" stroke="#dc2626" stroke-width="2" /></svg>
            <span style="color: var(--text-muted);">Firewall-protected ingress</span>
          </div>
          <div style="display: flex; align-items: center; gap: 8px;">
            <svg width="32" height="3" style="flex-shrink: 0;"><line x1="0" y1="1.5" x2="32" y2="1.5" stroke="#94a3b8" stroke-width="1" /></svg>
            <span style="color: var(--text-muted);">Internal data flows (backend → data stores)</span>
          </div>
        </div>
      `;
    }
  }, []);

  const loadGraph = useCallback(async (subscriptionName, viewMode = activeViewMode) => {
    hideError();
    summaryLineEl.textContent = "Loading architecture from CozoDB…";
    legendEl.innerHTML = "";

    const url = new URL("/api/cloud/architecture", window.location.origin);
    const sub = (subscriptionName || "").trim();
    const mode = (viewMode || activeViewMode || "overview").trim().toLowerCase() === "full" ? "full" : "overview";
    activeViewMode = mode;
    syncViewButtons();
    if (sub) {
      url.searchParams.set("sub", sub);
    }
    url.searchParams.set("view", mode);

    try {
      const resp = await fetch(url.toString(), { headers: { Accept: "application/json" } });
      const payload = await resp.json();
      if (!resp.ok) {
        throw new Error(payload?.error || `Request failed with status ${resp.status}`);
      }

      const nodeMap = new Map();
      for (const node of payload.nodes || []) {
        nodeMap.set(String(node.id), node);
      }

      const preparedNodes = (payload.nodes || []).map((node, index) => ({
        ...node,
        position: node.position || { x: (index % 4) * 420, y: Math.floor(index / 4) * 180 },
        data: {
          ...node.data,
          nodeId: node.id,  // Add node ID to data for expand button
          index,
          color: themeFor(node.data?.providerKey).border,
        },
        style: {
          ...(node.style || {}),
          width: node.style?.width || 340,
          minHeight: node.style?.minHeight || 132,
        },
      }));

      const preparedEdges = (payload.edges || []).map((edge) => {
        const sourceNode = nodeMap.get(String(edge.source));
        const targetNode = nodeMap.get(String(edge.target));
        
        // Use backend-provided style if available, otherwise fallback to computed style
        let edgeColor;
        let strokeWidth = 2;
        let strokeOpacity = 1.0;
        
        // Check if backend provided style
        if (edge.style) {
          edgeColor = edge.style.stroke || edge.style.strokeColor || "#94a3b8";
          strokeWidth = edge.style.strokeWidth || 2;
          strokeOpacity = edge.style.strokeOpacity || 1.0;
        } else {
          // Fallback: Security-based color coding for edges
          const connType = edge.data?.connection_type || "";
          const isFromInternet = String(edge.source) === "Internet";
          const wafProtected = edge.data?.waf_protected === true;
          
          if (isFromInternet) {
            // Public Internet → Resource connections
            if (wafProtected) {
              edgeColor = "#f59e0b"; // Orange - WAF protected entry
              strokeWidth = 3;
            } else {
              edgeColor = "#ef4444"; // Red - Direct public exposure (HIGH RISK)
              strokeWidth = 3;
              strokeOpacity = 0.9;
            }
          } else if (connType === "public") {
            edgeColor = "#f97316"; // Orange - Cross-resource public connection
            strokeWidth = 2.5;
          } else if (connType === "internal" || connType === "private") {
            edgeColor = "#10b981"; // Green - Secure internal connection
            strokeWidth = 2;
            strokeOpacity = 0.7;
          } else if (connType === "waf_protection") {
            edgeColor = "#f97316"; // Orange for WAF protection
            strokeWidth = 3;
          } else if (connType === "firewall_ingress" || connType === "firewall") {
            edgeColor = "#dc2626"; // Red for firewall
            strokeWidth = 2;
          } else if (connType === "routing" || connType === "backend") {
            edgeColor = "#f59e0b"; // Amber for routing/backend
            strokeWidth = 2;
          } else if (connType === "data_access" || connType === "telemetry") {
            edgeColor = "#94a3b8"; // Grey for data access
            strokeWidth = 1;
          } else {
            // Default: use provider theme color for cross-tier connections
            edgeColor = themeFor(sourceNode?.data?.providerKey || targetNode?.data?.providerKey).border;
            strokeWidth = 2;
            strokeOpacity = 0.8;
          }
        }
        
        let labelColor = "#e2e8f0"; // Default light text for dark backgrounds
        if (edgeColor === "#f97316" || edgeColor === "#f59e0b") {
          labelColor = "#fef3c7"; // Light yellow for orange/amber edges
        } else if (edgeColor === "#ef4444" || edgeColor === "#dc2626") {
          labelColor = "#ffffff"; // White for red edges
        } else if (edgeColor === "#10b981") {
          labelColor = "#d1fae5"; // Light green for green edges
        }
        
        return {
          ...edge,
          type: "smoothstep",
          style: {
            stroke: edgeColor,
            strokeWidth,
            strokeOpacity,
          },
          labelStyle: {
            fill: labelColor,
            fontSize: 11,
            fontWeight: 600,
          },
          labelBgStyle: {
            fill: "rgba(15, 23, 42, 0.95)",
            stroke: "rgba(30, 41, 59, 0.8)",
            strokeWidth: 1,
            padding: "6px 10px",
            borderRadius: "4px",
            boxShadow: "0 2px 8px rgba(0, 0, 0, 0.3)",
          },
          labelBgPadding: [6, 10],
          labelShowBg: true,
          labelBgBorderRadius: 4,
          markerEnd: {
            type: MarkerType.ArrowClosed,
            color: edgeColor,
          },
        };
      });

      // Deduplicate edge labels to prevent overlapping
      // Group edges by source->target and only show label on first edge of each group
      const edgeLabelGroups = new Map();
      preparedEdges.forEach(edge => {
        const key = `${edge.source}->${edge.target}->${edge.label || ''}`;
        if (!edgeLabelGroups.has(key)) {
          edgeLabelGroups.set(key, []);
        }
        edgeLabelGroups.get(key).push(edge);
      });
      
      // Hide labels on duplicate edges (keep only first in each group)
      const deduplicatedEdges = preparedEdges.map(edge => {
        const key = `${edge.source}->${edge.target}->${edge.label || ''}`;
        const group = edgeLabelGroups.get(key);
        if (group && group.length > 1 && group.indexOf(edge) > 0) {
          // This is a duplicate - hide the label
          return {
            ...edge,
            label: undefined,
            labelStyle: undefined,
            labelBgStyle: undefined,
          };
        }
        return edge;
      });

      setNodes(preparedNodes);
      setEdges(deduplicatedEdges);
      setGraphKey(`${payload.subscription_id || sub || "latest"}:${payload?.summary?.layout_mode || mode}:${preparedNodes.length}:${preparedEdges.length}`);
      renderSummary(payload, payload.subscription_name || sub || "subscription-production");
      const isEmpty = preparedNodes.length === 0;
      emptyEl.hidden = !isEmpty;
      rootEl.hidden = isEmpty;
      if (!isEmpty) {
        const query = new URLSearchParams();
        if (payload.subscription_id || sub) query.set("sub", payload.subscription_name || sub);
        if (mode) query.set("view", mode);
        window.history.replaceState(null, "", `${window.location.pathname}${query.toString() ? `?${query}` : ""}`);
      }
    } catch (err) {
      setNodes([]);
      setEdges([]);
      setGraphKey(`error:${Date.now()}`);
      emptyEl.hidden = true;
      rootEl.hidden = true;
      showError(err instanceof Error ? err.message : String(err));
      summaryLineEl.textContent = "Unable to load cloud architecture.";
      legendEl.innerHTML = "";
    }
  }, [hideError, renderSummary, showError]);

  useEffect(() => {
    loadGraph(CONFIG.initialSubscription || "");
  }, [loadGraph]);

  useEffect(() => {
    window.__triageCloudArchLoad = loadGraph;
    return () => {
      delete window.__triageCloudArchLoad;
    };
  }, [loadGraph]);

  const miniMapNodeColor = useCallback((node) => themeFor(node?.data?.providerKey).border, []);

  return h(
    "div",
    { style: { width: "100%", height: "100%" } },
    h(
      ReactFlow,
      {
        key: graphKey,
        nodes,
        edges,
        nodeTypes,
        onNodeClick,
        onEdgeClick,
        fitView: true,
        fitViewOptions: { padding: 0.22, includeHiddenNodes: false },
        defaultEdgeOptions: { type: "smoothstep" },
        proOptions: { hideAttribution: true },
        minZoom: 0.08,
        maxZoom: 2.5,
        panOnScroll: true,
        zoomOnScroll: true,
        nodesDraggable: false,
        nodesConnectable: false,
      },
      h(MiniMap, { nodeColor: miniMapNodeColor, zoomable: true, pannable: true }),
      h(Controls, null),
      h(Background, { gap: 16, size: 1, color: "rgba(148, 163, 184, 0.24)" }),
    ),
  );
}

function mount() {
  if (!rootEl) return;
  createRoot(rootEl).render(h(App));
}

if (formEl) {
  formEl.addEventListener("submit", (event) => {
    event.preventDefault();
    if (typeof window.__triageCloudArchLoad === "function") {
      window.__triageCloudArchLoad((subscriptionInput.value || "").trim(), activeViewMode);
    }
  });
}

for (const button of viewButtons) {
  button.addEventListener("click", () => {
    const mode = (button.dataset.cloudArchView || "").toLowerCase();
    if (!mode || mode === activeViewMode) {
      return;
    }
    activeViewMode = mode === "full" ? "full" : "overview";
    syncViewButtons();
    if (typeof window.__triageCloudArchLoad === "function") {
      window.__triageCloudArchLoad((subscriptionInput.value || "").trim(), activeViewMode);
    }
  });
}

mount();
syncViewButtons();
