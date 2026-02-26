# Cloud Architecture Summary: ${provider}

**Provider:** ${provider_title}  
**Repository:** ${repo_name}  
**Generated:** ${timestamp}

## 🧭 Overview

- **Provider:** ${provider_title}
- **Scope:** Experiment-scoped (inferred from repo \`${repo_name}\`; not platform-wide)
- **Auth signals:** ${auth_signals}

## 📊 TL;DR - Executive Summary

| Aspect | Value |
|--------|-------|
| **Key services** | ${services} |
| **Top risk** | ${top_risk} |
| **Primary next step** | ${next_step} |
| **Attack surface** | ${attack_surface} |
| **Edge gateway** | ${edge_gateway} |

## 🏗️ High-Level Architecture

\`\`\`mermaid
${architecture_diagram}
\`\`\`

**Legend:**
- **Border Colors:** 🔵 Blue = Applications/Services | 🟢 Green = Data Stores | 🟠 Orange = Identity/Secrets/Pipeline | 🔴 Red = Security/Network Controls
- **Line Styles:** Solid = direct dependency | Dashed = protection/monitoring
- **Arrow Colors:** 🔴 Red arrows = Direct internet exposure (attack surface)
- **Arrow Labels:** Only shown where context adds value (e.g., HTTPS protocol, State storage, Telemetry)

## 🎯 Resource Inventory

${resource_inventory}

## 🔐 Security Controls

${security_controls}

## 📝 Recommendations

${recommendations}

---
*Generated: ${timestamp}*  
*Scope: Experiment-scoped analysis from repository ${repo_name}*
