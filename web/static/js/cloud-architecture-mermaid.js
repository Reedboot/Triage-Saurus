import {
  sanitizeMermaidSource,
  injectDiagramIconsIntoSvg,
} from "./diagram-shared.js?v=3";
import {
  enhancePlaceholderGlyphs,
  applyEmojiIconFallback,
} from "./diagram-base.js?v=3";
import { renderMermaidDiagram, postProcessSvg } from "./subscription-diagrams.js?v=3";
import { autoFitDiagram } from "./diagram-base.js?v=3";
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
const modalOverlay = document.getElementById("cloud-arch-modal-overlay");
const modalTitle = document.getElementById("modal-title");
const modalSubtitle = document.getElementById("modal-subtitle");
const modalBody = document.getElementById("modal-body");
const modalIcon = document.getElementById("modal-icon");

let activeViewMode = normalizeViewMode(CONFIG.initialViewMode || "mermaid");
const isFirefox = /firefox/i.test(navigator.userAgent || "");
let firefoxOverlayRaf = null;
let firefoxOverlayTimeout = null;
let firefoxOverlayResizeObserver = null;
let firefoxIconMapPromise = null;
let activeModalRequest = null;

async function loadFirefoxIconMap() {
  if (!firefoxIconMapPromise) {
    firefoxIconMapPromise = fetch("/api/icon-mappings?provider=all", {
      headers: { Accept: "application/json" },
    })
      .then((resp) => (resp.ok ? resp.json() : {}))
      .catch(() => ({}));
  }
  return firefoxIconMapPromise;
}

async function renderFirefoxIconOverlay(svgEl) {
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
  const diagramScale = Math.max(
    0.01,
    parseFloat(mermaidRootEl.dataset.diagramScale || "1") || 1
  );
  const toLocalRect = (rect) => {
    if (!rect) return null;
    return {
      left: (rect.left - svgRect.left) / diagramScale,
      top: (rect.top - svgRect.top) / diagramScale,
      width: rect.width / diagramScale,
      height: rect.height / diagramScale,
    };
  };
  const iconMap = await loadFirefoxIconMap();

  svgEl.querySelectorAll("g.node").forEach((nodeEl) => {
    const img = nodeEl.querySelector("img.ni");
    let src = img?.getAttribute("src") || "";
    const rawNodeId = nodeEl.getAttribute("id") || "";
    const nodeId = normalizeMermaidNodeId(rawNodeId);
    const nodeData = mermaidNodeDataById.get(nodeId) || {};

    if (!src) {
      const fromDataPath = String(nodeData.iconPath || nodeData.icon_path || "").trim();
      if (fromDataPath) {
        src = fromDataPath;
      } else {
        const iconClass =
          String(nodeData.iconClass || nodeData.icon_class || "").trim() ||
          normalizeIconClass(
            nodeData.resourceType || nodeData.resource_type || nodeData.arm_type || nodeData.type || "",
            nodeData.providerKey || "azure"
          );
        const iconKey = iconClass.startsWith("icon-")
          ? iconClass.slice(5).replace(/-/g, "_")
          : "";
        if (iconKey && iconMap && typeof iconMap === "object") {
          src = String(iconMap[iconKey] || "").trim();
        }
      }
    }
    if (!src) return;
    const labelEl = nodeEl?.querySelector(".nl");
    const labelRect = labelEl?.getBoundingClientRect();
    const labelText =
      (labelEl?.innerText || nodeEl?.querySelector(".nodeLabel")?.textContent || "").trim();

    const overlayImg = document.createElement("img");
    overlayImg.src = src;
    overlayImg.alt = "";
    overlayImg.setAttribute("aria-hidden", "true");
    overlayImg.dataset.nodeId = nodeId;
    const nodeRect = toLocalRect(nodeEl.getBoundingClientRect()) || {
      left: 0,
      top: 0,
      width: 120,
      height: 80,
    };
    const fallbackSize = Math.max(
      18,
      Math.min(42, Math.round((nodeRect.width || 120) * 0.22))
    );
    const iconWidth = Math.max(0, fallbackSize);
    const iconHeight = Math.max(0, fallbackSize);
    const iconTop = nodeRect.top + Math.max(8, Math.min(16, nodeRect.height * 0.14));
    const iconLeft = nodeRect.left + Math.max(0, ((nodeRect.width || iconWidth) - iconWidth) / 2);

    overlayImg.style.cssText = [
      `width:${iconWidth}px`,
      `height:${iconHeight}px`,
      "object-fit:contain",
      "pointer-events:none",
      "user-select:none",
      "position:absolute",
      `left:${Math.max(0, iconLeft)}px`,
      `top:${Math.max(0, iconTop)}px`,
    ].join(";");
    overlay.appendChild(overlayImg);

    if (labelText && labelRect) {
      const labelStyles = window.getComputedStyle(labelEl);
      const overlayLabel = document.createElement("div");
      overlayLabel.textContent = labelText;
      overlayLabel.dataset.nodeId = nodeId;
      const labelRectLocal = toLocalRect(labelRect);
      const labelWidth = Math.max(0, Math.min(nodeRect.width - 10, labelRectLocal?.width || nodeRect.width - 10));
      const labelLeft = nodeRect.left + Math.max(5, (nodeRect.width - labelWidth) / 2);
      const labelTop = Math.max(
        nodeRect.top + iconHeight + Math.max(12, Math.min(20, nodeRect.height * 0.2)),
        labelRectLocal?.top || 0
      );
      overlayLabel.style.cssText = [
        "position:absolute",
        `left:${Math.max(0, labelLeft)}px`,
        `top:${Math.max(0, labelTop)}px`,
        `width:${Math.max(0, labelWidth)}px`,
        `height:${Math.max(0, labelRectLocal?.height || 24)}px`,
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
      labelEl.style.opacity = "0";
      labelEl.style.visibility = "hidden";
    }
    if (img) {
      img.style.opacity = "0";
      img.style.visibility = "hidden";
    }
  });
}

function buildFallbackModalData(resourceId, nodeData, lookup = {}) {
  const resources = Array.isArray(nodeData?.resources) ? nodeData.resources : [];
  const primary = resources[0] || {};
  const fallbackFqdns = resources
  .flatMap((item) => [
    item?.fqdn,
    ...(Array.isArray(item?.dns_names) ? item.dns_names : []),
  ])
  .map((value) => String(value || "").trim())
  .filter(Boolean);
  const fallbackIps = resources
  .flatMap((item) => [
    item?.public_ip,
    ...(Array.isArray(item?.public_ips) ? item.public_ips : []),
  ])
  .map((value) => String(value || "").trim())
  .filter(Boolean);
  return {
  title: firstNonEmpty(
    lookup.name,
    primary?.name,
    nodeData?.title,
    nodeData?.label,
    nodeData?.providerLabel,
    resourceId
  ),
  name: firstNonEmpty(lookup.name, primary?.name, nodeData?.label, resourceId),
  resource_group: firstNonEmpty(lookup.resourceGroup, primary?.rg, nodeData?.resourceGroup),
  type_label: firstNonEmpty(lookup.type, nodeData?.typeLabel, nodeData?.type),
  type: firstNonEmpty(lookup.type, nodeData?.resourceType, nodeData?.arm_type, nodeData?.type),
  fqdn: firstNonEmpty(nodeData?.fqdn, primary?.fqdn),
  dns_names: fallbackFqdns,
  public_ip: firstNonEmpty(nodeData?.public_ip, primary?.public_ip),
  public_ips: fallbackIps,
  icon_path: firstNonEmpty(nodeData?.icon_path, nodeData?.iconPath),
  network: {
    vnet: firstNonEmpty(nodeData?.vnet, nodeData?.vnet_name) || null,
    subnet: firstNonEmpty(nodeData?.subnet, nodeData?.subnet_name) || null,
  },
  };
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
    .map((item) => {
      if (/^\d{1,3}(?:\.\d{1,3}){3}$/.test(item)) return item;
      if (item.includes(" ") || !item.includes(".")) return item;
      const shortLabel = item.split(".", 1)[0];
      return shortLabel || item;
    })
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
  "microsoft.network/loadbalancers": "azurerm_load_balancer",
  "microsoft.network/bastionhosts": "azurerm_bastions",
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
  "microsoft.compute/virtualmachinescalesets": "azurerm_vm_scale_sets",
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
  const subgraphStyleAssignments = [];
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
  const networkTypePrefixes = [
    "microsoft.network/",
    "azurerm_virtual_network",
    "azurerm_subnet",
    "azurerm_network_security_group",
    "azurerm_route_table",
    "azurerm_private_endpoint",
    "azurerm_nat_gateway",
    "azurerm_firewall",
    "azurerm_public_ip",
    "aws_vpc",
    "aws_subnet",
    "aws_security_group",
    "aws_route_table",
    "aws_network_acl",
    "aws_internet_gateway",
    "aws_nat_gateway",
    "aws_vpc_endpoint",
    "google_compute_network",
    "google_compute_subnetwork",
    "google_compute_firewall",
    "oci_core_vcn",
    "oci_core_subnet",
    "alicloud_vpc",
    "alicloud_vswitch",
  ];

  function isNetworkAssetNode(node) {
    const data = node?.data || {};
    const fields = [
      data?.resourceType,
      data?.type,
      data?.arm_type,
      data?.typeLabel,
      data?.providerLabel,
      data?.label,
      data?.vnet,
      data?.vnet_name,
      data?.subnet,
      data?.subnet_name,
    ]
      .map((value) => String(value || "").trim().toLowerCase())
      .filter(Boolean);

    const joined = fields.join(" ");
    if (!joined) return false;
    if (networkTypePrefixes.some((prefix) => joined.includes(prefix))) return true;

    return [
      /\bvirtual[_\s-]*network\b/,
      /\bvnet\b/,
      /\bnetwork\b/,
      /\bsubnet\b/,
      /\bvirtual[_\s-]*machine[_\s-]*scale[_\s-]*set(s)?\b/,
      /\bvirtualmachinescalesets\b/,
      /\bapp[_\s-]*service[_\s-]*environment\b/,
      /\bhostingenvironment(s)?\b/,
      /\bnsg\b/,
      /\bnetwork[_\s-]*security[_\s-]*group\b/,
      /\broute[_\s-]*table\b/,
      /\bnetwork[_\s-]*interface\b/,
      /\bnic\b/,
      /\bprivate[_\s-]*endpoint\b/,
      /\bnat[_\s-]*gateway\b/,
      /\bvpn[_\s-]*gateway\b/,
      /\bvpc\b/,
    ].some((pattern) => pattern.test(joined));
  }

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

    const networkRootNodes = [];
    const otherRootNodes = [];
    for (const node of rootNodes) {
      if (isNetworkAssetNode(node)) {
        networkRootNodes.push(node);
      } else {
        otherRootNodes.push(node);
      }
    }

    for (const node of otherRootNodes) {
      renderNode(node, "    ");
    }

    if (networkRootNodes.length) {
      const networkGroupId = `${groupId}_network`;
      lines.push(`    subgraph ${networkGroupId}["${escapeMermaidText("🛡️ Networks / VNet")}"]`);
      for (const node of networkRootNodes) {
        renderNode(node, "      ");
      }
      lines.push("    end");
      subgraphStyleAssignments.push(`  style ${networkGroupId} stroke:#1971c2,stroke-width:2px,fill:none;`);
    }

    lines.push("  end");
  }

  if (subgraphStyleAssignments.length) {
    lines.push("");
    lines.push(...subgraphStyleAssignments);
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
        await injectDiagramIconsIntoSvg(svgEl, "all");
        const scrollEl = document.getElementById("cloud-arch-mermaid-scroll");
        if (scrollEl) {
          const fitScale = autoFitDiagram(mermaidRootEl, scrollEl);
          mermaidRootEl.dataset.diagramScale = String(fitScale || 1);
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

function resetModalRequestState() {
  activeModalRequest = null;
}

function startModalRequest() {
  if (activeModalRequest) {
    activeModalRequest.abort();
  }
  const controller = new AbortController();
  activeModalRequest = controller;
  return controller;
}

function closeModal() {
  if (activeModalRequest) {
    activeModalRequest.abort();
  }
  if (modalOverlay) {
    modalOverlay.hidden = true;
  }
  resetModalRequestState();
}

function openNodePopup(resourceId, nodeData) {
  const resources = Array.isArray(nodeData?.resources) ? nodeData.resources.filter(Boolean) : [];
  const isGroupedNode = Boolean(nodeData?.is_group || nodeData?.isGroupNode || nodeData?.summaryNode || nodeData?.groupType);
  const armType = String(nodeData?.arm_type || nodeData?.type || nodeData?.resourceType || "").toLowerCase();
  const prefersChildDrilldown = armType.includes("serverfarms") || armType.includes("hostingenvironments");
  if (resources.length > 1 || (isGroupedNode && resources.length > 0)) {
    if (currentMermaidSubscriptionId) {
      openDrilldownModal(nodeData, currentMermaidSubscriptionId);
      return;
    }
    renderGroupedResourcesModal(nodeData);
    return;
  }
  if (resources.length === 1) {
    const resource = resources[0] || {};
    if (prefersChildDrilldown && currentMermaidSubscriptionId) {
      openDrilldownModal(nodeData, currentMermaidSubscriptionId, () => {
        openModal(resourceId, nodeData, {
          id: resource.id,
          name: resource.name,
          resourceGroup: resource.rg,
          type: nodeData?.arm_type || nodeData?.type || nodeData?.resourceType || "",
          subscription: currentMermaidSubscriptionId,
          nodeId: resourceId,
        });
      });
      return;
    }
    openModal(resourceId, nodeData, {
      id: resource.id,
      name: resource.name,
      resourceGroup: resource.rg,
      type: nodeData?.arm_type || nodeData?.type || nodeData?.resourceType || "",
      subscription: currentMermaidSubscriptionId,
      nodeId: resourceId,
    });
    return;
  }
  openModal(resourceId, nodeData, { nodeId: resourceId });
}

function openDrilldownModal(nodeData, subId, fallback = null) {
  if (!modalOverlay || !subId) return;
  const controller = startModalRequest();
  const url = new URL(`/api/subscriptions/${encodeURIComponent(subId)}/drilldown`, window.location.origin);
  fetch(url.toString(), {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ arm_type: nodeData?.arm_type || nodeData?.type || "", resources: nodeData?.resources || [] }),
    signal: controller.signal,
  })
    .then(async (resp) => {
      if (controller.signal.aborted) return;
      const data = await readJsonResponse(resp);
      if (!resp.ok) {
        throw new Error(data?.error || `Request failed with status ${resp.status}`);
      }
      if (!data || data.error) {
        throw new Error(data?.error || "No drilldown data available");
      }
      if (controller.signal.aborted) return;
      const hasRows =
        (Array.isArray(data?.rows) && data.rows.length > 0) ||
        (Array.isArray(data?.sections) && data.sections.some((section) => Array.isArray(section?.rows) && section.rows.length > 0));
      if (!hasRows && typeof fallback === "function") {
        fallback();
        activeModalRequest = null;
        return;
      }
      if ((data.view_type === "table" || data.view_type === "tree_table") && Array.isArray(data.columns)) {
        renderTabularModalContent(data);
      } else {
        renderModalContent(data);
      }
      activeModalRequest = null;
    })
    .catch((err) => {
      if (err?.name === "AbortError") return;
      renderGroupedResourcesModal(nodeData);
      activeModalRequest = null;
    });
}

function renderGroupedResourcesModal(nodeData) {
  if (!modalOverlay || !modalTitle || !modalBody) return;
  const resources = Array.isArray(nodeData?.resources) ? nodeData.resources.filter(Boolean) : [];
  const title = firstNonEmpty(nodeData?.title, nodeData?.label, "Grouped resources");
  const typeLabel = firstNonEmpty(nodeData?.typeLabel, nodeData?.arm_type, nodeData?.type);

  modalOverlay.hidden = false;
  modalTitle.textContent = title;
  if (modalSubtitle) {
    modalSubtitle.textContent = `${typeLabel}${resources.length ? ` • ${resources.length} resources` : ""}`;
  }
  setModalHeaderIcon(firstNonEmpty(nodeData?.icon_path, nodeData?.iconPath), "🧩");

  if (!resources.length) {
    modalBody.innerHTML = '<div class="cloud-arch-modal-empty">No resources found in this group.</div>';
    return;
  }

  const sorted = [...resources].sort((a, b) => {
    const arg = String(a?.rg || "");
    const brg = String(b?.rg || "");
    if (arg !== brg) return arg.localeCompare(brg);
    return String(a?.name || "").localeCompare(String(b?.name || ""));
  });

  modalBody.innerHTML = `
    <div class="cloud-arch-modal-section">
      <div class="cloud-arch-modal-section-title">
        <span class="cloud-arch-modal-section-icon">📦</span>
        Group Members
      </div>
      <ul class="cloud-arch-modal-list">
        ${sorted
          .map((resource) => {
            const name = escapeHtml(String(resource?.name || "unnamed"));
            const rg = escapeHtml(String(resource?.rg || ""));
            return `<li class="cloud-arch-modal-list-item"><strong>${name}</strong>${rg ? ` <span style="color: var(--text-muted);">(${rg})</span>` : ""}</li>`;
          })
          .join("")}
      </ul>
    </div>
  `;
}

function openModal(resourceId, nodeData, lookup = {}) {
  if (!modalOverlay || !modalTitle || !modalBody) {
    console.error("Modal overlay not found");
    return;
  }
  const controller = startModalRequest();
  const nodeId = String(lookup.nodeId || resourceId || "").trim();
  const nodeChildren = collectChildNodeSummaries(nodeId);
  const withNodeContext = (payload = {}) => ({
    ...payload,
    __node_id: nodeId,
    __node_children: nodeChildren,
    __node_label: firstNonEmpty(nodeData?.title, nodeData?.label, nodeId),
  });
  modalOverlay.hidden = false;
  modalTitle.textContent = "Loading details…";
  if (modalSubtitle) modalSubtitle.textContent = "";
  modalBody.innerHTML = '<div class="cloud-arch-modal-loading">Loading resource details…</div>';

  const url = new URL("/api/cloud/resource-details", window.location.origin);
  const resolvedResourceId = String(lookup.id || lookup.resourceId || resourceId || "").trim();
  if (resolvedResourceId) url.searchParams.set("id", resolvedResourceId);
  const name = String(lookup.name || lookup.resourceName || lookup.label || nodeData?.label || "").trim();
  const resourceGroup = String(lookup.resourceGroup || lookup.rg || nodeData?.resourceGroup || "").trim();
  const type = String(lookup.type || lookup.armType || lookup.resourceType || nodeData?.arm_type || nodeData?.type || "").trim();
  const subscription = String(lookup.subscription || lookup.sub || currentMermaidSubscriptionId || "").trim();
  if (name) url.searchParams.set("name", name);
  if (resourceGroup) url.searchParams.set("resource_group", resourceGroup);
  if (type) url.searchParams.set("type", type);
  if (subscription) url.searchParams.set("sub", subscription);

  fetch(url.toString(), { headers: { Accept: "application/json" }, signal: controller.signal })
    .then(async (resp) => {
      if (controller.signal.aborted) return;
      const data = await readJsonResponse(resp);
      if (!resp.ok) {
        throw new Error(data?.error || `Request failed with status ${resp.status}`);
      }
      if (!data || data.error) {
        renderModalContent(withNodeContext(buildFallbackModalData(resourceId, nodeData, lookup)));
        activeModalRequest = null;
        return;
      }
      if (controller.signal.aborted) return;
      const mergedData = {
        ...buildFallbackModalData(resourceId, nodeData, lookup),
        ...data,
      };
      renderModalContent(withNodeContext(mergedData));
      activeModalRequest = null;
    })
    .catch((err) => {
      if (err?.name === "AbortError") return;
      const fallbackData = buildFallbackModalData(resourceId, nodeData, lookup);
      const hasFallback =
        fallbackData.name ||
        fallbackData.resource_group ||
        fallbackData.type_label ||
        fallbackData.type ||
        fallbackData.fqdn ||
        (Array.isArray(fallbackData.public_ips) && fallbackData.public_ips.length > 0);
      if (hasFallback) {
        renderModalContent(withNodeContext(fallbackData));
      } else {
        modalTitle.textContent = "Unable to load details";
        if (modalSubtitle) modalSubtitle.textContent = "";
        modalBody.innerHTML = `<div class="cloud-arch-modal-empty">${escapeHtml(err.message || "Unknown error")}</div>`;
        if (modalIcon) modalIcon.textContent = "⚠";
      }

      activeModalRequest = null;
    });
}

function collectChildNodeSummaries(nodeId) {
  if (!nodeId || !mermaidNodeDataById || typeof mermaidNodeDataById.entries !== "function") {
    return [];
  }
  const children = [];
  for (const [childId, childData] of mermaidNodeDataById.entries()) {
    const parentId = String(childData?.parentNodeId || childData?.parent_node_id || "").trim();
    if (!parentId || parentId !== nodeId) continue;
    const resources = Array.isArray(childData?.resources) ? childData.resources.filter(Boolean) : [];
    children.push({
      id: childId,
      label: firstNonEmpty(childData?.title, childData?.label, childId),
      type: firstNonEmpty(childData?.typeLabel, childData?.resourceType, childData?.arm_type, childData?.type),
      resourceGroup: firstNonEmpty(childData?.resourceGroup, resources[0]?.rg),
      resourcesCount: resources.length,
    });
  }
  children.sort((a, b) => a.label.localeCompare(b.label));
  return children;
}

function setModalHeaderIcon(iconPath, fallbackText = "☁") {
  if (!modalIcon) return;
  if (iconPath) {
    modalIcon.innerHTML = `<img src="${escapeHtml(iconPath)}" alt="" aria-hidden="true" style="width:28px;height:28px;object-fit:contain;" />`;
    return;
  }
  modalIcon.textContent = fallbackText;
}

function firstNonEmpty(...values) {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }
  return "";
}

function collectPublicIps(data) {
  const candidates = [
    data?.public_ip,
    data?.publicIp,
    ...(Array.isArray(data?.public_ips) ? data.public_ips : []),
    ...(Array.isArray(data?.network?.public_ips) ? data.network.public_ips : []),
    ...(Array.isArray(data?.attack_surface?.public_ips) ? data.attack_surface.public_ips : []),
  ];
  const seen = new Set();
  return candidates
    .map((value) => String(value || "").trim())
    .filter((value) => {
      if (!value || seen.has(value)) return false;
      seen.add(value);
      return true;
    });
}

function collectFqdns(data) {
  const candidates = [
    data?.fqdn,
    data?.configuration?.hostname,
    ...(Array.isArray(data?.dns_names) ? data.dns_names : []),
    ...(Array.isArray(data?.network?.dns_names) ? data.network.dns_names : []),
    ...(Array.isArray(data?.attack_surface?.dns_names) ? data.attack_surface.dns_names : []),
  ];
  const seen = new Set();
  return candidates
    .map((value) => String(value || "").trim())
    .filter((value) => {
      if (!value || seen.has(value)) return false;
      seen.add(value);
      return true;
    });
}

function collectVnet(data) {
  const value = firstNonEmpty(
    data?.network?.vnet,
    data?.vnet,
    data?.vnetName,
    data?.vnet_name
  );
  return value || "";
}

function collectSubnet(data) {
  const value = firstNonEmpty(
    data?.network?.subnet,
    data?.subnet,
    data?.subnetName,
    data?.subnet_name
  );
  return value || "";
}

function collectRoutingTargets(data) {
  const candidates = [
    ...(Array.isArray(data?.routing_targets) ? data.routing_targets : []),
    ...(Array.isArray(data?.network?.routing_targets) ? data.network.routing_targets : []),
  ];
  const seen = new Set();
  const values = [];
  for (const item of candidates) {
    const raw =
      typeof item === "string"
        ? item
        : (item && typeof item === "object"
          ? String(item.target || item.name || "").trim()
          : "");
    const value = String(raw || "").trim();
    const key = value.toLowerCase();
    if (!value || seen.has(key)) continue;
    seen.add(key);
    values.push(value);
  }
  return values;
}

function renderTabularModalContent(data) {
  if (!modalOverlay || !modalTitle || !modalBody) return;
  modalOverlay.hidden = false;
  modalTitle.textContent = firstNonEmpty(data?.title, data?.name, "Details");
  if (modalSubtitle) modalSubtitle.textContent = "";
  setModalHeaderIcon(data?.icon_path || data?.parent_resource?.icon_path || "", "☁");

  const parentResource = data?.parent_resource && typeof data.parent_resource === "object" ? data.parent_resource : null;
  const parentResourceSection = parentResource
    ? `
      <div class="cloud-arch-modal-section">
        <div class="cloud-arch-modal-section-title">
          <span class="cloud-arch-modal-section-icon">🧭</span>
          Parent Resource
        </div>
        <div class="cloud-arch-modal-grid">
          ${[
            parentResource.name ? `<div class="cloud-arch-modal-field"><div class="cloud-arch-modal-field-label">Asset Name</div><div class="cloud-arch-modal-field-value">${escapeHtml(String(parentResource.name))}</div></div>` : "",
            parentResource.type_label || parentResource.type ? `<div class="cloud-arch-modal-field"><div class="cloud-arch-modal-field-label">Service Type</div><div class="cloud-arch-modal-field-value">${escapeHtml(String(parentResource.type_label || parentResource.type))}</div></div>` : "",
            parentResource.resource_group ? `<div class="cloud-arch-modal-field"><div class="cloud-arch-modal-field-label">Resource Group</div><div class="cloud-arch-modal-field-value">${escapeHtml(String(parentResource.resource_group))}</div></div>` : "",
            parentResource.location ? `<div class="cloud-arch-modal-field"><div class="cloud-arch-modal-field-label">Location</div><div class="cloud-arch-modal-field-value">${escapeHtml(String(parentResource.location))}</div></div>` : "",
            parentResource.sku ? `<div class="cloud-arch-modal-field"><div class="cloud-arch-modal-field-label">SKU</div><div class="cloud-arch-modal-field-value">${escapeHtml(String(parentResource.sku))}</div></div>` : "",
            parentResource.fqdn ? `<div class="cloud-arch-modal-field"><div class="cloud-arch-modal-field-label">FQDN</div><div class="cloud-arch-modal-field-value"><code>${escapeHtml(String(parentResource.fqdn))}</code></div></div>` : "",
          ].filter(Boolean).join("")}
        </div>
      </div>
    `
    : "";

  const columns = Array.isArray(data?.columns) ? data.columns : [];
  if (!columns.length) {
    modalBody.innerHTML = `<div class="cloud-arch-modal-empty">${escapeHtml(data?.empty_message || "No tabular data available.")}</div>`;
    return;
  }

  const renderCell = (cell, depth = 0, isFirstCol = false) => {
    if (cell == null) {
      return "<span style=\"color: var(--text-muted);\">—</span>";
    }
    if (typeof cell === "object") {
      const label = escapeHtml(String(cell.label || "—"));
      const style = cell.style ? String(cell.style) : "";
      if (cell.href) {
        const href = escapeHtml(String(cell.href));
        const title = cell.title ? ` title="${escapeHtml(String(cell.title))}"` : "";
        return `<a href="${href}" target="_blank" rel="noopener noreferrer"${title} style="color:#60a5fa;text-decoration:underline;${isFirstCol ? `padding-left:${depth * 16}px;display:inline-block;` : ""}">${label}</a>`;
      }
      return `<span style="${style}${isFirstCol ? `;padding-left:${depth * 16}px;display:inline-block;` : ""}">${label}</span>`;
    }
    const text = escapeHtml(String(cell));
    if (!isFirstCol || depth <= 0) return text;
    return `<span style="padding-left:${depth * 16}px;display:inline-block;">${text}</span>`;
  };

  const buildTable = (rows) => {
    const rowList = Array.isArray(rows) ? rows : [];
    const rowById = new Map(
      rowList
        .filter((row) => row && typeof row === "object" && row.id)
        .map((row) => [String(row.id), row])
    );
    const calcDepth = (row) => {
      if (!row || typeof row !== "object") return 0;
      let depth = 0;
      let parentId = String(row.parent_id || "").trim();
      const seen = new Set();
      while (parentId && rowById.has(parentId) && !seen.has(parentId)) {
        depth += 1;
        seen.add(parentId);
        const parent = rowById.get(parentId);
        parentId = String(parent?.parent_id || "").trim();
      }
      return depth;
    };

    return `
      <div style="overflow:auto;border:1px solid var(--border);border-radius:8px;">
        <table style="width:100%;border-collapse:collapse;font-size:0.84rem;">
          <thead>
            <tr>
              ${columns
                .map(
                  (col) =>
                    `<th style="padding:8px 10px;text-align:left;background:var(--bg-base);border-bottom:1px solid var(--border);font-size:0.75rem;text-transform:uppercase;letter-spacing:0.03em;color:var(--text-muted);">${escapeHtml(String(col || ""))}</th>`
                )
                .join("")}
            </tr>
          </thead>
          <tbody>
            ${rowList
              .map((row) => {
                const isObjectRow = row && typeof row === "object" && Array.isArray(row.cells);
                const cells = isObjectRow ? row.cells : (Array.isArray(row) ? row : []);
                const depth = isObjectRow ? calcDepth(row) : 0;
                return `
                  <tr style="border-bottom:1px solid var(--border);">
                    ${columns
                      .map((_, idx) => {
                        const cell = cells[idx];
                        return `<td style="padding:8px 10px;vertical-align:top;">${renderCell(cell, depth, idx === 0)}</td>`;
                      })
                      .join("")}
                  </tr>
                `;
              })
              .join("")}
          </tbody>
        </table>
      </div>
    `;
  };

  if (data.view_type === "tree_table" && Array.isArray(data.sections) && data.sections.length > 0) {
    modalBody.innerHTML = `${parentResourceSection}${data.sections
      .map((section) => {
        const title = escapeHtml(String(section?.title || ""));
        const subtitle = escapeHtml(String(section?.subtitle || ""));
        return `
          <div class="cloud-arch-modal-section">
            <div class="cloud-arch-modal-section-title">
              <span class="cloud-arch-modal-section-icon">📋</span>
              ${title}
            </div>
            ${subtitle ? `<div class="cloud-arch-modal-subtitle" style="margin-bottom:10px;">${subtitle}</div>` : ""}
            ${buildTable(section?.rows)}
          </div>
        `;
      })
      .join("")}`;
    return;
  }

  const rows = Array.isArray(data.rows) ? data.rows : [];
  if (!rows.length) {
    modalBody.innerHTML = `<div class="cloud-arch-modal-empty">${escapeHtml(data.empty_message || "No data available.")}</div>`;
    return;
  }
  modalBody.innerHTML = `${parentResourceSection}${buildTable(rows)}`;
}

function renderModalContent(data) {
  if (!modalOverlay || !modalTitle || !modalBody) return;
  modalOverlay.hidden = false;
  modalTitle.textContent = firstNonEmpty(data.title, data.name, "Resource details");
  if (modalSubtitle) {
    modalSubtitle.textContent = firstNonEmpty(data.type_label, data.type, data.resourceType);
  }
  setModalHeaderIcon(data.icon_path || data.parent_resource?.icon_path || "", "☁");

  const resourceName = firstNonEmpty(data.name, data.title, data.resource_name, data.__node_label);
  const resourceGroup = firstNonEmpty(
    data.resource_group,
    data.resourceGroup,
    data.parent_resource?.resource_group
  );
  const serviceType = firstNonEmpty(data.type_label, data.typeLabel, data.type, data.resourceType);
  const resourceType = firstNonEmpty(data.type, data.resourceType, data.arm_type);
  const fqdns = collectFqdns(data);
  const publicIps = collectPublicIps(data);
  const routingTargets = collectRoutingTargets(data);
  const vnet = collectVnet(data);
  const subnet = collectSubnet(data);
  const childNodes = Array.isArray(data.__node_children) ? data.__node_children : [];

  const sections = [];

  const overviewFields = [];
  if (resourceName) overviewFields.push({ label: "Asset Name", value: escapeHtml(resourceName) });
  if (serviceType) overviewFields.push({ label: "Service Type", value: escapeHtml(serviceType) });
  if (resourceType) overviewFields.push({ label: "Resource Type", value: escapeHtml(resourceType) });
  if (data.sku || data.configuration?.sku_name) {
    overviewFields.push({ label: "SKU", value: escapeHtml(String(data.sku || data.configuration?.sku_name || "")) });
  }
  if (data.security && typeof data.security.is_public === "boolean") {
    overviewFields.push({
      label: "Public",
      value: data.security.is_public
        ? '<span class="cloud-arch-modal-badge cloud-arch-modal-badge--danger">🌐 Public</span>'
        : '<span class="cloud-arch-modal-badge cloud-arch-modal-badge--success">🔒 Private</span>',
      isHtml: true,
    });
  }
  if (resourceGroup) overviewFields.push({ label: "Resource Group", value: escapeHtml(resourceGroup) });
  if (data.location) overviewFields.push({ label: "Location", value: escapeHtml(String(data.location)) });
  if (overviewFields.length > 0) {
    sections.push({ title: "Asset Overview", icon: "🧩", fields: overviewFields });
  }

  const configFields = [];
  if (data.configuration?.sku_name) configFields.push({ label: "SKU", value: escapeHtml(String(data.configuration.sku_name)) });
  if (data.configuration?.sku_tier) configFields.push({ label: "Tier", value: escapeHtml(String(data.configuration.sku_tier)) });
  if (data.location) configFields.push({ label: "Location", value: escapeHtml(String(data.location)) });
  if (data.subscription) configFields.push({ label: "Subscription", value: escapeHtml(String(data.subscription)) });
  if (data.environment) configFields.push({ label: "Environment", value: escapeHtml(String(data.environment)) });
  if (data.configuration?.kind) configFields.push({ label: "Kind", value: escapeHtml(String(data.configuration.kind)) });
  if (data.configuration?.hostname) configFields.push({ label: "Hostname", value: escapeHtml(String(data.configuration.hostname)) });
  if (data.configuration?.protocol) configFields.push({ label: "Protocol", value: escapeHtml(String(data.configuration.protocol)) });
  if (data.configuration?.port) configFields.push({ label: "Port", value: escapeHtml(String(data.configuration.port)) });
  if (configFields.length > 0) {
    sections.push({ title: "Configuration", icon: "⚙️", fields: configFields });
  }

  const networkFields = [];
  if (fqdns.length > 0) {
    networkFields.push({
      label: "FQDN",
      value: fqdns.map((fqdn) => `<code>${escapeHtml(fqdn)}</code>`).join("<br/>"),
      isHtml: true,
    });
  }
  if (publicIps.length > 0) {
    networkFields.push({
      label: "Public IP",
      value: publicIps.map((ip) => `<code>${escapeHtml(ip)}</code>`).join("<br/>"),
      isHtml: true,
    });
  }
  if (routingTargets.length > 0) {
    networkFields.push({
      label: "Routes To",
      value: routingTargets.map((target) => `<code>${escapeHtml(target)}</code>`).join("<br/>"),
      isHtml: true,
    });
  }
  if (vnet) networkFields.push({ label: "Virtual Network", value: escapeHtml(vnet) });
  if (subnet) networkFields.push({ label: "Subnet", value: escapeHtml(subnet) });
  if (networkFields.length > 0) {
    sections.push({ title: "Network", icon: "🌐", fields: networkFields });
  }

  if (childNodes.length > 0) {
    sections.push({
      title: "Children",
      icon: "🧬",
      fields: [
        {
          label: "Child Resources",
          value: `<ul class="cloud-arch-modal-list">${childNodes
            .map((child) => {
              const type = child.type ? `<span style="color: var(--text-muted);">${escapeHtml(child.type)}</span>` : "";
              const rg = child.resourceGroup ? `<span style="color: var(--text-muted);">(${escapeHtml(child.resourceGroup)})</span>` : "";
              const count = child.resourcesCount > 1 ? ` <span class="cloud-arch-modal-badge cloud-arch-modal-badge--info">${child.resourcesCount} resources</span>` : "";
              return `<li class="cloud-arch-modal-list-item"><strong>${escapeHtml(child.label)}</strong>${type ? ` • ${type}` : ""} ${rg}${count}</li>`;
            })
            .join("")}</ul>`,
          isHtml: true,
        },
      ],
    });
  }

  if (!sections.length) {
    modalBody.innerHTML = '<div class="cloud-arch-modal-empty">No core resource details found for this node.</div>';
    return;
  }

  modalBody.innerHTML = sections
    .map(
      (section) => `
      <div class="cloud-arch-modal-section">
        <div class="cloud-arch-modal-section-title">
          <span class="cloud-arch-modal-section-icon">${section.icon}</span>
          ${section.title}
        </div>
        <div class="cloud-arch-modal-grid">
          ${(section.fields || [])
            .map(
              (field) => `
            <div class="cloud-arch-modal-field">
              <div class="cloud-arch-modal-field-label">${field.label}</div>
              <div class="cloud-arch-modal-field-value">${field.isHtml ? field.value : field.value}</div>
            </div>
          `
            )
            .join("")}
        </div>
      </div>
    `
    )
    .join("");
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

  currentMermaidSubscriptionId = String(subscriptionName || "").trim();
  mermaidViewEl.hidden = false;

  const resolveSubscriptionId = async (selector) => {
    const raw = String(selector || "").trim();
    if (!raw) return "";
    try {
      const resp = await fetch("/api/subscriptions", { headers: { Accept: "application/json" } });
      if (!resp.ok) return raw;
      const data = await readJsonResponse(resp);
      const subs = Array.isArray(data?.subscriptions) ? data.subscriptions : [];
      const exact = subs.find((s) => String(s?.id || "").toLowerCase() === raw.toLowerCase());
      if (exact?.id) return String(exact.id);
      const byName = subs.find((s) => String(s?.display_name || "").toLowerCase() === raw.toLowerCase());
      if (byName?.id) return String(byName.id);
    } catch (_) {
      // Fall back to raw selector and let API resolve if possible.
    }
    return raw;
  };

  const pickIngressView = (subscriptionPayload) => {
    const ingress = subscriptionPayload?.ingress_diagram || {};
    const views = ingress?.views || {};
    const preferredMode = ingress?.default_view === "attack_paths" ? "attack_paths" : "connectivity";
    const chosen = views[preferredMode] || views.connectivity || ingress;
    return {
      mermaid: chosen?.mermaid || ingress?.mermaid || "",
      css_code: chosen?.css_code || ingress?.css_code || "",
      node_drilldown_map: chosen?.node_drilldown_map || ingress?.node_drilldown_map || {},
    };
  };

  try {
    let payload = null;
    let renderPayload = null;
    let summaryPayload = null;

    const subscriptionId = await resolveSubscriptionId(subscriptionName);
    if (subscriptionId) {
      currentMermaidSubscriptionId = String(subscriptionId);
    }
    if (subscriptionId) {
      const previewResp = await fetch(
        `/api/subscriptions/${encodeURIComponent(subscriptionId)}/diagram`,
        { headers: { Accept: "application/json" } }
      );
      if (previewResp.ok) {
        payload = await readJsonResponse(previewResp);
        renderPayload = pickIngressView(payload);
        summaryPayload = {
          summary: {
            resource_count: payload?.total_assets || 0,
            displayed_resource_count: payload?.total_assets || 0,
            omitted_resource_count: 0,
            connection_count: (payload?.ingress_diagram?.attack_paths || []).length || 0,
            provider_counts: [{ key: "azure", label: "Azure", count: payload?.total_assets || 0 }],
            layout_mode: "mermaid",
          },
          subscription_name: payload?.subscription_name || subscriptionName,
        };
      }
    }

    if (!renderPayload) {
      const resp = await fetch(
        `/api/cloud/architecture?sub=${encodeURIComponent(subscriptionName)}&view=mermaid`,
        { headers: { Accept: "application/json" } }
      );
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      payload = await readJsonResponse(resp);
      renderPayload = payload;
      summaryPayload = payload;
    }

    if (!payload) {
      return false;
    }

    const isEmpty =
      !String(renderPayload?.mermaid || "").trim() &&
      (!renderPayload?.nodes || renderPayload.nodes.length === 0);

    if (isEmpty) {
      mermaidRootEl.innerHTML = "";
    } else {
      await renderMermaidGraph(renderPayload, subscriptionName);
    }

    renderSummary(summaryPayload, summaryPayload?.subscription_name || subscriptionName, activeViewMode);
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
    e.preventDefault();
    const container = document.getElementById("cloud-arch-mermaid-root");
    if (container && window.applyDiagramScale) {
      const current = parseFloat(container.dataset.diagramScale || "1") || 1;
      const zoomFactor = Math.exp(-e.deltaY * 0.0015);
      window.applyDiagramScale(container, current * zoomFactor);
    }
  }, { passive: false });
})();

if (mermaidViewEl) {
  mermaidViewEl.hidden = activeViewMode !== "mermaid";
}

syncViewButtons();
