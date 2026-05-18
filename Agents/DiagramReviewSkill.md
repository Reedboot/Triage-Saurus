## Invocation & Output

**Script:** `python3 Scripts/Validate/review_generated_diagrams.py --base-url http://127.0.0.1:9000`

**Output artifacts** (written under `Output/Audit/DiagramReviewSkill_<timestamp>/`):
- `diagram_review_report.md` — main before/after report with metrics table
- `baseline/` — baseline pass summaries and provider-tab screenshots
  - `screenshots/` — complete multi-view capture set (panned/scrolled for large diagrams)
- `after/` — after pass summaries and screenshots (post rule-apply cycle)
  - `screenshots/` — complete multi-view capture set (panned/scrolled for large diagrams)

**Full SKILL.md** (Copilot slash command): `.github/skills/diagram-review/SKILL.md`

---

# 🔍 Diagram Review Skill

## Role
Review generated architecture diagrams using browser-rendered evidence (Playwright screenshots) and structural checks from a **security architect** perspective.

## Core Rule
For each diagram element, answer: **why is it here and how does it support the threat model?**

If an element is unconnected, unnested, or unsupported by evidence, treat it as a strong detection smell and investigate whether:
- detection rules are missing (`Rules/Detection/*`)
- hierarchy extraction logic is incomplete
- diagram generation/parsing logic dropped relationships

## Screenshot Capture for Large Diagrams
The diagram review automatically captures multiple viewport views for diagrams that exceed standard browser size:
- **Initial capture** — Viewport at default zoom/pan position
- **Horizontal panning** — Scrolls left-to-right to capture wide diagrams
- **Vertical panning** — Scrolls top-to-bottom to capture tall diagrams
- **Multi-quadrant capture** — For very large diagrams, captures all four quadrants systematically

Each captured view is stored separately in the `screenshots/` folder with metadata indicating the viewport position and visible elements. This ensures that even large cloud architectures with 50+ resources can be fully reviewed element-by-element.

**Note:** When reviewing panned screenshots, cross-reference element positions across multiple views to identify connections that may span viewport boundaries.

## Required Workflow
1. Run baseline diagram validation with screenshots for each provider tab.
   - For large diagrams, the tool automatically captures multi-view screenshots via panning
2. Review each issue with element-level rationale:
   - attack-path contribution (ingress, trust boundary, identity, data, control plane)
   - smell severity when connection/hierarchy is missing
   - Cross-check element visibility across panned screenshots if diagram is large
3. Propose or update OpenGrep detection rules for confirmed gaps.
4. Validate each new/updated rule:
   - `opengrep scan --config <rule-file.yml> <target-repo>`
5. Re-run scan + diagram validation.
6. Produce before/after report with:
   - screenshots (multi-view for large diagrams)
   - issue deltas
   - rule changes + validation results
   - unresolved risks

## Hierarchy Expectations
- Child resources should be nested under logical parents (examples):

  **Azure**
  - Storage Account → Container → Blob/Object
  - SQL Server/Instance → Database
  - API Management → APIs/Operations
  - Service Bus Namespace → Queue / Topic → Subscription

  **AWS**
  - S3 Bucket → Object Prefix
  - API Gateway → Stage → Resource/Method
  - RDS Instance → Database

  **GCP**
  - Project → VPC Network → Subnet
  - Pub/Sub Topic → Subscription
  - Cloud SQL Instance → Database
  - GKE Cluster → Node Pool

  **Alibaba Cloud (Alicloud)**
  - OSS Bucket → Object Prefix
  - RDS Instance → Database
  - VPC → VSwitch → ECS Instance
  - API Gateway Group → API
  - MQ (RocketMQ) Instance → Topic → Group

  **Oracle Cloud (OCI)**
  - Compartment → VCN → Subnet
  - Object Storage Bucket → Object Prefix
  - Autonomous Database → Schema
  - API Gateway → Deployment → Route
  - Streaming → Stream Pool → Stream

  **Kubernetes**
  - Cluster → Namespace → Workload (Deployment / StatefulSet / DaemonSet)
  - Workload → Pod → Container
  - Namespace → Service → Endpoint
  - Ingress → Service → Pod
  - ConfigMap / Secret scoped to Namespace (flag if shown at cluster-level without justification)

If children appear flat while parent context exists, flag as hierarchy smell.

## Unknown / External Client Handling
When a client, app, or service calls cloud resources but its hosting is not known from IaC evidence:

- **Represent it explicitly** — show an `External Client` or `Unknown Origin` node rather than leaving the call-path unmodelled.
- **Flag the ambiguity** — mark the node with ⚠ and note: *"origin not confirmed in IaC; may be SaaS, on-prem, 3rd-party, or another cloud"*.
- **Treat as high-risk until proven otherwise** — an uncontrolled caller reaching cloud services (storage, databases, queues) is a potential trust boundary violation.
- **Investigate for evidence:**
  1. Check for API keys / client credentials in config files or CI/CD secrets — may indicate an external system.
  2. Check CORS / allowlist settings on the cloud service — may reveal expected callers.
  3. Check IaC `allowed_origins`, `ip_rules`, `network_acls`, or WAF rules for expected source ranges.
  4. If the caller is another cloud provider or region, flag cross-cloud data-flow as a supply-chain risk.
- **Do not drop the node** — an unhosted caller left off the diagram is a missing threat-model element, not noise.

## HTML Entity Checking in Diagram Elements
The diagram validation automatically detects HTML entities in element names and labels that may cause rendering issues:

### Common problematic HTML entities:
- `&gt;` — Should be `>`
- `&lt;` — Should be `<`
- `&amp;` — Should be `&`
- `&br;` — Should be a space or line break (context-dependent)
- `&quot;` — Should be `"` (unless literal quote is needed)
- `&nbsp;` — Should be a regular space
- `&#...;` — Numeric HTML entities that should use plain characters

These encoded entities often result from unintended escaping during diagram generation and should be replaced with their plain-text equivalents. The validation framework will warn on detection and suggest the appropriate replacement character.

## Noise Reduction Guidance
Call out resource types that likely should not appear on architecture diagrams when they do not contribute to threat modeling (e.g., pure metadata/config scaffolding). Keep rationale explicit and evidence-backed.

## Enhanced Security Architecture Review

### Resource Type Classification
For **each diagram element**, verify:
- ✓ **Has proper icon/classification** (can identify resource type)
- ✓ **Missing icon = detection gap** (resource type detection rule missing or incomplete)
- ✓ **Examples:** Route Tables, Security Groups, EC2 Instances should have icons representing their type

#### Icon Validation & Creation Workflow
When reviewing diagram icons:
1. **Verify icon correctness** — does the displayed icon match the cloud provider's official resource icon?
   - AWS: Check AWS icon library (https://aws.amazon.com/architecture/icons/)
   - Azure: Check Azure icon library (https://learn.microsoft.com/en-us/azure/architecture/icons/)
   - GCP: Check GCP icon library (https://cloud.google.com/architecture/icons/)
   
2. **Missing or incorrect icons** — if icon is missing or wrong:
   - Search the internet for the official cloud resource icon
   - Document the resource type (e.g., "AWS Route Table", "Azure Application Gateway")
   - Capture or reference the official icon URL
   
3. **Create SVG version** — if icon doesn't exist in diagram library:
   - Download or screenshot the official icon
   - Convert to SVG format using online tools (e.g., Convertio, CloudConvert, Inkscape)
   - Create appropriately sized SVG (suggest 64x64 or 128x128px)
   - Name convention: `<provider>-<resource-type>.svg` (e.g., `aws-route-table.svg`, `azure-api-gateway.svg`)
   - Add to diagram generation icon library for future use
   - Document icon source and creation date in icon metadata comments
   
4. **Flag in review report**:
   - ⚠ **Icon gap:** Resource type without proper visual representation
   - ✓ **Icon added:** New SVG created and integrated
   - Include icon comparison (before/after) if updated

### Connection Validity
Verify **all diagram edges** (connections) represent actual data flows:
- ✓ **Cross-reference with IaC** (does dependency exist in Terraform/CloudFormation?)
- ✓ **Flag high-fan-out resources** (>5 connections: are they all justified?)
- ✓ **Examples to investigate:**
  - Lambda functions connecting to many S3 buckets/DynamoDB tables
  - API Gateways connecting to multiple Lambdas
  - RDS instances accessed by multiple application services
- ⚠ **Suspicious pattern:** High connectivity without clear architectural role

### Security Posture Analysis
Identify **security-relevant resource states**:

#### Storage Buckets
- ✓ **Check bucket policies** (who can access?)
- ✓ **Classify as private or internet-accessible**
- ✓ **Flag public ACLs or open policies** (PublicRead, PublicReadWrite)
- ✓ **Check public access block settings**
- ✓ **Note:** Bucket policy + internet-accessible ingress = high-risk

#### Lambda Functions
- ✓ **Trace IAM role permissions** (what can Lambda access?)
- ✓ **Check if role allows S3, DynamoDB, RDS, Secrets Manager access**
- ✓ **Note:** High-connectivity Lambdas need scrutiny → verify permissions are least-privilege
- ✓ **Example:** If Lambda connects to 5 S3 buckets, verify role only allows needed buckets

#### Network Exposure
- ✓ **Security Groups:** Check ingress rules (0.0.0.0/0 = world-accessible)
- ✓ **Route Tables:** Identify routes to Internet Gateway (what's internet-facing?)
- ✓ **EC2 Instances:** Check if in public subnet + has public IP
- ✓ **API Gateways/Load Balancers:** Assume internet-facing, verify they're protected

### Investigation Checklist
When diagram shows unexplained or suspicious connectivity:
1. **Locate IaC definition** (Terraform resource block)
2. **Trace depends_on** (Terraform dependencies)
3. **Check IAM/policies** (permissions allowing access)
4. **Verify threat model relevance** (does connection serve security-relevant purpose?)
5. **Document finding** (why it exists, risk assessment, recommendation)

### Multi-View Screenshot Review (Large Diagrams)
For diagrams requiring multiple panned views to capture all elements:
1. **Review each screenshot in sequence** — Cross-check element visibility across viewport pans
2. **Identify boundary-crossing connections** — Some edges may span multiple screenshots; trace them across views
3. **Build mental model of full diagram** — Assemble panned views mentally to understand global architecture
4. **Check for orphans in secondary views** — Orphaned elements may appear only in non-initial screenshots
5. **Document which view shows each key element** — Note in review for reproducibility (e.g., "API Gateway visible in top-left pan")
6. **Flag if panned views are incomplete** — If no screenshot captures a known resource, escalate as coverage gap
