from collections import Counter
from datetime import datetime
from pathlib import Path
import re
import sqlite3

from models import RepositoryContext
from template_renderer import render_template
from markdown_validator import validate_markdown_file
import resource_type_db as _rtdb

# Lazy DB connection — initialised on first use, None if DB unavailable
_db_conn: sqlite3.Connection | None = None

def _get_db() -> sqlite3.Connection | None:
    global _db_conn
    if _db_conn is None:
        db_path = Path(__file__).resolve().parents[1] / "Output/Learning/triage.db"
        if db_path.exists():
            _db_conn = sqlite3.connect(str(db_path))
    return _db_conn


def now_uk() -> str:
    return datetime.now().strftime("%d/%m/%Y %H:%M")


def _provider_for_resource(resource_type: str) -> str:
    conn = _get_db()
    if conn:
        return _rtdb.get_provider_key(conn, resource_type)
    # Fallback: prefix check (no DB)
    if resource_type.startswith("azurerm_"): return "azure"
    if resource_type.startswith("aws_"):     return "aws"
    if resource_type.startswith("google_"):  return "gcp"
    return "unknown"


def _provider_title(provider: str) -> str:
    return {"azure": "Azure", "aws": "AWS", "gcp": "GCP"}.get(provider, provider.upper())


def _friendly_service_name(resource_type: str) -> str:
    conn = _get_db()
    if conn:
        return _rtdb.get_friendly_name(conn, resource_type)
    # Fallback: strip prefix + title-case (no DB)
    cleaned = resource_type
    for prefix in ("azurerm_", "aws_", "google_"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]
            break
    return cleaned.replace("_", " ").title()


def _is_key_service_type(resource_type: str) -> bool:
    # Exclude helper/data/control artifacts from executive "Key services" list.
    excluded_exact = {
        "azurerm_client_config",
        "aws_ami",
        "aws_caller_identity",
        "google_compute_zones",
    }
    if resource_type in excluded_exact:
        return False

    excluded_tokens = (
        "policy_assignment",
        "policy_definition",
        "role_definition",
        "security_group_rule",
        "route_table_association",
        "route_table",
        "route",
        "network_interface",
        "subnet",
        "sql_firewall_rule",
        "configuration",
        "iam_access_key",
        "iam_instance_profile",
        "iam_role",
        "iam_user",
        "iam_role_policy",
        "iam_user_policy",
        "db_option_group",
        "db_parameter_group",
        "db_subnet_group",
        "ebs_",
        "volume_attachment",
        "flow_log",
        "internet_gateway",
        "kms_alias",
    )
    return not any(token in resource_type for token in excluded_tokens)


def _summarize_service_names(resource_types: list[str]) -> str:
    service_names = sorted({_friendly_service_name(r) for r in resource_types if _is_key_service_type(r)})
    return ", ".join(service_names) if service_names else "None"


def _group_parent_services(resource_types: list[str]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for rtype in resource_types:
        parent = _friendly_service_name(rtype)
        grouped.setdefault(parent, []).append(rtype)
    return grouped


def _resource_kind_label(resource_type: str) -> str:
    cleaned = resource_type
    for prefix in ("azurerm_", "aws_", "google_"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]
            break
    return cleaned.replace("_", " ").title()


def _child_node_label(resource_type: str, parent_service: str) -> str:
    friendly = _friendly_service_name(resource_type)
    if friendly == parent_service:
        return f"{friendly}: {_resource_kind_label(resource_type)}"
    return friendly


def _filter_mermaid_children(parent_service: str, raw_types: list[str]) -> list[str]:
    """Filter child nodes for diagram clarity where vulnerability is on parent resource."""
    if parent_service == "Azure Key Vault":
        vault_only = [r for r in raw_types if r == "azurerm_key_vault"]
        return vault_only if vault_only else raw_types
    if parent_service == "Azure SQL Database":
        # Terraform provider v2/v3 aliases can define both logical server types.
        # Show one canonical server node in Mermaid to avoid visual duplication.
        if "azurerm_mssql_server" in raw_types:
            return ["azurerm_mssql_server"]
        if "azurerm_sql_server" in raw_types:
            return ["azurerm_sql_server"]
    if parent_service == "Amazon RDS":
        if "aws_rds_cluster" in raw_types:
            return ["aws_rds_cluster"]
        if "aws_db_instance" in raw_types:
            return ["aws_db_instance"]
    if parent_service == "Amazon Neptune":
        if "aws_neptune_cluster" in raw_types:
            return ["aws_neptune_cluster"]
    if parent_service == "Amazon OpenSearch Service":
        if "aws_elasticsearch_domain" in raw_types:
            return ["aws_elasticsearch_domain"]
    if parent_service == "Amazon S3":
        if "aws_s3_bucket" in raw_types:
            return ["aws_s3_bucket"]
    if parent_service == "Google Kubernetes Engine":
        if "google_container_cluster" in raw_types:
            return ["google_container_cluster"]
    if parent_service == "Cloud Storage":
        if "google_storage_bucket" in raw_types:
            return ["google_storage_bucket"]
    return raw_types


def _is_data_routing_resource(resource_type: str) -> bool:
    """Heuristic: resources that typically receive/forward data-plane traffic."""
    exclude_tokens = (
        "policy_",
        "role_",
        "iam_",
        "client_config",
        "diagnostic",
        "monitor",
        "alert_policy",
        "threat_detection",
        "security_",
        "kms_",
        "firewall_rule",
        "network_rules",
        "_configuration",
    )
    if any(token in resource_type for token in exclude_tokens):
        return False

    include_tokens = (
        "application_gateway",
        "api_management",
        "ingress",
        "load_balancer",
        "elb",
        "app_service",
        "function",
        "container",
        "cluster",
        "instance",
        "server",
        "sql",
        "mysql",
        "postgres",
        "storage",
        "bucket",
        "key_vault",
        "redis",
        "cosmos",
        "neptune",
        "rds",
        "bigquery",
    )
    return any(token in resource_type for token in include_tokens)


def _is_paas_resource(resource_type: str) -> bool:
    """Heuristic classification for Terraform resource/data types that map to PaaS services."""
    paas_tokens = (
        "sql",
        "mysql",
        "postgres",
        "storage",
        "key_vault",
        "app_service",
        "function_app",
        "container_registry",
        "redis",
        "cosmos",
        "bigquery",
        "gke",
        "container_cluster",
        "neptune",
        "elasticsearch",
        "rds",
    )
    return any(token in resource_type for token in paas_tokens)


def _is_network_control_resource(resource_type: str) -> bool:
    control_tokens = (
        "network_rules",
        "firewall",
        "security_group",
        "network_security_group",
        "private_endpoint",
        "subnet",
        "virtual_network",
        "vpc",
        "compute_firewall",
    )
    return any(token in resource_type for token in control_tokens)


def _has_private_access_signal(resource_types: list[str]) -> bool:
    private_tokens = (
        "private_endpoint",
        "private_link",
        "private_service_connect",
        "vpc_endpoint",
        "subnet",
        "virtual_network",
        "vpc",
    )
    return any(any(token in r for token in private_tokens) for r in resource_types)


def _has_restriction_signal(resource_types: list[str]) -> bool:
    restriction_tokens = (
        "firewall",
        "network_rules",
        "security_group",
        "network_security_group",
        "authorized_networks",
        "ingress",
    )
    return any(any(token in r for token in restriction_tokens) for r in resource_types)


def _service_signal_tokens(service: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    # private_tokens, restriction_tokens
    if any(t in service for t in ("key_vault",)):
        return (
            ("private_endpoint", "private_link"),
            ("network_acls", "firewall", "network_rules", "private_endpoint", "private_link"),
        )
    if any(t in service for t in ("storage", "s3_bucket", "storage_bucket")):
        return (
            ("private_endpoint", "private_link", "vpc_endpoint", "private_service_connect"),
            ("network_rules", "firewall", "bucket_policy", "public_access_block"),
        )
    if any(t in service for t in ("sql", "mysql", "postgres", "rds", "neptune")):
        return (
            ("private_endpoint", "private_link", "private_service_connect", "db_subnet_group", "subnet"),
            ("firewall", "sql_firewall_rule", "security_group", "network_security_group", "authorized_networks"),
        )
    if any(t in service for t in ("container_cluster", "gke", "app_service", "function_app")):
        return (
            ("private_endpoint", "private_link", "private_cluster", "private_service_connect"),
            ("compute_firewall", "firewall", "security_group", "network_security_group", "network_policy", "master_authorized_networks"),
        )
    if any(t in service for t in ("bigquery",)):
        return (
            ("private_service_connect", "private_endpoint"),
            ("access", "iam"),
        )
    return (
        ("private_endpoint", "private_link", "private_service_connect"),
        ("firewall", "network_rules", "security_group", "network_security_group"),
    )


def _service_access_signals(service: str, provider_resources: list[str]) -> tuple[bool, bool]:
    private_tokens, restriction_tokens = _service_signal_tokens(service)
    private_signal = any(any(tok in r for tok in private_tokens) for r in provider_resources)
    restriction_signal = any(any(tok in r for tok in restriction_tokens) for r in provider_resources)
    return private_signal, restriction_signal


def _build_paas_exposure_checks(provider_resources: list[str]) -> str:
    paas_types = sorted({t for t in provider_resources if _is_paas_resource(t)})
    if not paas_types:
        return "No PaaS services detected in Phase 1."

    lines = [
        "| PaaS Service Type | Private Access Signal | Restriction Signal | Potential Exposure |",
        "|---|---|---|---|",
    ]
    for service in paas_types:
        has_private, has_restriction = _service_access_signals(service, provider_resources)
        if has_private and has_restriction:
            exposure = "Medium (controls signaled; validate config)"
        elif has_private or has_restriction:
            exposure = "High (partial controls signaled)"
        else:
            exposure = "Critical (no control signals detected)"
        lines.append(
            f"| `{service}` | {'Yes' if has_private else 'No'} | {'Yes' if has_restriction else 'No'} | {exposure} |"
        )

    lines.append("")
    lines.append(
        "Heuristic only: derived from Terraform resource types in Phase 1; requires property-level validation in Phase 2."
    )
    return "\n".join(lines)


def _network_boundary_label(provider: str) -> str:
    return {"azure": "VNet", "aws": "VPC", "gcp": "VPC Network"}.get(provider, "Network")


def _is_network_boundary_resource(provider: str, resource_type: str) -> bool:
    boundary_types = {
        "azure": {"azurerm_virtual_network"},
        "aws": {"aws_vpc"},
        "gcp": {"google_compute_network"},
    }
    return resource_type in boundary_types.get(provider, set())


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value)


def _relationship_label(src_type: str, dst_type: str) -> tuple[str, bool] | None:
    """Return (label, dashed) for meaningful data/telemetry relationships."""
    if "elb" in src_type and "instance" in dst_type:
        return ("routes", False)
    if "instance" in src_type and any(tok in dst_type for tok in ("db_instance", "rds_cluster", "neptune", "sql", "mysql", "postgres")):
        return ("db access", False)
    if "flow_log" in src_type and "s3_bucket" in dst_type:
        return ("logs", True)
    return None


def _is_edge_gateway_service(service_name: str) -> bool:
    tokens = (
        "Application Gateway",
        "Load Balancing",
        "Api Management",
        "Ingress",
    )
    return any(tok in service_name for tok in tokens)


def _terraform_resource_blocks(repo_path: Path, prefix: str | None = None) -> list[tuple[str, str, str]]:
    """Return Terraform resource blocks as (resource_type, resource_name, body_text)."""
    if not repo_path.exists():
        return []
    block_start_re = re.compile(r'^\s*resource\s+"([^"]+)"\s+"([^"]+)"\s*\{')
    blocks: list[tuple[str, str, str]] = []
    for tf in repo_path.rglob("*.tf"):
        try:
            lines = tf.read_text(errors="ignore").splitlines()
        except Exception:
            continue
        i = 0
        while i < len(lines):
            m = block_start_re.match(lines[i])
            if not m:
                i += 1
                continue
            rtype, rname = m.group(1), m.group(2)
            brace = lines[i].count("{") - lines[i].count("}")
            body = [lines[i]]
            i += 1
            while i < len(lines) and brace > 0:
                body.append(lines[i])
                brace += lines[i].count("{") - lines[i].count("}")
                i += 1
            if not prefix or rtype.startswith(prefix):
                blocks.append((rtype, rname, "\n".join(body)))
    return blocks


def _ingress_posture_for_service(repo_path: Path | None, provider: str, service_name: str) -> tuple[str, bool]:
    """
    Return (label, insecure_http) for internet ingress edge.
    insecure_http=True when listener explicitly permits HTTP.
    """
    if repo_path is None or not repo_path.exists():
        return ("ingress", False)
    if provider != "azure" or service_name != "Azure Application Gateway":
        return ("ingress", False)

    protocols: set[str] = set()
    listener_re = re.compile(r"http_listener\s*\{(.*?)\}", re.DOTALL)
    protocol_re = re.compile(r'protocol\s*=\s*"([^"]+)"', re.IGNORECASE)
    for rtype, _, body in _terraform_resource_blocks(repo_path, prefix="azurerm_"):
        if rtype != "azurerm_application_gateway":
            continue
        for listener_match in listener_re.finditer(body):
            listener_body = listener_match.group(1)
            pm = protocol_re.search(listener_body)
            if pm:
                protocols.add(pm.group(1).strip().lower())

    if not protocols:
        return ("ingress", False)
    if protocols == {"https"}:
        return ("HTTPS ingress", False)
    if protocols == {"http"}:
        return ("HTTP ingress", True)
    if "http" in protocols and "https" in protocols:
        return ("HTTP/HTTPS ingress", True)
    return (f"{'/'.join(sorted(p.upper() for p in protocols))} ingress", False)


def _ingress_security_warnings(repo_path: Path | None, provider: str) -> list[str]:
    if repo_path is None or not repo_path.exists():
        return []
    warnings: list[str] = []
    if provider == "azure":
        label, insecure = _ingress_posture_for_service(repo_path, provider, "Azure Application Gateway")
        if insecure:
            warnings.append(
                f"- Warning: Azure Application Gateway allows `{label}` from internet; enforce HTTPS-only listeners and TLS."
            )
    return warnings


def _extract_service_relationships(repo_path: Path, provider: str) -> list[tuple[str, str, str, bool]]:
    """Infer service-to-service relationships from Terraform references."""
    prefix = {"aws": "aws_", "azure": "azurerm_", "gcp": "google_"}.get(provider)
    if not prefix or not repo_path.exists():
        return []

    ref_re = re.compile(rf'({re.escape(prefix)}[A-Za-z0-9_]+)\.([A-Za-z0-9_-]+)')
    blocks = _terraform_resource_blocks(repo_path, prefix=prefix)

    defined = {(rtype, rname) for rtype, rname, _ in blocks}
    rels: set[tuple[str, str, str, bool]] = set()
    for src_type, _src_name, body in blocks:
        for dst_type, dst_name in ref_re.findall(body):
            if (dst_type, dst_name) not in defined:
                continue
            lbl = _relationship_label(src_type, dst_type)
            if not lbl:
                continue
            src_service = _friendly_service_name(src_type)
            dst_service = _friendly_service_name(dst_type)
            if src_service == dst_service:
                continue
            rels.add((src_service, dst_service, lbl[0], lbl[1]))
    return sorted(rels)


def _build_simple_architecture_diagram(
    repo_name: str,
    provider_resources: dict[str, list[object]],
    repo_path: Path | None = None,
) -> str:
    lines = ["flowchart LR"]
    edge_lines: list[str] = []
    link_styles: list[str] = []
    node_styles: list[str] = []
    styled_ids: set[str] = set()
    link_index = 0

    category_colors = {
        "app": "#1976d2",       # blue
        "data": "#2e7d32",      # green
        "identity": "#ff8f00",  # orange
        "security": "#7e57c2",  # purple
    }

    def style_id(target_id: str, category: str) -> None:
        if target_id in styled_ids:
            return
        color = category_colors.get(category, category_colors["app"])
        node_styles.append(f"  style {target_id} stroke:{color},stroke-width:2px")
        styled_ids.add(target_id)

    def category_for_service(service_name: str) -> str:
        s = service_name.lower()
        if any(tok in s for tok in ("key vault", "iam", "identity", "secret", "policy")):
            return "identity"
        if any(tok in s for tok in ("sql", "database", "storage", "bucket", "redis", "cosmos", "rds", "neptune", "bigquery")):
            return "data"
        if any(tok in s for tok in ("security", "firewall", "network", "waf", "monitor")):
            return "security"
        return "app"

    def add_link(
        src: str,
        dst: str,
        label: str | None = None,
        red: bool = False,
        orange: bool = False,
        dashed: bool = False,
    ) -> None:
        nonlocal link_index
        edge_lines.append(f'  {src} -->|{label}| {dst}' if label else f"  {src} --> {dst}")
        if dashed:
            link_styles.append(f"  linkStyle {link_index} stroke-dasharray: 5 5")
        if red:
            link_styles.append(f"  linkStyle {link_index} stroke:#ff0000,stroke-width:3px")
        elif orange:
            link_styles.append(f"  linkStyle {link_index} stroke:#ff8c00,stroke-width:3px")
        link_index += 1

    lines.append('  Internet[Internet Users]')
    if not provider_resources:
        lines.append("  Internet --> Unknown[No cloud provider resources detected]")
        style_id("Unknown", "security")
        return "\n".join(lines)

    for provider, resources in provider_resources.items():
        provider_title = _provider_title(provider)
        provider_id = _safe_id(provider_title)
        boundary_label = _network_boundary_label(provider)

        resource_types = sorted({r.resource_type for r in resources})
        parent_groups = _group_parent_services(resource_types)
        boundary_resources = [r for r in resources if _is_network_boundary_resource(provider, r.resource_type)]
        non_boundary_parents = {
            parent: raw_types
            for parent, raw_types in parent_groups.items()
            if not any(_is_network_boundary_resource(provider, raw) for raw in raw_types)
        }
        routable_parents = {}
        for parent, raw_types in non_boundary_parents.items():
            routable_children = [raw for raw in raw_types if _is_data_routing_resource(raw)]
            if routable_children:
                routable_parents[parent] = routable_children
        paas_services = [
            parent for parent, raw_types in sorted(routable_parents.items())
            if any(_is_paas_resource(raw) for raw in raw_types)
        ]
        other_services = [
            parent for parent, raw_types in sorted(routable_parents.items())
            if not any(_is_paas_resource(raw) for raw in raw_types)
        ]
        has_alerting_signal = any(
            any(tok in raw for tok in ("alert_policy", "threat_detection", "security_alert"))
            for raw_types in non_boundary_parents.values()
            for raw in raw_types
        )
        service_anchor_nodes: dict[str, str] = {}

        lines.append(f'  subgraph {provider_id}_Cloud["{provider_title} Services"]')
        lines.append("    direction TB")
        monitoring_node = f"{provider_id}_Monitoring"
        if has_alerting_signal:
            lines.append(f'    {monitoring_node}["Security Monitoring"]')
            style_id(monitoring_node, "security")

        if paas_services:
            paas_subgraph_id = f"{provider_id}_PaaS"
            lines.append(f'    subgraph {paas_subgraph_id}["PaaS Services"]')
            style_id(paas_subgraph_id, "app")
            for p_idx, service in enumerate(paas_services, start=1):
                service_raw = sorted(set(routable_parents.get(service, [])))
                service_raw = _filter_mermaid_children(service, service_raw)
                service_raw_all = sorted(set(non_boundary_parents.get(service, [])))
                exposure_target = None
                if len(service_raw) <= 1:
                    node_id = f"{paas_subgraph_id}_Svc_{p_idx}"
                    lines.append(f'      {node_id}["{service}"]')
                    style_id(node_id, category_for_service(service))
                    exposure_target = node_id
                else:
                    svc_subgraph = f"{paas_subgraph_id}_Svc_{p_idx}"
                    lines.append(f'      subgraph {svc_subgraph}["{service}"]')
                    style_id(svc_subgraph, category_for_service(service))
                    first_child = None
                    for c_idx, child in enumerate(service_raw, start=1):
                        child_node = f"{svc_subgraph}_Child_{c_idx}"
                        child_label = _child_node_label(child, service)
                        lines.append(f'        {child_node}["{child_label}"]')
                        style_id(child_node, category_for_service(service))
                        if first_child is None:
                            first_child = child_node
                    lines.append("      end")
                    exposure_target = first_child

                if exposure_target:
                    service_anchor_nodes[service] = exposure_target
                    has_private, has_restriction = _service_access_signals(service, service_raw)
                    if not has_private and not has_restriction:
                        add_link("Internet", exposure_target, label="Public exposure", red=True)
                    elif has_private != has_restriction:
                        add_link("Internet", exposure_target, label="Partial controls", orange=True)
                    if has_alerting_signal and any(
                        tok in raw for raw in service_raw_all for tok in ("alert_policy", "threat_detection", "security_alert")
                    ):
                        add_link(exposure_target, monitoring_node, label="alerts", dashed=True)
            lines.append("    end")

        networks = boundary_resources if boundary_resources else [None]
        for idx, boundary in enumerate(networks, start=1):
            net_id = f"{provider_id}_Net_{idx}" if boundary is not None else f"{provider_id}_Net_Default"
            net_name = (
                f"{boundary_label}: {boundary.name}"
                if boundary is not None
                else f"{boundary_label}: Unspecified"
            )
            lines.append(f'    subgraph {net_id}["{net_name}"]')
            style_id(net_id, "security")

            if idx == 1 and other_services:
                for o_idx, service in enumerate(other_services, start=1):
                    service_raw = sorted(set(routable_parents.get(service, [])))
                    service_raw = _filter_mermaid_children(service, service_raw)
                    if len(service_raw) <= 1:
                        node_id = f"{net_id}_OtherSvc_{o_idx}"
                        lines.append(f'      {node_id}["{service}"]')
                        style_id(node_id, category_for_service(service))
                        service_anchor_nodes[service] = node_id
                    else:
                        svc_subgraph = f"{net_id}_OtherSvc_{o_idx}"
                        lines.append(f'      subgraph {svc_subgraph}["{service}"]')
                        style_id(svc_subgraph, category_for_service(service))
                        first_child = None
                        for c_idx, child in enumerate(service_raw, start=1):
                            child_node = f"{svc_subgraph}_Child_{c_idx}"
                            child_label = _child_node_label(child, service)
                            lines.append(f'        {child_node}["{child_label}"]')
                            style_id(child_node, category_for_service(service))
                            if first_child is None:
                                first_child = child_node
                        lines.append("      end")
                        if first_child:
                            service_anchor_nodes[service] = first_child

            lines.append("    end")

        if repo_path is not None:
            for src_service, dst_service, rel_label, is_dashed in _extract_service_relationships(repo_path, provider):
                src_node = service_anchor_nodes.get(src_service)
                dst_node = service_anchor_nodes.get(dst_service)
                if src_node and dst_node:
                    add_link(src_node, dst_node, label=rel_label, dashed=is_dashed)

        # Explicitly show ingress to gateway-like edge services.
        for service_name, node_id in service_anchor_nodes.items():
            if _is_edge_gateway_service(service_name):
                ingress_label, insecure_http = _ingress_posture_for_service(repo_path, provider, service_name)
                add_link("Internet", node_id, label=ingress_label, red=insecure_http)

        lines.append("  end")

    lines.extend(edge_lines)
    lines.extend(link_styles)
    lines.extend(node_styles)
    return "\n".join(lines)


def write_repo_summary(
    *,
    repo: Path,
    repo_name: str,
    providers: list[str],
    context: RepositoryContext,
    summary_dir: Path,
) -> Path:
    out_path = summary_dir / "Repos" / f"{repo_name}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    resource_types = [r.resource_type for r in context.resources]
    resource_counter = Counter(resource_types)
    provider_resources = {
        provider: [r for r in context.resources if _provider_for_resource(r.resource_type) == provider]
        for provider in providers
    }

    provider_text = ", ".join(_provider_title(p) for p in providers) if providers else "Unknown"
    language_text = "Terraform" if resource_types else "Unknown"
    hosting_text = provider_text
    ci_cd_text = "Unknown"

    auth_summary = "- No explicit identity resources detected in Phase 1 extraction."
    if any("key_vault" in t for t in resource_types):
        auth_summary = "- Azure Key Vault resources detected."

    network_summary = "- No explicit network segmentation resources detected."
    if any("vpc" in t or "virtual_network" in t for t in resource_types):
        network_summary = "- VPC/VNet style network resources detected."

    ingress_summary = "- Ingress paths require Phase 2 deep route tracing."
    egress_summary = "- Egress paths require Phase 2 dependency tracing."

    external_deps = "- Dependency mapping pending deeper scan."
    if any("sql" in t for t in resource_types):
        external_deps = "- SQL/database resources detected."

    top_evidence = []
    for resource_type, count in resource_counter.most_common(10):
        top_evidence.append(f"- `{resource_type}` x{count}")
    evidence_list = "\n".join(top_evidence) if top_evidence else "- No resources extracted."

    notes = (
        f"Repository path: `{repo}`\n\n"
        f"Extracted resources: {len(context.resources)}\n\n"
        "Generated from Phase 1 local heuristics."
    )

    content = render_template(
        "RepoSummary.md",
        {
            "repo_name": repo_name,
            "repo_type": "Infrastructure (likely IaC/platform)" if resource_types else "Application/Other",
            "timestamp": now_uk(),
            "architecture_diagram": _build_simple_architecture_diagram(repo_name, provider_resources, repo_path=repo),
            "languages": language_text,
            "hosting": hosting_text,
            "ci_cd": ci_cd_text,
            "providers": provider_text,
            "auth_summary": auth_summary,
            "network_summary": network_summary,
            "ingress_summary": ingress_summary,
            "egress_summary": egress_summary,
            "external_deps": external_deps,
            "evidence_list": evidence_list,
            "notes": notes,
        },
    )
    out_path.write_text(content, encoding="utf-8")
    validate_markdown_file(out_path, fix=True)
    return out_path


def write_experiment_cloud_architecture_summary(
    *,
    repo: Path,
    repo_name: str,
    providers: list[str],
    context: RepositoryContext,
    summary_dir: Path,
) -> list[Path]:
    out_files: list[Path] = []
    resource_types = [r.resource_type for r in context.resources]

    for provider in providers:
        provider_resource_objs = [r for r in context.resources if _provider_for_resource(r.resource_type) == provider]
        provider_resources = [r.resource_type for r in provider_resource_objs]
        unique_provider_resources = sorted(set(provider_resources))
        edge_gateway_detected = any(
            _is_edge_gateway_service(_friendly_service_name(rtype)) for rtype in unique_provider_resources
        )
        if unique_provider_resources:
            inventory_lines = [
                "| Service Name | Terraform Resource Type |",
                "|---|---|",
            ]
            for rtype in unique_provider_resources:
                inventory_lines.append(f"| {_friendly_service_name(rtype)} | `{rtype}` |")
            resource_inventory = "\n".join(inventory_lines)
        else:
            resource_inventory = "None detected."

        paas_types = sorted({t for t in provider_resources if _is_paas_resource(t)})
        network_control_types = sorted({t for t in provider_resources if _is_network_control_resource(t)})
        paas_service_names = sorted({_friendly_service_name(t) for t in paas_types})
        network_control_service_names = sorted({_friendly_service_name(t) for t in network_control_types})
        paas_exposure_checks = _build_paas_exposure_checks(provider_resources)
        if paas_types:
            controls_line = (
                "- Network control signals detected: "
                + ", ".join(network_control_service_names)
                if network_control_types
                else "- Network control signals detected: none"
            )
            security_controls = "\n".join(
                [
                    "- PaaS services detected: " + ", ".join(paas_service_names),
                    controls_line,
                    "- Action required: validate each PaaS service has explicit access restrictions/private access and default-deny behavior.",
                ]
            )
            if not network_control_types:
                security_controls += (
                    "\n- Warning: PaaS resources are commonly internet-reachable by default; no explicit network-control resources were detected in Phase 1."
                )
            ingress_warnings = _ingress_security_warnings(repo, provider)
            if ingress_warnings:
                security_controls += "\n" + "\n".join(ingress_warnings)
        else:
            security_controls = "- No PaaS services detected in Phase 1."

        recommendations = "\n".join(
            [
                "- Complete Phase 2 route and middleware tracing.",
                "- Run full IaC rule scan and map findings to risk register.",
                "- Add explicit network restrictions and private connectivity where applicable.",
            ]
        )

        provider_title = _provider_title(provider)
        out_path = summary_dir / "Cloud" / f"Architecture_{provider_title}.md"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        content = render_template(
            "CloudArchitectureSummary.md",
            {
                "provider": provider,
                "provider_title": provider_title,
                "repo_name": repo_name,
                "timestamp": now_uk(),
                "auth_signals": "Detected from Terraform resource metadata.",
                "services": _summarize_service_names(provider_resources),
                "top_risk": "Public exposure and weak network defaults require validation.",
                "next_step": "Run Phase 2 deep context discovery and skeptic reviews.",
                "attack_surface": "Internet-reachable services must be confirmed in Phase 2.",
                "edge_gateway": "Detected in Phase 1." if edge_gateway_detected else "Not confirmed in Phase 1.",
                "architecture_diagram": _build_simple_architecture_diagram(
                    repo_name, {provider: provider_resource_objs}, repo_path=repo
                ),
                "resource_inventory": resource_inventory,
                "security_controls": security_controls,
                "paas_exposure_checks": paas_exposure_checks,
                "recommendations": recommendations,
            },
        )
        content = content.replace("\\`\\`\\`", "```")
        out_path.write_text(content, encoding="utf-8")
        validate_markdown_file(out_path, fix=True)
        out_files.append(out_path)

    return out_files


def generate_reports(context: RepositoryContext, summary_dir_str: str, repo_path: Path | None = None) -> list[Path]:
    summary_dir = Path(summary_dir_str)
    summary_dir.mkdir(parents=True, exist_ok=True)
    repo = repo_path if repo_path is not None else Path(context.repository_name)

    providers = sorted(
        {
            _provider_for_resource(r.resource_type)
            for r in context.resources
            if _provider_for_resource(r.resource_type) != "unknown"
        }
    )

    generated: list[Path] = []
    generated.append(
        write_repo_summary(
            repo=repo,
            repo_name=context.repository_name,
            providers=providers,
            context=context,
            summary_dir=summary_dir,
        )
    )
    generated.extend(
        write_experiment_cloud_architecture_summary(
            repo=repo,
            repo_name=context.repository_name,
            providers=providers,
            context=context,
            summary_dir=summary_dir,
        )
    )
    return generated


def write_to_database(context: RepositoryContext, db_path: str = None, experiment_id: str = "001") -> None:
    from db_helpers import insert_repository, insert_resource, insert_connection, get_db_connection

    with get_db_connection(db_path):
        insert_repository(
            experiment_id=experiment_id,
            repo_path=Path(context.repository_name),
        )

        for resource in context.resources:
            insert_resource(
                experiment_id=experiment_id,
                repo_name=context.repository_name,
                resource_name=resource.name,
                resource_type=resource.resource_type,
                provider=_provider_for_resource(resource.resource_type),
                source_file=resource.file_path,
                source_line=resource.line_number,
            )

        for connection in context.connections:
            insert_connection(
                experiment_id=experiment_id,
                source_name=connection.source,
                target_name=connection.target,
                connection_type=connection.connection_type,
            )
