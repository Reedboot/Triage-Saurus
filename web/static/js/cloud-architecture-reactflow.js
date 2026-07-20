import {
  CONFIG,
  PROVIDER_THEMES,
  normalizeViewMode,
  viewModeLabel,
  themeFor,
  readJsonResponse,
  escapeHtml,
} from "./cloud-architecture-shared.js";

const React = window.React;
const { createRoot } = window.ReactDOM;
const { Background, Controls, Handle, MarkerType, MiniMap, Position, ReactFlow } = window.ReactFlow;
const { useCallback, useEffect, useState } = React;

const h = React.createElement;
const rootEl = document.getElementById("cloud-arch-root");
const emptyEl = document.getElementById("cloud-arch-empty");
const summaryLineEl = document.getElementById("cloud-arch-summary-line");
const legendEl = document.getElementById("cloud-arch-provider-legend");
const missingAssetsEl = document.getElementById("cloud-arch-missing-assets");
const errorCardEl = document.getElementById("cloud-arch-error-card");
const errorEl = document.getElementById("cloud-arch-error");
const formEl = document.getElementById("cloud-arch-form");
const subscriptionInput = document.getElementById("subscription-input");
const mermaidViewEl = document.getElementById("cloud-arch-mermaid-view");
const mermaidRootEl = document.getElementById("cloud-arch-mermaid-root");
const viewButtons = Array.from(document.querySelectorAll("[data-cloud-arch-view]"));
const INITIAL_VIEW_MODE = (CONFIG.initialViewMode || "mermaid").toLowerCase();

let activeViewMode = normalizeViewMode(INITIAL_VIEW_MODE);
let currentMermaidSubscriptionId = "";
if (rootEl) {
  rootEl.hidden = activeViewMode === "mermaid";
}

function cacheBustUrl(url) {
  const stamp = `t=${Date.now()}`;
  return `${url}${url.includes("?") ? "&" : "?"}${stamp}`;
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

function isNoisyMonitoringAsset(asset) {
  const typeKey = String(asset?.type || asset?.resourceType || asset?.type_label || "").toLowerCase();
  return (
    typeKey.endsWith("microsoft.insights/actiongroups") ||
    typeKey.endsWith("microsoft.insights/components") ||
    typeKey.includes("/actiongroups") ||
    typeKey.includes("/components")
  );
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
      parentNodeId: parentId || null,
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

function reflowVisibleTree(nodes, expandedNodes) {
  const hierarchy = buildHierarchyContext(nodes);
  const expandedSet = expandedNodes instanceof Set ? expandedNodes : new Set(expandedNodes || []);
  const nodeById = new Map(nodes.map((node) => [String(node?.id || ""), node]));
  const visibleIds = new Set(nodes.filter((node) => !node.hidden).map((node) => String(node?.id || "")));
  const sizeById = new Map();
  const subtreeSizeById = new Map();
  const gapX = 80;
  const gapY = 28;

  for (const node of nodes) {
    const id = String(node?.id || "").trim();
    if (!id) continue;
    sizeById.set(id, {
      width: Number(node?.style?.width || 340),
      height: Number(node?.style?.minHeight || node?.style?.height || 132),
    });
  }

  const subtreeSize = (id) => {
    if (subtreeSizeById.has(id)) {
      return subtreeSizeById.get(id);
    }

    const node = nodeById.get(id);
    if (!node) {
      const fallback = { width: 0, height: 0 };
      subtreeSizeById.set(id, fallback);
      return fallback;
    }

    const size = sizeById.get(id) || { width: 340, height: 132 };
    const childIds = (hierarchy.childrenByParent.get(id) || []).filter((childId) => visibleIds.has(childId));
    const shouldExpand = expandedSet.has(id) && childIds.length > 0;

    if (!shouldExpand) {
      subtreeSizeById.set(id, size);
      return size;
    }

    const childSizes = childIds.map((childId) => subtreeSize(childId));
    const totalChildHeight = childSizes.reduce((sum, childSize) => sum + childSize.height, 0) + gapY * Math.max(0, childSizes.length - 1);
    const maxChildWidth = childSizes.reduce((max, childSize) => Math.max(max, childSize.width), 0);
    const result = {
      width: size.width + gapX + maxChildWidth,
      height: Math.max(size.height, totalChildHeight),
    };
    subtreeSizeById.set(id, result);
    return result;
  };

  const nextNodes = nodes.map((node) => ({
    ...node,
    position: {
      x: Number(node?.position?.x || 0),
      y: Number(node?.position?.y || 0),
    },
  }));
  const nextNodeById = new Map(nextNodes.map((node) => [String(node?.id || ""), node]));
  const placedIds = new Set();

  const placeNode = (id, parentAbs = null, absPos = null) => {
    const node = nextNodeById.get(id);
    if (!node) return;

    const nodeSize = sizeById.get(id) || { width: 340, height: 132 };
    const childIds = (hierarchy.childrenByParent.get(id) || []).filter((childId) => visibleIds.has(childId));
    const shouldExpand = expandedSet.has(id) && childIds.length > 0;
    const currentAbs = absPos || (parentAbs ? {
      x: parentAbs.x + Number(node.position?.x || 0),
      y: parentAbs.y + Number(node.position?.y || 0),
    } : {
      x: Number(node.position?.x || 0),
      y: Number(node.position?.y || 0),
    });

    if (parentAbs) {
      node.position = {
        x: Math.round(currentAbs.x - parentAbs.x),
        y: Math.round(currentAbs.y - parentAbs.y),
      };
      node.parentNode = String(nodeById.get(id)?.parentNode || nodeById.get(id)?.data?.parentNodeId || "") || undefined;
      node.extent = node.parentNode ? "parent" : undefined;
    } else {
      node.position = {
        x: Math.round(currentAbs.x),
        y: Math.round(currentAbs.y),
      };
      node.parentNode = undefined;
      node.extent = undefined;
    }
    node.data = {
      ...(node.data || {}),
      parentNodeId: parentAbs ? (String(nodeById.get(id)?.parentNode || nodeById.get(id)?.data?.parentNodeId || "") || null) : null,
    };
    placedIds.add(id);

    if (!shouldExpand) {
      return;
    }

    const childSizes = childIds.map((childId) => subtreeSize(childId));
    const totalChildHeight = childSizes.reduce((sum, childSize) => sum + childSize.height, 0) + gapY * Math.max(0, childSizes.length - 1);
    const childX = currentAbs.x + nodeSize.width + gapX;
    let nextY = currentAbs.y + Math.max(0, Math.round((nodeSize.height - totalChildHeight) / 2));

    childIds.forEach((childId, index) => {
      const childSize = childSizes[index];
      const childAbs = { x: childX, y: nextY };
      const childNode = nextNodeById.get(childId);
      if (childNode) {
        childNode.parentNode = id;
        childNode.extent = "parent";
        childNode.position = {
          x: Math.round(childAbs.x - currentAbs.x),
          y: Math.round(childAbs.y - currentAbs.y),
        };
        childNode.data = {
          ...(childNode.data || {}),
          parentNodeId: id,
        };
      }
      placeNode(childId, currentAbs, childAbs);
      nextY += childSize.height + gapY;
    });
  };

  for (const rootId of hierarchy.roots) {
    const rootNode = nextNodeById.get(rootId);
    if (!rootNode) continue;
    placeNode(rootId, null, {
      x: Number(rootNode.position?.x || 0),
      y: Number(rootNode.position?.y || 0),
    });
  }

  for (const node of nextNodes) {
    const id = String(node?.id || "").trim();
    if (!id || placedIds.has(id) || !visibleIds.has(id)) continue;
    placeNode(id, null, {
      x: Number(node.position?.x || 0),
      y: Number(node.position?.y || 0),
    });
  }

  return nextNodes;
}

// Modal management
const modalOverlay = document.getElementById("cloud-arch-modal-overlay");
const modalTitle = document.getElementById("modal-title");
const modalSubtitle = document.getElementById("modal-subtitle");
const modalBody = document.getElementById("modal-body");
const modalIcon = document.getElementById("modal-icon");
const modalCloseBtn = document.getElementById("modal-close-btn");
let activeModalRequest = null;
let activeModalTimeout = null;

function resetModalRequestState() {
  if (activeModalTimeout) {
    clearTimeout(activeModalTimeout);
    activeModalTimeout = null;
  }
  activeModalRequest = null;
}

function startModalRequest() {
  if (activeModalRequest) {
    activeModalRequest.abort();
  }
  resetModalRequestState();
  const controller = new AbortController();
  activeModalRequest = controller;
  activeModalTimeout = setTimeout(() => {
    if (activeModalRequest !== controller) return;
    controller.abort();
    resetModalRequestState();
    showModalError("Timed out loading cloud details. Please try again.");
  }, 20000);
  return controller;
}

function closeModal() {
  if (activeModalRequest) {
    activeModalRequest.abort();
  }
  resetModalRequestState();
  if (modalOverlay) {
    modalOverlay.hidden = true;
  }
}

function showModalError(message) {
  if (!modalOverlay || !modalBody) return;
  modalTitle.textContent = "Unable to load details";
  modalSubtitle.textContent = "";
  setModalHeaderIcon("", "⚠");
  modalBody.innerHTML = `<div class="cloud-arch-modal-empty">${escapeHtml(message)}</div>`;
  modalOverlay.hidden = false;
}

function openModal(resourceId, nodeData, lookup = {}) {
  if (!modalOverlay) return;
  const controller = startModalRequest();
  const url = new URL("/api/cloud/resource-details", window.location.origin);
  const resolvedResourceId = String(lookup.id || lookup.resourceId || resourceId || "").trim();
  if (resolvedResourceId) {
    url.searchParams.set("id", resolvedResourceId);
  }
  const name = String(lookup.name || lookup.resourceName || lookup.label || "").trim();
  const resourceGroup = String(lookup.resourceGroup || lookup.rg || "").trim();
  const type = String(lookup.type || lookup.armType || lookup.resourceType || "").trim();
  const subscription = String(lookup.subscription || lookup.sub || "").trim();
  if (name) url.searchParams.set("name", name);
  if (resourceGroup) url.searchParams.set("resource_group", resourceGroup);
  if (type) url.searchParams.set("type", type);
  if (subscription) url.searchParams.set("sub", subscription);
  
  // Add subscription for Internet node
  if (resourceId.toLowerCase() === "internet") {
    const subInput = document.getElementById("subscription-input");
    if (subInput && subInput.value) {
      url.searchParams.set("sub", subInput.value);
    }
  }
  
  fetch(cacheBustUrl(url.toString()), {
    headers: { Accept: "application/json" },
    cache: "no-store",
    signal: controller.signal,
  })
    .then(async (resp) => {
      if (controller.signal.aborted) return;
      const data = await readJsonResponse(resp);
      if (!resp.ok) {
        throw new Error(data?.error || `Request failed with status ${resp.status}`);
      }
      if (data?.error) {
        throw new Error(data.error);
      }
      if (controller.signal.aborted) return;
      try {
        renderModalContent(data);
      } catch (err) {
        showModalError(`Error rendering details: ${err.message}`);
      }
    })
    .catch(err => {
      if (err?.name === "AbortError") return;
      showModalError(`Error loading details: ${err.message}`);
    });
}

function isApimNode(nodeData) {
  const haystack = [
    nodeData?.arm_type,
    nodeData?.type,
    nodeData?.resourceType,
    nodeData?.typeLabel,
    nodeData?.label,
    nodeData?.name,
  ]
    .map((value) => String(value || "").toLowerCase())
    .join(" ");
  return haystack.includes("apimanagement") || haystack.includes("api management") || haystack === "apim";
}

function openApimApisModal(resourceId) {
  if (!modalOverlay || !resourceId) return;
  const controller = startModalRequest();
  const url = new URL("/api/cloud/apim-child-apis", window.location.origin);
  url.searchParams.set("resource_id", resourceId);

  fetch(cacheBustUrl(url.toString()), {
    headers: { Accept: "application/json" },
    cache: "no-store",
    signal: controller.signal,
  })
    .then(async (resp) => {
      if (controller.signal.aborted) return;
      const data = await readJsonResponse(resp);
      if (!resp.ok) {
        throw new Error(data?.error || `Request failed with status ${resp.status}`);
      }
      if (data?.error) {
        throw new Error(data.error);
      }
      if (controller.signal.aborted) return;
      renderModalContent(data);
    })
    .catch((err) => {
      if (err?.name === "AbortError") return;
      showModalError(`Error loading APIM APIs: ${err.message}`);
    });
}

function openNodePopup(resourceId, nodeData) {
  const resources = Array.isArray(nodeData?.resources) ? nodeData.resources.filter(Boolean) : [];
  const isGroupedNode = Boolean(nodeData?.is_group || nodeData?.isGroupNode || nodeData?.summaryNode || nodeData?.groupType);
  const armType = String(nodeData?.arm_type || nodeData?.type || nodeData?.resourceType || "").toLowerCase();
  if (isApimNode(nodeData)) {
    openApimApisModal(resourceId);
    return;
  }
  const prefersChildDrilldown =
    armType.includes("applicationgateway") ||
    armType.includes("serverfarms") ||
    armType.includes("hostingenvironments");
  if (resources.length > 1 || (isGroupedNode && resources.length > 0)) {
    if (currentMermaidSubscriptionId) {
      openDrilldownModal(nodeData, currentMermaidSubscriptionId);
      return;
    }
  } else if (resources.length === 1) {
    const resource = resources[0] || {};
    if (prefersChildDrilldown && currentMermaidSubscriptionId) {
      openDrilldownModal(
        nodeData,
        currentMermaidSubscriptionId,
        () => openModal(resourceId, nodeData, {
          id: resource.id,
          name: resource.name,
          resourceGroup: resource.rg,
          type: nodeData?.arm_type || nodeData?.type || nodeData?.resourceType || "",
          subscription: currentMermaidSubscriptionId,
        }),
      );
      return;
    }
    openModal(resourceId, nodeData, {
      id: resource.id,
      name: resource.name,
      resourceGroup: resource.rg,
      type: nodeData?.arm_type || nodeData?.type || nodeData?.resourceType || "",
      subscription: currentMermaidSubscriptionId,
    });
    return;
  }
  openModal(resourceId, nodeData);
}

function openDrilldownModal(entry, subId, fallback = null) {
  if (!modalOverlay || !subId) return;
  const controller = startModalRequest();

  const url = new URL(`/api/subscriptions/${encodeURIComponent(subId)}/drilldown`, window.location.origin);
  fetch(url.toString(), {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({
      arm_type: entry.arm_type,
      resources: entry.resources,
      node: entry,
    }),
    signal: controller.signal,
  })
    .then(async (resp) => {
      if (controller.signal.aborted) return;
      const data = await readJsonResponse(resp);
      if (!resp.ok) {
        throw new Error(data?.error || `Request failed with status ${resp.status}`);
      }
      if (data?.error) {
        throw new Error(data.error);
      }
      if (controller.signal.aborted) return;
      const hasRows =
        (Array.isArray(data?.rows) && data.rows.length > 0) ||
        (Array.isArray(data?.sections) && data.sections.some((section) => Array.isArray(section?.rows) && section.rows.length > 0));
      if (!hasRows && typeof fallback === "function") {
        fallback();
        return;
      }
      try {
        renderModalContent(data);
      } catch (err) {
        showModalError(`Error rendering details: ${err.message}`);
      }
    })
    .catch((err) => {
      if (err?.name === "AbortError") return;
      showModalError(`Error loading drilldown: ${err.message}`);
    });
}

function renderModalContent(data) {
  if (modalOverlay) {
    modalOverlay.hidden = false;
  }
  modalTitle.textContent = data.title || data.name || "Details";
  modalSubtitle.textContent = data.type_label ? `${data.type_label}${data.resource_group ? " • " + data.resource_group : ""}` : (data.resource_group || "");
  setModalHeaderIcon(data.icon_path || data.parent_resource?.icon_path || "", "☁");

  const suppressParentHeading =
    String(data.title || data.name || "").toLowerCase() === "simulation-knowledgecentre-uksouth" &&
    String(data.type_label || data.type || data.resourceType || "").toLowerCase().includes("app service plan");
  const dataTypeKey = normalizeResourceTypeKey(data.type || data.resourceType || data.type_label);
  const parentTypeKey = normalizeResourceTypeKey(data.parent_resource?.type || data.parent_resource?.type_label);
  const hasDistinctParent = data.parent_resource && data.parent_resource.name && data.parent_resource.type_label && dataTypeKey && parentTypeKey && dataTypeKey !== parentTypeKey;
  const sections = [];

  if (hasDistinctParent) {
    const parentNetwork = data.parent_resource.network && typeof data.parent_resource.network === "object" ? data.parent_resource.network : null;
    const parentVnet = firstNonEmpty(
      parentNetwork?.vnet,
      data.parent_resource.vnet,
      data.parent_resource.vnet_name,
      data.parent_resource.vnetName
    );
    const parentSubnet = firstNonEmpty(
      parentNetwork?.subnet,
      data.parent_resource.subnet,
      data.parent_resource.subnet_name,
      data.parent_resource.subnetName
    );
    const parentNetworkType = firstNonEmpty(
      parentNetwork?.virtual_network_type,
      parentNetwork?.virtualNetworkType,
      data.parent_resource.virtual_network_type
    );
    sections.push({
      title: suppressParentHeading ? "" : "Parent Resource",
      icon: data.parent_resource.icon_path
        ? `<img src="${escapeHtml(data.parent_resource.icon_path)}" alt="" aria-hidden="true" style="width:18px;height:18px;object-fit:contain;vertical-align:middle;" />`
        : "🔗",
      fields: [
        { label: "Name", value: data.parent_resource.name },
        { label: "Type", value: data.parent_resource.type_label || data.parent_resource.type },
        { label: "Resource Group", value: data.parent_resource.resource_group || "—" },
        parentVnet ? { label: "Inherited Virtual Network", value: parentVnet } : null,
        parentSubnet ? { label: "Inherited Subnet", value: parentSubnet } : null,
        parentNetworkType ? { label: "Inherited Virtual Network Type", value: parentNetworkType } : null,
        data.parent_resource.fqdn ? { label: "FQDN", value: data.parent_resource.fqdn } : null,
      ].filter(Boolean),
    });
  }

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
            ${section.title ? `
            <div class="cloud-arch-modal-section-title">
              <span class="cloud-arch-modal-section-icon">${section.icon}</span>
              ${section.title}
            </div>` : ""}
            <div class="cloud-arch-modal-grid">
              ${fieldsHtml}
            </div>
          </div>
      `;
    }).join('');
    
    return;
  }
  
  // Configuration Section (regular resources)
  if (data.name || data.type_label || data.type || data.sku || data.fqdn) {
    const summaryFields = [];
    if (data.name) summaryFields.push({ label: "Asset Name", value: data.name });
    if (data.type_label || data.typeLabel) summaryFields.push({ label: "Service Type", value: data.type_label || data.typeLabel });
    if (data.type) summaryFields.push({ label: "Resource Type", value: data.type });
    if (data.sku || data.configuration?.sku_name) summaryFields.push({ label: "SKU", value: data.sku || data.configuration?.sku_name });
    if (data.security && typeof data.security.is_public === "boolean") {
      summaryFields.push({
        label: "Public",
        value: data.security.is_public
          ? '<span class="cloud-arch-modal-badge cloud-arch-modal-badge--danger">🌐 Public</span>'
          : '<span class="cloud-arch-modal-badge cloud-arch-modal-badge--success">🔒 Private</span>',
        isHtml: true,
      });
    } else if (typeof data.public === "boolean") {
      summaryFields.push({
        label: "Public",
        value: data.public
          ? '<span class="cloud-arch-modal-badge cloud-arch-modal-badge--danger">🌐 Public</span>'
          : '<span class="cloud-arch-modal-badge cloud-arch-modal-badge--success">🔒 Private</span>',
        isHtml: true,
      });
    }
    if (data.fqdn) summaryFields.push({ label: "FQDN", value: data.fqdn });
    if (data.resource_group) summaryFields.push({ label: "Resource Group", value: data.resource_group });
    if (data.location) summaryFields.push({ label: "Location", value: data.location });
    if (summaryFields.length > 0) {
      sections.push({ title: "Asset Overview", icon: "🧩", fields: summaryFields });
    }
  }

  if (data.configuration) {
    const configFields = [];
    if (data.configuration.sku_name) configFields.push({ label: "SKU", value: data.configuration.sku_name });
    if (data.configuration.sku_tier) configFields.push({ label: "Tier", value: data.configuration.sku_tier });
    if (data.configuration.operating_system) configFields.push({ label: "Operating System", value: data.configuration.operating_system });
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
    
    const outboundPublicIps = Array.isArray(net.outbound_public_ips) ? net.outbound_public_ips : [];
    if (outboundPublicIps.length > 0) {
      const ipsHtml = outboundPublicIps.map(ip => 
        `<span class="cloud-arch-modal-badge cloud-arch-modal-badge--info">🌐 ${ip}</span>`
      ).join(' ');
      networkFields.push({ label: "Outbound Public IPs", value: ipsHtml, isHtml: true, fullWidth: true });
    } else if (net.public_ips && net.public_ips.length > 0) {
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
    
    if (net.vnet) networkFields.push({ label: "Network", value: net.vnet });
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

  const triggerSection = buildTriggersSection(data);
  if (triggerSection) {
    sections.push({
      title: "",
      icon: "",
      fields: [],
      __rawHtml: triggerSection,
    });
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
    if (section.__rawHtml) return section.__rawHtml;
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

function normalizeResourceTypeKey(value) {
  return String(value || "").trim().toLowerCase();
}

function setModalHeaderIcon(iconPath, fallbackText = "☁") {
  if (!modalIcon) return;
  if (iconPath) {
    modalIcon.innerHTML = `<img src="${escapeHtml(iconPath)}" alt="" aria-hidden="true" style="width:28px;height:28px;object-fit:contain;" />`;
    return;
  }
  modalIcon.textContent = fallbackText;
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
    setNodes(reflowVisibleTree(visibility.nodes, nextExpanded));
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
    const missingAssets = (Array.isArray(payload?.summary?.missing_assets) ? payload.summary.missing_assets : [])
      .filter((asset) => !isNoisyMonitoringAsset(asset));
    const missingCount = payload?.summary?.missing_asset_count ?? missingAssets.length;
    const connectionCount = payload?.summary?.connection_count ?? 0;
    const modeLabel = viewModeLabel(viewMode);
    summaryLineEl.innerHTML = [
      `<span><strong>${resourceCount}</strong> resources</span>`,
      omittedCount > 0 ? `<span><strong>${displayedCount}</strong> shown</span>` : null,
      missingCount > 0 ? `<span><strong>${missingCount}</strong> missing</span>` : null,
      `<span><strong>${connectionCount}</strong> connections</span>`,
      payload?.summary?.layout_mode ? `<span><strong>${modeLabel}</strong> mode</span>` : null,
      `<span><strong>${subscriptionName || "subscription-production"}</strong></span>`,
    ]
      .filter(Boolean)
      .join(" ");

    if (missingAssetsEl) {
      if (missingAssets.length > 0) {
        const itemsHtml = missingAssets.slice(0, 12).map((asset) => {
          const name = escapeHtml(asset?.name || asset?.id || "Unknown asset");
          const typeLabel = escapeHtml(asset?.type_label || asset?.type || "Unknown type");
          const resourceGroup = asset?.resource_group ? ` • ${escapeHtml(asset.resource_group)}` : "";
          const reason = escapeHtml(asset?.reason || "Architecturally relevant resource hidden by overview compaction");
          return `
            <li class="cloud-arch-missing-assets__item">
              <span class="cloud-arch-missing-assets__name">${name}</span>
              <div class="cloud-arch-missing-assets__meta">${typeLabel}${resourceGroup}</div>
              <div class="cloud-arch-missing-assets__meta">${reason}</div>
            </li>
          `;
        }).join("");
        const moreCount = missingAssets.length - Math.min(missingAssets.length, 12);
        missingAssetsEl.hidden = false;
        missingAssetsEl.innerHTML = `
          <div class="cloud-arch-missing-assets__title">Missing from chart</div>
          <ul class="cloud-arch-missing-assets__list">
            ${itemsHtml}
          </ul>
          ${moreCount > 0 ? `<div class="cloud-arch-missing-assets__meta" style="margin-top:8px;">+${moreCount} more omitted resources</div>` : ""}
        `;
      } else {
        missingAssetsEl.hidden = true;
        missingAssetsEl.innerHTML = "";
      }
    }

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
        <div style="font-weight: 700; margin-bottom: 8px; color: var(--text);">Arrow Colour Key</div>
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
    if (missingAssetsEl) {
      missingAssetsEl.hidden = true;
      missingAssetsEl.innerHTML = "";
    }

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
      const resp = await fetch(cacheBustUrl(url.toString()), {
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
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
          type: "smoothstep",
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
      setNodes(reflowVisibleTree(visibility.nodes, initialExpandedNodes));
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
          if (typeof window.__triageCloudArchLoadMermaid === "function") {
         await window.__triageCloudArchLoadMermaid(payload.subscription_name || sub || "subscription-production");
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
      if (missingAssetsEl) {
        missingAssetsEl.hidden = true;
        missingAssetsEl.innerHTML = "";
      }
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
    
    // Load Mermaid support when switching to mermaid mode
    if (mode === "mermaid" && !window.__triageCloudArchLoadMermaid) {
      import("./cloud-architecture-mermaid.js?v=16").then(() => {
        if (typeof window.__triageCloudArchLoad === "function") {
          window.__triageCloudArchLoad((subscriptionInput.value || "").trim(), activeViewMode);
        }
      });
    } else if (typeof window.__triageCloudArchLoad === "function") {
      window.__triageCloudArchLoad((subscriptionInput.value || "").trim(), activeViewMode);
    }
  });
}

mount();
syncViewButtons();
