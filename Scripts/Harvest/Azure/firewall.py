"""Harvest Azure Firewall assets and rule summaries."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from ._helpers import _az_rest, az, infer_sku, safe_str

RESOURCE_TYPE = "Microsoft.Network/azureFirewalls"


def _dedupe_strs(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.lower()
        if normalized not in seen:
            seen.add(normalized)
            result.append(value)
    return result


def _tail(resource_id: str | None) -> str | None:
    value = safe_str(resource_id)
    if not value:
        return None
    return value.rstrip("/").split("/")[-1]


def _get_firewall_exposure_level(firewall: dict[str, Any]) -> str:
    props = firewall.get("properties") or firewall
    for config in props.get("ipConfigurations") or []:
        config_props = config.get("properties") or config
        public_ip = config_props.get("publicIPAddress") or config.get("publicIPAddress")
        if isinstance(public_ip, dict) and safe_str(public_ip.get("id")):
            return "Public"
        if safe_str(public_ip):
            return "Public"
    return "Internal"


def _collection_name(collection: dict[str, Any]) -> str | None:
    return safe_str(collection.get("name") or (collection.get("properties") or {}).get("name"))


def _collection_rules(collection: dict[str, Any]) -> list[dict[str, Any]]:
    return ((collection.get("properties") or collection).get("rules") or [])


def _extract_nat_rules(
    collections: list[dict[str, Any]],
    firewall_name: str | None = None,
    resource_group: str | None = None,
    exposure_level: str | None = None,
) -> list[dict[str, Any]]:
    rules_out: list[dict[str, Any]] = []
    for collection in collections or []:
        collection_name = _collection_name(collection)
        for rule in _collection_rules(collection):
            rule_props = rule.get("properties") or rule
            translated_address = safe_str(rule_props.get("translatedAddress"))
            translated_fqdn = safe_str(rule_props.get("translatedFqdn"))
            translated_value = translated_address or translated_fqdn
            entry_hosts = [host for host in (rule_props.get("destinationAddresses") or []) if safe_str(host)]
            destination_address = safe_str(rule_props.get("destinationAddress"))
            if destination_address:
                entry_hosts.append(destination_address)
            entry_hosts = _dedupe_strs([host for host in entry_hosts if safe_str(host)])
            if not entry_hosts or not translated_value:
                continue
            protocols = [
                protocol
                for protocol in (rule_props.get("ipProtocols") or rule_props.get("protocols") or [])
                if safe_str(protocol)
            ]
            rule_name = safe_str(rule.get("name"))
            rules_out.append({
                "id": f"{firewall_name or 'firewall'}::{collection_name or 'collection'}::{rule_name or 'rule'}::{translated_value}",
                "firewall_name": firewall_name,
                "resource_group": resource_group,
                "collection_name": collection_name,
                "rule_name": rule_name,
                "entry_hosts": entry_hosts,
                "translated_address": translated_address,
                "translated_fqdn": translated_fqdn,
                "translated_port": safe_str(rule_props.get("translatedPort")),
                "protocols": protocols,
                "exposure_level": exposure_level,
            })
    return rules_out


def _normalize_app_protocols(protocols: list[Any]) -> list[str]:
    normalized: list[str] = []
    for protocol in protocols or []:
        if isinstance(protocol, dict):
            proto_type = safe_str(protocol.get("protocolType") or protocol.get("type"))
            port = safe_str(protocol.get("port"))
            if proto_type and port:
                normalized.append(f"{proto_type}:{port}")
            elif proto_type:
                normalized.append(proto_type)
        elif safe_str(protocol):
            normalized.append(str(protocol))
    return normalized


def _extract_app_rules(
    collections: list[dict[str, Any]],
    firewall_name: str | None = None,
    resource_group: str | None = None,
) -> list[dict[str, Any]]:
    rules_out: list[dict[str, Any]] = []
    for collection in collections or []:
        collection_name = _collection_name(collection)
        for rule in _collection_rules(collection):
            rule_props = rule.get("properties") or rule
            target_fqdns = _dedupe_strs([
                fqdn for fqdn in (rule_props.get("targetFqdns") or []) if safe_str(fqdn)
            ])
            if not target_fqdns:
                continue
            rule_name = safe_str(rule.get("name"))
            rules_out.append({
                "id": f"{firewall_name or 'firewall'}::{collection_name or 'collection'}::{rule_name or 'rule'}",
                "firewall_name": firewall_name,
                "resource_group": resource_group,
                "collection_name": collection_name,
                "rule_name": rule_name,
                "source_addresses": _dedupe_strs([
                    source for source in (rule_props.get("sourceAddresses") or []) if safe_str(source)
                ]),
                "target_fqdns": target_fqdns,
                "protocols": _normalize_app_protocols(rule_props.get("protocols") or []),
            })
    return rules_out


def _fetch_policy_rule_collections(policy_id: str) -> list[dict[str, Any]]:
    response = _az_rest(f"https://management.azure.com{policy_id}/ruleCollectionGroups?api-version=2024-05-01")
    collections: list[dict[str, Any]] = []
    for group in response.get("value") or []:
        group_props = group.get("properties") or {}
        collections.extend(group_props.get("ruleCollections") or [])
    return collections


def harvest(subscription_id: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for firewall in az(["network", "firewall", "list"], subscription_id):
        exposure_level = _get_firewall_exposure_level(firewall)
        results.append({
            "id": firewall["id"],
            "subscription_id": subscription_id,
            "resource_group": firewall.get("resourceGroup"),
            "name": firewall.get("name"),
            "type": firewall.get("type", RESOURCE_TYPE),
            "location": firewall.get("location"),
            "sku": infer_sku(firewall),
            "tags": json.dumps(firewall.get("tags") or {}),
            "is_public": 1 if exposure_level == "Public" else 0,
            "is_restricted": 0,
            "ip_restrictions": json.dumps([]),
            "endpoints": json.dumps([]),
            "auth_methods": json.dumps([]),
            "fqdn": None,
            "pipeline_tag": None,
            "raw_json": json.dumps({**firewall, "_extra": {"exposure_level": exposure_level}}),
        })
    return results


def harvest_rules(
    subscription_id: str,
    conn: sqlite3.Connection,
    dry_run: bool = False,
) -> tuple[int, int]:
    """Harvest Azure Firewall NAT and application rules."""
    firewalls = az(["network", "firewall", "list"], subscription_id)
    if not firewalls:
        return (0, 0)

    now = datetime.now(timezone.utc).isoformat()
    nat_total = 0
    app_total = 0

    for firewall in firewalls:
        firewall_name = safe_str(firewall.get("name"))
        resource_group = safe_str(firewall.get("resourceGroup"))
        if not firewall_name:
            continue

        print(f"    [firewall-rules] {firewall_name}...", end=" ", flush=True)
        try:
            props = firewall.get("properties") or {}
            exposure_level = _get_firewall_exposure_level(firewall)
            policy_collections: list[dict[str, Any]] = []
            policy_id = safe_str(((props.get("firewallPolicy") or {}).get("id")))
            if policy_id:
                try:
                    policy_collections = _fetch_policy_rule_collections(policy_id)
                except Exception as exc:
                    print(f"policy-partial ({exc})", end="; ", flush=True)

            nat_rules = _extract_nat_rules(
                list(props.get("natRuleCollections") or []) + policy_collections,
                firewall_name,
                resource_group,
                exposure_level,
            )
            app_rules = _extract_app_rules(
                list(props.get("applicationRuleCollections") or []) + policy_collections,
                firewall_name,
                resource_group,
            )

            if not dry_run:
                conn.execute(
                    "DELETE FROM firewall_nat_rules WHERE subscription_id = ? AND firewall_name = ?",
                    (subscription_id, firewall_name),
                )
                conn.execute(
                    "DELETE FROM firewall_app_rules WHERE subscription_id = ? AND firewall_name = ?",
                    (subscription_id, firewall_name),
                )
                for rule in nat_rules:
                    conn.execute(
                        """
                        INSERT INTO firewall_nat_rules (
                            id, subscription_id, firewall_name, resource_group,
                            collection_name, rule_name, entry_hosts, translated_address,
                            translated_fqdn, translated_port, protocols, exposure_level, last_synced
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(id) DO UPDATE SET
                            subscription_id    = excluded.subscription_id,
                            resource_group     = excluded.resource_group,
                            entry_hosts        = excluded.entry_hosts,
                            translated_address = excluded.translated_address,
                            translated_fqdn    = excluded.translated_fqdn,
                            translated_port    = excluded.translated_port,
                            protocols          = excluded.protocols,
                            exposure_level     = excluded.exposure_level,
                            last_synced        = excluded.last_synced
                        """,
                        (
                            rule["id"], subscription_id, firewall_name, resource_group,
                            rule["collection_name"], rule["rule_name"], json.dumps(rule["entry_hosts"]),
                            rule["translated_address"], rule["translated_fqdn"], rule["translated_port"],
                            json.dumps(rule["protocols"]), rule["exposure_level"], now,
                        ),
                    )
                for rule in app_rules:
                    conn.execute(
                        """
                        INSERT INTO firewall_app_rules (
                            id, subscription_id, firewall_name, resource_group,
                            collection_name, rule_name, source_addresses, target_fqdns,
                            protocols, last_synced
                        ) VALUES (?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(id) DO UPDATE SET
                            subscription_id  = excluded.subscription_id,
                            resource_group   = excluded.resource_group,
                            source_addresses = excluded.source_addresses,
                            target_fqdns     = excluded.target_fqdns,
                            protocols        = excluded.protocols,
                            last_synced      = excluded.last_synced
                        """,
                        (
                            rule["id"], subscription_id, firewall_name, resource_group,
                            rule["collection_name"], rule["rule_name"], json.dumps(rule["source_addresses"]),
                            json.dumps(rule["target_fqdns"]), json.dumps(rule["protocols"]), now,
                        ),
                    )
                conn.commit()

            nat_total += len(nat_rules)
            app_total += len(app_rules)
            print(f"{len(nat_rules)} NAT, {len(app_rules)} app rules")
        except Exception as exc:
            print(f"SKIPPED ({exc})")

    return nat_total, app_total
