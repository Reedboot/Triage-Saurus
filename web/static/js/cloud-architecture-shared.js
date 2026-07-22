// Shared utilities for cloud architecture views
export const CONFIG = window.__TRIAGE_CLOUD_ARCH__ || {};

export const PROVIDER_THEMES = {
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

export function normalizeViewMode(value) {
  const mode = (value || "").trim().toLowerCase();
  if (mode === "reactflow" || mode === "full") {
    return "reactflow";
  }
  return "mermaid";
}

export function viewModeLabel(mode) {
  return normalizeViewMode(mode) === "reactflow" ? "React Flow" : "Mermaid";
}

export function themeFor(key) {
  return PROVIDER_THEMES[key] || PROVIDER_THEMES.unknown;
}

export async function readJsonResponse(resp) {
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

export function escapeHtml(text) {
  const map = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  };
  return String(text || "").replace(/[&<>"']/g, (m) => map[m]);
}

export function normalizeResourceTypeKey(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/[^a-z0-9]/g, "_");
}

export function normalizeIconClass(resourceType, providerKey = "azure") {
  const rawType = String(resourceType || "").trim().toLowerCase();
  if (providerKey === "azure") {
    if (rawType.includes("microsoft.containerservice/managedclusters")) {
      return "icon_azurerm_aks_azure";
    }
    if (rawType.includes("microsoft.kubernetes/services")) {
      return "icon_azurerm_kubernetes_service_azure";
    }
  }
  const key = normalizeResourceTypeKey(resourceType);
  return `icon_${key}_${normalizeResourceTypeKey(providerKey)}`;
}
