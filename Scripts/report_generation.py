from collections import Counter
from datetime import datetime
from pathlib import Path

from models import RepositoryContext
from template_renderer import render_template
from markdown_validator import validate_markdown_file


def now_uk() -> str:
    return datetime.now().strftime("%d/%m/%Y %H:%M")


def _provider_for_resource(resource_type: str) -> str:
    if resource_type.startswith("azurerm_"):
        return "azure"
    if resource_type.startswith("aws_"):
        return "aws"
    if resource_type.startswith("google_"):
        return "gcp"
    return "unknown"


def _provider_title(provider: str) -> str:
    return {"azure": "Azure", "aws": "AWS", "gcp": "GCP"}.get(provider, provider.upper())


def _friendly_service_name(resource_type: str) -> str:
    explicit = {
        "azurerm_application_gateway": "Azure Application Gateway",
        "azurerm_key_vault": "Azure Key Vault",
        "azurerm_key_vault_key": "Azure Key Vault",
        "azurerm_key_vault_secret": "Azure Key Vault",
        "azurerm_mssql_server": "Azure SQL Database",
        "azurerm_sql_server": "Azure SQL Database",
        "azurerm_mssql_server_security_alert_policy": "Azure SQL Database",
        "azurerm_mysql_server": "Azure Database for MySQL",
        "azurerm_postgresql_server": "Azure Database for PostgreSQL",
        "azurerm_postgresql_configuration": "Azure Database for PostgreSQL",
        "azurerm_storage_account": "Azure Storage",
        "azurerm_storage_account_network_rules": "Azure Storage",
        "azurerm_virtual_network": "Azure Virtual Network",
        "azurerm_subnet": "Azure Virtual Network",
        "azurerm_network_interface": "Azure Virtual Network",
        "aws_rds_cluster": "Amazon RDS",
        "aws_db_instance": "Amazon RDS",
        "aws_neptune_cluster": "Amazon Neptune",
        "aws_neptune_cluster_instance": "Amazon Neptune",
        "aws_neptune_cluster_snapshot": "Amazon Neptune",
        "aws_elasticsearch_domain": "Amazon OpenSearch Service",
        "aws_elasticsearch_domain_policy": "Amazon OpenSearch Service",
        "aws_s3_bucket": "Amazon S3",
        "aws_s3_bucket_object": "Amazon S3",
        "aws_vpc": "Amazon VPC",
        "aws_subnet": "Amazon VPC",
        "aws_security_group": "Amazon VPC",
        "google_sql_database_instance": "Cloud SQL",
        "google_storage_bucket": "Cloud Storage",
        "google_storage_bucket_iam_binding": "Cloud Storage",
        "google_bigquery_dataset": "BigQuery",
        "google_container_cluster": "Google Kubernetes Engine",
        "google_container_node_pool": "Google Kubernetes Engine",
        "google_compute_network": "VPC Network",
        "google_compute_subnetwork": "VPC Network",
    }
    if resource_type in explicit:
        return explicit[resource_type]

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
        "subnet",
        "network_interface",
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


def _build_simple_architecture_diagram(
    repo_name: str,
    provider_resources: dict[str, list[object]],
) -> str:
    lines = ["flowchart LR"]
    edge_lines: list[str] = []
    link_styles: list[str] = []
    link_index = 0

    def add_link(
        src: str,
        dst: str,
        label: str | None = None,
        red: bool = False,
        orange: bool = False,
    ) -> None:
        nonlocal link_index
        edge_lines.append(f'  {src} -->|{label}| {dst}' if label else f"  {src} --> {dst}")
        if red:
            link_styles.append(f"  linkStyle {link_index} stroke:#ff0000,stroke-width:3px")
        elif orange:
            link_styles.append(f"  linkStyle {link_index} stroke:#ff8c00,stroke-width:3px")
        link_index += 1

    lines.append('  Internet[Internet Users]')
    if not provider_resources:
        lines.append("  Internet --> Unknown[No cloud provider resources detected]")
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

        networks = boundary_resources if boundary_resources else [None]
        for idx, boundary in enumerate(networks, start=1):
            net_id = f"{provider_id}_Net_{idx}" if boundary is not None else f"{provider_id}_Net_Default"
            net_name = (
                f"{boundary_label}: {boundary.name}"
                if boundary is not None
                else f"{boundary_label}: Unspecified"
            )
            boundary_node = (
                f'{net_id}_Boundary["{_friendly_service_name(boundary.resource_type)}"]'
                if boundary is not None
                else f'{net_id}_Boundary["{boundary_label} Boundary"]'
            )

            lines.append(f'    subgraph {net_id}["{net_name}"]')
            lines.append(f"      {boundary_node}")

            if idx == 1 and paas_services:
                paas_subgraph_id = f"{net_id}_PaaS"
                lines.append(f'      subgraph {paas_subgraph_id}["PaaS Services"]')
                for p_idx, service in enumerate(paas_services, start=1):
                    service_raw = sorted(set(routable_parents.get(service, [])))
                    service_raw_all = sorted(set(non_boundary_parents.get(service, [])))
                    exposure_target = None
                    if len(service_raw) <= 1:
                        node_id = f"{paas_subgraph_id}_Svc_{p_idx}"
                        lines.append(f'        {node_id}["{service}"]')
                        exposure_target = node_id
                    else:
                        svc_subgraph = f"{paas_subgraph_id}_Svc_{p_idx}"
                        lines.append(f'        subgraph {svc_subgraph}["{service}"]')
                        first_child = None
                        for c_idx, child in enumerate(service_raw, start=1):
                            child_node = f"{svc_subgraph}_Child_{c_idx}"
                            child_label = _child_node_label(child, service)
                            lines.append(f'          {child_node}["{child_label}"]')
                            if first_child is None:
                                first_child = child_node
                        lines.append("        end")
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
                            add_link(exposure_target, monitoring_node, label="alerts")
                lines.append("      end")

            if idx == 1 and other_services:
                for o_idx, service in enumerate(other_services, start=1):
                    service_raw = sorted(set(routable_parents.get(service, [])))
                    if len(service_raw) <= 1:
                        node_id = f"{net_id}_OtherSvc_{o_idx}"
                        lines.append(f'      {node_id}["{service}"]')
                        service_anchor_nodes[service] = node_id
                    else:
                        svc_subgraph = f"{net_id}_OtherSvc_{o_idx}"
                        lines.append(f'      subgraph {svc_subgraph}["{service}"]')
                        first_child = None
                        for c_idx, child in enumerate(service_raw, start=1):
                            child_node = f"{svc_subgraph}_Child_{c_idx}"
                            child_label = _child_node_label(child, service)
                            lines.append(f'        {child_node}["{child_label}"]')
                            if first_child is None:
                                first_child = child_node
                        lines.append("      end")
                        if first_child:
                            service_anchor_nodes[service] = first_child

            lines.append("    end")
            add_link("Internet", f"{net_id}_Boundary")

        lines.append("  end")

    lines.extend(edge_lines)
    lines.extend(link_styles)
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
            "architecture_diagram": _build_simple_architecture_diagram(repo_name, provider_resources),
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
        paas_exposure_checks = _build_paas_exposure_checks(provider_resources)
        if paas_types:
            controls_line = (
                "- Network control signals detected: "
                + ", ".join(f"`{t}`" for t in network_control_types)
                if network_control_types
                else "- Network control signals detected: none"
            )
            security_controls = "\n".join(
                [
                    "- PaaS services detected: " + ", ".join(f"`{t}`" for t in paas_types),
                    controls_line,
                    "- Action required: validate each PaaS service has explicit access restrictions/private access and default-deny behavior.",
                ]
            )
            if not network_control_types:
                security_controls += (
                    "\n- Warning: PaaS resources are commonly internet-reachable by default; no explicit network-control resources were detected in Phase 1."
                )
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
                "edge_gateway": "Not confirmed in Phase 1.",
                "architecture_diagram": _build_simple_architecture_diagram(repo_name, {provider: provider_resource_objs}),
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
