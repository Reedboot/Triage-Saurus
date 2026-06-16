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
  
  // Logging Section
  if (data.logging) {
    const log = data.logging;
    const loggingFields = [];
    
    if (log.diagnostic_logging_enabled !== undefined) {
      const logBadge = log.diagnostic_logging_enabled
        ? '<span class="cloud-arch-modal-badge cloud-arch-modal-badge--success">📊 Enabled</span>'
        : '<span class="cloud-arch-modal-badge cloud-arch-modal-badge--danger">⚠️ Disabled</span>';
      loggingFields.push({ label: "Diagnostic Logging", value: logBadge, isHtml: true });
    }
    
    if (log.log_categories && log.log_categories.length > 0) {
      loggingFields.push({ label: "Log Categories", value: log.log_categories.join(", ") });
    }
    
    if (loggingFields.length > 0) {
      sections.push({ title: "Logging", icon: "📊", fields: loggingFields });
    }
  }
  
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
  
  // Toggle node expansion - fetch children and add them to the graph
  const toggleNodeExpansion = useCallback(async (nodeId) => {
    const isCurrentlyExpanded = expandedNodes.has(nodeId);
    
    if (isCurrentlyExpanded) {
      // Collapse: remove child nodes and edges
      setNodes(prevNodes => prevNodes.filter(n => !n.data?.parentNodeId || n.data.parentNodeId !== nodeId));
      setEdges(prevEdges => prevEdges.filter(e => !e.data?.parentNodeId || e.data.parentNodeId !== nodeId));
      setExpandedNodes(prev => {
        const next = new Set(prev);
        next.delete(nodeId);
        return next;
      });
      
      // Update parent node data to show collapsed state
      setNodes(prevNodes => prevNodes.map(n => 
        n.id === nodeId ? { ...n, data: { ...n.data, expanded: false } } : n
      ));
    } else {
      // Expand: fetch children from API
      try {
        // Find the node to determine if it's a group node
        const parentNode = nodes.find(n => n.id === nodeId);
        if (!parentNode) return;
        
        const isGroupNode = parentNode.data.isGroupNode || false;
        const apiUrl = isGroupNode 
          ? `/api/cloud/group-members?group_id=${encodeURIComponent(nodeId)}`
          : `/api/cloud/resource-children?resource_id=${encodeURIComponent(nodeId)}`;
        
        const response = await fetch(apiUrl);
        if (!response.ok) {
          console.error("Failed to fetch children:", response.statusText);
          return;
        }
        
        const data = await response.json();
        const children = isGroupNode ? data.members : data.children;
        
        if (children && children.length > 0) {
          const parentX = parentNode.position.x;
          const parentY = parentNode.position.y;
          
          // Limit number of displayed children for performance
          const displayChildren = children.slice(0, 50);
          const hasMore = children.length > 50;
          
          // Create child nodes
          const childNodes = displayChildren.map((child, idx) => ({
            id: child.id,
            type: "cloudNode",
            position: {
              x: parentX + 380,  // Position to the right of parent
              y: parentY + (idx * 90) - ((displayChildren.length - 1) * 45)  // Center vertically around parent
            },
            data: {
              label: child.name,
              providerKey: "azure",
              providerLabel: "Azure",
              typeLabel: child.type,
              repoName: parentNode.data.repoName,
              sourceFile: child.details ? Object.entries(child.details).map(([k, v]) => `${k}: ${v}`).join(", ") : "",
              public: false,
              tier: "child",
              iconPath: null,
              synthetic: false,
              resourceType: child.type,
              hasManagedIdentity: false,
              loggingEnabled: false,
              isChildNode: true,
              parentNodeId: nodeId,
              childIcon: child.icon || "📄",
            },
            style: { width: 300, minHeight: 90 },
          }));
          
          // Create edges from parent to children
          const childEdges = data.children.map(child => ({
            id: `edge-child-${nodeId}-${child.id}`,
            source: nodeId,
            target: child.id,
            type: "smoothstep",
            label: "contains",
            style: {
              stroke: "#6b7280",
              strokeWidth: 2,
              strokeDasharray: "5,5",
            },
            labelStyle: {
              fill: "#9ca3af",
              fontSize: 10,
              fontWeight: 500,
            },
            labelBgStyle: {
              fill: "rgba(15, 23, 42, 0.9)",
              padding: "4px 6px",
              borderRadius: "3px",
            },
            markerEnd: {
              type: MarkerType.ArrowClosed,
              color: "#6b7280",
            },
            data: {
              parentNodeId: nodeId,
              connection_type: "contains",
            },
          }));
          
          setNodes(prevNodes => [...prevNodes, ...childNodes]);
          setEdges(prevEdges => [...prevEdges, ...childEdges]);
          setExpandedNodes(prev => new Set(prev).add(nodeId));
          
          // Update parent node data to show expanded state
          setNodes(prevNodes => prevNodes.map(n => 
            n.id === nodeId ? { ...n, data: { ...n.data, expanded: true } } : n
          ));
        }
      } catch (error) {
        console.error("Error fetching children:", error);
      }
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
      `<span><strong>${subscriptionName || "pipeline-customer-production"}</strong></span>`,
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
        
        // Security-based color coding for edges
        let edgeColor;
        let strokeWidth = 2;
        let strokeOpacity = 1.0;
        
        const connType = edge.data?.connection_type || "";
        const isFromInternet = String(edge.source) === "Internet";
        const wafProtected = edge.data?.waf_protected === true;
        
        let labelColor = "#e2e8f0"; // Default light text for dark backgrounds
        
        if (isFromInternet) {
          // Public Internet → Resource connections
          if (wafProtected) {
            edgeColor = "#f59e0b"; // Orange - WAF protected entry
            labelColor = "#fef3c7"; // Light yellow for high contrast on orange
            strokeWidth = 3;
          } else {
            edgeColor = "#ef4444"; // Red - Direct public exposure (HIGH RISK)
            labelColor = "#ffffff"; // White for maximum contrast on red
            strokeWidth = 3;
            strokeOpacity = 0.9;
          }
        } else if (connType === "public") {
          edgeColor = "#f97316"; // Orange - Cross-resource public connection
          labelColor = "#fef3c7"; // Light yellow for orange edges
          strokeWidth = 2.5;
        } else if (connType === "internal" || connType === "private") {
          edgeColor = "#10b981"; // Green - Secure internal connection
          labelColor = "#d1fae5"; // Light green for green edges
          strokeWidth = 2;
          strokeOpacity = 0.7;
        } else {
          // Default: use provider theme color for cross-tier connections
          edgeColor = themeFor(sourceNode?.data?.providerKey || targetNode?.data?.providerKey).border;
          labelColor = "#e2e8f0"; // Light gray for default edges
          strokeWidth = 2;
          strokeOpacity = 0.8;
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
      renderSummary(payload, payload.subscription_name || sub || "pipeline-customer-production");
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
