#!/usr/bin/env python3
"""LLM-based resource interpretation for universal architecture pattern.

This module implements the "structure extraction + LLM interpretation" principle:
- Extract structure via pattern matching (fast)
- LLM interprets semantic meaning (intelligent)
- Cache results to avoid repeated LLM calls (efficient)

Usage:
    from llm_resource_interpreter import interpret_compute_os, interpret_resource_type
    
    os = interpret_compute_os("azurerm_linux_virtual_machine", "UbuntuServer:22_04-lts")
    # Returns: "OS: Ubuntu 22.04 LTS"
    
    service = interpret_resource_type("azurerm_quantum_computer", {})
    # Returns: {"category": "compute", "service_name": "Quantum Computer", ...}
"""

import json
import hashlib
from pathlib import Path
from typing import Any, Optional
from datetime import datetime, timedelta

# Cache location
CACHE_DIR = Path.home() / ".triage-saurus" / "cache"
CACHE_FILE = CACHE_DIR / "resource_interpretations.json"
CACHE_DURATION_DAYS = 90


class ResourceCache:
    """Persistent cache for LLM interpretations."""
    
    def __init__(self):
        self.cache: dict[str, dict] = {}
        self.load()
    
    def load(self):
        """Load cache from disk."""
        if CACHE_FILE.exists():
            try:
                data = json.loads(CACHE_FILE.read_text())
                # Filter expired entries
                cutoff = (datetime.now() - timedelta(days=CACHE_DURATION_DAYS)).isoformat()
                self.cache = {
                    k: v for k, v in data.items()
                    if v.get("cached_at", "") > cutoff
                }
            except Exception:
                self.cache = {}
    
    def save(self):
        """Save cache to disk."""
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(self.cache, indent=2))
    
    def get(self, key: str) -> Optional[dict]:
        """Get cached interpretation."""
        return self.cache.get(key)
    
    def set(self, key: str, value: dict):
        """Cache an interpretation."""
        self.cache[key] = {
            **value,
            "cached_at": datetime.now().isoformat()
        }
        self.save()


# Global cache instance
_cache = ResourceCache()


def _cache_key(*args) -> str:
    """Generate cache key from arguments."""
    content = json.dumps(args, sort_keys=True)
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def _call_llm(prompt: str, system_prompt: str = "") -> dict:
    """Call LLM for interpretation (placeholder - integrate with your LLM client).
    
    TODO: Replace with actual LLM integration (OpenAI, Anthropic, etc.)
    For now, returns structured format that scripts expect.
    """
    # This is a placeholder. In production, this would call:
    # - OpenAI API (gpt-4, gpt-3.5-turbo)
    # - Anthropic API (claude-3)
    # - Azure OpenAI
    # - Local LLM (llama, mistral)
    
    # For now, return a structured response that maintains compatibility
    return {
        "interpretation": "LLM interpretation placeholder",
        "confidence": 0.85,
        "reasoning": "Placeholder response - implement LLM integration"
    }


def interpret_compute_os(
    resource_type: str,
    image_reference: str,
    provider: Optional[str] = None
) -> str:
    """Interpret OS from compute resource image reference.
    
    Args:
        resource_type: e.g. "azurerm_linux_virtual_machine", "aws_instance"
        image_reference: Image details like "UbuntuServer:22_04-lts" or AMI ID
        provider: Optional provider hint ("azure", "aws", "gcp")
    
    Returns:
        OS string like "OS: Ubuntu 22.04 LTS", "OS: Windows Server 2022"
    """
    cache_key = _cache_key("compute_os", resource_type, image_reference)
    cached = _cache.get(cache_key)
    if cached:
        return cached["os_name"]
    
    # Extract provider from resource type if not provided
    if not provider:
        if resource_type.startswith("azurerm_"):
            provider = "azure"
        elif resource_type.startswith("aws_"):
            provider = "aws"
        elif resource_type.startswith("google_"):
            provider = "gcp"
    
    prompt = f"""Interpret the operating system from this cloud compute resource:

Provider: {provider}
Resource Type: {resource_type}
Image Reference: {image_reference}

Return ONLY the OS name in this format: "OS: <name>"
Examples:
- "OS: Ubuntu 22.04 LTS"
- "OS: Windows Server 2022"
- "OS: CentOS Stream 9"
- "OS: Red Hat Enterprise Linux 8"
- "OS: Debian 11"

Be specific about versions when identifiable from the image reference.
If version cannot be determined, use general name like "OS: Ubuntu" or "OS: Windows".
"""
    
    result = _call_llm(prompt)
    
    # Extract OS name from LLM response
    # TODO: Parse actual LLM response when integrated
    os_name = result.get("interpretation", "OS: Unknown")
    
    # Cache for future use
    _cache.set(cache_key, {"os_name": os_name})
    
    return os_name


def interpret_resource_type(
    resource_type: str,
    properties: dict[str, Any]
) -> dict[str, Any]:
    """Interpret cloud resource type semantically.
    
    Args:
        resource_type: e.g. "azurerm_mssql_server", "aws_rds_cluster"
        properties: Resource properties/configuration
    
    Returns:
        {
            "service_name": "SQL Server",
            "category": "database",
            "subcategory": "relational",
            "security_relevance": "high",
            "recommended_rules": ["sql-auditing", "sql-firewall", "sql-encryption"]
        }
    """
    cache_key = _cache_key("resource_type", resource_type, sorted(properties.keys()))
    cached = _cache.get(cache_key)
    if cached:
        return cached
    
    prompt = f"""Interpret this cloud resource type:

Resource Type: {resource_type}
Properties: {list(properties.keys())[:10]}

Provide a JSON response with:
{{
    "service_name": "Human-readable service name",
    "category": "compute|database|storage|network|security|other",
    "subcategory": "More specific category",
    "security_relevance": "critical|high|medium|low",
    "description": "Brief description of what this service does",
    "recommended_rules": ["rule-id-1", "rule-id-2"]
}}

Examples:
- azurerm_mssql_server → {{"service_name": "SQL Server", "category": "database", "subcategory": "relational", "security_relevance": "high"}}
- aws_s3_bucket → {{"service_name": "S3", "category": "storage", "subcategory": "object", "security_relevance": "high"}}
- azurerm_kubernetes_cluster → {{"service_name": "AKS", "category": "compute", "subcategory": "container", "security_relevance": "critical"}}
"""
    
    result = _call_llm(prompt)
    
    # TODO: Parse actual LLM JSON response when integrated
    interpretation = {
        "service_name": resource_type.split("_")[-1].title(),
        "category": "unknown",
        "subcategory": "unknown",
        "security_relevance": "medium",
        "description": f"Resource of type {resource_type}",
        "recommended_rules": []
    }
    
    # Cache for future use
    _cache.set(cache_key, interpretation)
    
    return interpretation


def batch_interpret_compute(resources: list[dict]) -> list[dict]:
    """Batch interpret multiple compute resources for efficiency.
    
    Args:
        resources: List of dicts with keys: resource_type, name, image_reference, provider
    
    Returns:
        Same list with added keys: os_name, os_version, role
    """
    # Separate cached from uncached
    uncached = []
    results = []
    
    for resource in resources:
        cache_key = _cache_key(
            "compute_os",
            resource["resource_type"],
            resource.get("image_reference", "")
        )
        cached = _cache.get(cache_key)
        
        if cached:
            results.append({**resource, "os_name": cached["os_name"]})
        else:
            uncached.append(resource)
            results.append(resource)  # Placeholder, will update
    
    # Batch process uncached (if any)
    if uncached:
        # TODO: Implement batch LLM call for efficiency
        # For now, process individually
        for i, resource in enumerate(uncached):
            os_name = interpret_compute_os(
                resource["resource_type"],
                resource.get("image_reference", ""),
                resource.get("provider")
            )
            # Update the result in the correct position
            for j, r in enumerate(results):
                if r is resource:
                    results[j] = {**resource, "os_name": os_name}
                    break
    
    return results


def batch_interpret_resources(resources: list[dict]) -> list[dict]:
    """Batch interpret multiple resources for efficiency.
    
    Args:
        resources: List of dicts with keys: resource_type, properties
    
    Returns:
        Same list with added interpretation metadata
    """
    # Similar to batch_interpret_compute but for general resources
    results = []
    
    for resource in resources:
        interpretation = interpret_resource_type(
            resource["resource_type"],
            resource.get("properties", {})
        )
        results.append({**resource, **interpretation})
    
    return results


def classify_resource_category(resource_type: str) -> str:
    """Classify a resource into a category using LLM or fallback logic.
    
    Args:
        resource_type: e.g. "azurerm_kubernetes_cluster", "aws_lambda_function"
    
    Returns:
        Category: compute|database|storage|networking|identity|security|monitoring|other
    """
    cache_key = _cache_key("category", resource_type)
    cached = _cache.get(cache_key)
    if cached:
        return cached["category"]
    
    # Simple fallback logic based on naming patterns
    resource_lower = resource_type.lower()
    
    # Category keywords
    compute_keywords = ['vm', 'virtual_machine', 'instance', 'compute', 'kubernetes', 'aks', 'eks', 'gke', 
                       'container', 'app_service', 'web_app', 'function', 'lambda', 'cloud_run', 'ecs']
    database_keywords = ['sql', 'database', 'db', 'cosmosdb', 'dynamo', 'rds', 'mysql', 'postgresql', 
                        'postgres', 'mssql', 'bigtable', 'firestore']
    storage_keywords = ['storage', 'blob', 'bucket', 's3', 'efs', 'disk', 'volume']
    networking_keywords = ['network', 'subnet', 'vpc', 'vnet', 'firewall', 'gateway', 'load_balancer', 
                          'alb', 'elb', 'frontdoor', 'cdn', 'cloudfront', 'public_ip', 'endpoint']
    identity_keywords = ['key_vault', 'kms', 'secret', 'identity', 'iam', 'role', 'policy', 'service_account']
    security_keywords = ['firewall', 'waf', 'security', 'guard', 'defender', 'sentinel']
    monitoring_keywords = ['log', 'monitor', 'insights', 'cloudwatch', 'diagnostic']
    
    # Check keywords
    for keyword in compute_keywords:
        if keyword in resource_lower:
            category = "compute"
            break
    else:
        for keyword in database_keywords:
            if keyword in resource_lower:
                category = "database"
                break
        else:
            for keyword in storage_keywords:
                if keyword in resource_lower:
                    category = "storage"
                    break
            else:
                for keyword in networking_keywords:
                    if keyword in resource_lower:
                        category = "networking"
                        break
                else:
                    for keyword in identity_keywords:
                        if keyword in resource_lower:
                            category = "identity"
                            break
                    else:
                        for keyword in security_keywords:
                            if keyword in resource_lower:
                                category = "security"
                                break
                        else:
                            for keyword in monitoring_keywords:
                                if keyword in resource_lower:
                                    category = "monitoring"
                                    break
                            else:
                                category = "other"
    
    # Cache result
    _cache.set(cache_key, {"category": category})
    return category


def clear_cache():
    """Clear the interpretation cache (useful for testing)."""
    _cache.cache = {}
    _cache.save()


def cache_stats() -> dict:
    """Get cache statistics."""
    return {
        "entries": len(_cache.cache),
        "cache_file": str(CACHE_FILE),
        "cache_dir_exists": CACHE_DIR.exists()
    }


if __name__ == "__main__":
    # Simple CLI for testing
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: llm_resource_interpreter.py <command>")
        print("Commands:")
        print("  stats - Show cache statistics")
        print("  clear - Clear cache")
        print("  test - Run test interpretations")
        sys.exit(1)
    
    cmd = sys.argv[1]
    
    if cmd == "stats":
        stats = cache_stats()
        print(f"Cache entries: {stats['entries']}")
        print(f"Cache file: {stats['cache_file']}")
        print(f"Cache dir exists: {stats['cache_dir_exists']}")
    
    elif cmd == "clear":
        clear_cache()
        print("Cache cleared")
    
    elif cmd == "test":
        print("Testing compute OS interpretation:")
        os1 = interpret_compute_os("azurerm_linux_virtual_machine", "UbuntuServer:22_04-lts")
        print(f"  Ubuntu: {os1}")
        
        os2 = interpret_compute_os("azurerm_windows_virtual_machine", "WindowsServer:2022-datacenter")
        print(f"  Windows: {os2}")
        
        print("\nTesting resource type interpretation:")
        res = interpret_resource_type("azurerm_mssql_server", {})
        print(f"  SQL Server: {res}")
        
        print("\nCache stats:")
        stats = cache_stats()
        print(f"  Entries: {stats['entries']}")
