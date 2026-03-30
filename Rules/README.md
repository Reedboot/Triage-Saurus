# Rules

Rules follow the two-phase scan pipeline:

1. **Detection** — discover what assets exist (INFO severity, no findings)
2. **Misconfigurations** — find what is wrong with those assets (WARN/ERROR severity, generates findings)

---

## Detection/

Run first. Maps the attack surface — what resources, services, frameworks, and auth patterns exist.

```
Detection/
├── Azure/          Azure resource detection (Terraform/HCL)
├── AWS/            AWS resource detection (Terraform/HCL)
├── GCP/            GCP resource detection (Terraform/HCL)
├── Code/           Code-level detection (JWT auth, APIM middleware, etc.)
├── AppConfig/      App config / connection string detection
├── Containers/     Dockerfile base image detection
└── Frameworks/     Language and framework version detection
```

### Recent context-discovery additions

- `Code/ingress-wildcard-bind-detection.yml`
	- Detects wildcard runtime bind/listen patterns (for example `0.0.0.0`, `listen(*)`, `.NET UseUrls` wildcard forms).
	- Used as ingress posture signal by context extraction.
- `AppConfig/cloud-endpoint-dependency-detection.yml`
	- Detects AWS RDS, AWS S3, and GCP Cloud SQL endpoint references in app/config files.
	- Used to seed external dependency inference before Python fallback scanning.

These are `context_discovery` rules (INFO signal extraction) and are consumed by `Scripts/Context/context_extraction.py` in a rules-first, Python-fallback flow.

---

## Misconfigurations/

Run second, scoped to the resource types found in Phase 1.
Organised by provider → resource type so scans can be targeted precisely.

```
Misconfigurations/
├── Azure/
│   ├── AKS/                AKS cluster misconfigurations
│   ├── AppService/         App Service misconfigurations
│   ├── ContainerRegistry/  ACR misconfigurations
│   ├── IAM/                Managed Identity, Service Principal, AAD
│   ├── KeyVault/           Key Vault misconfigurations
│   ├── NSG/                Network Security Group misconfigurations
│   ├── SQL/                Azure SQL misconfigurations
│   ├── Storage/            Storage Account misconfigurations
│   └── VM/                 Virtual Machine misconfigurations
├── AWS/
│   ├── EC2/                EC2 instance misconfigurations
│   ├── IAM/                IAM policy misconfigurations
│   ├── RDS/                RDS instance misconfigurations
│   └── SecurityGroup/      Security Group misconfigurations
├── GCP/
│   ├── CloudSQL/           Cloud SQL misconfigurations
│   └── ComputeFirewall/    Compute firewall misconfigurations
├── Kubernetes/
│   ├── Workload/           Pod/container security (privileged, root, capabilities...)
│   ├── RBAC/               RBAC misconfigurations (wildcard, cluster-admin...)
│   ├── Ingress/            Ingress misconfigurations (no TLS, no auth, no rate limit)
│   └── Service/            Service exposure (NodePort, LoadBalancer public...)
├── Terraform/
│   ├── Secrets/            Hardcoded secrets and credential exposure in HCL
│   ├── State/              Backend and state management misconfigurations
│   └── Providers/          Provider version pinning
├── CICD/                   CI/CD pipeline misconfigurations
└── Secrets/                Cross-provider hardcoded credential patterns
```

---

## Targeted scanning

`targeted_scan.py` automates the two-phase approach — run it instead of calling opengrep directly:

```bash
python3 Scripts/targeted_scan.py /path/to/repo --experiment <id> --repo <name>

# Preview what would run without scanning
python3 Scripts/targeted_scan.py /path/to/repo --experiment <id> --repo <name> --dry-run

# Detection only (asset inventory, no findings)
python3 Scripts/targeted_scan.py /path/to/repo --experiment <id> --repo <name> --detection-only
```

What it does:
1. Runs `Detection/` rules → identifies which resource types exist
2. Maps fired rule IDs → only the relevant `Misconfigurations/<Provider>/<ResourceType>/` folders
3. Runs a single targeted scan against those folders only
4. Streams findings directly into `store_findings.py` for DB persistence (no intermediate scan JSON artifact)

`triage_experiment.py run <id>` calls `targeted_scan.py` automatically.

Manual opengrep is still available for ad-hoc checks:
```bash
# Single resource type
opengrep scan --config Rules/Misconfigurations/Azure/Storage/ /path/to/repo

# All Azure
opengrep scan --config Rules/Misconfigurations/Azure/ /path/to/repo
```

---

## Adding Rules

See [CreationGuide.md](CreationGuide.md) for naming conventions, metadata standards, and testing.
