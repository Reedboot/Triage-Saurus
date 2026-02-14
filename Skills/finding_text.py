from __future__ import annotations

import re


def _lc(s: str) -> str:
    return (s or "").strip().lower()


def _has_any(haystack: str, needles: list[str]) -> bool:
    return any(n in haystack for n in needles)


def cloud_description_for_title(title: str) -> str:
    """
    Generate a short, plain-English issue description for title-only cloud findings.
    Goal: explain "why someone should care" without over-claiming resource IDs.
    """
    t = _lc(title)

    if _has_any(t, ["key vault", "keyvault"]):
        if _has_any(t, ["private link", "private endpoint", "private endpoints"]):
            return "Secrets/keys are reachable over the public network path; that expands your attack surface and can lead to app/service compromise if access controls fail or are misconfigured."
        if _has_any(t, ["firewall", "public network access", "public access"]):
            return "If Key Vault can be reached from public networks, a single access-control mistake or compromised identity can turn into stolen secrets/keys and downstream service compromise."
        if _has_any(t, ["rbac", "role-based", "role based"]):
            return "Over-privileged Key Vault access makes it easier for attackers (or mistakes) to exfiltrate secrets/keys and take over other systems that depend on them."
        if _has_any(t, ["secret", "secrets", "key ", " keys", "expiration", "expire", "expiry"]):
            return "Long-lived secrets/keys increase blast radius: if one is leaked, it may remain usable for a long time and enable persistent compromise."
        if _has_any(t, ["soft delete", "purge protection"]):
            return "Without recovery protections, an attacker (or accident) can delete critical secrets/keys and cause prolonged outages or irreversible data loss."

    if _has_any(t, ["storage account", "storage accounts", "blob", "secure transfer", "shared key"]):
        if _has_any(t, ["public blob", "public access", "prevent public", "anonymous"]):
            return "If blobs/containers allow anonymous access, customer or internal data can be exposed to the internet without authentication."
        if _has_any(t, ["shared key"]):
            return "Shared keys are effectively high-privilege passwords; if one leaks, an attacker can access/modify data and it’s harder to attribute actions to a person/workload."
        if _has_any(t, ["secure transfer", "https", "http"]):
            return "Allowing HTTP increases the chance of data/credential interception or tampering on the network path."
        if _has_any(t, ["firewall", "virtual network", "vnet", "network"]):
            return "Broad Storage network access increases the chance of unauthorized data access and makes it harder to contain incidents."
        return "Storage configuration may allow broader-than-intended access, increasing the risk of data exposure or destructive actions."

    if _has_any(t, ["nsg", "network security group", "inbound", "ports", "management ports"]):
        if _has_any(t, ["management", "ssh", "rdp", "22", "3389"]):
            return "Exposed SSH/RDP makes it much easier to get initial access (brute force, credential stuffing, exploits) and can lead to full workload compromise."
        return "Overly broad inbound rules expand attack surface and make lateral movement easier once any foothold is gained."

    if _has_any(t, ["sql server", "sql servers", "azure sql", "tde", "auditing", "firewall"]):
        if _has_any(t, ["allow azure services", "allow azure"]):
            return "Allowing broad Azure-sourced access expands who can reach your databases and increases the risk of unauthorized access and data theft."
        if _has_any(t, ["tde", "unencrypted", "encryption at rest"]):
            return "Without encryption at rest, database files/backups are more likely to expose sensitive data if storage is accessed or copied."
        if _has_any(t, ["auditing"]):
            return "Without auditing, you may not detect or be able to investigate suspicious/privileged database activity in time."
        return "SQL network or security settings are not aligned to least-privilege, increasing unauthorized access and data loss risk."

    if _has_any(t, ["aks", "kubernetes"]):
        if _has_any(t, ["rbac", "role based", "role-based"]):
            return "If AKS RBAC isn’t enforced, it’s easier for users/workloads to gain excessive permissions and compromise the cluster and hosted applications."
        return "AKS configuration is not aligned to baseline hardening, increasing cluster and workload compromise risk."

    if _has_any(t, ["acr", "container registry", "admin user"]):
        return "Shared admin credentials for the container registry increase the risk of credential leakage and supply-chain impact (malicious image push/pull)."

    if _has_any(t, ["ddos"]):
        return "Without DDoS protection, internet-facing services are more likely to suffer outages during volumetric attacks."

    if _has_any(t, ["mfa", "owner", "subscription owners", "entra"]):
        return "Weak authentication for privileged accounts increases the chance of account takeover and rapid, broad compromise of the environment."

    if _has_any(t, ["managed identity", "secrets", "credential"]):
        return "Long-lived credentials are easier to leak and reuse; managed identity reduces secret sprawl and lowers the chance of credential-based compromise."

    if _has_any(t, ["endpoint protection"]):
        return "Without endpoint protection, malware and post-exploitation tooling are more likely to persist undetected on compute workloads."

    if _has_any(t, ["ftps", "ftp", "app service"]):
        return "Allowing weaker deployment/management protocols increases the risk of credential theft and unauthorized application changes."

    # Generic fallback (better than repeating the title).
    cleaned = re.sub(r"\s+", " ", title.strip()).strip()
    return f"Security configuration does not meet baseline guidance: {cleaned}."
