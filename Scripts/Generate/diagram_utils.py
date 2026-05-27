"""
Shared diagram generation utilities for both architecture and subscription diagrams.

This module provides common utilities that both diagram systems use to:
- Sanitize node IDs for Mermaid compatibility
- Categorize Azure resource types
- Generate unique node IDs with deduplication

Used by:
- Scripts/Generate/generate_diagram.py (HierarchicalDiagramBuilder)
- web/app.py (_build_ingress_diagram, _build_subscription_diagrams_by_rg)
"""

import re
from typing import Dict, Set, Optional


def sanitize_node_id(name: str, max_length: int = 80) -> str:
    """
    Sanitize a resource name for use as a Mermaid node ID.
    
    Mermaid node IDs must:
    - Start with alphanumeric
    - Contain only alphanumeric, hyphens, underscores
    - Be reasonably short (>80 chars causes issues)
    
    Args:
        name: Resource name to sanitize
        max_length: Maximum length of output ID
        
    Returns:
        Sanitized node ID safe for Mermaid
        
    Example:
        >>> sanitize_node_id("my-app_service (prod)")
        'my-app_service-prod'
    """
    if not name:
        return 'resource'
    
    # Convert to lowercase and remove problematic characters
    sanitized = name.lower()
    
    # Replace spaces with hyphens
    sanitized = re.sub(r'\s+', '-', sanitized)
    
    # Remove parentheses and brackets
    sanitized = re.sub(r'[\[\](){}]', '', sanitized)
    
    # Remove special characters except hyphens and underscores
    sanitized = re.sub(r'[^a-z0-9\-_]', '', sanitized)
    
    # Clean up consecutive hyphens/underscores
    sanitized = re.sub(r'[-_]+', '-', sanitized)
    
    # Remove leading/trailing hyphens
    sanitized = sanitized.strip('-_')
    
    # Truncate if needed
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length].rstrip('-_')
    
    # Ensure it starts with alphanumeric
    if not sanitized or not sanitized[0].isalnum():
        sanitized = 'node-' + sanitized
    
    return sanitized or 'node'


def categorize_resource_type(arm_type: str) -> str:
    """
    Extract user-friendly category from Azure ARM resource type.
    
    Args:
        arm_type: Full ARM type path (e.g., "Microsoft.Compute/virtualMachines")
        
    Returns:
        Friendly category name
        
    Example:
        >>> categorize_resource_type("Microsoft.Compute/virtualMachines")
        'virtualMachines'
    """
    if not arm_type:
        return 'resource'
    
    # Extract the last component (e.g., "virtualMachines" from full type path)
    parts = str(arm_type).split('/')
    if len(parts) >= 2:
        return parts[-1]
    return parts[-1] if parts else 'resource'


class UniqueNodeIdGenerator:
    """
    Generate unique Mermaid node IDs with deduplication.
    
    When multiple resources have the same name, qualifies IDs with type prefix:
    - First occurrence: "resource-name"
    - Second occurrence: "resource-type-resource-name"
    
    This prevents Mermaid from collapsing distinct resources into one node.
    
    Usage:
        generator = UniqueNodeIdGenerator()
        id1 = generator.get_node_id("database", "app-db")  # Returns "app-db"
        id2 = generator.get_node_id("function", "app-db")  # Returns "function-app-db"
    """
    
    def __init__(self):
        self.emitted_ids: Set[str] = set()
        self.id_first_owner: Dict[str, str] = {}  # base_id -> resource_type of first
    
    def get_node_id(self, resource_type: str, resource_name: str) -> str:
        """
        Generate unique node ID, qualifying if needed to prevent collisions.
        
        Args:
            resource_type: Type of resource (e.g., "azurerm_virtual_machine")
            resource_name: Name of resource
            
        Returns:
            Unique node ID for Mermaid
        """
        base_id = sanitize_node_id(resource_name)
        
        # First use of this ID
        if base_id not in self.id_first_owner:
            self.emitted_ids.add(base_id)
            self.id_first_owner[base_id] = resource_type
            return base_id
        
        # ID already used — check if same owner (duplicate) or different
        first_owner = self.id_first_owner[base_id]
        if first_owner == resource_type:
            # Same resource type, same name — likely a duplicate
            return base_id
        
        # Different resource type — qualify to prevent collision
        qualified_id = f"{sanitize_node_id(resource_type)}-{base_id}"
        self.emitted_ids.add(qualified_id)
        return qualified_id
    
    def reset(self):
        """Reset the deduplication tracking."""
        self.emitted_ids.clear()
        self.id_first_owner.clear()


def build_mermaid_classdefs(provider: str = "azure", 
                            style_map: Optional[Dict[str, str]] = None) -> str:
    """
    Generate Mermaid classDef statements for resource styling.
    
    Args:
        provider: Cloud provider (azure, aws, gcp, kubernetes)
        style_map: Optional custom style map {resource_type: css_class}
        
    Returns:
        Mermaid classDef statements as string
        
    Example:
        >>> defs = build_mermaid_classdefs("azure")
        >>> print(defs)  # classDef app-service fill:#3b82f6,stroke:#1e40af...
    """
    # Default Azure color scheme
    default_styles = {
        "app-service": "fill:#3b82f6,stroke:#1e40af,stroke-width:2px,color:#fff",
        "database": "fill:#8b5cf6,stroke:#6d28d9,stroke-width:2px,color:#fff",
        "storage": "fill:#06b6d4,stroke:#0891b2,stroke-width:2px,color:#fff",
        "network": "fill:#10b981,stroke:#059669,stroke-width:2px,color:#fff",
        "security": "fill:#ef4444,stroke:#dc2626,stroke-width:2px,color:#fff",
    }
    
    if provider == "aws":
        default_styles = {
            "compute": "fill:#ff9900,stroke:#ff6600,stroke-width:2px,color:#000",
            "database": "fill:#527fff,stroke:#2540b0,stroke-width:2px,color:#fff",
            "storage": "fill:#5294cf,stroke:#2d72b8,stroke-width:2px,color:#fff",
            "network": "fill:#ff9900,stroke:#ff6600,stroke-width:2px,color:#000",
        }
    elif provider == "gcp":
        default_styles = {
            "compute": "fill:#4285f4,stroke:#1a73e8,stroke-width:2px,color:#fff",
            "database": "fill:#34a853,stroke:#0d9488,stroke-width:2px,color:#fff",
            "storage": "fill:#fbbc04,stroke:#f59e0b,stroke-width:2px,color:#000",
            "network": "fill:#4285f4,stroke:#1a73e8,stroke-width:2px,color:#fff",
        }
    
    # Merge custom styles
    if style_map:
        default_styles.update(style_map)
    
    # Generate classDef statements
    lines = []
    for style_name, style_def in default_styles.items():
        lines.append(f"    classDef {style_name} {style_def}")
    
    return '\n'.join(lines)
