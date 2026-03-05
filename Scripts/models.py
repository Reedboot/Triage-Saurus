# models.py
from dataclasses import dataclass, field
from typing import List, Dict, Optional

@dataclass
class Resource:
    name: str
    resource_type: str
    file_path: str
    line_number: int
    parent: Optional[str] = None
    properties: Dict[str, str] = field(default_factory=dict)

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
