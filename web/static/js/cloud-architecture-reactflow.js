import {
  sanitizeMermaidSource,
  stampSvgDimensions,
} from "./diagram-shared.js";
import {
  patchForeignObjectLabels,
  enhancePlaceholderGlyphs,
  applyEmojiIconFallback,
} from "./diagram-base.js";
import { renderMermaidDiagram } from "./subscription-diagrams.js";

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
const mermaidViewEl = document.getElementById("cloud-arch-mermaid-view");
const mermaidRootEl = document.getElementById("cloud-arch-mermaid-root");
const viewButtons = Array.from(document.querySelectorAll("[data-cloud-arch-view]"));
const INITIAL_VIEW_MODE = (CONFIG.initialViewMode || "mermaid").toLowerCase();
const MERMAID_STYLE_ID = "cloud-arch-mermaid-style";
let mermaidNodeDataById = new Map();
let mermaidClickHandler = null;
let currentMermaidSubscriptionId = "";

function normalizeViewMode(value) {
  const mode = (value || "").trim().toLowerCase();
  if (mode === "reactflow" || mode === "full") {
    return "reactflow";
  }
  return "mermaid";
}

function viewModeLabel(mode) {
  return normalizeViewMode(mode) === "reactflow" ? "React Flow" : "Mermaid";
}

let activeViewMode = normalizeViewMode(INITIAL_VIEW_MODE);
if (rootEl) {
  rootEl.hidden = activeViewMode === "mermaid";
}
if (mermaidViewEl) {
  mermaidViewEl.hidden = activeViewMode !== "mermaid";
}

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

async function readJsonResponse(resp) {
  const contentType = (resp.headers.get("content-type") || "").toLowerCase();
  const bodyText = await resp.text();

  if (!bodyText) {
    return null;
  }

  if (contentType.includes("application/json") || bodyText.trim().startsWith("{") || bodyText.trim().startsWith("[")) {
    try {
      return JSON.parse(bodyText);
    } catch (err) {
      throw new Error(`Invalid JSON response: ${err.message}`);
    }
  }

  throw new Error(bodyText.trim().slice(0, 300) || `Unexpected ${resp.status} response`);
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
  "microsoft.cdn/profiles/afdendpoints": "azurerm_cdn_frontdoor_endpoint",
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
  "microsoft.containerregistry/registries": "azurerm_container_registries",
  "microsoft.servicefabric/clusters": "azurerm_service_fabric_clusters",
  "microsoft.search/searchservices": "azurerm_search",
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

  const lines = ["flowchart TB"];
  const rootLabel = escapeMermaidText(subscriptionName || "Cloud Architecture");
  lines.push(`  subgraph ARCH["${rootLabel}"]`);

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
      nodeClassAssignments.push(`    class ${mermaidId} ${mermaidSafeIconClass};`);
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
    lines.push(`    subgraph ${groupId}["${escapeMermaidText(theme.label)}"]`);
    const rootNodes = bucket.filter((node) => {
      const parentId = node?.data?.parentNodeId ? String(node.data.parentNodeId) : "";
      if (!parentId) return true;
      const parent = hierarchy.nodeById.get(parentId);
      return !parent || (parent?.data?.providerKey || "unknown") !== providerKey;
    });
    for (const node of rootNodes) {
      renderNode(node, "      ");
    }
    lines.push("    end");
  }

  if (nodeClassAssignments.length) {
    lines.push("");
    lines.push(...nodeClassAssignments);
  }

  lines.push("");
  lines.push("    classDef cloudSummary fill:#111827,stroke:#94a3b8,stroke-width:2px,stroke-dasharray:4 3,color:#e2e8f0;");

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
    lines.push(`    ${sourceId} -->${label} ${targetId}`);
  }

  lines.push("  end");
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
        stampSvgDimensions(svgEl);
        patchForeignObjectLabels(svgEl);
        enhancePlaceholderGlyphs(svgEl);
        applyEmojiIconFallback(svgEl);
        attachMermaidDrilldownHandlers(svgEl);
        ensureMermaidClickHandler(svgEl);
        if (window.MermaidIconInjector) {
          const iconDataUrl = "/api/icon-mappings?provider=all";
          [0, 250, 700].forEach((delay) => {
            setTimeout(() => window.MermaidIconInjector.processAllDiagrams({ iconDataUrl }), delay);
          });
        }
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
  const childCount = Number(data.childrenCount || 0);
  const hasChildren = Boolean(data.hasChildren || isGroupNode || childCount > 0);
  
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
      title: hasChildren ? `${data.resourceType || data.typeLabel || data.label} (${childCount} children)` : (data.resourceType || data.typeLabel || data.label),
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
      hasChildren ? h(
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

function layoutReactFlowNodes(rawNodes) {
  const nodes = rawNodes.map((node) => ({
    ...node,
    position: {
      x: Number(node?.position?.x || 0),
      y: Number(node?.position?.y || 0),
    },
  }));
  const hierarchy = buildHierarchyContext(nodes);
  const nodeById = new Map(nodes.map((node) => [String(node?.id || ""), node]));
  const basePositionById = new Map();
  const sizeById = new Map();
  const boundsById = new Map();
  const originById = new Map();
  const orderedNodes = [];
  const padding = 28;

  for (const node of nodes) {
    const id = String(node?.id || "").trim();
    if (!id) continue;
    basePositionById.set(id, {
      x: Number(node?.position?.x || 0),
      y: Number(node?.position?.y || 0),
    });
    sizeById.set(id, {
      width: Number(node?.style?.width || 340),
      height: Number(node?.style?.height || node?.style?.minHeight || 132),
    });
  }

  const rectFromNode = (id, position) => {
    const size = sizeById.get(id) || { width: 340, height: 132 };
    return {
      x: position.x,
      y: position.y,
      width: size.width,
      height: size.height,
    };
  };

  const unionRects = (a, b) => {
    if (!a) return b;
    if (!b) return a;
    const x1 = Math.min(a.x, b.x);
    const y1 = Math.min(a.y, b.y);
    const x2 = Math.max(a.x + a.width, b.x + b.width);
    const y2 = Math.max(a.y + a.height, b.y + b.height);
    return {
      x: x1,
      y: y1,
      width: x2 - x1,
      height: y2 - y1,
    };
  };

  const measure = (id, stack = new Set()) => {
    if (boundsById.has(id)) {
      return boundsById.get(id);
    }
    if (stack.has(id)) {
      const basePos = basePositionById.get(id) || { x: 0, y: 0 };
      const fallback = rectFromNode(id, basePos);
      boundsById.set(id, fallback);
      return fallback;
    }

    stack.add(id);
    const basePos = basePositionById.get(id) || { x: 0, y: 0 };
    let bounds = rectFromNode(id, basePos);
    for (const childId of hierarchy.childrenByParent.get(id) || []) {
      bounds = unionRects(bounds, measure(childId, stack));
    }
    stack.delete(id);
    boundsById.set(id, bounds);
    return bounds;
  };

  const place = (id, parentOrigin = { x: 0, y: 0 }, stack = new Set()) => {
    if (originById.has(id) || stack.has(id)) {
      return;
    }
    const node = nodeById.get(id);
    if (!node) return;

    stack.add(id);
    const bounds = measure(id);
    const childIds = hierarchy.childrenByParent.get(id) || [];
    const hasChildren = childIds.length > 0;
    const basePos = basePositionById.get(id) || { x: 0, y: 0 };
    const origin = hasChildren
      ? { x: bounds.x - padding, y: bounds.y - padding }
      : basePos;
    originById.set(id, origin);

    const parentId = hierarchy.parentById.get(id) || "";
    const inheritedOrigin = parentId ? (originById.get(parentId) || basePositionById.get(parentId) || { x: 0, y: 0 }) : parentOrigin;
    const localPosition = {
      x: Math.round(origin.x - inheritedOrigin.x),
      y: Math.round(origin.y - inheritedOrigin.y),
    };

    const size = sizeById.get(id) || { width: 340, height: 132 };
    const width = hasChildren ? Math.max(size.width, Math.ceil(bounds.width + padding * 2)) : size.width;
    const height = hasChildren ? Math.max(size.height, Math.ceil(bounds.height + padding * 2)) : size.height;

    node.position = localPosition;
    node.parentNode = parentId || undefined;
    node.extent = parentId ? "parent" : undefined;
    node.data = {
      ...(node.data || {}),
      parentNodeId: parentId || node.data?.parentNodeId || null,
      childrenCount: childIds.length,
      hasChildren,
    };
    node.style = {
      ...(node.style || {}),
      width: Math.round(width),
      minHeight: Math.round(height),
      zIndex: hasChildren ? 0 : 1,
    };

    orderedNodes.push(node);
    for (const childId of childIds) {
      place(childId, origin, stack);
    }
    stack.delete(id);
  };

  for (const rootId of hierarchy.roots) {
    place(rootId);
  }
  for (const node of nodes) {
    const id = String(node?.id || "").trim();
    if (id && !originById.has(id)) {
      place(id);
    }
  }

  return orderedNodes.length ? orderedNodes : nodes;
}

function applyHierarchyVisibility(nodes, edges, expandedNodes) {
  const nodeLookup = new Map(nodes.map((node) => [String(node?.id || ""), node]));
  const expandedSet = expandedNodes instanceof Set ? expandedNodes : new Set(expandedNodes || []);

  const isNodeVisible = (nodeId) => {
    const id = String(nodeId || "").trim();
    if (!id || id === "Internet") return true;
    let current = nodeLookup.get(id);
    const seen = new Set();
    while (current) {
      const parentId = String(current.parentNode || current.data?.parentNodeId || "").trim();
      if (!parentId) return true;
      if (seen.has(parentId)) return true;
      if (!expandedSet.has(parentId)) return false;
      seen.add(parentId);
      current = nodeLookup.get(parentId);
    }
    return true;
  };

  const nextNodes = nodes.map((node) => ({
    ...node,
    hidden: !isNodeVisible(node.id),
    data: {
      ...(node.data || {}),
      expanded: expandedSet.has(String(node.id || "")),
    },
  }));

  const nextEdges = edges.map((edge) => ({
    ...edge,
    hidden: !isNodeVisible(edge.source) || !isNodeVisible(edge.target),
  }));

  return { nodes: nextNodes, edges: nextEdges };
}

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
    .then(async (resp) => {
      const data = await readJsonResponse(resp);
      if (!resp.ok) {
        throw new Error(data?.error || `Request failed with status ${resp.status}`);
      }
      if (data?.error) {
        throw new Error(data.error);
      }
      renderModalContent(data);
    })
    .catch(err => {
      modalBody.innerHTML = `<div class="cloud-arch-modal-empty">❌ Error loading details: ${escapeHtml(err.message)}</div>`;
    });
}

function openNodePopup(resourceId, nodeData) {
  if (nodeData?.resources?.length && currentMermaidSubscriptionId) {
    openDrilldownModal(nodeData, currentMermaidSubscriptionId);
    return;
  }
  openModal(resourceId, nodeData);
}

function openDrilldownModal(entry, subId) {
  if (!modalOverlay || !subId) return;

  modalOverlay.hidden = false;
  modalTitle.textContent = "Loading...";
  modalSubtitle.textContent = "";
  modalBody.innerHTML = '<div class="cloud-arch-modal-loading">Loading drilldown data...</div>';

  const theme = themeFor(entry.providerKey);
  modalIcon.style.background = theme.background;
  modalIcon.style.color = theme.border;
  modalIcon.textContent = "☁";

  const url = new URL(`/api/subscriptions/${encodeURIComponent(subId)}/drilldown`, window.location.origin);
  fetch(url.toString(), {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ arm_type: entry.arm_type, resources: entry.resources }),
  })
    .then(async (resp) => {
      const data = await readJsonResponse(resp);
      if (!resp.ok) {
        throw new Error(data?.error || `Request failed with status ${resp.status}`);
      }
      if (data?.error) {
        throw new Error(data.error);
      }
      renderModalContent(data);
    })
    .catch((err) => {
      modalBody.innerHTML = `<div class="cloud-arch-modal-empty">❌ Error loading drilldown: ${escapeHtml(err.message)}</div>`;
    });
}

function renderModalContent(data) {
  modalTitle.textContent = data.title || data.name || "Details";
  modalSubtitle.textContent = data.type_label ? `${data.type_label}${data.resource_group ? " • " + data.resource_group : ""}` : (data.resource_group || "");

  if ((data.view_type === "table" || data.view_type === "tree_table") && Array.isArray(data.columns)) {
    const rows = Array.isArray(data.rows) ? data.rows : [];
    if (!rows.length) {
      modalBody.innerHTML = `<div class="cloud-arch-modal-empty">${escapeHtml(data.empty_message || "No data available.")}</div>`;
      return;
    }

    const filterWrap = document.createElement("div");
    filterWrap.style.cssText = "margin-bottom:10px;";
    const filterInput = document.createElement("input");
    filterInput.type = "text";
    filterInput.placeholder = "🔎 Filter…";
    filterInput.style.cssText = "width:100%;box-sizing:border-box;padding:6px 10px;background:#1e293b;border:1px solid #334155;border-radius:6px;color:#e2e8f0;font-size:0.85rem;outline:none;";
    filterWrap.appendChild(filterInput);

    const tableWrap = document.createElement("div");
    tableWrap.style.cssText = "overflow:auto;flex:1;border-radius:6px;border:1px solid #1e293b;";
    const table = document.createElement("table");
    table.style.cssText = "width:100%;border-collapse:collapse;font-size:0.82rem;";

    const thead = document.createElement("thead");
    const hRow = document.createElement("tr");
    (data.columns || []).forEach((col) => {
      const th = document.createElement("th");
      th.textContent = col;
      th.style.cssText = "padding:8px 12px;text-align:left;background:#0f172a;color:#94a3b8;font-weight:600;font-size:0.78rem;text-transform:uppercase;letter-spacing:.04em;border-bottom:1px solid #1e293b;white-space:nowrap;";
      hRow.appendChild(th);
    });
    thead.appendChild(hRow);
    table.appendChild(thead);

    const tbody = document.createElement("tbody");
    const allRows = [];
    rows.forEach((row, ri) => {
      const tr = document.createElement("tr");
      tr.style.cssText = `border-bottom:1px solid #1e293b;background:${ri % 2 === 0 ? "transparent" : "rgba(255,255,255,0.02)"};transition:background .15s;`;
      tr.addEventListener("mouseenter", () => { tr.style.background = "rgba(96,165,250,0.07)"; });
      tr.addEventListener("mouseleave", () => { tr.style.background = ri % 2 === 0 ? "transparent" : "rgba(255,255,255,0.02)"; });
      row.forEach((cell) => {
        const td = document.createElement("td");
        td.style.cssText = "padding:7px 12px;color:var(--text-primary,#e5e7eb);vertical-align:middle;";
        const val = cell == null ? "—" : String(cell);
        if (val === "—") {
          td.textContent = val;
          td.style.color = "#4b5563";
        } else if (cell && typeof cell === "object" && cell.href && cell.label) {
          const link = document.createElement("a");
          link.href = String(cell.href);
          link.textContent = String(cell.label);
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          if (cell.title) link.title = String(cell.title);
          link.style.cssText = "color:#60a5fa;text-decoration:underline;word-break:break-all;";
          td.appendChild(link);
        } else if (cell && typeof cell === "object" && cell.label) {
          td.innerHTML = `<span style="${cell.style || ""}">${escapeHtml(cell.label)}</span>`;
        } else {
          td.textContent = val;
        }
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
      allRows.push({ tr, searchText: row.map((c) => (c && typeof c === "object" ? c.label : c) || "").join(" ").toLowerCase() });
    });
    table.appendChild(tbody);
    tableWrap.appendChild(table);
    modalBody.replaceChildren(filterWrap, tableWrap);
    filterInput.addEventListener("input", () => {
      const q = filterInput.value.toLowerCase();
      allRows.forEach(({ tr, searchText }) => {
        tr.style.display = searchText.includes(q) ? "" : "none";
      });
    });
    return;
  }

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
    if (sec.waf_enabled !== undefined && !isBastionResource(data)) {
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

function isBastionResource(data) {
  const haystack = [
    data?.id,
    data?.name,
    data?.type,
    data?.typeLabel,
    data?.resourceType,
    data?.label,
  ]
    .map((value) => String(value || "").toLowerCase())
    .join(" ");
  return haystack.includes("bastion");
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
      openNodePopup(node.id, node.data);
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
    const nextExpanded = new Set(expandedNodes);
    if (nextExpanded.has(nodeId)) {
      nextExpanded.delete(nodeId);
    } else {
      nextExpanded.add(nodeId);
    }

    const visibility = applyHierarchyVisibility(nodes, edges, nextExpanded);
    setNodes(visibility.nodes);
    setEdges(visibility.edges);
    setExpandedNodes(nextExpanded);
  }, [expandedNodes, nodes, edges]);
  
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

  const renderSummary = useCallback((payload, subscriptionName, viewMode) => {
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
    const mode = normalizeViewMode(viewMode || activeViewMode || "mermaid");
    activeViewMode = mode;
    syncViewButtons();
    if (sub) {
      url.searchParams.set("sub", sub);
    }
    url.searchParams.set("view", mode);

    try {
      const resp = await fetch(url.toString(), { headers: { Accept: "application/json" } });
      const payload = await readJsonResponse(resp);
      if (!resp.ok) {
        throw new Error(payload?.error || `Request failed with status ${resp.status}`);
      }

      const hasDirectMermaid = mode === "mermaid" && String(payload?.mermaid || "").trim().length > 0;
      currentMermaidSubscriptionId = String(payload?.subscription_id || sub || currentMermaidSubscriptionId || "");
      const nodeMap = new Map();
      for (const node of payload.nodes || []) {
        nodeMap.set(String(node.id), node);
      }

      const rawPreparedNodes = (payload.nodes || []).map((node, index) => ({
        ...node,
        position: node.position || { x: (index % 4) * 420, y: Math.floor(index / 4) * 180 },
        data: {
          ...node.data,
          nodeId: node.id,
          index,
          color: themeFor(node.data?.providerKey).border,
          childrenCount: Number(node.data?.childrenCount || 0),
        },
        style: {
          ...(node.style || {}),
          width: node.style?.width || 340,
          minHeight: node.style?.minHeight || 132,
        },
      }));

      const preparedNodes = layoutReactFlowNodes(rawPreparedNodes);
      const initialExpandedNodes = new Set();

      const preparedEdges = (payload.edges || []).filter((edge) => {
        const connType = String(edge?.data?.connection_type || "").toLowerCase();
        return connType !== "contains";
      }).map((edge) => {
        const sourceNode = nodeMap.get(String(edge.source));
        const targetNode = nodeMap.get(String(edge.target));
        const connType = String(edge.data?.connection_type || "").toLowerCase();
        
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
          type: connType === "public" || connType === "waf_protection" || connType === "firewall_ingress" || connType === "firewall" ? "straight" : "smoothstep",
          sourcePosition: edge.sourcePosition || Position.Right,
          targetPosition: edge.targetPosition || Position.Left,
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

      const visibility = applyHierarchyVisibility(preparedNodes, deduplicatedEdges, initialExpandedNodes);
      setNodes(visibility.nodes);
      setEdges(visibility.edges);
      setExpandedNodes(initialExpandedNodes);
      setGraphKey(`${payload.subscription_id || sub || "latest"}:${payload?.summary?.layout_mode || mode}:${preparedNodes.length}:${deduplicatedEdges.length}`);
      renderSummary(payload, payload.subscription_name || sub || "subscription-production", mode);
      const isEmpty = preparedNodes.length === 0 && !hasDirectMermaid;
      emptyEl.hidden = !isEmpty;
      if (mermaidViewEl) {
        mermaidViewEl.hidden = isEmpty || mode !== "mermaid";
      }
      rootEl.hidden = isEmpty || mode === "mermaid";
      if (mermaidRootEl && mode !== "mermaid") {
        mermaidRootEl.innerHTML = "";
      }
      if (!isEmpty) {
        const query = new URLSearchParams();
        if (payload.subscription_id || sub) query.set("sub", payload.subscription_name || sub);
        if (mode) query.set("view", mode);
        window.history.replaceState(null, "", `${window.location.pathname}${query.toString() ? `?${query}` : ""}`);
        if (mode === "mermaid") {
          let rendered = false;
          if (payload.subscription_id) {
            try {
              const diagResp = await fetch(`/api/subscriptions/${encodeURIComponent(payload.subscription_id)}/diagram`, { headers: { Accept: "application/json" } });
              const diagPayload = await readJsonResponse(diagResp);
              if (diagResp.ok && diagPayload?.ingress_diagram) {
                rendered = await renderMermaidDiagram({
                  source: sanitizeMermaidSource(String(diagPayload.ingress_diagram?.views?.connectivity?.mermaid || diagPayload.ingress_diagram?.mermaid || "")),
                  rootEl: mermaidRootEl,
                  onRendered: async (svgEl) => {
                    mermaidNodeDataById = new Map(
                      Object.entries(diagPayload.ingress_diagram?.views?.connectivity?.node_drilldown_map || diagPayload.ingress_diagram?.node_drilldown_map || {})
                        .map(([id, data]) => [String(id || ""), data || null])
                        .filter(([id, data]) => id && data)
                    );
                    currentMermaidSubscriptionId = String(diagPayload.ingress_diagram?.subscription_id || payload.subscription_id || currentMermaidSubscriptionId || "");
                    applyMermaidCss(diagPayload.ingress_diagram?.views?.connectivity?.css_code || diagPayload.ingress_diagram?.css_code || "");
                    stampSvgDimensions(svgEl);
                    patchForeignObjectLabels(svgEl);
                    enhancePlaceholderGlyphs(svgEl);
                    applyEmojiIconFallback(svgEl);
                    attachMermaidDrilldownHandlers(svgEl);
                    ensureMermaidClickHandler(svgEl);
                    if (window.MermaidIconInjector && diagPayload.ingress_diagram?.views?.connectivity?.icon_map) {
                      await window.MermaidIconInjector.injectIcons(svgEl, diagPayload.ingress_diagram.views.connectivity.icon_map);
                    }
                  },
                });
              }
            } catch (_) {
              rendered = false;
            }
          }
          if (!rendered) {
            await renderMermaidGraph(payload, payload.subscription_name || sub || "subscription-production");
          }
        }
      }
    } catch (err) {
      setNodes([]);
      setEdges([]);
      setGraphKey(`error:${Date.now()}`);
      emptyEl.hidden = true;
      rootEl.hidden = true;
      if (mermaidViewEl) {
        mermaidViewEl.hidden = true;
      }
      if (mermaidRootEl) {
        mermaidRootEl.innerHTML = "";
      }
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
        panOnScroll: false,
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
    const mode = normalizeViewMode(button.dataset.cloudArchView || "");
    if (!mode || mode === activeViewMode) {
      return;
    }
    activeViewMode = mode;
    syncViewButtons();
    if (typeof window.__triageCloudArchLoad === "function") {
      window.__triageCloudArchLoad((subscriptionInput.value || "").trim(), activeViewMode);
    }
  });
}

mount();
syncViewButtons();
