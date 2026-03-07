# models.py
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional

@dataclass
class Resource:
    name: str
    resource_type: str
    file_path: str
    line_number: int
    parent: Optional[str] = None
    properties: Dict[str, str] = field(default_factory=dict)


class RelationshipType(str, Enum):
    CONTAINS           = "contains"
    GRANTS_ACCESS_TO   = "grants_access_to"
    ROUTES_INGRESS_TO  = "routes_ingress_to"
    DEPENDS_ON         = "depends_on"
    ENCRYPTS           = "encrypts"
    RESTRICTS_ACCESS   = "restricts_access"
    MONITORS           = "monitors"
    AUTHENTICATES_VIA  = "authenticates_via"


@dataclass
class Relationship:
    """A typed, directed relationship between two resources in the knowledge graph."""
    source_type:       str   # terraform resource_type of source
    source_name:       str   # terraform block name of source
    target_type:       str   # terraform resource_type of target
    target_name:       str   # terraform block name of target
    relationship_type: RelationshipType
    source_repo:       str   = ""
    confidence:        str   = "extracted"   # extracted | inferred | user_confirmed
    notes:             str   = ""


@dataclass
class Connection:
    source: str
    target: str
    connection_type: str

@dataclass
class RepositoryContext:
    repository_name: str
    resources: List[Resource] = field(default_factory=list)
    connections: List[Connection] = field(default_factory=list)
    relationships: List[Relationship] = field(default_factory=list)
