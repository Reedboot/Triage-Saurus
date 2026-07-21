import {
  sanitizeMermaidSource,
  injectDiagramIconsIntoSvg,
} from "./diagram-shared.js?v=3";
import {
  enhancePlaceholderGlyphs,
  applyEmojiIconFallback,
} from "./diagram-base.js?v=6";
import { renderMermaidDiagram, postProcessSvg } from "./subscription-diagrams.js?v=5";
import { autoFitDiagram, applyDiagramScale } from "./diagram-base.js?v=6";
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
const FIREFOX_FIT_SAFETY = 0.96;
const FIREFOX_VIEWBOX_PADDING_X = 18;
const FIREFOX_VIEWBOX_PADDING_TOP = 150;
const FIREFOX_VIEWBOX_PADDING_BOTTOM = 18;
let mermaidNodeDataById = new Map();
let mermaidOriginalIdByNodeId = new Map();
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
let mermaidFitRaf = null;
let mermaidFitTimeout = null;
let mermaidFitResizeObserver = null;
let mermaidManualZoom = false;

function cancelMermaidDiagramFit() {
  if (mermaidFitRaf) cancelAnimationFrame(mermaidFitRaf);
  if (mermaidFitTimeout) clearTimeout(mermaidFitTimeout);
  mermaidFitRaf = null;
  mermaidFitTimeout = null;
}

window.__triageDiagramZoomStateChanged = (container, action) => {
  if (!container || container.id !== "cloud-arch-mermaid-root") {
    return;
  }
  if (action === "zoom") {
    mermaidManualZoom = true;
    cancelMermaidDiagramFit();
    return;
  }
  if (action === "fit") {
    mermaidManualZoom = false;
    cancelMermaidDiagramFit();
  }
};

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
    overlay.setAttribute("data-diagram-icon-overlay", "");
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

function addFirefoxViewBoxPadding(svgEl) {
  if (!isFirefox || !svgEl) return;
  const vb = svgEl.viewBox?.baseVal;
  if (!vb || vb.width <= 0 || vb.height <= 0) return;

  const x = vb.x - FIREFOX_VIEWBOX_PADDING_X;
  const y = vb.y - FIREFOX_VIEWBOX_PADDING_TOP;
  const width = vb.width + FIREFOX_VIEWBOX_PADDING_X * 2;
  const height = vb.height + FIREFOX_VIEWBOX_PADDING_TOP + FIREFOX_VIEWBOX_PADDING_BOTTOM;

  svgEl.setAttribute("viewBox", `${x} ${y} ${width} ${height}`);
  svgEl.setAttribute("width", `${width}px`);
  svgEl.setAttribute("height", `${height}px`);
  svgEl.style.width = `${width}px`;
  svgEl.style.height = `${height}px`;
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
    ...(Array.isArray(item?.associated_public_ips) ? item.associated_public_ips : []),
    ...(Array.isArray(item?.public_ips) ? item.public_ips : []),
  ])
  .map((value) => String(value || "").trim())
  .filter(Boolean);
  const resolvedDnsNames = Array.from(new Set([
    ...fallbackFqdns,
    String(nodeData?.fqdn || "").trim(),
    String(primary?.fqdn || "").trim(),
  ].filter(Boolean)));
  return {
  title: firstNonEmpty(
    nodeData?.title,
    nodeData?.label,
    lookup.name,
    primary?.name,
    nodeData?.providerLabel,
    resourceId
  ),
  name: firstNonEmpty(nodeData?.label, nodeData?.title, lookup.name, primary?.name, resourceId),
  resource_group: firstNonEmpty(lookup.resourceGroup, primary?.rg, nodeData?.resourceGroup),
  type_label: firstNonEmpty(lookup.type, nodeData?.typeLabel, nodeData?.type),
  type: firstNonEmpty(lookup.type, nodeData?.resourceType, nodeData?.arm_type, nodeData?.type),
  fqdn: firstNonEmpty(nodeData?.fqdn, primary?.fqdn, resolvedDnsNames[0]),
  dns_names: resolvedDnsNames,
  public_ip: firstNonEmpty(nodeData?.public_ip, primary?.public_ip),
  public_ips: fallbackIps,
  icon_path: firstNonEmpty(nodeData?.icon_path, nodeData?.iconPath),
  routing_targets: Array.isArray(nodeData?.routing_targets)
    ? nodeData.routing_targets
    : Array.isArray(nodeData?.network?.routing_targets)
      ? nodeData.network.routing_targets
      : [],
  network: {
    vnet: firstNonEmpty(nodeData?.network?.vnet, nodeData?.vnet, nodeData?.vnetName, nodeData?.vnet_name) || null,
    subnet: firstNonEmpty(nodeData?.network?.subnet, nodeData?.subnet, nodeData?.subnetName, nodeData?.subnet_name) || null,
    routing_targets: Array.isArray(nodeData?.network?.routing_targets)
      ? nodeData.network.routing_targets
      : Array.isArray(nodeData?.routing_targets)
        ? nodeData.routing_targets
        : [],
  },
  security: {
    is_public: Boolean(nodeData?.security?.is_public ?? nodeData?.public ?? nodeData?.is_public ?? nodeData?.isPublic),
    waf_mode: firstNonEmpty(nodeData?.security?.waf_mode, nodeData?.waf_mode, ""),
    waf_enabled: Boolean(nodeData?.security?.waf_enabled ?? nodeData?.waf_enabled ?? nodeData?.has_waf),
  },
  };
}

function refreshFirefoxOverlay() {
  if (!isFirefox || !mermaidRootEl) return;
  const svgEl = mermaidRootEl.querySelector("svg");
  if (!svgEl) return;
  renderFirefoxIconOverlay(svgEl);
}

function fitMermaidDiagram() {
  const scrollEl = document.getElementById("cloud-arch-mermaid-scroll");
  if (!scrollEl || !mermaidRootEl) return 1;
  const fitScale = autoFitDiagram(mermaidRootEl, scrollEl);
  const appliedScale = isFirefox ? Math.max(0.05, fitScale * FIREFOX_FIT_SAFETY) : fitScale;
  if (isFirefox && Math.abs(appliedScale - fitScale) > 0.0001) {
    applyDiagramScale(mermaidRootEl, appliedScale);
  }
  mermaidRootEl.dataset.diagramScale = String(appliedScale || fitScale || 1);
  return appliedScale;
}

function scheduleMermaidDiagramFit() {
  if (!mermaidRootEl || mermaidManualZoom || mermaidRootEl.dataset.diagramManualZoom === "true") {
    return;
  }
  cancelMermaidDiagramFit();

  const getSvgBounds = (svgEl) => {
    if (!svgEl) return null;
    const width =
      parseFloat(svgEl.getAttribute("width") || "") ||
      svgEl.viewBox?.baseVal?.width ||
      svgEl.scrollWidth ||
      0;
    const height =
      parseFloat(svgEl.getAttribute("height") || "") ||
      svgEl.viewBox?.baseVal?.height ||
      svgEl.scrollHeight ||
      0;
    return { width, height };
  };

  const attemptFit = (attempt = 0) => {
    const scrollEl = document.getElementById("cloud-arch-mermaid-scroll");
    const svgEl = mermaidRootEl?.querySelector("svg");
    const bounds = getSvgBounds(svgEl);
    const ready =
      scrollEl &&
      bounds &&
      bounds.width > 0 &&
      bounds.height > 0 &&
      (scrollEl.clientWidth || scrollEl.offsetWidth) &&
      (scrollEl.clientHeight || scrollEl.offsetHeight);

    if (ready || attempt >= 8) {
      fitMermaidDiagram();
      mermaidFitTimeout = setTimeout(() => fitMermaidDiagram(), 75);
      return;
    }

    mermaidFitRaf = requestAnimationFrame(() => attemptFit(attempt + 1));
  };

  mermaidFitRaf = requestAnimationFrame(() => requestAnimationFrame(() => attemptFit()));
}

function bindMermaidFitSync() {
  if (!mermaidRootEl || typeof ResizeObserver === "undefined") return;
  if (mermaidFitResizeObserver) return;

  mermaidFitResizeObserver = new ResizeObserver(() => {
    scheduleMermaidDiagramFit();
  });
  mermaidFitResizeObserver.observe(mermaidRootEl);

  const scrollEl = document.getElementById("cloud-arch-mermaid-scroll");
  if (scrollEl) {
    mermaidFitResizeObserver.observe(scrollEl);
  }
}

function scheduleFirefoxOverlayRefresh() {
  if (!isFirefox) return;
  if (firefoxOverlayRaf) cancelAnimationFrame(firefoxOverlayRaf);
  if (firefoxOverlayTimeout) clearTimeout(firefoxOverlayTimeout);
  firefoxOverlayRaf = requestAnimationFrame(() => {
    refreshFirefoxOverlay();
    scheduleMermaidDiagramFit();
    // One extra delayed pass after layout settles during zoom/fit.
    firefoxOverlayTimeout = setTimeout(() => {
      refreshFirefoxOverlay();
      scheduleMermaidDiagramFit();
    }, 60);
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

function resolveMermaidOriginalId(nodeId) {
  const compactId = String(nodeId || "").trim();
  return mermaidOriginalIdByNodeId.get(compactId) || compactId;
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
    if (!parentId || parentId === id || !nodeById.has(parentId)) continue;
    if ((providerById.get(parentId) || "unknown") !== (providerById.get(id) || "unknown")) continue;
    let cursor = parentId;
    const seen = new Set([id]);
    let createsCycle = false;
    while (cursor) {
      if (seen.has(cursor)) {
        createsCycle = true;
        break;
      }
      seen.add(cursor);
      cursor = parentById.get(cursor) || "";
    }
    if (createsCycle) continue;
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

  function isSubnetNode(node) {
    const data = node?.data || {};
    const fields = [
      data?.resourceType,
      data?.type,
      data?.arm_type,
      data?.typeLabel,
    ]
      .map((value) => String(value || "").trim().toLowerCase())
      .filter(Boolean);
    const joined = fields.join(" ");
    if (!joined) return false;
    return [
      /\bsubnet\b/,
      /\bsubnetwork\b/,
      /\bvswitch\b/,
    ].some((pattern) => pattern.test(joined));
  }

  function normalizeGroupKey(value) {
    return String(value || "").trim().toLowerCase();
  }

  function collectNodeVnet(node) {
    return collectVnet(node?.data);
  }

  function collectNodeSubnet(node) {
    return collectSubnet(node?.data);
  }

  function isNetworkScopedNode(node) {
    return Boolean(collectNodeVnet(node) || collectNodeSubnet(node) || isNetworkAssetNode(node));
  }

  function renderNode(node, indent = "    ") {
    const mermaidId = sanitizeMermaidId(node?.id, `node_${autoIndex++}`);
    nodeIdMap.set(String(node?.id), mermaidId);

    const title = node?.data?.label || node?.data?.providerLabel || node?.id || "Node";
    const typeLabel = node?.data?.typeLabel || "";
    const repoLabel = node?.data?.repoName || "";
    const nodeLabel = buildNodeLabel(title, typeLabel, repoLabel);

    const children = hierarchy.childrenByParent.get(String(node?.id)) || [];
    if (children.length && isSubnetNode(node)) {
      lines.push(`${indent}subgraph ${mermaidId}["${escapeMermaidText(nodeLabel)}"]`);
      for (const child of children) {
        renderNode(child, `${indent}  `);
      }
      lines.push(`${indent}end`);
      subgraphStyleAssignments.push(`  style ${mermaidId} stroke:#94a3b8,stroke-width:2px;`);
      return;
    }

    lines.push(`${indent}${mermaidId}["${nodeLabel}"]`);

    const iconClass = String(node?.data?.iconClass || "").trim() || normalizeIconClass(node?.data?.resourceType || "", node?.data?.providerKey || "azure");
    if (iconClass) {
      const mermaidSafeIconClass = iconClass.replace(/-/g, "_");
      nodeClassAssignments.push(`  class ${mermaidId} ${mermaidSafeIconClass};`);
    }

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
      if (isNetworkScopedNode(node)) {
        networkRootNodes.push(node);
      } else {
        otherRootNodes.push(node);
      }
    }

    for (const node of otherRootNodes) {
      renderNode(node, "    ");
    }

    if (networkRootNodes.length) {
      const networkGroups = new Map();
      for (const node of networkRootNodes) {
        const vnet = collectNodeVnet(node);
        const subnet = collectNodeSubnet(node);
        const networkKey = vnet ? `vnet::${normalizeGroupKey(vnet)}` : "__default_network__";
        if (!networkGroups.has(networkKey)) {
          networkGroups.set(networkKey, {
            label: vnet ? `Network: ${vnet}` : "🛡️ Networks / VNet",
            nodes: [],
            subnets: new Map(),
          });
        }
        const group = networkGroups.get(networkKey);
        if (subnet) {
          const subnetKey = `subnet::${normalizeGroupKey(subnet)}`;
          if (!group.subnets.has(subnetKey)) {
            group.subnets.set(subnetKey, {
              label: `Subnet: ${subnet}`,
              nodes: [],
            });
          }
          group.subnets.get(subnetKey).nodes.push(node);
        } else {
          group.nodes.push(node);
        }
      }

      const orderedNetworkGroups = Array.from(networkGroups.values()).sort((a, b) => a.label.localeCompare(b.label));
      for (const group of orderedNetworkGroups) {
        const networkGroupId = `${groupId}_network_${sanitizeMermaidId(group.label, `net_${autoIndex++}`)}`;
        lines.push(`    subgraph ${networkGroupId}["${escapeMermaidText(group.label)}"]`);
        for (const node of group.nodes) {
          renderNode(node, "      ");
        }
        const orderedSubnetGroups = Array.from(group.subnets.values()).sort((a, b) => a.label.localeCompare(b.label));
        for (const subnetGroup of orderedSubnetGroups) {
          const subnetGroupId = `${networkGroupId}_${sanitizeMermaidId(subnetGroup.label, `sub_${autoIndex++}`)}`;
          lines.push(`      subgraph ${subnetGroupId}["${escapeMermaidText(subnetGroup.label)}"]`);
          for (const node of subnetGroup.nodes) {
            renderNode(node, "        ");
          }
          lines.push("      end");
          subgraphStyleAssignments.push(`  style ${subnetGroupId} stroke:#94a3b8,stroke-width:2px,fill:none;`);
        }
        lines.push("    end");
        subgraphStyleAssignments.push(`  style ${networkGroupId} stroke:#1971c2,stroke-width:2px,fill:none;`);
      }
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
  mermaidOriginalIdByNodeId = new Map(
    Object.entries(payload?.id_map || {})
      .map(([compactId, originalId]) => [String(compactId || ""), String(originalId || "")])
      .filter(([compactId, originalId]) => compactId && originalId)
  );
  if (!mermaidOriginalIdByNodeId.size) {
    mermaidOriginalIdByNodeId = new Map(
      (Array.isArray(payload?.nodes) ? payload.nodes : [])
        .map((node) => [String(node?.id || ""), String(node?.id || "")])
        .filter(([id, originalId]) => id && originalId)
    );
  }
  applyMermaidCss(payload?.css_code || "");
  try {
    mermaidManualZoom = false;
    mermaidRootEl.dataset.diagramManualZoom = "false";
    const svg = await renderMermaidDiagram({
      source: mermaidSource,
      rootEl: mermaidRootEl,
      onRendered: async (svgEl) => {
        postProcessSvg(svgEl);
        addFirefoxViewBoxPadding(svgEl);
        enhancePlaceholderGlyphs(svgEl);
        applyEmojiIconFallback(svgEl);
        attachMermaidDrilldownHandlers(svgEl);
        ensureMermaidClickHandler(svgEl);
        await injectDiagramIconsIntoSvg(svgEl, "all");
        bindMermaidFitSync();
        scheduleMermaidDiagramFit();
        if (document.fonts?.ready) {
          document.fonts.ready.then(() => scheduleMermaidDiagramFit());
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
  const targetFilter = document.getElementById("ingress-diagram-div-target-filter");
  if (targetFilter) {
    targetFilter.hidden = activeViewMode !== "reactflow";
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
  const originalResourceId = resolveMermaidOriginalId(resourceId);
  const prefersChildDrilldown =
    armType.includes("applicationgateway") ||
    armType.includes("sites") ||
    armType.includes("serverfarms") ||
    armType.includes("hostingenvironments");
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
        openModal(originalResourceId, nodeData, {
          id: resolveMermaidOriginalId(resource.id),
          name: resource.name,
          resourceGroup: resource.rg,
          type: nodeData?.arm_type || nodeData?.type || nodeData?.resourceType || "",
          subscription: currentMermaidSubscriptionId,
          nodeId: resourceId,
        });
      });
      return;
    }
    openModal(originalResourceId, nodeData, {
      id: resolveMermaidOriginalId(resource.id),
      name: resource.name,
      resourceGroup: resource.rg,
      type: nodeData?.arm_type || nodeData?.type || nodeData?.resourceType || "",
      subscription: currentMermaidSubscriptionId,
      nodeId: resourceId,
    });
    return;
  }
  openModal(originalResourceId, nodeData, { nodeId: resourceId, id: originalResourceId });
}

function openDrilldownModal(nodeData, subId, fallback = null) {
  if (!modalOverlay || !subId) return;
  const controller = startModalRequest();

  // Carry diagram-level ingress/egress so renderModalContent can append the table.
  const withTrafficFlow = (data) => ({
    ingress: Array.isArray(nodeData?.ingress) ? nodeData.ingress : [],
    egress:  Array.isArray(nodeData?.egress)  ? nodeData.egress  : [],
    ...data,
  });

  const url = new URL(`/api/subscriptions/${encodeURIComponent(subId)}/drilldown`, window.location.origin);
  const remapNodeRef = (value) => resolveMermaidOriginalId(value);
  const remapResourceRefs = (items) => (Array.isArray(items) ? items.map((item) => {
    if (!item || typeof item !== "object") return item;
    const next = { ...item };
    if (next.id) next.id = remapNodeRef(next.id);
    if (next.parentNodeId) next.parentNodeId = remapNodeRef(next.parentNodeId);
    if (next.parent_id) next.parent_id = remapNodeRef(next.parent_id);
    if (next.resource_id) next.resource_id = remapNodeRef(next.resource_id);
    return next;
  }) : items);
  const nodePayload = nodeData && typeof nodeData === "object"
    ? {
        ...nodeData,
        id: remapNodeRef(nodeData.id),
        parentNodeId: remapNodeRef(nodeData.parentNodeId),
        resources: remapResourceRefs(nodeData.resources),
        items: remapResourceRefs(nodeData.items),
        members: remapResourceRefs(nodeData.members),
        children: remapResourceRefs(nodeData.children),
      }
    : nodeData;
  fetch(url.toString(), {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({
      arm_type: nodeData?.arm_type || nodeData?.type || "",
      resources: remapResourceRefs(nodeData?.resources || []),
      node: nodePayload || null,
    }),
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
        renderTabularModalContent(withTrafficFlow(data));
      } else {
        renderModalContent(withTrafficFlow(data));
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
  const resources = [
    ...(Array.isArray(nodeData?.resources) ? nodeData.resources : []),
    ...(Array.isArray(nodeData?.items) ? nodeData.items : []),
    ...(Array.isArray(nodeData?.members) ? nodeData.members : []),
    ...(Array.isArray(nodeData?.children) ? nodeData.children : []),
  ].filter(Boolean);
  const title = firstNonEmpty(nodeData?.title, nodeData?.label, "Grouped resources");
  const typeLabel = firstNonEmpty(nodeData?.typeLabel, nodeData?.arm_type, nodeData?.type);
  const resourceLabel = (resource) =>
    firstNonEmpty(
      resource?.name,
      resource?.label,
      resource?.title,
      resource?.resource_name,
      resource?.resourceName,
      resource?.display_name,
      resource?.displayName,
      resource?.id,
      "unnamed"
    );

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
            const name = escapeHtml(String(resourceLabel(resource)));
            const rg = escapeHtml(String(resource?.rg || ""));
            const kind = escapeHtml(String(resource?.type_label || resource?.type || resource?.arm_type || ""));
            const suffix = [rg ? `<span style="color: var(--text-muted);">(${rg})</span>` : "", kind ? `<span style="color: var(--text-faint); margin-left: 8px;">${kind}</span>` : ""].filter(Boolean).join("");
            return `<li class="cloud-arch-modal-list-item"><strong>${name}</strong>${suffix}</li>`;
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
  // Seed ingress/egress from the diagram's node_drilldown_map so the Traffic
  // Flow table is always populated even when the API response has no such keys.
  const withNodeContext = (payload = {}) => ({
    ingress: Array.isArray(nodeData?.ingress) ? nodeData.ingress : [],
    egress:  Array.isArray(nodeData?.egress)  ? nodeData.egress  : [],
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
    const primaryResource = resources[0] || {};
    const fqdn = firstNonEmpty(
      childData?.fqdn,
      childData?.configuration?.hostname,
      primaryResource?.fqdn,
      Array.isArray(childData?.dns_names) ? childData.dns_names[0] : "",
      Array.isArray(primaryResource?.dns_names) ? primaryResource.dns_names[0] : ""
    );
    children.push({
      id: childId,
      label: firstNonEmpty(childData?.title, childData?.label, childId),
      type: firstNonEmpty(childData?.typeLabel, childData?.resourceType, childData?.arm_type, childData?.type),
      fqdn,
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
    ...(Array.isArray(data?.associated_public_ips) ? data.associated_public_ips : []),
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
    data?.vnet_name,
    data?.parent_vnet_name
  );
  return value || "";
}

function collectVirtualNetworkType(data) {
  return firstNonEmpty(
    data?.network?.virtual_network_type,
    data?.network?.virtualNetworkType,
    data?.virtual_network_type,
    data?.virtualNetworkType
  );
}

function collectPublicNetworkAccess(data) {
  return firstNonEmpty(
    data?.security?.public_network_access,
    data?.security?.publicNetworkAccess,
    data?.network?.public_network_access,
    data?.network?.publicNetworkAccess,
    data?.public_network_access
  );
}

function collectIpRestrictions(data) {
  const value = data?.security?.ip_restrictions ?? data?.security?.ipRestrictions;
  if (Array.isArray(value)) {
    return value
      .map((item) => String(item || "").trim())
      .filter(Boolean);
  }
  if (typeof value === "string" && value.trim()) {
    return [value.trim()];
  }
  return [];
}

function collectSubnet(data) {
  const value = firstNonEmpty(
    data?.network?.subnet,
    data?.subnet,
    data?.subnetName,
    data?.subnet_name,
    data?.parent_subnet_name
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

function collectRoutingTargetDetails(data) {
  const candidates = [
    ...(Array.isArray(data?.routing_targets) ? data.routing_targets : []),
    ...(Array.isArray(data?.network?.routing_targets) ? data.network.routing_targets : []),
  ];
  const seen = new Set();
  const values = [];
  for (const item of candidates) {
    if (!item || typeof item !== "object") continue;
    const target = firstNonEmpty(item.target, item.name, item.target_resource_id);
    if (!target) continue;
    const backendPool = firstNonEmpty(item.backend_pool_name, item.backend_pool);
    const listenerName = firstNonEmpty(item.listener_name, item.listener);
    const urlPath = firstNonEmpty(item.url_path, item.path);
    const suffixParts = [];
    if (backendPool) suffixParts.push(`pool: ${backendPool}`);
    if (listenerName) suffixParts.push(`listener: ${listenerName}`);
    if (urlPath && urlPath !== "/*") suffixParts.push(`path: ${urlPath}`);
    const display = suffixParts.length ? `${target} (${suffixParts.join(", ")})` : target;
    const marker = display.toLowerCase();
    if (seen.has(marker)) continue;
    seen.add(marker);
    values.push(display);
  }
  return values;
}

function isAppGatewayDetails(data) {
  const typeLabel = String(firstNonEmpty(data?.type_label, data?.type, data?.resourceType, "")).toLowerCase();
  const resourceType = String(firstNonEmpty(data?.arm_type, data?.type, data?.resourceType, "")).toLowerCase();
  return (
    typeLabel.includes("app gateway") ||
    resourceType.includes("applicationgateways")
  );
}

function buildAppGatewayListenerTable(data) {
  const candidates = [
    ...(Array.isArray(data?.routing_targets) ? data.routing_targets : []),
    ...(Array.isArray(data?.network?.routing_targets) ? data.network.routing_targets : []),
  ];
  if (!candidates.length) {
    return "";
  }

  const grouped = new Map();
  for (const item of candidates) {
    if (!item || typeof item !== "object") continue;
    const listenerName = firstNonEmpty(item.listener_name, item.listener, item.name, "Listener");
    const protocol = firstNonEmpty(item.protocol, "HTTPS");
    const urlPath = firstNonEmpty(item.url_path, item.path, "/*");
    const backendPool = firstNonEmpty(item.backend_pool_name, item.backend_pool, "—");
    const wafPolicy = firstNonEmpty(item.waf_policy_name, item.waf_policy, "—");
    const key = [listenerName, protocol, urlPath, backendPool, wafPolicy].join("::").toLowerCase();
    if (!grouped.has(key)) {
      grouped.set(key, {
        listenerName,
        protocol,
        urlPath,
        backendPool,
        wafPolicy,
        targets: [],
        targetKeys: new Set(),
      });
    }
    const group = grouped.get(key);
    const targetName = firstNonEmpty(item.name, item.target, item.target_resource_name, item.target_resource_id);
    const targetValue = firstNonEmpty(item.target, item.target_resource_id, targetName);
    const display = targetName && targetValue && targetName !== targetValue
      ? `${targetName} (${targetValue})`
      : (targetName || targetValue || "—");
    const marker = display.toLowerCase();
    if (!group.targetKeys.has(marker)) {
      group.targetKeys.add(marker);
      group.targets.push(display);
    }
  }

  const rows = Array.from(grouped.values())
    .sort((a, b) => {
      const aKey = `${a.listenerName} ${a.urlPath} ${a.backendPool}`;
      const bKey = `${b.listenerName} ${b.urlPath} ${b.backendPool}`;
      return aKey.localeCompare(bKey);
    });

  if (!rows.length) return "";

  const renderTargetList = (targets) => {
    if (!targets.length) return "—";
    return `<ul class="cloud-arch-modal-list" style="margin:0;padding-left:18px;">${targets
      .map((target) => `<li class="cloud-arch-modal-list-item"><code>${escapeHtml(String(target))}</code></li>`)
      .join("")}</ul>`;
  };

  return `
    <div class="cloud-arch-modal-section">
      <div class="cloud-arch-modal-section-title">
        <span class="cloud-arch-modal-section-icon">📡</span>
        HTTP Listeners
      </div>
      <div class="cloud-arch-modal-subtitle" style="margin-bottom:10px;">Routing rules grouped by listener and URL path.</div>
      <div style="overflow:auto;border:1px solid var(--border);border-radius:8px;">
        <table style="width:100%;border-collapse:collapse;font-size:0.84rem;">
          <thead>
            <tr>
              ${["Listener", "Protocol", "URL Path", "Backend Pool", "Backend Targets", "WAF Policy"].map(
                (col) => `<th style="padding:8px 10px;text-align:left;background:var(--bg-base);border-bottom:1px solid var(--border);font-size:0.75rem;text-transform:uppercase;letter-spacing:0.03em;color:var(--text-muted);">${escapeHtml(col)}</th>`
              ).join("")}
            </tr>
          </thead>
          <tbody>
            ${rows.map((row) => `
              <tr style="border-bottom:1px solid var(--border);">
                <td style="padding:8px 10px;vertical-align:top;"><strong>${escapeHtml(row.listenerName)}</strong></td>
                <td style="padding:8px 10px;vertical-align:top;">${escapeHtml(row.protocol)}</td>
                <td style="padding:8px 10px;vertical-align:top;"><code>${escapeHtml(row.urlPath)}</code></td>
                <td style="padding:8px 10px;vertical-align:top;"><code>${escapeHtml(row.backendPool)}</code></td>
                <td style="padding:8px 10px;vertical-align:top;">${renderTargetList(row.targets)}</td>
                <td style="padding:8px 10px;vertical-align:top;">${row.wafPolicy && row.wafPolicy !== "—" ? `<code>${escapeHtml(row.wafPolicy)}</code>` : "—"}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    </div>
  `;
}

function isWafPolicyDetails(data) {
  const typeLabel = String(firstNonEmpty(data?.type_label, data?.type, data?.resourceType, "")).toLowerCase();
  return typeLabel.includes("waf policy") || Boolean(data?.waf_policy);
}

function formatManagedRuleSet(ruleSet = {}) {
  const type = firstNonEmpty(ruleSet?.type, ruleSet?.ruleSetType, "Managed ruleset");
  const version = firstNonEmpty(ruleSet?.version, ruleSet?.ruleSetVersion);
  return version ? `${type} ${version}` : type;
}

function buildParentResourceFields(parentResource) {
  const fields = [];
  const parentNetwork = parentResource?.network && typeof parentResource.network === "object" ? parentResource.network : null;
  const parentVnet = firstNonEmpty(
    parentNetwork?.vnet,
    parentResource?.vnet,
    parentResource?.vnet_name,
    parentResource?.vnetName
  );
  const parentSubnet = firstNonEmpty(
    parentNetwork?.subnet,
    parentResource?.subnet,
    parentResource?.subnet_name,
    parentResource?.subnetName
  );
  const parentNetworkType = firstNonEmpty(
    parentNetwork?.virtual_network_type,
    parentNetwork?.virtualNetworkType,
    parentResource?.virtual_network_type
  );

  if (parentResource?.name) fields.push(`<div class="cloud-arch-modal-field"><div class="cloud-arch-modal-field-label">Asset Name</div><div class="cloud-arch-modal-field-value">${escapeHtml(String(parentResource.name))}</div></div>`);
  if (parentResource?.type_label || parentResource?.type) fields.push(`<div class="cloud-arch-modal-field"><div class="cloud-arch-modal-field-label">Service Type</div><div class="cloud-arch-modal-field-value">${escapeHtml(String(parentResource.type_label || parentResource.type))}</div></div>`);
  if (parentResource?.resource_group) fields.push(`<div class="cloud-arch-modal-field"><div class="cloud-arch-modal-field-label">Resource Group</div><div class="cloud-arch-modal-field-value">${escapeHtml(String(parentResource.resource_group))}</div></div>`);
  if (parentResource?.location) fields.push(`<div class="cloud-arch-modal-field"><div class="cloud-arch-modal-field-label">Location</div><div class="cloud-arch-modal-field-value">${escapeHtml(String(parentResource.location))}</div></div>`);
  if (parentResource?.sku) fields.push(`<div class="cloud-arch-modal-field"><div class="cloud-arch-modal-field-label">SKU</div><div class="cloud-arch-modal-field-value">${escapeHtml(String(parentResource.sku))}</div></div>`);
  if (parentVnet) fields.push(`<div class="cloud-arch-modal-field"><div class="cloud-arch-modal-field-label">Inherited Virtual Network</div><div class="cloud-arch-modal-field-value">${escapeHtml(parentVnet)}</div></div>`);
  if (parentSubnet) fields.push(`<div class="cloud-arch-modal-field"><div class="cloud-arch-modal-field-label">Inherited Subnet</div><div class="cloud-arch-modal-field-value">${escapeHtml(parentSubnet)}</div></div>`);
  if (parentNetworkType) fields.push(`<div class="cloud-arch-modal-field"><div class="cloud-arch-modal-field-label">Inherited Virtual Network Type</div><div class="cloud-arch-modal-field-value">${escapeHtml(parentNetworkType)}</div></div>`);
  const parentDnsNames = collectFqdns(parentResource);
  if (parentDnsNames.length > 0) {
    fields.push(`<div class="cloud-arch-modal-field cloud-arch-modal-field--full"><div class="cloud-arch-modal-field-label">${parentDnsNames.length > 1 ? "DNS Names" : "DNS Name"}</div><div class="cloud-arch-modal-field-value">${parentDnsNames.map((fqdn) => `<code>${escapeHtml(String(fqdn))}</code>`).join("<br/>")}</div></div>`);
  }
  return fields.join("");
}

function buildTriggersSection(data) {
  const triggers = Array.isArray(data?.triggers) ? data.triggers.filter(Boolean) : [];
  if (!triggers.length) return "";

  const rows = triggers.map((trigger) => {
    const kind = firstNonEmpty(trigger?.kind, trigger?.trigger_type, trigger?.type, "Trigger");
    const kindLower = kind.toLowerCase();
    const functionName = firstNonEmpty(trigger?.function_name, trigger?.functionName, trigger?.name, "—");
    const binding = kindLower.includes("http")
      ? firstNonEmpty(trigger?.binding, trigger?.route, trigger?.endpoint, "—")
      : firstNonEmpty(
          trigger?.binding,
          trigger?.entity_name,
          trigger?.entityName,
          trigger?.subscription_name,
          trigger?.subscriptionName,
          "—"
        );
    const details = [];

    if (kindLower.includes("http")) {
      const methods = Array.isArray(trigger?.methods)
        ? trigger.methods.map((method) => String(method || "").trim()).filter(Boolean)
        : typeof trigger?.methods === "string" && trigger.methods.trim()
          ? [trigger.methods.trim()]
          : [];
      if (trigger?.auth_level) details.push(`auth: ${trigger.auth_level}`);
      if (methods.length) details.push(`methods: ${methods.join(", ")}`);
      if (trigger?.endpoint) details.push(`endpoint: ${trigger.endpoint}`);
    } else {
      if (trigger?.entity_type) details.push(`entity: ${trigger.entity_type}`);
      if (trigger?.subscription_name) details.push(`subscription: ${trigger.subscription_name}`);
      if (trigger?.connection) details.push(`connection: ${trigger.connection}`);
    }

    return {
      kind,
      functionName,
      binding,
      details: details.length ? details.join("; ") : "—",
    };
  });

  return `
    <div class="cloud-arch-modal-section">
      <div class="cloud-arch-modal-section-title">
        <span class="cloud-arch-modal-section-icon">⚡</span>
        Triggers
      </div>
      <div style="overflow:auto;border:1px solid var(--border);border-radius:8px;">
        <table style="width:100%;border-collapse:collapse;font-size:0.84rem;">
          <thead>
            <tr>
              ${["Type", "Function", "Binding", "Details"].map(
                (col) => `<th style="padding:8px 10px;text-align:left;background:var(--bg-base);border-bottom:1px solid var(--border);font-size:0.75rem;text-transform:uppercase;letter-spacing:0.03em;color:var(--text-muted);">${escapeHtml(col)}</th>`
              ).join("")}
            </tr>
          </thead>
          <tbody>
            ${rows.map((row) => `
              <tr style="border-bottom:1px solid var(--border);">
                <td style="padding:8px 10px;vertical-align:top;"><strong>${escapeHtml(row.kind)}</strong></td>
                <td style="padding:8px 10px;vertical-align:top;">${escapeHtml(row.functionName)}</td>
                <td style="padding:8px 10px;vertical-align:top;"><code>${escapeHtml(row.binding)}</code></td>
                <td style="padding:8px 10px;vertical-align:top;">${escapeHtml(row.details)}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    </div>
  `;
}

function isApimServiceDetails(data) {
  const typeText = String(firstNonEmpty(data?.arm_type, data?.type, data?.resourceType, data?.type_label, "")).toLowerCase();
  return typeText.includes("apimanagement") || typeText.includes("api management");
}

function buildApimBackendsSection(data) {
  const apis = Array.isArray(data?.apis) ? data.apis.filter(Boolean) : [];
  const backends = Array.isArray(data?.backends) ? data.backends.filter(Boolean) : [];
  if (!apis.length && !backends.length) return "";

  const isApiMode = apis.length > 0;
  const rows = [...(isApiMode ? apis : backends)].sort((a, b) => {
    const aKey = isApiMode
      ? `${firstNonEmpty(a?.api_display_name, a?.api_name, "")} ${firstNonEmpty(a?.backend_target, "")}`
      : `${firstNonEmpty(a?.name, a?.backend_id, "")} ${firstNonEmpty(a?.type, "")}`;
    const bKey = isApiMode
      ? `${firstNonEmpty(b?.api_display_name, b?.api_name, "")} ${firstNonEmpty(b?.backend_target, "")}`
      : `${firstNonEmpty(b?.name, b?.backend_id, "")} ${firstNonEmpty(b?.type, "")}`;
    return aKey.localeCompare(bKey);
  });

  const renderRuntimeUrl = (value) => {
    const url = String(value || "").trim();
    if (!url || url === "—") return "—";
    const escaped = escapeHtml(url);
    if (/^https?:\/\//i.test(url)) {
      return `<a href="${escaped}" target="_blank" rel="noopener noreferrer"><code>${escaped}</code></a>`;
    }
    return `<code>${escaped}</code>`;
  };

  const renderApiTarget = (api) => {
    const target = firstNonEmpty(api?.backend_target, api?.backend_id, api?.backend_url, api?.service_url, "—");
    const backendUrl = firstNonEmpty(api?.backend_url, "");
    const serviceUrl = firstNonEmpty(api?.service_url, "");
    const bits = [];
    if (backendUrl && backendUrl !== "—") bits.push(`<div style="color:var(--text-muted);font-size:0.75rem;"><code>${escapeHtml(backendUrl)}</code></div>`);
    if (serviceUrl && serviceUrl !== "—" && serviceUrl !== backendUrl) bits.push(`<div style="color:var(--text-faint);font-size:0.75rem;"><code>${escapeHtml(serviceUrl)}</code></div>`);
    return `<div><strong>${escapeHtml(target)}</strong>${bits.join("")}</div>`;
  };

  return `
    <div class="cloud-arch-modal-section" data-apim-section>
      <div class="cloud-arch-modal-section-title">
        <span class="cloud-arch-modal-section-icon">🔌</span>
        ${isApiMode ? "APIs and Backend Targets" : "Backends"}
      </div>
      <div class="cloud-arch-modal-subtitle" style="margin-bottom:10px;">
        ${isApiMode ? "Search by API name or backend target." : "Search by backend name or URL."}
      </div>
      <div style="margin-bottom:10px;">
        <input
          type="search"
          data-apim-search
          placeholder="${isApiMode ? "Filter APIs or backend targets…" : "Filter backends…"}"
          style="width:100%;padding:8px 10px;border:1px solid var(--border);border-radius:8px;background:var(--bg-base);color:var(--text);"
        />
      </div>
      <div style="overflow:auto;border:1px solid var(--border);border-radius:8px;">
        <table style="width:100%;border-collapse:collapse;font-size:0.84rem;">
          <thead>
            <tr>
              ${(isApiMode
                ? ["API", "Path", "Backend Target", "Subscription"]
                : ["Backend Name", "Description", "Type", "Runtime URL"]
              ).map(
                (col) => `<th style="padding:8px 10px;text-align:left;background:var(--bg-base);border-bottom:1px solid var(--border);font-size:0.75rem;text-transform:uppercase;letter-spacing:0.03em;color:var(--text-muted);">${escapeHtml(col)}</th>`
              ).join("")}
            </tr>
          </thead>
          <tbody>
            ${rows.map((item) => {
              if (isApiMode) {
                const apiName = firstNonEmpty(item?.api_display_name, item?.api_name, "—");
                const apiPath = firstNonEmpty(item?.api_path, "—");
                const backendTarget = firstNonEmpty(item?.backend_target, item?.backend_id, item?.backend_url, item?.service_url, "—");
                const subscription = item?.requires_subscription ? "Required" : "Not required";
                const searchText = [
                  apiName,
                  item?.api_name,
                  apiPath,
                  backendTarget,
                  item?.backend_url,
                  item?.service_url,
                  subscription,
                ]
                  .map((value) => String(value || "").toLowerCase())
                  .join(" ");
                return `
                  <tr data-apim-row data-search-text="${escapeHtml(searchText)}" style="border-bottom:1px solid var(--border);">
                    <td style="padding:8px 10px;vertical-align:top;"><strong>${escapeHtml(apiName)}</strong></td>
                    <td style="padding:8px 10px;vertical-align:top;"><code>${escapeHtml(apiPath)}</code></td>
                    <td style="padding:8px 10px;vertical-align:top;">${renderApiTarget(item)}</td>
                    <td style="padding:8px 10px;vertical-align:top;">${subscription}</td>
                  </tr>
                `;
              }
              return `
                <tr data-apim-row data-search-text="${escapeHtml([
                  item?.name,
                  item?.backend_id,
                  item?.description,
                  item?.type,
                  item?.runtime_url,
                  item?.url,
                  item?.protocol,
                ].map((value) => String(value || "").toLowerCase()).join(" "))}" style="border-bottom:1px solid var(--border);">
                  <td style="padding:8px 10px;vertical-align:top;"><strong>${escapeHtml(firstNonEmpty(item?.name, item?.backend_id, "—"))}</strong></td>
                  <td style="padding:8px 10px;vertical-align:top;color:var(--text-muted);">${escapeHtml(firstNonEmpty(item?.description, "—"))}</td>
                  <td style="padding:8px 10px;vertical-align:top;"><code>${escapeHtml(firstNonEmpty(item?.type, "Custom URL"))}</code></td>
                  <td style="padding:8px 10px;vertical-align:top;">${renderRuntimeUrl(firstNonEmpty(item?.runtime_url, item?.url, "—"))}</td>
                </tr>
              `;
            }).join("")}
          </tbody>
        </table>
      </div>
      ${isApiMode ? '<div data-apim-empty-filter class="cloud-arch-modal-empty" style="display:none;margin-top:10px;">No APIs match this filter.</div>' : ""}
    </div>
  `;
}

function bindApimSectionSearch() {
  if (!modalBody) return;
  const search = modalBody.querySelector("[data-apim-search]");
  const rows = Array.from(modalBody.querySelectorAll("[data-apim-row]"));
  const empty = modalBody.querySelector("[data-apim-empty-filter]");
  if (!search || !rows.length) return;

  const update = () => {
    const query = String(search.value || "").trim().toLowerCase();
    let visible = 0;
    for (const row of rows) {
      const searchText = String(row.dataset.searchText || "").toLowerCase();
      const show = !query || searchText.includes(query);
      row.hidden = !show;
      if (show) visible += 1;
    }
    if (empty) {
      empty.style.display = visible ? "none" : "block";
    }
  };

  search.addEventListener("input", update);
  update();
}

function isApimBackendTargetDetails(data) {
  const typeText = String(firstNonEmpty(data?.type_label, data?.type, data?.resourceType, data?.configuration?.type, "")).toLowerCase();
  return typeText.includes("apim backend target") || typeText.includes("apim backend pool");
}

function buildApimBackendUsageSection(data) {
  const apimName = firstNonEmpty(
    data?.configuration?.apim_name,
    data?.parent_resource?.name,
    data?.parent_resource?.label
  );
  const routes = Array.isArray(data?.network?.routing_targets)
    ? data.network.routing_targets.filter(Boolean)
    : [];
  if (!routes.length) return "";

  const rows = [...routes].sort((a, b) => {
    const aKey = `${firstNonEmpty(a?.api_display_name, a?.api_name, "")} ${firstNonEmpty(a?.api_path, "")}`;
    const bKey = `${firstNonEmpty(b?.api_display_name, b?.api_name, "")} ${firstNonEmpty(b?.api_path, "")}`;
    return aKey.localeCompare(bKey);
  });

  return `
    <div class="cloud-arch-modal-section">
      <div class="cloud-arch-modal-section-title">
        <span class="cloud-arch-modal-section-icon">🧭</span>
        APIs Using This Backend
      </div>
      ${apimName ? `<div class="cloud-arch-modal-subtitle" style="margin-bottom:10px;">APIM: ${escapeHtml(apimName)}</div>` : ""}
      <div style="overflow:auto;border:1px solid var(--border);border-radius:8px;">
        <table style="width:100%;border-collapse:collapse;font-size:0.84rem;">
          <thead>
            <tr>
              ${["API", "Display Name", "Path", "Requires Subscription"].map(
                (col) => `<th style="padding:8px 10px;text-align:left;background:var(--bg-base);border-bottom:1px solid var(--border);font-size:0.75rem;text-transform:uppercase;letter-spacing:0.03em;color:var(--text-muted);">${escapeHtml(col)}</th>`
              ).join("")}
            </tr>
          </thead>
          <tbody>
            ${rows.map((route) => {
              const apiName = firstNonEmpty(route?.api_name, route?.name, "—");
              const apiDisplayName = firstNonEmpty(route?.api_display_name, apiName, "—");
              const apiPath = firstNonEmpty(route?.api_path, "—");
              const requiresSubscription = route?.requires_subscription ? "Yes" : "No";
              return `
                <tr style="border-bottom:1px solid var(--border);">
                  <td style="padding:8px 10px;vertical-align:top;"><strong>${escapeHtml(apiName)}</strong></td>
                  <td style="padding:8px 10px;vertical-align:top;">${escapeHtml(apiDisplayName)}</td>
                  <td style="padding:8px 10px;vertical-align:top;"><code>${escapeHtml(apiPath)}</code></td>
                  <td style="padding:8px 10px;vertical-align:top;">${requiresSubscription}</td>
                </tr>
              `;
            }).join("")}
          </tbody>
        </table>
      </div>
    </div>
  `;
}

/**
 * Build the Traffic Flow HTML section (ingress/egress table) from diagram node data.
 * Returns an empty string when both arrays are empty.
 */
function buildTrafficFlowSection(data) {
  const ingressItems = Array.isArray(data?.ingress) ? data.ingress : [];
  const egressItems  = Array.isArray(data?.egress)  ? data.egress  : [];
  if (!ingressItems.length && !egressItems.length) return "";

  // Strip any residual Mermaid HTML tags (e.g. <br/>) from label strings.
  const plainText = (s) => String(s || "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();

  const tableRows = [
    ...ingressItems.map((item) => ({ direction: "ingress", item })),
    ...egressItems.map((item) => ({ direction: "egress", item })),
  ];
  return `
    <div class="cloud-arch-modal-section">
      <div class="cloud-arch-modal-section-title">
        <span class="cloud-arch-modal-section-icon">🔀</span>
        Traffic Flow
      </div>
      <div class="cloud-arch-modal-grid">
        <div class="cloud-arch-modal-field cloud-arch-modal-field--full">
          <div style="overflow:auto;border:1px solid var(--border);border-radius:8px;">
            <table style="width:100%;border-collapse:collapse;font-size:0.84rem;">
              <thead>
                <tr>
                  <th style="padding:8px 10px;text-align:left;background:var(--bg-base);border-bottom:1px solid var(--border);font-size:0.75rem;text-transform:uppercase;letter-spacing:0.03em;color:var(--text-muted);width:90px;">Direction</th>
                  <th style="padding:8px 10px;text-align:left;background:var(--bg-base);border-bottom:1px solid var(--border);font-size:0.75rem;text-transform:uppercase;letter-spacing:0.03em;color:var(--text-muted);">Node</th>
                  <th style="padding:8px 10px;text-align:left;background:var(--bg-base);border-bottom:1px solid var(--border);font-size:0.75rem;text-transform:uppercase;letter-spacing:0.03em;color:var(--text-muted);">Connection</th>
                </tr>
              </thead>
              <tbody>
                ${tableRows.map(({ direction, item }) => {
                  const lbl  = escapeHtml(plainText(item.label || item.node_id || "Unknown"));
                  const conn = item.edge_label ? escapeHtml(plainText(item.edge_label)) : "—";
                  const dirLabel = direction === "ingress"
                    ? `<span style="color:#60a5fa;">← Ingress</span>`
                    : `<span style="color:#34d399;">→ Egress</span>`;
                  return `
                    <tr style="border-bottom:1px solid var(--border);">
                      <td style="padding:8px 10px;vertical-align:top;white-space:nowrap;">${dirLabel}</td>
                      <td style="padding:8px 10px;vertical-align:top;"><strong>${lbl}</strong></td>
                      <td style="padding:8px 10px;vertical-align:top;color:var(--text-muted);">${conn}</td>
                    </tr>`;
                }).join("")}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>`;
}

function renderTabularModalContent(data) {
  if (!modalOverlay || !modalTitle || !modalBody) return;
  modalOverlay.hidden = false;
  modalTitle.textContent = firstNonEmpty(data?.__node_label, data?.title, data?.name, "Details");
  if (modalSubtitle) modalSubtitle.textContent = "";
  setModalHeaderIcon(data?.icon_path || data?.parent_resource?.icon_path || "", "☁");
  const suppressParentHeading =
    String(firstNonEmpty(data?.title, data?.name, "")).toLowerCase() === "simulation-knowledgecentre-uksouth" &&
    String(firstNonEmpty(data?.type_label, data?.type, data?.resourceType, "")).toLowerCase().includes("app service plan");

  const parentResource = data?.parent_resource && typeof data.parent_resource === "object" ? data.parent_resource : null;
  const parentResourceSection = parentResource
    ? `
      <div class="cloud-arch-modal-section">
        ${suppressParentHeading ? "" : `
        <div class="cloud-arch-modal-section-title">
          <span class="cloud-arch-modal-section-icon">🧭</span>
          Parent Resource
        </div>`}
        <div class="cloud-arch-modal-grid">
          ${buildParentResourceFields(parentResource)}
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
  modalTitle.textContent = firstNonEmpty(data.__node_label, data.title, data.name, "Resource details");
  if (modalSubtitle) {
    modalSubtitle.textContent = firstNonEmpty(data.type_label, data.type, data.resourceType);
  }
  setModalHeaderIcon(data.icon_path || data.parent_resource?.icon_path || "", "☁");

  if (isWafPolicyDetails(data)) {
    const wafPolicy = data?.waf_policy || {};
    const associatedGateways = Array.isArray(wafPolicy.associated_gateways) ? wafPolicy.associated_gateways : [];
    const managedRuleSets = Array.isArray(wafPolicy.managed_rule_sets) ? wafPolicy.managed_rule_sets : [];
    const sections = [];

    const overviewFields = [];
    if (data.name) overviewFields.push({ label: "Policy Name", value: escapeHtml(String(data.name)) });
    if (data.resource_group) overviewFields.push({ label: "Resource Group", value: escapeHtml(String(data.resource_group)) });
    if (data.subscription) overviewFields.push({ label: "Subscription", value: escapeHtml(String(data.subscription)) });
    if (data.configuration?.state) overviewFields.push({ label: "State", value: escapeHtml(String(data.configuration.state)) });
    if (data.configuration?.mode) overviewFields.push({ label: "Mode", value: escapeHtml(String(data.configuration.mode)) });
    if (overviewFields.length > 0) {
      sections.push({ title: "Policy Overview", icon: "🧩", fields: overviewFields });
    }

    const securityFields = [];
    securityFields.push({
      label: "Managed Rulesets",
      value: managedRuleSets.length
        ? `<span class="cloud-arch-modal-badge cloud-arch-modal-badge--success">Enabled</span>`
        : `<span class="cloud-arch-modal-badge cloud-arch-modal-badge--muted">Disabled</span>`,
      isHtml: true,
    });
    securityFields.push({
      label: "Custom Rules",
      value: `${Number(wafPolicy.custom_rules_count ?? data?.security?.custom_rules_count ?? 0)} configured`,
    });
    securityFields.push({
      label: "Exclusions",
      value: `${Number(wafPolicy.exclusions_count ?? data?.security?.exclusions_count ?? 0)} configured`,
    });
    if (wafPolicy.request_body_check !== undefined && wafPolicy.request_body_check !== null) {
      securityFields.push({
        label: "Request Body Check",
        value: wafPolicy.request_body_check ? "Enabled" : "Disabled",
      });
    }
    if (wafPolicy.max_body_kb !== undefined && wafPolicy.max_body_kb !== null && String(wafPolicy.max_body_kb).trim() !== "") {
      securityFields.push({
        label: "Max Body Size",
        value: `${escapeHtml(String(wafPolicy.max_body_kb))} KB`,
      });
    }
    if (securityFields.length > 0) {
      sections.push({ title: "Policy Security", icon: "🛡️", fields: securityFields });
    }

    sections.push({
      title: "Associated Resources",
      icon: "🔗",
      fields: [
        {
          label: "Gateways Using This Policy",
          value: associatedGateways.length
            ? `<ul class="cloud-arch-modal-list">${associatedGateways
              .map((gateway) => `<li class="cloud-arch-modal-list-item"><strong>${escapeHtml(String(gateway))}</strong></li>`)
              .join("")}</ul>`
            : '<div class="cloud-arch-modal-empty" style="margin:0;">No gateways are associated with this policy.</div>',
          isHtml: true,
        },
      ],
    });

    if (managedRuleSets.length) {
      sections.push({
        title: "Managed Rulesets",
        icon: "📚",
        fields: [
          {
            label: "Rule Sets",
            value: managedRuleSets.map((ruleSet) => `<code>${escapeHtml(formatManagedRuleSet(ruleSet))}</code>`).join("<br/>"),
            isHtml: true,
          },
        ],
      });
    }

    modalBody.innerHTML = sections
      .map((section) => `
        <div class="cloud-arch-modal-section">
          <div class="cloud-arch-modal-section-title">
            <span class="cloud-arch-modal-section-icon">${section.icon}</span>
            ${section.title}
          </div>
          <div class="cloud-arch-modal-grid">
            ${(section.fields || []).map((field) => `
              <div class="cloud-arch-modal-field">
                <div class="cloud-arch-modal-field-label">${field.label}</div>
                <div class="cloud-arch-modal-field-value">${field.isHtml ? field.value : field.value}</div>
              </div>
            `).join("")}
          </div>
        </div>
      `)
      .join("");
    return;
  }

  const suppressParentHeading =
    String(firstNonEmpty(data.title, data.name, "")).toLowerCase() === "simulation-knowledgecentre-uksouth" &&
    String(firstNonEmpty(data.type_label, data.type, data.resourceType, "")).toLowerCase().includes("app service plan");
  const resourceName = firstNonEmpty(data.__node_label, data.name, data.title, data.resource_name);
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
  const routingTargetDetails = collectRoutingTargetDetails(data);
  const vnet = collectVnet(data);
  const subnet = collectSubnet(data);
  const virtualNetworkType = collectVirtualNetworkType(data);
  const publicNetworkAccess = collectPublicNetworkAccess(data);
  const ipRestrictions = collectIpRestrictions(data);
  const childNodes = Array.isArray(data.__node_children) ? data.__node_children : [];
  const parentResource = data.parent_resource && typeof data.parent_resource === "object" ? data.parent_resource : null;
  const parentResourceSection = parentResource
    ? `
      <div class="cloud-arch-modal-section">
        ${suppressParentHeading ? "" : `
        <div class="cloud-arch-modal-section-title">
          <span class="cloud-arch-modal-section-icon">🧭</span>
          Parent Resource
        </div>`}
        <div class="cloud-arch-modal-grid">
          ${buildParentResourceFields(parentResource)}
        </div>
      </div>
    `
    : "";

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
      label: fqdns.length > 1 ? "DNS Names" : "DNS Name",
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
  if (virtualNetworkType) networkFields.push({ label: "Virtual Network Type", value: escapeHtml(virtualNetworkType) });
  if (publicNetworkAccess) networkFields.push({ label: "Public Network Access", value: escapeHtml(publicNetworkAccess) });
  if (ipRestrictions.length > 0) {
    networkFields.push({
      label: "IP Restrictions",
      value: ipRestrictions.map((value) => `<code>${escapeHtml(value)}</code>`).join("<br/>"),
      isHtml: true,
    });
  }
  if (routingTargetDetails.length > 0) {
    if (!isAppGatewayDetails(data)) {
      networkFields.push({
        label: "Routes To",
        value: routingTargetDetails.map((target) => `<code>${escapeHtml(target)}</code>`).join("<br/>"),
        isHtml: true,
      });
    }
  }
  if (vnet) networkFields.push({ label: "Virtual Network", value: escapeHtml(vnet) });
  if (subnet) networkFields.push({ label: "Subnet", value: escapeHtml(subnet) });
  if (networkFields.length > 0) {
    sections.push({ title: "Network", icon: "🌐", fields: networkFields });
  }

  const apimBackendsSection = isApimServiceDetails(data) ? buildApimBackendsSection(data) : "";
  if (apimBackendsSection) {
    sections.push({
      title: "",
      icon: "",
      fields: [],
      __rawHtml: apimBackendsSection,
    });
  }

  const apimBackendUsageSection = isApimBackendTargetDetails(data) ? buildApimBackendUsageSection(data) : "";
  if (apimBackendUsageSection) {
    sections.push({
      title: "",
      icon: "",
      fields: [],
      __rawHtml: apimBackendUsageSection,
    });
  }

  const listenerTable = isAppGatewayDetails(data) ? buildAppGatewayListenerTable(data) : "";
  if (listenerTable) {
    sections.push({
      title: "HTTP Listeners",
      icon: "📡",
      fields: [
        {
          label: "Routing Rules",
          value: listenerTable,
          isHtml: true,
        },
      ],
    });
  }

  const triggerSection = buildTriggersSection(data);
  if (triggerSection) {
    sections.push({
      title: "",
      icon: "",
      fields: [],
      __rawHtml: triggerSection,
    });
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
              const fqdn = child.fqdn ? `<code>${escapeHtml(child.fqdn)}</code>` : "";
              const rg = child.resourceGroup ? `<span style="color: var(--text-muted);">(${escapeHtml(child.resourceGroup)})</span>` : "";
              const count = child.resourcesCount > 1 ? ` <span class="cloud-arch-modal-badge cloud-arch-modal-badge--info">${child.resourcesCount} resources</span>` : "";
              return `<li class="cloud-arch-modal-list-item"><strong>${escapeHtml(child.label)}</strong>${type ? ` • ${type}` : ""}${fqdn ? ` • ${fqdn}` : ""} ${rg}${count}</li>`;
            })
            .join("")}</ul>`,
          isHtml: true,
        },
      ],
    });
  }

  if (!sections.length && !parentResourceSection) {
    modalBody.innerHTML = '<div class="cloud-arch-modal-empty">No core resource details found for this node.</div>';
    return;
  }

  modalBody.innerHTML = `${parentResourceSection}${sections
    .map(
      (section) => {
        if (section.__rawHtml) return section.__rawHtml;
        return `
      <div class="cloud-arch-modal-section">
        ${section.title ? `
        <div class="cloud-arch-modal-section-title">
          <span class="cloud-arch-modal-section-icon">${section.icon}</span>
          ${section.title}
        </div>` : ""}
        <div class="cloud-arch-modal-grid">
          ${(section.fields || [])
            .map(
              (field) => `
            <div class="cloud-arch-modal-field${field.fullWidth ? " cloud-arch-modal-field--full" : ""}">
              ${field.label ? `<div class="cloud-arch-modal-field-label">${field.label}</div>` : ""}
              <div class="cloud-arch-modal-field-value">${field.isHtml ? field.value : field.value}</div>
            </div>
          `
            )
            .join("")}
        </div>
      </div>
    `
      }
    )
    .join("")}`;
  bindApimSectionSearch();
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
  mermaidManualZoom = false;
  if (mermaidRootEl) mermaidRootEl.dataset.diagramManualZoom = "false";
  cancelMermaidDiagramFit();

  const cacheBust = () => `t=${Date.now()}`;
  const withCacheBust = (url) => `${url}${url.includes("?") ? "&" : "?"}${cacheBust()}`;

  const resolveSubscriptionId = async (selector) => {
    const raw = String(selector || "").trim();
    if (!raw) return "";
    try {
      const resp = await fetch(withCacheBust("/api/subscriptions"), {
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
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
        withCacheBust(`/api/subscriptions/${encodeURIComponent(subscriptionId)}/diagram`),
        { headers: { Accept: "application/json" }, cache: "no-store" }
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
        withCacheBust(`/api/cloud/architecture?sub=${encodeURIComponent(subscriptionName)}&view=mermaid`),
        { headers: { Accept: "application/json" }, cache: "no-store" }
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
      mermaidManualZoom = false;
      if (mermaidRootEl) mermaidRootEl.dataset.diagramManualZoom = "false";
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
      container.dataset.diagramManualZoom = "true";
      mermaidManualZoom = true;
      cancelMermaidDiagramFit();
      if (typeof window.__triageDiagramZoomStateChanged === "function") {
        window.__triageDiagramZoomStateChanged(container, "zoom");
      }
    }
  }, { passive: false });
})();

if (mermaidViewEl) {
  mermaidViewEl.hidden = activeViewMode !== "mermaid";
}

syncViewButtons();
