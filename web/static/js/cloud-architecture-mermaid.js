import {
  sanitizeMermaidSource,
  injectDiagramIconsIntoSvg,
} from "./diagram-shared.js?v=2";
import {
  enhancePlaceholderGlyphs,
  applyEmojiIconFallback,
} from "./diagram-base.js?v=2";
import { renderMermaidDiagram, postProcessSvg } from "./subscription-diagrams.js?v=2";
import {
  CONFIG,
  PROVIDER_THEMES,
  normalizeViewMode,
  viewModeLabel,
  themeFor,
  readJsonResponse,
  escapeHtml,
} from "./cloud-architecture-shared.js?v=2";

const MERMAID_STYLE_ID = "cloud-arch-mermaid-style";
let mermaidNodeDataById = new Map();
let mermaidClickHandler = null;
let currentMermaidSubscriptionId = "";

const mermaidViewEl = document.getElementById("cloud-arch-mermaid-view");
const mermaidRootEl = document.getElementById("cloud-arch-mermaid-root");
const summaryLineEl = document.getElementById("cloud-arch-summary-line");
const legendEl = document.getElementById("cloud-arch-provider-legend");
const formEl = document.getElementById("cloud-arch-form");
const subscriptionInput = document.getElementById("subscription-input");
const viewButtons = Array.from(document.querySelectorAll("[data-cloud-arch-view]"));

let activeViewMode = normalizeViewMode(CONFIG.initialViewMode || "mermaid");
const isFirefox = /firefox/i.test(navigator.userAgent || "");
let firefoxOverlayRaf = null;
let firefoxOverlayTimeout = null;
let firefoxOverlayResizeObserver = null;

function renderFirefoxIconOverlay(svgEl) {
  if (!isFirefox || !mermaidRootEl || !svgEl) return;

  let overlay = mermaidRootEl.querySelector(":scope > .cloud-arch-firefox-icon-overlay");
  if (!overlay) {
    overlay = document.createElement("div");
    overlay.className = "cloud-arch-firefox-icon-overlay";
    overlay.style.cssText = [
      "position:absolute",
      "inset:0",
      "pointer-events:none",
      "z-index:3",
    ].join(";");
    mermaidRootEl.style.position = "relative";
    mermaidRootEl.appendChild(overlay);
  }

  overlay.innerHTML = "";
  const svgRect = svgEl.getBoundingClientRect();

  svgEl.querySelectorAll("g.node img.ni").forEach((img) => {
    const src = img.getAttribute("src");
    if (!src) return;
    const rect = img.getBoundingClientRect();
    const nodeEl = img.closest("g.node");
    const labelEl = nodeEl?.querySelector(".nl");
    const labelRect = labelEl?.getBoundingClientRect();
    const labelText =
      (labelEl?.innerText || nodeEl?.querySelector(".nodeLabel")?.textContent || "").trim();

    const overlayImg = document.createElement("img");
    overlayImg.src = src;
    overlayImg.alt = "";
    overlayImg.setAttribute("aria-hidden", "true");
    overlayImg.style.cssText = [
      `width:${Math.max(0, rect.width)}px`,
      `height:${Math.max(0, rect.height)}px`,
      "object-fit:contain",
      "pointer-events:none",
      "user-select:none",
      "position:absolute",
      `left:${Math.max(0, rect.left - svgRect.left)}px`,
      `top:${Math.max(0, rect.top - svgRect.top)}px`,
    ].join(";");
    overlay.appendChild(overlayImg);

    if (labelText && labelRect) {
      const labelStyles = window.getComputedStyle(labelEl);
      const overlayLabel = document.createElement("div");
      overlayLabel.textContent = labelText;
      overlayLabel.style.cssText = [
        "position:absolute",
        `left:${Math.max(0, labelRect.left - svgRect.left)}px`,
        `top:${Math.max(0, labelRect.top - svgRect.top)}px`,
        `width:${Math.max(0, labelRect.width)}px`,
        `height:${Math.max(0, labelRect.height)}px`,
        `font-size:${labelStyles.fontSize || "11px"}`,
        `line-height:${labelStyles.lineHeight || "1.15"}`,
        `font-weight:${labelStyles.fontWeight || "500"}`,
        "font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif",
        "color:#e2e8f0",
        "text-shadow:0 1px 1px rgba(0,0,0,0.75)",
        "white-space:pre-wrap",
        "word-break:break-word",
        "display:flex",
        "align-items:center",
        "justify-content:center",
        "text-align:center",
        "pointer-events:none",
      ].join(";");
      overlay.appendChild(overlayLabel);
      labelEl.style.visibility = "hidden";
    }
    // Keep layout metrics intact for future refreshes.
    img.style.visibility = "hidden";
    img.style.opacity = "0";
  });
}

function refreshFirefoxOverlay() {
  if (!isFirefox || !mermaidRootEl) return;
  const svgEl = mermaidRootEl.querySelector("svg");
  if (!svgEl) return;
  renderFirefoxIconOverlay(svgEl);
}

function scheduleFirefoxOverlayRefresh() {
  if (!isFirefox) return;
  if (firefoxOverlayRaf) cancelAnimationFrame(firefoxOverlayRaf);
  if (firefoxOverlayTimeout) clearTimeout(firefoxOverlayTimeout);
  firefoxOverlayRaf = requestAnimationFrame(() => {
    refreshFirefoxOverlay();
    // One extra delayed pass after layout settles during zoom/fit.
    firefoxOverlayTimeout = setTimeout(() => refreshFirefoxOverlay(), 60);
  });
}

function bindFirefoxOverlaySync(svgEl) {
  if (!isFirefox || !svgEl) return;
  if (firefoxOverlayResizeObserver) {
    firefoxOverlayResizeObserver.disconnect();
    firefoxOverlayResizeObserver = null;
  }
  if (typeof ResizeObserver !== "undefined") {
    firefoxOverlayResizeObserver = new ResizeObserver(() => {
      scheduleFirefoxOverlayRefresh();
    });
    firefoxOverlayResizeObserver.observe(svgEl);
    firefoxOverlayResizeObserver.observe(mermaidRootEl);
  }
}

function applyMermaidCss(cssText) {
  let styleEl = document.getElementById(MERMAID_STYLE_ID);
  const css = String(cssText || "").trim();
  if (!css) {
    if (styleEl) {
      styleEl.remove();
    }
    return;
  }
  if (!styleEl) {
    styleEl = document.createElement("style");
    styleEl.id = MERMAID_STYLE_ID;
    document.head.appendChild(styleEl);
  }
  styleEl.textContent = css;
}

function normalizeMermaidNodeId(rawId) {
  return String(rawId || "")
    .replace(/^.*?flowchart-/, "")
    .replace(/^mermaid-\d+-/, "")
    .replace(/-\d+$/, "");
}

function attachMermaidDrilldownHandlers(svg) {
  if (!svg) return;

  svg.querySelectorAll("g.node[id]").forEach((el) => {
    const rawId = el.getAttribute("id") || "";
    const nodeId = normalizeMermaidNodeId(rawId);
    const nodeData = mermaidNodeDataById.get(nodeId);
    if (!nodeData || nodeData.summaryNode) return;
    el.classList.add("node-drillable");
    el.style.cursor = "pointer";
    el.setAttribute("title", `Click to explore ${nodeData.title || nodeId}`);
    el.setAttribute("tabindex", "0");
    el.addEventListener("click", (evt) => {
      evt.stopPropagation();
      evt.preventDefault();
      openNodePopup(nodeId, nodeData);
    });
    el.addEventListener("keydown", (evt) => {
      if (evt.key === "Enter") {
        openNodePopup(nodeId, nodeData);
      }
    });
  });
}

function ensureMermaidClickHandler(svg) {
  if (mermaidClickHandler || !svg) {
    return;
  }

  mermaidClickHandler = (event) => {
    const target = event.target instanceof Element ? event.target : null;
    let nodeGroup = null;

    if (event.composedPath) {
      for (const el of event.composedPath()) {
        if (el === svg) break;
        if (el?.tagName === "g" && el.classList && (el.classList.contains("node") || el.classList.contains("cluster")) && el.id) {
          nodeGroup = el;
          break;
        }
      }
    }

    if (!nodeGroup && target?.closest) {
      const candidate = target.closest("g.node[id], g.cluster[id]");
      if (candidate && svg.contains(candidate)) {
        nodeGroup = candidate;
      }
    }

    if (!nodeGroup) {
      let el = target;
      while (el && el !== svg) {
        if (el.tagName === "g" && el.classList && (el.classList.contains("node") || el.classList.contains("cluster")) && el.id) {
          nodeGroup = el;
          break;
        }
        el = el.parentElement || el.parentNode;
      }
    }

    if (!nodeGroup) return;

    const nodeId = normalizeMermaidNodeId(nodeGroup.getAttribute("id") || "");
    const nodeData = mermaidNodeDataById.get(nodeId);
    if (!nodeData || nodeData.summaryNode) return;
    openNodePopup(nodeId, nodeData);
  };
  svg.addEventListener("click", mermaidClickHandler);
}

function escapeMermaidText(value) {
  return String(value ?? "")
    .replace(/\\/g, "\\\\")
    .replace(/"/g, '\\"')
    .replace(/\r?\n/g, " ")
    .replace(/\|/g, "/")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function sanitizeMermaidId(value, fallback) {
  const raw = String(value ?? "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_]+/g, "_")
    .replace(/^_+|_+$/g, "");
  return raw || fallback;
}

function buildNodeLabel(nodeLabel, typeLabel, repoLabel) {
  const lines = [typeLabel, nodeLabel, repoLabel]
    .map((item) => String(item || "").trim())
    .filter(Boolean)
    .map((item) => escapeMermaidText(item));

  return lines.join("\\n");
}

function buildHierarchyContext(nodes) {
  const nodeById = new Map();
  const providerById = new Map();
  const parentById = new Map();
  const childrenByParent = new Map();

  for (const node of nodes) {
    const id = String(node?.id || "").trim();
    if (!id) continue;
    nodeById.set(id, node);
    providerById.set(id, String(node?.data?.providerKey || "unknown"));
  }

  for (const node of nodes) {
    const id = String(node?.id || "").trim();
    if (!id) continue;
    const parentId = String(node?.data?.parentNodeId || "").trim();
    if (!parentId || !nodeById.has(parentId)) continue;
    if ((providerById.get(parentId) || "unknown") !== (providerById.get(id) || "unknown")) continue;
    parentById.set(id, parentId);
    if (!childrenByParent.has(parentId)) {
      childrenByParent.set(parentId, []);
    }
    childrenByParent.get(parentId).push(id);
  }

  for (const childIds of childrenByParent.values()) {
    childIds.sort((a, b) => {
      const aNode = nodeById.get(a);
      const bNode = nodeById.get(b);
      const aLabel = String(aNode?.data?.label || aNode?.id || "");
      const bLabel = String(bNode?.data?.label || bNode?.id || "");
      return aLabel.localeCompare(bLabel);
    });
  }

  const roots = [];
  for (const id of nodeById.keys()) {
    if (!parentById.has(id)) {
      roots.push(id);
    }
  }

  const isDescendantOf = (nodeId, ancestorId) => {
    let current = parentById.get(String(nodeId || "").trim()) || "";
    const seen = new Set();
    while (current && !seen.has(current)) {
      if (current === ancestorId) return true;
      seen.add(current);
      current = parentById.get(current) || "";
    }
    return false;
  };

  const getDescendants = (ancestorId) => {
    const result = [];
    const queue = [...(childrenByParent.get(String(ancestorId || "").trim()) || [])];
    const seen = new Set();
    while (queue.length) {
      const current = queue.shift();
      if (!current || seen.has(current)) continue;
      seen.add(current);
      result.push(current);
      queue.push(...(childrenByParent.get(current) || []));
    }
    return result;
  };

  return {
    nodeById,
    providerById,
    parentById,
    childrenByParent,
    roots,
    getDescendants,
    isDescendantOf,
  };
}

const ARM_TO_ICON_CLASS = {
  "microsoft.network/applicationgateways": "azurerm_app_gateway",
  "microsoft.network/applicationgatewaybackendpools": "azurerm_app_gateway_backend_pool",
  "microsoft.network/applicationgatewaylisteners/http": "azurerm_app_gateway_listener_http",
  "microsoft.network/applicationgatewaylisteners/https": "azurerm_app_gateway_listener_https",
  "microsoft.network/frontdoors": "azurerm_front_door_and_cdn_profiles",
  "microsoft.cdn/profiles": "azurerm_cdn_profile",
  "microsoft.cdn/profiles/afdendpoints": "azurerm_front_door_and_cdn_profiles",
  "microsoft.cdn/profiles/cdndeliveryrules": "azurerm_front_door_and_cdn_profiles",
  "microsoft.compute/virtualmachinescalesets": "azurerm_vm_scale_sets",
  "microsoft.managedidentity/userassignedidentities": "azurerm_identity_governance",
  "microsoft.operationalinsights/workspaces": "azurerm_log_analytics",
  "microsoft.network/trafficmanagerprofiles": "azurerm_traffic_manager",
  "microsoft.network/azurefirewalls": "azurerm_firewall",
  "microsoft.network/firewallpolicies": "azurerm_firewall_policy",
  "microsoft.network/virtualnetworks": "azurerm_virtual_network",
  "microsoft.network/networksecuritygroups": "azurerm_network_security_group",
  "microsoft.network/routetables": "azurerm_route_table",
  "microsoft.network/publicipaddresses": "azurerm_public_ip",
  "microsoft.network/loadbalancers": "azurerm_lb",
  "microsoft.network/bastionhosts": "azurerm_bastion_host",
  "microsoft.apimanagement/service": "azurerm_apim",
  "microsoft.containerservice/managedclusters": "azurerm_aks",
  "microsoft.storage/storageaccounts": "azurerm_storage_account",
  "microsoft.keyvault/vaults": "azurerm_key_vault",
  "microsoft.sql/servers": "azurerm_sql_server",
  "microsoft.sql/servers/databases": "azurerm_sql_database",
  "microsoft.documentdb/databaseaccounts": "azurerm_cosmos_db",
  "microsoft.web/sites": "azurerm_app_service",
  "microsoft.web/functionapps": "azurerm_function_app",
  "microsoft.web/serverfarms": "azurerm_app_service_plan",
  "microsoft.web/certificates": "azurerm_app_service_certificate",
  "microsoft.certificateregistration/certificateorders": "azurerm_app_service_certificate_order",
  "microsoft.web/hostingenvironments": "azurerm_app_service_environment",
  "microsoft.cache/redis": "azurerm_redis",
  "microsoft.eventhub/namespaces": "azurerm_event_hub",
  "microsoft.servicebus/namespaces": "azurerm_service_bus",
  "microsoft.managedidentity/userassignedidentities": "azurerm_user_assigned_identity",
  "microsoft.compute/virtualmachinescalesets": "azurerm_virtual_machine_scale_set",
  "microsoft.compute/images": "azurerm_image",
  "microsoft.operationalinsights/workspaces": "azurerm_log_analytics_workspace",
  "microsoft.insights/actiongroups": "azurerm_monitor_action_group",
  "microsoft.insights/activitylogalerts": "azurerm_monitor_activity_log_alert",
  "microsoft.appconfiguration/configurationstores": "azurerm_app_configuration",
  "microsoft.insights/components": "azurerm_app_insights",
  "microsoft.insights/actiongroups": "azurerm_alerts",
  "microsoft.insights/activitylogalerts": "azurerm_activity_log",
  "microsoft.containerregistry/registries": "azurerm_container_registries",
  "microsoft.servicefabric/clusters": "azurerm_service_fabric_clusters",
  "microsoft.search/searchservices": "azurerm_search",
  "microsoft.certificateregistration/certificateorders": "azurerm_app_service_certificate",
};

function normalizeIconClass(resourceType, providerKey = "azure") {
  const rawType = String(resourceType || "").trim().toLowerCase();
  if (!rawType || rawType === "external_endpoint") {
    return "";
  }

  if (
    rawType.startsWith("azurerm_") ||
    rawType.startsWith("aws_") ||
    rawType.startsWith("google_") ||
    rawType.startsWith("kubernetes_") ||
    rawType.startsWith("oci_") ||
    rawType.startsWith("alicloud_")
  ) {
    return `icon-${rawType.replace(/_/g, "-")}`;
  }

  let normalized = rawType;
  if (ARM_TO_ICON_CLASS[rawType]) {
    normalized = ARM_TO_ICON_CLASS[rawType];
  } else if (rawType.startsWith("microsoft.")) {
    const parts = rawType.split("/");
    const leaf = (parts[parts.length - 1] || "").replace(/[^a-z0-9]+/g, "_");
    const singular = leaf.endsWith("ies")
      ? `${leaf.slice(0, -3)}y`
      : (leaf.endsWith("s") && !leaf.endsWith("ss") ? leaf.slice(0, -1) : leaf);
    normalized = `azurerm_${singular}`;
  } else if (providerKey === "azure") {
    normalized = rawType.replace(/[^a-z0-9_]+/g, "_");
  }

  return normalized ? `icon-${normalized.replace(/_/g, "-")}` : "";
}

function buildMermaidGraph(payload, subscriptionName) {
  const nodes = Array.isArray(payload?.nodes) ? payload.nodes : [];
  const edges = Array.isArray(payload?.edges) ? payload.edges : [];
  const nodeIdMap = new Map();
  const nodeClassAssignments = [];
  const providerOrder = Object.keys(PROVIDER_THEMES);
  const providerGroups = new Map();
  const hierarchy = buildHierarchyContext(nodes);
  let autoIndex = 0;

  for (const node of nodes) {
    const providerKey = node?.data?.providerKey || "unknown";
    if (!providerGroups.has(providerKey)) {
      providerGroups.set(providerKey, []);
    }
    providerGroups.get(providerKey).push(node);
  }

  const orderedProviders = [
    ...providerOrder,
    ...Array.from(providerGroups.keys()).filter((key) => !providerOrder.includes(key)).sort(),
  ];

  for (const providerKey of orderedProviders) {
    if (!providerGroups.has(providerKey)) continue;
    providerGroups.get(providerKey).sort((a, b) => {
      const aLabel = String(a?.data?.label || a?.id || "");
      const bLabel = String(b?.data?.label || b?.id || "");
      return aLabel.localeCompare(bLabel);
    });
  }

  const lines = ["flowchart LR"];

  function renderNode(node, indent = "    ") {
    const mermaidId = sanitizeMermaidId(node?.id, `node_${autoIndex++}`);
    nodeIdMap.set(String(node?.id), mermaidId);

    const title = node?.data?.label || node?.data?.providerLabel || node?.id || "Node";
    const typeLabel = node?.data?.typeLabel || "";
    const repoLabel = node?.data?.repoName || "";
    const nodeLabel = buildNodeLabel(title, typeLabel, repoLabel);

    lines.push(`${indent}${mermaidId}["${nodeLabel}"]`);

    const iconClass = String(node?.data?.iconClass || "").trim() || normalizeIconClass(node?.data?.resourceType || "", node?.data?.providerKey || "azure");
    if (iconClass) {
      const mermaidSafeIconClass = iconClass.replace(/-/g, "_");
      nodeClassAssignments.push(`  class ${mermaidId} ${mermaidSafeIconClass};`);
    }

    const children = hierarchy.childrenByParent.get(String(node?.id)) || [];
    if (children.length) {
      const nestedLabel = escapeMermaidText(
        node?.data?.isGroupNode
          ? `${title} (${children.length})`
          : `${title} nested resources`
      );
      const nestedId = `grp_${sanitizeMermaidId(node?.id, `node_${autoIndex++}`)}_nested`;
      lines.push(`${indent}subgraph ${nestedId}["${nestedLabel}"]`);
      for (const child of children) {
        renderNode(child, `${indent}  `);
      }
      lines.push(`${indent}end`);
    }
  }

  for (const providerKey of orderedProviders) {
    const bucket = providerGroups.get(providerKey);
    if (!bucket || bucket.length === 0) continue;
    const theme = themeFor(providerKey);
    const groupId = `grp_${sanitizeMermaidId(providerKey, "provider")}`;
    lines.push(`  subgraph ${groupId}["${escapeMermaidText(theme.label)}"]`);
    const rootNodes = bucket.filter((node) => {
      const parentId = node?.data?.parentNodeId ? String(node.data.parentNodeId) : "";
      if (!parentId) return true;
      const parent = hierarchy.nodeById.get(parentId);
      return !parent || (parent?.data?.providerKey || "unknown") !== providerKey;
    });
    for (const node of rootNodes) {
      renderNode(node, "    ");
    }
    lines.push("  end");
  }

  if (nodeClassAssignments.length) {
    lines.push("");
    lines.push(...nodeClassAssignments);
  }

  lines.push("");
  lines.push("  classDef cloudSummary fill:#111827,stroke:#94a3b8,stroke-width:2px,stroke-dasharray:4 3,color:#e2e8f0;");

  const seenEdges = new Set();
  for (const edge of edges) {
    const sourceId = nodeIdMap.get(String(edge?.source));
    const targetId = nodeIdMap.get(String(edge?.target));
    if (!sourceId || !targetId) continue;
    const rawLabel = String(edge?.label || "").trim();
    const label = rawLabel ? `|${escapeMermaidText(rawLabel)}|` : "";
    const edgeKey = `${sourceId}->${targetId}->${label}`;
    if (seenEdges.has(edgeKey)) continue;
    seenEdges.add(edgeKey);
    lines.push(`  ${sourceId} -->${label} ${targetId}`);
  }

  return lines.join("\n");
}

async function renderMermaidGraph(payload, subscriptionName) {
  if (!mermaidViewEl || !mermaidRootEl) {
    return;
  }

  const directDiagram = String(payload?.mermaid || "").trim();
  const mermaidSource = sanitizeMermaidSource(directDiagram || buildMermaidGraph(payload, subscriptionName));
  if (directDiagram) {
    mermaidNodeDataById = new Map(
      Object.entries(payload?.node_drilldown_map || {})
        .map(([id, data]) => [String(id || ""), data || null])
        .filter(([id, data]) => id && data)
    );
  } else {
    mermaidNodeDataById = new Map(
      (Array.isArray(payload?.nodes) ? payload.nodes : [])
        .map((node) => [String(node?.id || ""), node?.data || null])
        .filter(([id, data]) => id && data)
    );
  }
  applyMermaidCss(payload?.css_code || "");
  try {
    const svg = await renderMermaidDiagram({
      source: mermaidSource,
      rootEl: mermaidRootEl,
      onRendered: async (svgEl) => {
        postProcessSvg(svgEl);
        enhancePlaceholderGlyphs(svgEl);
        applyEmojiIconFallback(svgEl);
        attachMermaidDrilldownHandlers(svgEl);
        ensureMermaidClickHandler(svgEl);
        renderFirefoxIconOverlay(svgEl);
        bindFirefoxOverlaySync(svgEl);
        await injectDiagramIconsIntoSvg(svgEl, "all");
        scheduleFirefoxOverlayRefresh();
      },
    });
    return Boolean(svg);
  } catch (err) {
    console.error("[cloud-architecture] Mermaid render failed:", err);
    mermaidRootEl.innerHTML = `<pre style="color: var(--red); white-space: pre-wrap;">${escapeHtml(err.message || String(err))}</pre>`;
    return false;
  }
}

function syncViewButtons() {
  for (const button of viewButtons) {
    const mode = normalizeViewMode(button.dataset.cloudArchView || "");
    const isActive = mode === activeViewMode;
    button.classList.toggle("is-active", isActive);
    button.setAttribute("aria-pressed", isActive ? "true" : "false");
  }
}

function resetModalRequestState() {
  window.__triageCloudArchModalState = {
    inProgress: false,
    resourceId: null,
    resourceData: null,
  };
}

function startModalRequest() {
  window.__triageCloudArchModalState = window.__triageCloudArchModalState || {};
  window.__triageCloudArchModalState.inProgress = true;
}

function closeModal() {
  const overlay = document.getElementById("cloud-arch-modal-overlay");
  if (overlay) {
    overlay.hidden = true;
  }
  resetModalRequestState();
}

function openNodePopup(resourceId, nodeData) {
  openModal(resourceId, nodeData);
}

function openModal(resourceId, nodeData, lookup = {}) {
  startModalRequest();
  const overlay = document.getElementById("cloud-arch-modal-overlay");
  if (!overlay) {
    console.error("Modal overlay not found");
    return;
  }
  overlay.hidden = false;
  window.__triageCloudArchModalState.resourceId = resourceId;
  window.__triageCloudArchModalState.resourceData = nodeData;
}

function renderSummary(payload, subscriptionName, viewMode) {
  const providers = payload?.summary?.provider_counts || [];
  const resourceCount = payload?.summary?.resource_count ?? 0;
  const displayedCount = payload?.summary?.displayed_resource_count ?? resourceCount;
  const omittedCount = payload?.summary?.omitted_resource_count ?? 0;
  const connectionCount = payload?.summary?.connection_count ?? 0;
  const modeLabel = viewModeLabel(viewMode);
  summaryLineEl.innerHTML = [
    `<span><strong>${resourceCount}</strong> resources</span>`,
    omittedCount > 0 ? `<span><strong>${displayedCount}</strong> shown</span>` : null,
    `<span><strong>${connectionCount}</strong> connections</span>`,
    payload?.summary?.layout_mode ? `<span><strong>${modeLabel}</strong> mode</span>` : null,
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
  
  const connectionLegendEl = document.getElementById("cloud-arch-connection-legend");
  if (connectionLegendEl && connectionCount > 0) {
    connectionLegendEl.style.display = "block";
  }
}

async function loadMermaidView(subscriptionName) {
  if (!mermaidViewEl) {
    return false;
  }

  if (activeViewMode !== "mermaid") {
    mermaidViewEl.hidden = true;
    return true;
  }

  currentMermaidSubscriptionId = subscriptionName;
  mermaidViewEl.hidden = false;

  try {
    const resp = await fetch(
      `/api/cloud/architecture?sub=${encodeURIComponent(subscriptionName)}&view=mermaid`
    );
    if (!resp.ok) {
      throw new Error(`HTTP ${resp.status}`);
    }

    const payload = await readJsonResponse(resp);
    if (!payload) {
      return false;
    }

    const isEmpty = (!payload?.nodes || payload.nodes.length === 0) && !String(payload?.mermaid || "").trim();

    if (isEmpty) {
      mermaidRootEl.innerHTML = "";
    } else {
      await renderMermaidGraph(payload, subscriptionName);
    }

    renderSummary(payload, subscriptionName, activeViewMode);
    return !isEmpty;
  } catch (err) {
    console.error("[cloud-architecture] Mermaid load failed:", err);
    mermaidRootEl.innerHTML = `<pre style="color: var(--red); white-space: pre-wrap;">${escapeHtml(err.message || String(err))}</pre>`;
    return false;
  }
}

window.__triageCloudArchLoadMermaid = loadMermaidView;

if (formEl) {
  formEl.addEventListener("submit", (event) => {
    event.preventDefault();
    const subscription = (subscriptionInput.value || "").trim();
    if (activeViewMode === "mermaid") {
      loadMermaidView(subscription);
    }
  });
}

for (const button of viewButtons) {
  button.addEventListener("click", () => {
    const mode = normalizeViewMode(button.dataset.cloudArchView || "");
    if (!mode) return;
    
    if (mode === "mermaid" && mode !== activeViewMode) {
      activeViewMode = mode;
      syncViewButtons();
      loadMermaidView((subscriptionInput.value || "").trim());
    } else if (mode === "mermaid") {
      syncViewButtons();
    }
  });
}

// ── Drag-to-pan for Mermaid scroll area ──────────────────────────────────────
(function () {
  const mermaidScroll = document.getElementById("cloud-arch-mermaid-scroll");
  if (!mermaidScroll) return;
  let panning = false, panX = 0, panY = 0, sl0 = 0, st0 = 0;
  mermaidScroll.addEventListener("mousedown", (e) => {
    if (e.target.closest("a,button")) return;
    panning = true;
    panX = e.pageX; panY = e.pageY;
    sl0 = mermaidScroll.scrollLeft; st0 = mermaidScroll.scrollTop;
  });
  document.addEventListener("mousemove", (e) => {
    if (!panning) return;
    mermaidScroll.scrollLeft = sl0 - (e.pageX - panX);
    mermaidScroll.scrollTop  = st0 - (e.pageY - panY);
  });
  document.addEventListener("mouseup", () => { panning = false; });
  mermaidScroll.addEventListener("wheel", (e) => {
    if (e.ctrlKey || e.metaKey) {
      e.preventDefault();
      const container = document.getElementById("cloud-arch-mermaid-root");
      if (container && window.applyDiagramScale) {
        const current = parseFloat(container.dataset.diagramScale || "1") || 1;
        window.applyDiagramScale(container, current * (e.deltaY > 0 ? 0.9 : 1.1));
        scheduleFirefoxOverlayRefresh();
      }
    }
  }, { passive: false });
})();

if (isFirefox) {
  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!target || !target.closest) return;
    if (
      target.closest('[data-diagram-zoom-in="cloud-arch-mermaid-root"]') ||
      target.closest('[data-diagram-zoom-out="cloud-arch-mermaid-root"]') ||
      target.closest('[data-diagram-fit="cloud-arch-mermaid-root"]')
    ) {
      setTimeout(() => scheduleFirefoxOverlayRefresh(), 0);
      setTimeout(() => scheduleFirefoxOverlayRefresh(), 80);
    }
  });
}

if (mermaidViewEl) {
  mermaidViewEl.hidden = activeViewMode !== "mermaid";
}

syncViewButtons();
