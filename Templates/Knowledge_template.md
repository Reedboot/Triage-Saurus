# 🟣 Knowledge Template

## [SYSTEM PROMPT] Purpose
You are capturing *learnt* environment knowledge that should persist across future triage sessions.

- **Purpose:** Record stable facts about the environment (providers, services, guardrails, defaults) that help future findings.
- **Use when:** The user confirms a detail during triage (e.g., “We use Azure Policy”, “Public network access is disabled by default”).
- **Rule:** Append new facts; avoid speculation. Keep entries concise and actionable.

## 🧭 Overview
- **Domain:** <e.g., Azure / AWS / GCP / Code / DevOps>
- **Owner/Audience:** <who this is for>
- **What this file contains:** <1–2 sentences>

## 🗓️ Learned log (append-only)
- **Format:** `DD/MM/YYYY HH:MM — <fact>` (UK date/time)
- <timestamp> — <new confirmed fact>

## ☁️ Cloud Provider
- <Provider name>

## 🧩 Services In Use
- <Service 1>
- <Service 2>

## 🔗 Service Dependencies (for compounding)
Record how services depend on each other so compounding issues are easier to spot.

- **Format:** `Service A` ➜ depends on ➜ `Service B` — <short reason>
- Prefer stable relationships (platform/app architecture), not one-off resource IDs.
- If a dependency changes, append a new bullet rather than rewriting history.

## 🛡️ Guardrails and Enforcement
- **Policy/Guardrails:** <e.g., Azure Policy initiatives, SCPs, OPA, CI checks>
- **Monitoring/Alerting:** <e.g., Defender for Cloud, SIEM integration>
- **Deployment/IaC:** <e.g., Terraform, Bicep, ARM>

## 🔐 Identity and Access
- **Primary model:** <e.g., RBAC, access policies, IAM roles>
- **Privileged access:** <e.g., PIM/JIT>
- **Workload identity:** <e.g., managed identities, workload identity federation>

## 🌐 Network Exposure Defaults
- **Public network access default:** <Enabled/Disabled/Varies>
- **Private connectivity:** <Private endpoints/VPC endpoints/VNet integration>
- **Inbound controls:** <WAF/firewall/IP allowlists>

## 🔑 Secrets, Keys, and Rotation
- **Storage:** <Key Vault / Secrets Manager / Vault>
- **Rotation/expiry defaults:** <policy>
- **Break-glass process:** <if applicable>

## ✅ Known Good Baselines
- <Short bullet list of “this is already in place” controls that reduce risk>

## 🚩 Known Exceptions / Risk Acceptances
- <Exception + rationale + scope>

## ❓ Open Questions
- <If a detail would materially affect risk scoring, track it here for later>

## 📝 Notes
- Keep entries factual; separate *confirmed* vs *assumed*.
- Prefer reusable statements (defaults/guardrails) over one-off resource IDs.

---

Last updated: DD/MM/YYYY HH:MM
