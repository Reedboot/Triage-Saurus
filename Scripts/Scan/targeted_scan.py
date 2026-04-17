#!/usr/bin/env python3
"""
targeted_scan.py — Two-phase targeted scan.

Phase 1: Run Detection rules → identify which resource types exist.
Phase 2: Run only the Misconfiguration subfolders relevant to detected assets.

Usage:
    python3 Scripts/targeted_scan.py <target_path> --experiment <id> --repo <name>
    python3 Scripts/targeted_scan.py <target_path> --experiment <id> --repo <name> --dry-run
    python3 Scripts/targeted_scan.py <target_path> --experiment <id> --repo <name> --detection-only
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Utils"))
from log_formatter import format_scan_complete

# ── Root paths ────────────────────────────────────────────────────────────────

SCRIPT_DIR   = Path(__file__).resolve().parent
REPO_ROOT    = SCRIPT_DIR.parent.parent
RULES_ROOT   = REPO_ROOT / "Rules"
DETECTION    = RULES_ROOT / "Detection"
MISCONFIGS   = RULES_ROOT / "Misconfigurations"

# ── Detection rule ID → Misconfiguration folder(s) ───────────────────────────
# Maps a fired detection rule ID to one or more relative Misconfiguration paths.

DETECTION_TO_MISCONFIG: dict[str, list[str]] = {
    # Azure — Compute / Containers
    "context-azure-aks-cluster":               ["Azure/AKS", "Kubernetes/Workload", "Kubernetes/RBAC", "Kubernetes/Ingress", "Kubernetes/Service"],
    "context-azure-app-service-plan":          ["Azure/AppService"],
    "context-azure-function-app":              ["Azure/AppService"],
    "context-azure-app-service-environment":   ["Azure/AppService"],
    "context-azure-container-registry":        ["Azure/ContainerRegistry"],
    "context-azure-linux-vm":                 ["Azure/VM", "Azure/Compute"],
    "context-azure-virtual-machine":          ["Azure/VM", "Azure/Compute"],
    "context-azure-windows-vm":               ["Azure/VM", "Azure/Compute"],
    "context-azure-nsg":                      ["Azure/NSG"],

    # Azure — Data
    "context-azure-sql-server":                ["Azure/SQL"],
    "context-azure-sql-database":              ["Azure/SQL"],
    "context-azure-cosmosdb-account":          ["Azure/CosmosDB"],
    "context-azure-storage-account":           ["Azure/Storage"],
    "context-azure-storage-container":         ["Azure/Storage"],

    # Azure — Identity
    "context-azure-service-principal":         ["Azure/IAM"],
    "context-azure-user-assigned-identity":    ["Azure/IAM"],
    "context-azuread-application":             ["Azure/IAM"],
    "context-azuread-application-password":    ["Azure/IAM"],
    "context-azuread-app-role-assignment":     ["Azure/IAM"],
    "context-azuread-group":                   ["Azure/IAM"],
    "context-azuread-group-member":            ["Azure/IAM"],

    # Azure — Security / Networking
    "context-azure-keyvault":                  ["Azure/KeyVault"],
    "context-azure-keyvault-secret":           ["Azure/KeyVault"],

    # Azure — Application Config (connection strings suggest secrets)
    "context-azure-sql-connection-string":     ["Secrets"],
    "context-azure-storage-connection":        ["Secrets"],
    "context-azure-servicebus-connection":     ["Secrets"],

    # AWS — Compute / Containers
    "context-aws-eks-cluster":                 ["Kubernetes/Workload", "Kubernetes/RBAC", "Kubernetes/Ingress", "Kubernetes/Service", "AWS/IAM"],
    "context-aws-eks-cluster-module":          ["Kubernetes/Workload", "Kubernetes/RBAC", "Kubernetes/Ingress", "Kubernetes/Service", "AWS/IAM"],
    "context-aws-ec2-instance":                ["AWS/EC2", "AWS/SecurityGroup"],
    "context-aws-lambda-function":             ["AWS/IAM", "Secrets"],

    # AWS — Networking
    "context-aws-load-balancer":               ["AWS/SecurityGroup"],
    "context-aws-load-balancer-module":        ["AWS/SecurityGroup", "Kubernetes/Ingress"],
    "context-aws-security-group":              ["AWS/SecurityGroup"],
    "context-aws-vpc":                         ["AWS/SecurityGroup"],
    "context-aws-vpc-module":                  ["AWS/SecurityGroup"],
    "context-aws-api-gateway-rest":            ["AWS/IAM", "Secrets"],
    "context-aws-cloudfront-distribution":     ["AWS/SecurityGroup"],

    # AWS — Helm (ingress/LB controller trigger K8s rules)
    "context-aws-helm-ingress-nginx":          ["Kubernetes/Ingress", "Kubernetes/Service", "AWS/SecurityGroup"],
    "context-aws-helm-lb-controller":          ["Kubernetes/Ingress", "AWS/SecurityGroup"],
    "context-helm-release-generic":            ["Kubernetes/Workload"],
    "context-kubernetes-manifest":              ["Kubernetes/Workload", "Kubernetes/RBAC", "Kubernetes/Ingress", "Kubernetes/Service"],

    # AWS — Data
    "context-aws-rds-instance":                ["AWS/RDS"],
    "context-aws-dynamodb-table":              ["AWS/IAM"],
    "context-aws-s3-bucket":                   ["AWS/S3", "Secrets"],

    # AWS — Identity & Secrets
    "context-aws-iam-role":                    ["AWS/IAM"],
    "context-aws-kms-key":                     ["AWS/IAM"],

    # GCP
    "context-gcp-cloud-sql-instance":          ["GCP/CloudSQL", "GCP/ComputeFirewall"],

    # Framework / language detections → always add Secrets scan
    "context-python-requirements":             ["Secrets"],
    "context-nodejs-package-json":             ["Secrets"],
    "context-dotnet-project":                  ["Secrets"],
    "context-golang-module":                   ["Secrets"],
    "context-java-maven-project":              ["Secrets"],

    # Code-level auth pattern detections
    "context-dotnet-jwt-addjwtbearer":         ["Secrets"],
    "context-dotnet-jwt-parser":               ["Secrets"],
    "context-dotnet-apim-redirect-middleware": ["Secrets"],
    "context-node-jwt-auth":                   ["Secrets"],
    "context-go-jwt-auth":                     ["Secrets"],
    "context-java-spring-jwt":                 ["Secrets"],

    # AppConfig — connection strings always imply secrets scan
    "context-azure-appinsights-connection":    ["Secrets"],
    "context-azure-redis-connection":          ["Secrets"],
    "context-azure-servicebus-connection-appconfig": ["Secrets"],
    "context-cicd-pipeline":                      ["CICD"],

    # Azure Key Vault children
    "context-azure-keyvault-key":                  ["Azure/KeyVault"],
    "context-azure-keyvault-certificate":          ["Azure/KeyVault"],

    # Azure VM extension
    "context-azure-vm-extension":                  ["Azure/VM", "Azure/Compute"],

    # Azure networking
    "context-azure-subnet":                        ["Azure/NSG"],
    "context-azure-network-interface":             ["Azure/NSG"],
    "context-azure-public-ip":                     ["Azure/NSG"],

    # Azure Service Bus
    "context-azure-servicebus-namespace":          ["Azure/ServiceBus", "Secrets"],
    "context-azure-servicebus-queue":              ["Azure/ServiceBus", "Secrets"],
    "context-azure-servicebus-topic":              ["Azure/ServiceBus", "Secrets"],
    "context-azure-servicebus-subscription":       ["Azure/ServiceBus", "Secrets"],

    # Azure EventHub (Rules/Misconfigurations/Azure/EventHub does not exist yet)
    "context-azure-eventhub":                      [],
    "context-azure-eventhub-consumer-group":       [],

    # Azure AKS node pool
    "context-azure-aks-node-pool":                 ["Azure/AKS"],
    "context-azure-kubernetes-backend-deployment": ["Kubernetes/Workload", "Kubernetes/RBAC", "Azure/AKS"],

    # Azure Storage children
    "context-azure-storage-blob":                  ["Azure/Storage"],
    "context-azure-storage-queue":                 ["Azure/Storage"],
    "context-azure-storage-share":                 ["Azure/Storage"],

    # Azure CosmosDB (Rules/Misconfigurations/Azure/CosmosDB does not exist yet)
    "context-azure-cosmosdb-sql-database":         [],
    "context-azure-cosmosdb-sql-container":        [],

    # Azure databases
    "context-azure-mysql-database":                ["Azure/SQL"],
    "context-azure-postgresql-database":           ["Azure/SQL"],
    "context-azure-mssql-firewall-rule":           ["Azure/SQL"],

    # Azure NSG rule
    "context-azure-nsg-rule":                      ["Azure/NSG"],

    # Azure APIM
    "context-azure-api-management-api":            ["Azure/APIM", "Secrets"],
    "context-azure-apim-backend-routing":          ["Azure/APIM", "Secrets"],

    # Alicloud detection rules
    "context-alicloud-ecs-instance":               ["Alicloud/SecurityGroup"],
    "context-alicloud-ack-cluster":                ["Alicloud/ACK"],
    "context-alicloud-ack-node-pool":              ["Alicloud/ACK"],
    "context-alicloud-oss-bucket":                 ["Alicloud/OSS"],
    "context-alicloud-rds-instance":               ["Alicloud/RDS"],
    "context-alicloud-kms-key":                    [],
    "context-alicloud-kms-secret":                 [],
    "context-alicloud-vpc":                        [],
    "context-alicloud-vswitch":                    [],
    "context-alicloud-security-group":             ["Alicloud/SecurityGroup"],
    "context-alicloud-security-group-rule":        ["Alicloud/SecurityGroup"],
    "context-alicloud-ram-role":                   ["Alicloud/IAM"],
    "context-alicloud-ram-policy":                 ["Alicloud/IAM"],
    "context-alicloud-log-project":                [],
    "context-alicloud-log-store":                  [],
    "context-alicloud-slb":                        [],
    "context-alicloud-fc-function":                [],
    "context-alicloud-redis-instance":             [],

    # OCI detection rules
    "context-oci-compute-instance":                ["OCI/Compute"],
    "context-oci-oke-cluster":                     ["OCI/OKE"],
    "context-oci-oke-node-pool":                   ["OCI/OKE"],
    "context-oci-objectstorage-bucket":            ["OCI/ObjectStorage"],
    "context-oci-database":                        ["OCI/Database"],
    "context-oci-mysql":                           ["OCI/Database"],
    "context-oci-kms-vault":                       [],
    "context-oci-kms-key":                         [],
    "context-oci-vault-secret":                    [],
    "context-oci-vcn":                             ["OCI/Network"],
    "context-oci-subnet":                          ["OCI/Network"],
    "context-oci-nsg":                             ["OCI/Network"],
    "context-oci-load-balancer":                   [],
    "context-oci-functions":                       [],
    "context-oci-apigateway":                      [],
    "context-oci-logging":                         [],
    "context-oci-identity-policy":                 ["OCI/IAM"],
    "context-oci-container-registry":              [],

    # Tencent Cloud detection rules
    "context-tencentcloud-cvm-instance":           [],
    "context-tencentcloud-tke-cluster":            ["Kubernetes/Workload", "Kubernetes/RBAC", "Kubernetes/Ingress", "Kubernetes/Service"],
    "context-tencentcloud-tke-node-pool":          ["Kubernetes/Workload", "Kubernetes/RBAC", "Kubernetes/Ingress", "Kubernetes/Service"],
    "context-tencentcloud-cos-bucket":             ["Secrets"],
    "context-tencentcloud-mysql-instance":         ["SQL"],
    "context-tencentcloud-postgresql-instance":    ["SQL"],
    "context-tencentcloud-kms-key":                ["Secrets"],
    "context-tencentcloud-vpc":                    [],
    "context-tencentcloud-subnet":                 [],
    "context-tencentcloud-security-group":         [],
    "context-tencentcloud-security-group-rule":    [],
    "context-tencentcloud-clb-instance":           [],
    "context-tencentcloud-apigateway-service":     ["Secrets"],
    "context-tencentcloud-apigateway-api":         ["Secrets"],
    "context-tencentcloud-cam-role":               [],
    "context-tencentcloud-cam-policy":             [],

    # Huawei Cloud detection rules
    "context-huaweicloud-ecs-instance":            [],
    "context-huaweicloud-cce-cluster":             ["Kubernetes/Workload", "Kubernetes/RBAC", "Kubernetes/Ingress", "Kubernetes/Service"],
    "context-huaweicloud-cce-node-pool":           ["Kubernetes/Workload", "Kubernetes/RBAC", "Kubernetes/Ingress", "Kubernetes/Service"],
    "context-huaweicloud-obs-bucket":              ["Secrets"],
    "context-huaweicloud-rds-instance":            ["SQL"],
    "context-huaweicloud-gaussdb-instance":        ["SQL"],
    "context-huaweicloud-kms-key":                 ["Secrets"],
    "context-huaweicloud-vpc":                     [],
    "context-huaweicloud-vpc-subnet":              [],
    "context-huaweicloud-security-group":          [],
    "context-huaweicloud-security-group-rule":     [],
    "context-huaweicloud-elb-loadbalancer":        [],
    "context-huaweicloud-apigw-instance":          ["Secrets"],
    "context-huaweicloud-apigw-group":             ["Secrets"],
    "context-huaweicloud-iam-group":               [],
    "context-huaweicloud-iam-role":                [],
}

# ── Always-on folders (run regardless of what was detected) ──────────────────

ALWAYS_INCLUDE: list[str] = [
    "Terraform/Secrets",
    "Terraform/State",
    "Terraform/Providers",
    "Secrets",
]

# ── File-pattern fallbacks (deprecated) ───────────────────────────────────────
# File-pattern fallback logic has been removed. Detection is performed by opengrep rules only.
# If a resource type is not detected, add a Detection rule under Rules/Detection and map
# it in DETECTION_TO_MISCONFIG so scans include the appropriate misconfig checks.
FILE_PATTERN_FALLBACKS: list[tuple[str, str, list[str]]] = []


def run_opengrep(config_paths: list[Path], target: Path, label: str) -> dict:
    """Run opengrep scan and return parsed JSON results from stdout."""
    cmd = ["opengrep", "scan"]
    for config_path in config_paths:
        cmd += ["--config", str(config_path)]
    cmd += [str(target), "--json", "--quiet"]

    print(f"\n[{label}] Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    # opengrep exits non-zero when findings exist — that's expected
    stdout = (result.stdout or "").strip()
    if stdout:
        try:
            return json.loads(stdout)
        except json.JSONDecodeError as exc:
            print(f"[error] Failed to parse opengrep JSON output: {exc}", file=sys.stderr)
            if result.stderr:
                print(result.stderr, file=sys.stderr)
            sys.exit(1)
    # If no stdout (e.g. zero findings), return empty structure
    return {"results": [], "errors": [], "paths": {"scanned": []}}


def extract_fired_rule_ids(scan_data: dict) -> set[str]:
    """Return the set of rule IDs that fired in a detection scan."""
    return {r["check_id"] for r in scan_data.get("results", [])}


def resolve_misconfig_paths(fired_ids: set[str], target: Path) -> list[Path]:
    """
    Map fired detection IDs → relevant Misconfiguration folders.
    Also runs file-pattern fallbacks for resource types with no detection rule.
    Returns deduplicated, existing paths.
    """
    folders: set[str] = set(ALWAYS_INCLUDE)

    # Map from fired detection rule IDs
    for rule_id in fired_ids:
        # Extract just the context part (e.g., "context-azure-aks-cluster" from "Rules.Detection.Azure.context-azure-aks-cluster")
        context_id = rule_id.split('.')[-1] if '.' in rule_id else rule_id
        for folder in DETECTION_TO_MISCONFIG.get(context_id, []):
            folders.add(folder)

    # No file-pattern fallback scanning is performed. Rely on opengrep detection rule hits.
    # Add detection rules to Rules/Detection and update DETECTION_TO_MISCONFIG if additional
    # resource types should map to specific misconfiguration folders.

    # Resolve to absolute Paths, filtering to those that actually exist
    resolved = []
    for folder in sorted(folders):
        path = MISCONFIGS / folder
        if path.exists():
            resolved.append(path)
        else:
            print(f"  [warn] Misconfiguration folder not found (skipped): {path}")

    return resolved


def print_detection_summary(fired_ids: set[str], misconfig_paths: list[Path]) -> None:
    print(f"\n{'─'*60}")
    print(f"Detection: {len(fired_ids)} rule(s) fired")
    if fired_ids:
        for rule_id in sorted(fired_ids):
            # Extract context part for display
            context_id = rule_id.split('.')[-1] if '.' in rule_id else rule_id
            mapped = DETECTION_TO_MISCONFIG.get(context_id, [])
            tag = f"→ {', '.join(mapped)}" if mapped else "(no misconfig mapping)"
            print(f"  {context_id}  {tag}")
    print(f"\nTargeted misconfig folders ({len(misconfig_paths)}):")
    for p in misconfig_paths:
        print(f"  {p.relative_to(REPO_ROOT)}")
    print(f"{'─'*60}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Two-phase targeted opengrep scan")
    parser.add_argument("target",           help="Path to repository to scan")
    parser.add_argument("--experiment",     required=True, help="Experiment ID (e.g. 003)")
    parser.add_argument("--repo",           required=True, help="Repository name (e.g. terragoat)")
    parser.add_argument("--dry-run",        action="store_true",
                        help="Show what would run without executing Phase 2 scan")
    parser.add_argument("--detection-only", action="store_true",
                        help="Run Phase 1 detection only, skip Phase 2")
    args = parser.parse_args()

    target = Path(args.target).resolve()
    if not target.exists():
        print(f"[error] Target path does not exist: {target}", file=sys.stderr)
        sys.exit(1)

    # ── Phase 1: Detection ────────────────────────────────────────────────────
    print("=" * 60)
    print("PHASE 1 — Detection (asset discovery)")
    print("=" * 60)

    detection_data = run_opengrep([DETECTION], target, "Detection")
    fired_ids = extract_fired_rule_ids(detection_data)

    misconfig_paths = resolve_misconfig_paths(fired_ids, target)
    print_detection_summary(fired_ids, misconfig_paths)

    if args.detection_only:
        print("[detection-only] Stopping after Phase 1.")
        sys.exit(0)

    if not misconfig_paths:
        print("[warn] No applicable misconfiguration folders found. Exiting.")
        sys.exit(0)

    if args.dry_run:
        print("[dry-run] Would run Phase 2 against:")
        for p in misconfig_paths:
            print(f"  --config {p}")
        sys.exit(0)

    # ── Phase 2: Targeted Misconfigurations ───────────────────────────────────
    print("=" * 60)
    print("PHASE 2 — Targeted Misconfigurations")
    print("=" * 60)

    print(f"\n[Misconfigurations] Running targeted scan...")
    print(f"  Configs: {len(misconfig_paths)} folder(s)")
    print(f"  Target:  {target}")
    scan_data = run_opengrep(misconfig_paths, target, "Misconfigurations")
    finding_count = len(scan_data.get("results", []))
    print(f"\n[Misconfigurations] {finding_count} finding(s) ready for DB persistence")

    # ── Phase 3: Store findings in DB ─────────────────────────────────────────
    print("\n" + "=" * 60)
    print("PHASE 3 — Storing findings in DB")
    print("=" * 60)

    store_cmd = [
        sys.executable,
        str(REPO_ROOT / "Scripts" / "Persist" / "store_findings.py"),
        "--stdin-json",
        "--experiment", args.experiment,
        "--repo", args.repo,
    ]
    print(f"\n[Store] Running: {' '.join(store_cmd)}")
    subprocess.run(store_cmd, check=True, text=True, input=json.dumps(scan_data))

    print(f"\n{format_scan_complete()}")
    print("  Findings persisted directly to DB")
    print(f"\nNext steps:")
    print(f"  python3 Scripts/Enrich/enrich_findings.py --experiment {args.experiment}")
    print(f"  python3 Scripts/run_skeptics.py --experiment {args.experiment} --reviewer all")
    print(f"  python3 Scripts/Generate/generate_diagram.py --experiment-id {args.experiment}")


if __name__ == "__main__":
    main()
