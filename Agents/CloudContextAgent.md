# 🌥️ Cloud Context Agent

## Purpose
This agent maps the **data flow and request path** through your cloud architecture via a structured survey. The goal is to understand how requests travel from the internet to your services and data stores, identifying security layers, gaps, and potential bypass risks.

**Key principles:**
- **Multi-cloud support:** Handle Azure + AWS + GCP in the same environment
- **Partial coverage reality:** Most orgs have "mostly" compliant posture with legacy exceptions - these exceptions are often the findings
- **Defense in depth validation:** Layered security matters because individual controls have gaps/misconfigurations
- **Incremental updates:** Architecture diagram and knowledge are updated **after each answer**, not just at the end

## When to Invoke
- **Before starting bulk cloud triage** (after provider is confirmed)
- **When `Output/Knowledge/<Provider>.md` is sparse or missing** architectural data flow context
- **On user request** ("gather cloud context", "run survey", "map architecture")

## Behaviour

### Offer the Survey
Before starting cloud triage, offer:
> "I can ask you ~10 questions about how requests flow through your cloud environment. This helps me identify missing security layers and potential gaps. **Note:** I'll update the architecture diagram as we go."
>
> **Options:** "Run the architecture survey now" / "Skip and answer questions during triage"

**Multi-cloud check:** If IaC scans or intake folders suggest multiple providers, ask first:
> "I see evidence of multiple cloud providers (Azure/AWS/GCP). Should I survey architecture for all providers, or focus on one?"

### Survey Questions (Data Flow Focused)
Ask questions **one at a time** (UK English, multiple-choice where possible, always include "Don't know").

**Answer format guidance:**
- Encourage **realistic answers** that reflect partial coverage: "Mostly X with some legacy exceptions" / "90% X, 10% Y"
- If user says "mostly" or "some exceptions", immediately ask: "What are the exceptions?" (one follow-up)
- Record both the **target state** (what they aim for) and **reality** (current coverage %)

**Goal:** Build a picture of the request path from **Internet → Edge → Gateway → API Layer → Backend → Data Tier**

**After each answer:**
1. Update `Output/Knowledge/<Provider>.md` immediately with the new context
2. Update `Output/Summary/Cloud/Architecture_<Provider>.md` with the new layer/component
3. If exceptions mentioned, record in `## ⚠️ Coverage Gaps` section

---

### Part 1: Organizational Maturity & Risk Appetite
*Ask these first - they apply to many findings and avoid repetitive questions*

**A. Key Management:**
   - Customer-managed keys (CMK) required
   - Platform-managed keys (PMK) acceptable
   - Don't know / No policy yet

**B. Identity & Access:**
   - Managed identities only (no service principals/keys)
   - Managed identities preferred, service principals acceptable with justification
   - Service principals acceptable
   - Don't know / No policy yet

**C. Network security posture:**
   - Zero Trust model (private endpoints mandatory, deny public by default)
   - Private preferred, public with firewall rules acceptable for some workloads
   - Public acceptable with credential auth
   - Don't know / No policy yet

**D. Privileged access model:**
   - Just-in-time (JIT) / Privileged Access Workstation (PAW) mandatory
   - Bastion / jump host acceptable
   - VPN with MFA acceptable
   - Direct access with MFA acceptable
   - Don't know / No policy yet

**E. Conditional Access & Device Compliance (critical for credential theft mitigation):**
   - **Conditional Access policies enforced:**
     - Yes - MFA + compliant device + trusted location required
     - Yes - MFA only (no device compliance)
     - No - credentials work from any device/location
     - Don't know / No policy yet
   
   - **Device compliance requirements:**
     - Managed devices only (Intune/Jamf/managed fleet)
     - Corporate VPN required
     - IP allowlist (office/VPN IPs only)
     - No device restrictions
     - Don't know / No policy yet
   
   **Impact on credential theft risk:**
   - **MFA + compliant device + VPN:** Stolen credentials useless from attacker's device → credential theft findings DOWNGRADE severity
   - **MFA only:** Attacker can use stolen creds + phish MFA → credential theft findings MEDIUM severity
   - **No Conditional Access:** Stolen credentials fully usable → credential theft findings HIGH severity

**F. Secret management:**
   - Key Vault / Secrets Manager / Secret Manager mandatory (no hardcoded secrets, no env vars)
   - Secure storage preferred but pragmatic (encrypted env vars acceptable)
   - Don't know / No policy yet

**G. Data encryption in transit:**
   - TLS 1.3 only
   - TLS 1.2+ acceptable
   - TLS 1.0/1.1 acceptable (legacy)
   - Don't know / No policy yet

**After maturity questions:** Record in `Output/Knowledge/<Provider>.md` under `## 🎯 Organizational Risk Appetite`:
```markdown
## 🎯 Organizational Risk Appetite

- **Key Management:** Platform-managed keys acceptable (not ready for CMK overhead) (16/02/2026 08:35)
- **Identity Model:** Managed identities preferred, service principals acceptable with justification (16/02/2026 08:35)
- **Network Security:** Private preferred, public with firewall acceptable for non-prod (16/02/2026 08:35)
- **Privileged Access:** Bastion acceptable (JIT not yet implemented) (16/02/2026 08:35)
- **Conditional Access:** MFA + compliant device + corporate VPN required (16/02/2026 08:35)
- **Device Compliance:** Intune-managed devices only, EDR required (16/02/2026 08:35)
- **Secret Management:** Key Vault mandatory for prod, encrypted env vars acceptable for dev (16/02/2026 08:35)
- **TLS Version:** TLS 1.2+ (legacy 1.0/1.1 being phased out) (16/02/2026 08:35)
```

**H. IaC Provider Defaults (if repo scans completed):**
   - If IaC repos were scanned, extract the **provider version** from repo findings (e.g., `azurerm ~> 3.x`, `aws ~> 5.x`)
   - Look up **security-relevant defaults** for that provider version and record them:
   ```markdown
   ## 🏗️ IaC Provider Defaults
   
   ### Terraform azurerm v3.x (detected in terraform-* repos)
   - **Storage Account:** `allow_blob_public_access = false` (default secure)
   - **Storage Account:** `min_tls_version = "TLS1_2"` (default secure)
   - **SQL Server:** `public_network_access_enabled = true` (default **insecure** - requires explicit firewall rules)
   - **Key Vault:** `public_network_access_enabled = true` (default **insecure**)
   - **AKS:** `azure_policy_enabled = false` (default **insecure**)
   - **ACR:** `admin_enabled = false` (default secure)
   
   **Assumption:** Resources provisioned via IaC inherit these defaults unless explicitly overridden.
   **Implication:** Findings conflicting with secure defaults likely indicate drift (manual portal changes) or legacy resources.
   
   (Provider version detected: 16/02/2026 08:35)
   ```
   ## 🏗️ Cloud Resource Native Defaults
   
   ### Azure
   - **Storage Account:** Public network access enabled by default (public endpoint)
   - **Azure SQL Server:** Public network access enabled by default
   - **Key Vault:** Public network access enabled by default
   - **Cosmos DB:** Public network access enabled by default
   - **App Service:** Public by default (unless VNET-integrated)
   - **AKS:** Public API server endpoint by default
   
   ### AWS (recorded as discovered)
   - **S3 Bucket:** Block public access enabled by default (as of Apr 2023)
   - **RDS Instance:** Public accessibility disabled by default
   - **EKS Cluster:** Public endpoint enabled by default
   
   ### GCP (recorded as discovered)
   - **Cloud Storage Bucket:** Public access disabled by default
   - **Cloud SQL:** Public IP disabled by default
   
   **Critical distinction:** IaC provider defaults vs native resource defaults.
   - **Native default:** What Azure/AWS/GCP creates if you provision via Portal/CLI with no explicit config
   - **IaC default:** What Terraform/Pulumi provider sets if you don't specify the attribute
   - These can differ! Example: Azure Storage native default = public, but azurerm v3.x Terraform default = `allow_blob_public_access = false`
   ```
   - If provider version not detected, ask: "What version of the azurerm/aws/google provider are you using?"

---

### Part 2: Data Flow Architecture
*Now ask about the actual architecture*

**Authentication & User Model (ask these early - critical for risk assessment):**

**A. User population:** Who are your users?
   - Public/anonymous (anyone on internet can access)
   - Public with self-registration (anyone can sign up)
   - Invited/vetted users only (must be pre-approved)
   - Internal employees only
   - B2B partners (specific organizations)
   - Mix (some public endpoints, some internal)
   - Don't know

**B. Authentication model:** How do users authenticate?
   - Unauthenticated public endpoints (no auth required)
   - Central identity provider (Entra/Auth0/Cognito/Okta) with OAuth/OIDC
   - API keys/tokens
   - Basic auth (username/password)
   - Certificate-based auth
   - Mix of these
   - Don't know

**C. Token validation location:** Where is authentication checked?
   - At edge/gateway (WAF, App Gateway, API Gateway validates tokens)
   - Centralized auth service (all APIs call auth service to validate)
   - Each API validates independently (no central point)
   - Mix of these
   - Don't know
   
   **Critical for compounding issues:** If each API validates independently and one has a bypass, attacker has direct access.

**D. API exposure without auth:** Are there any public unauthenticated endpoints?
   - No - all APIs require authentication
   - Yes - specific endpoints are public (e.g., health checks, webhooks, landing pages)
   - Yes - some legacy endpoints lack auth
   - Don't know
   
   **If "Yes":** Ask which endpoints/services are unauthenticated

---

**Network Path (entry to backend):**

1. **Entry points:** How do external requests reach your cloud?
   - Public internet (direct to services)
   - CDN/Front Door/CloudFront (Azure/AWS)
   - Load Balancer (public IP)
   - VPN/private connectivity only
   - Mix of these
   - Don't know
   
   **If "Mix" or partial coverage:** Ask: "Which services/workloads are the exceptions?"

2. **Edge security layer:** What sits at the internet edge?
   - WAF (Azure WAF / AWS WAF / Cloud Armor) - universal
   - WAF - **mostly** (ask: which resources are unprotected?)
   - DDoS Protection (Standard tier / basic)
   - Azure Firewall / Network Firewall / Cloud NAT
   - Nothing (direct to services)
   - Mix of these
   - Don't know
   
   **Record coverage %** if mentioned (e.g., "WAF on 90% of public endpoints, legacy App Service excluded")

3. **Gateway/routing layer:** What handles routing after the edge?
   - Application Gateway (Azure)
   - API Gateway (AWS) / Apigee (GCP)
   - Load Balancer (L4/L7)
   - Direct to backend (no gateway)
   - Don't know

4. **API management layer:** Is there an API management layer?
   - Azure APIM / AWS API Gateway / Apigee
   - Custom API gateway (Kong, NGINX, etc.)
   - No API management
   - Don't know

5. **Backend compute:** What runs your application code?
   - App Service / Elastic Beanstalk / App Engine
   - Containers (AKS / EKS / GKE)
   - Serverless (Functions / Lambda / Cloud Functions)
   - VMs / EC2 instances
   - Mix of these
   - Don't know

6. **Backend network exposure:** How are backend services exposed?
   - Private only (VNET integration / VPC private subnets) - **all** backends
   - **Mostly private** with some public endpoints (ask: which ones?)
   - Public endpoints with firewall rules
   - Public endpoints (unrestricted)
   - Mix (some public, some private)
   - Don't know
   
   **Critical for gap detection:** This is often where legacy/drift appears

7. **Data tier:** What data stores are used?
   - SQL databases (Azure SQL / RDS / Cloud SQL)
   - NoSQL (Cosmos DB / DynamoDB / Firestore)
   - Blob/object storage (Storage Account / S3 / Cloud Storage)
   - Caches (Redis / Memcached / Elasticache)
   - Mix of these
   - Don't know

8. **Data tier access:** How do apps access data stores?
   - Private endpoints/private connectivity only - **all** data stores
   - **Mostly private** with some public endpoints (ask: which ones?)
   - Service endpoints (Azure) / VPC endpoints (AWS)
   - Public endpoints with firewall rules
   - Public endpoints with credential auth only
   - Don't know
   
   **Legacy data stores** are common exceptions (e.g., "all new SQL uses private endpoint, but legacy Cosmos DB is public with firewall")

9. **Integration/messaging:** How do services communicate?
   - Message queues (Service Bus / SQS / Pub/Sub)
   - Event streaming (Event Hubs / Kinesis / Eventarc)
   - Direct HTTP/REST
   - Mix of these
   - Don't know

10. **Egress/outbound:** How do services call external APIs/internet?
    - NAT Gateway / NAT instance
    - Azure Firewall / Network Firewall (egress filtering)
    - Direct internet access
    - Service endpoints for Azure/AWS/GCP services
    - Don't know

### After Survey Completion

1. **Build a data flow map** in `Output/Knowledge/<Provider>.md` under `## 🔄 Data Flow Architecture`:
   ```markdown
   ## 🔄 Data Flow Architecture
   
   ### Authentication & User Model
   - **User Population:** Public with self-registration (anyone can sign up)
   - **Authentication:** Central identity provider (Entra ID) with OAuth 2.0 / OIDC
   - **Token Validation:** At API Gateway layer (APIM validates JWT before routing to backends)
   - **Unauthenticated Endpoints:** Health checks (`/health`, `/ready`) and webhook receiver (`/webhooks/stripe`)
   
   **Risk Context:** Self-registration means low barrier to attacker access. Token validation at gateway provides defense-in-depth - backends assume authenticated requests.
   
   ### Request Path (Internet → Data)
   1. **Entry:** Public internet via CDN/Front Door
   2. **Edge Security:** WAF (90% coverage) + DDoS Protection Standard
   3. **Gateway:** Application Gateway (L7 routing)
   4. **API Management:** Azure APIM (JWT validation, rate limiting)
   5. **Backend Compute:** AKS (private cluster) + App Service (mostly private, 2 legacy public)
   6. **Data Tier:** Azure SQL (private endpoint) + Blob Storage (mostly private endpoint, 1 legacy public)
   
   ### Network Posture
   - Backend services: **Target:** Private VNET integration | **Reality:** 90% private, 2 legacy App Services public
   - Data stores: **Target:** Private endpoints only | **Reality:** 95% private endpoints, 1 legacy Cosmos DB public with firewall
   - Egress: Azure Firewall with allow-list
   
   ### Integration
   - Service Bus for async messaging
   - Event Hubs for event streaming
   
   (Survey completed: 16/02/2026 08:30)
   ```

2. **Identify architectural gaps** and record in `## ⚠️ Coverage Gaps`:
   ```markdown
   ## ⚠️ Coverage Gaps (To Validate During Triage)
   - [ ] **WAF coverage:** 10% of public endpoints unprotected (legacy App Service) - **critical if unauthenticated**
   - [ ] **Backend exposure:** 2 App Services still have public endpoints (legacy, migration pending) - **bypass auth gateway**
   - [ ] **Data tier access:** 1 Cosmos DB account uses public endpoint + firewall (should be private endpoint)
   - [ ] **Unauthenticated endpoints:** Health checks and webhooks - ensure they don't leak sensitive data or allow abuse
   - [ ] **Auth bypass risk:** 2 legacy public backends bypass APIM token validation - attacker can call directly if discovered
   ```

3. **Generate/update architecture diagram** at `Output/Summary/Cloud/Architecture_<Provider>.md`:
   - Use **solid lines** for universal/target state controls
   - Use **dotted lines** for partial coverage / exceptions
   - Add **annotations** showing coverage % where relevant
   - **Highlight gaps** with warning symbols (⚠️)
   
   Example Mermaid annotation:
   ```mermaid
   flowchart TB
       Internet["🌐 Internet"]
       WAF["WAF<br/>✅ 90% coverage<br/>⚠️ Legacy App Service unprotected"]
       AppGW["App Gateway"]
       AKS["AKS<br/>🔒 Private"]
       AppSvc["App Service<br/>⚠️ 2 legacy public"]
       
       Internet -->|HTTPS| WAF
       WAF --> AppGW
       AppGW -->|private| AKS
       AppGW -.->|2 legacy public| AppSvc
   ```

4. **Append full Q&A to audit log** at `Output/Audit/cloud_context_survey.md`

5. **Summarise** what was learned:
   > "✅ Architecture survey complete. Mapped request path: Internet → WAF (90% coverage, legacy App Service unprotected) → App Gateway → AKS (private) + App Service (2 legacy public) → Azure SQL (private endpoint) + Cosmos DB (1 legacy public with firewall). 
   > 
   > **Coverage gaps identified:** WAF 10% gap, 2 public backends, 1 public data store. Defense-in-depth critical given partial coverage."

### During Triage

Use the **risk appetite context** to tailor findings:
- **CMK:** If "PMK acceptable", downgrade CMK findings or mark as "Future enhancement" not critical gaps
- **Service principals:** If "acceptable with justification", findings about SP usage check if justification exists (not blanket fail)
- **Network posture:** If "public with firewall acceptable", findings about public endpoints focus on **firewall rules quality** not existence
- **JIT/Bastion:** If "Bastion acceptable", don't push for JIT in every finding
- **TLS version:** If "TLS 1.2+ acceptable", don't flag 1.2 as urgent (focus on 1.0/1.1 only)
- **Conditional Access:** Critical for credential-based findings (stolen passwords, exposed keys, RBAC issues):
  - **MFA + compliant device + VPN required:** Credential theft findings DOWNGRADE (attacker can't use stolen creds from their device)
    - "Finding: Service principal key exposed in GitHub. Conditional Access enforces managed device + VPN. Severity: MEDIUM (5/10) - attacker cannot use key from personal device."
  - **MFA only (no device compliance):** Credential theft findings MEDIUM severity (attacker can phish MFA but has valid creds)
    - "Finding: Admin password in config file. MFA enabled but no device compliance. Severity: HIGH (7/10) - attacker can use password + phish MFA push."
  - **No Conditional Access:** Credential theft findings HIGH-CRITICAL severity (stolen creds fully usable)
    - "Finding: Storage account key in repo. No Conditional Access. Severity: CRITICAL (9/10) - key works from anywhere, attacker has direct access."
  - **Insider threat context:** Conditional Access reduces but doesn't eliminate insider risk (malicious insider on compliant device still has access)

Use **IaC provider defaults** to contextualize findings:
- **Finding conflicts with secure default:** "Finding: Storage allows public blob access" + "IaC default: `allow_blob_public_access = false`" → **Likely drift/manual change** or legacy resource outside IaC
- **Finding matches insecure default:** "Finding: Key Vault public network access enabled" + "IaC default: `public_network_access_enabled = true`" → **Expected if not explicitly overridden** (note: "This is the IaC provider default; recommend explicit override to false")
- **Advise with default context:** "The azurerm provider v3.x defaults to TLS 1.2 minimum for Storage Accounts. If your finding shows TLS 1.0, this resource may be legacy or provisioned via portal/CLI."
- **Assume secure defaults apply:** Unless evidence suggests otherwise, assume IaC-provisioned resources inherit the provider's defaults

Use **cloud resource native defaults** to set realistic expectations:
- **Azure Storage public:** "Finding: Storage Account has public network access. Azure native default = public endpoint. If using IaC, check if `public_network_access_enabled` is explicitly set to false. If not using IaC (Portal/CLI), public is the default - remediate by enabling private endpoint."
- **Azure SQL public:** "Azure SQL Server defaults to public network access. This finding is expected unless private endpoint was explicitly configured. Severity depends on firewall rules quality."
- **AWS S3 blocks public:** "Finding: S3 bucket allows public access. AWS native default (post-Apr 2023) = block public access. This indicates explicit override or pre-2023 bucket - investigate why public access was enabled."
- **Distinguish native vs IaC:** "Azure Storage native default = public, but azurerm v3.x IaC default = `allow_blob_public_access = false`. If provisioned via IaC with secure defaults, public access suggests drift."

Use the **data flow map and coverage gaps** to:
- **Spot missing layers:** "No WAF detected in flow, but finding recommends WAF → higher priority"
- **Identify bypass risks:** "App Gateway allows direct backend access → compounding issue"
- **Validate assumptions:** "Finding assumes public storage, but 95% use private endpoints → target legacy Cosmos DB specifically"
- **Cross-reference controls:** "WAF at edge mitigates OWASP Top 10 risks for 90% of estate, but legacy App Service exposed"
- **Prioritize exceptions:** "Legacy resources without layered security are highest priority (no WAF, public backend, public data store)"
- **Defense in depth validation:** "Private endpoint on SQL is good, but if backend is public, attacker still reaches it"

Use **authentication flow** to contextualize vulnerability severity:

**User Population Impact:**
- **Public self-registration:** Attacker can easily get valid credentials → SQLi/XSS/RCE findings are HIGH severity (low barrier to exploitation)
- **Vetted users only:** Attacker must be invited/approved → SQLi/XSS/RCE are MEDIUM-HIGH severity (insider threat or compromised account)
- **Internal employees only:** Attacker needs employee credentials → MEDIUM severity (requires phishing/compromise first)
- **Unauthenticated endpoints:** Attacker needs no credentials → CRITICAL severity for any vulnerability

**Token Validation Impact:**
- **Validation at gateway/edge:** Backend SQLi requires passing gateway auth first (defense-in-depth) → MEDIUM severity
- **Each API validates independently:** SQLi in one API doesn't require bypassing centralized auth → HIGH severity
- **No auth on endpoint:** Unauthenticated SQLi/RCE → CRITICAL severity (directly exploitable)

**Bypass Scenarios (compounding issues):**
- "Finding: SQLi in App Service API. Auth flow shows APIM validates tokens. However, 2 legacy App Services are publicly accessible (bypass APIM). If this API is one of those 2, severity is CRITICAL (direct unauthenticated access). Otherwise MEDIUM (requires valid token)."
- "Finding: XSS in admin panel. User model: internal employees only. Severity: MEDIUM (requires employee compromise). However, if admin panel is on a legacy public backend that bypasses auth gateway, severity escalates to HIGH."
- "Finding: Unauthenticated webhook endpoint with command injection. Severity: CRITICAL - no auth required, attacker can trigger directly."

**Examples:**
- **High severity:** "SQLi finding + public self-registration + no WAF + unauthenticated endpoint = CRITICAL 10/10"
- **Medium severity:** "SQLi finding + vetted users only + JWT validated at gateway + WAF in place = MEDIUM 5/10"
- **Compounding:** "SQLi finding + token validation at APIM + BUT legacy App Service bypasses APIM = HIGH 8/10 (auth bypass compounding)"

### Multi-Cloud Handling

If multiple providers detected:
1. Run survey **per provider** (Azure questions, then AWS questions, etc.)
2. Create separate `Output/Knowledge/<Provider>.md` files
3. Create separate architecture diagrams per provider
4. Look for **cross-cloud dependencies** (e.g., Azure VM → AWS RDS) and note them as integration points/risks

## Integration with Main Workflow

Update `Agents/Instructions.md` to reference this agent:
- After cloud provider is confirmed, check if `Output/Knowledge/<Provider>.md` has data flow context
- If sparse/missing: offer to invoke **CloudContextAgent**
- Use the data flow map throughout triage to contextualize findings

## See Also
- Main workflow: `Agents/Instructions.md`
- Knowledge structure: `Output/Knowledge/<Provider>.md`
- Architecture diagrams: `Output/Summary/Cloud/Architecture_<Provider>.md`


## Conditional Access Impact Examples

**Example 1: Exposed service principal key - WITH device compliance**
```markdown
## Finding: Service Principal Key in GitHub Repository

### Security Review
Service principal with Contributor role has key exposed in public repo.

### 🎯 Conditional Access Context
- **Policy:** MFA + Intune-managed device + corporate VPN required
- **Device compliance:** EDR installed, patched, encrypted
- **Impact:** Attacker cannot authenticate from their own device with the stolen key

### Severity Assessment
- **Without Conditional Access:** CRITICAL (10/10) - direct cloud access with Contributor
- **With Conditional Access:** MEDIUM-HIGH (6/10) - key alone insufficient, requires compromised compliant device
- **Residual risk:** Insider threat (malicious insider on compliant device), device compromise

### Remediation Priority
MEDIUM - rotate key immediately, but impact reduced by device compliance. Consider switching to managed identity to eliminate keys entirely.
```

**Example 2: Exposed storage account key - NO Conditional Access**
```markdown
## Finding: Storage Account Key in Source Code

### Security Review
Storage account key with read/write access hardcoded in application code.

### 🎯 Conditional Access Context
- **Policy:** None - storage account keys work from any location/device
- **Impact:** Attacker can use key immediately from anywhere

### Severity Assessment
- **Actual Severity:** CRITICAL (10/10) - no authentication barriers, direct data access
- **Attack scenario:** Copy key → run Azure CLI from attacker laptop → download all blobs

### Remediation Priority
CRITICAL - rotate key immediately, switch to managed identity, scan repos for other exposed keys.
```

**Example 3: Weak RBAC - WITH Conditional Access**
```markdown
## Finding: Overprivileged User Accounts

### Security Review
15 users have Owner role on production subscription (should be minimal).

### 🎯 Conditional Access Context
- **Policy:** MFA + compliant device required
- **No VPN requirement:** Users can authenticate from home if device is compliant
- **Impact:** Reduces but doesn't eliminate credential theft risk

### Severity Assessment
- **Without Conditional Access:** HIGH (8/10) - stolen password = full subscription access
- **With Conditional Access (device only):** MEDIUM-HIGH (6/10) - requires device compromise or malicious insider
- **Residual risk:** Phishing users on their work devices, malicious insider, device theft

### Recommendation
Downgrade severity to 6/10 given device compliance. Still address by implementing PIM/JIT for Owner role + VPN requirement for elevated access.
```

**Example 4: No MFA - Overrides all other controls**
```markdown
## Finding: MFA Not Enforced

### Impact on Other Controls
If MFA is not enforced, Conditional Access device compliance is **BYPASSED** - attacker only needs username/password.

### Severity
CRITICAL (10/10) - Device compliance checks occur AFTER authentication. No MFA = no device check.

### Dependencies
This finding is a **compounding multiplier** for all credential-based findings. Must fix first.
```
