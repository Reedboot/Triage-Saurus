# Cloud Architecture Summary: ${provider}

## 🧭 Overview

- **Provider:** ${provider_title}
- **Repository:** ${repo_name}
- **Scope:** Experiment-scoped (inferred from repo \`${repo_name}\`; not platform-wide)
- **Auth signals:** ${auth_signals}

## 🏗️ High-Level Architecture

\`\`\`mermaid
${architecture_diagram}
\`\`\`

## 📊 TL;DR - Executive Summary

| Aspect | Value |
|--------|-------|
| **Key services** | ${services} |
| **Top risk** | ${top_risk} |
| **Primary next step** | ${next_step} |
| **Attack surface** | ${attack_surface} |
| **Edge gateway** | ${edge_gateway} |

## 🎯 Resource Inventory

> ⚠️ **Blast radius note:** A compromise of a parent service puts all its sub-services at risk, and vice versa — a vulnerable sub-service (e.g. misconfigured app) can be a pivot point into the parent compute layer.

${resource_inventory}

## 🔐 Security Controls

${security_controls}

## 🔗 External Dependencies

${external_dependencies}

## ⚠️ High-Risk PaaS Exposure Checks

${paas_exposure_checks}

## 📝 Recommendations

${recommendations}

## Meta Data

- **Generated:** ${timestamp}
- **Scope:** Experiment-scoped analysis from repository ${repo_name}
