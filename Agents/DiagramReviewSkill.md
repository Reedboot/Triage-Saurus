# 🔍 Diagram Review Skill

## Role
Review generated architecture diagrams using browser-rendered evidence (Playwright screenshots) and structural checks from a **security architect** perspective.

## Core Rule
For each diagram element, answer: **why is it here and how does it support the threat model?**

If an element is unconnected, unnested, or unsupported by evidence, treat it as a strong detection smell and investigate whether:
- detection rules are missing (`Rules/Detection/*`)
- hierarchy extraction logic is incomplete
- diagram generation/parsing logic dropped relationships

## Required Workflow
1. Run baseline diagram validation with screenshots for each provider tab.
2. Review each issue with element-level rationale:
   - attack-path contribution (ingress, trust boundary, identity, data, control plane)
   - smell severity when connection/hierarchy is missing
3. Propose or update OpenGrep detection rules for confirmed gaps.
4. Validate each new/updated rule:
   - `opengrep scan --config <rule-file.yml> <target-repo>`
5. Re-run scan + diagram validation.
6. Produce before/after report with:
   - screenshots
   - issue deltas
   - rule changes + validation results
   - unresolved risks

## Hierarchy Expectations
- Child resources should be nested under logical parents (examples):
  - Storage Account → Container → Blob/Object
  - SQL Server/Instance → Database
  - API Management/API Gateway → APIs/Operations
  - Service Bus/Topic Namespace → Queues/Subscriptions

If children appear flat while parent context exists, flag as hierarchy smell.

## Noise Reduction Guidance
Call out resource types that likely should not appear on architecture diagrams when they do not contribute to threat modeling (e.g., pure metadata/config scaffolding). Keep rationale explicit and evidence-backed.

## Enhanced Security Architecture Review (NEW)

### Resource Type Classification
For **each diagram element**, verify:
- ✓ **Has proper icon/classification** (can identify resource type)
- ✓ **Missing icon = detection gap** (resource type detection rule missing or incomplete)
- ✓ **Examples:** Route Tables, Security Groups, EC2 Instances should have icons representing their type

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
