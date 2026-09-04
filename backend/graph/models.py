"""Pydantic graph representations for memory and API transfers."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class NodeModel(BaseModel):
    """Memory representation of a Knowledge Graph node."""

    id: str
    repository_id: str
    node_type: str
    name: str
    qualified_name: str
    file_path: str
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    source_commit: str
    parser_version: str = "python_ast"
    confidence: float = 1.0
    properties: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class EdgeModel(BaseModel):
    """Memory representation of a Knowledge Graph edge with provenance."""

    id: str
    source_node_id: str
    target_node_id: str
    relationship_type: str
    source_commit: str
    confidence: float = 1.0
    derived_by: str = "python_ast"
    properties: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ImpactSet(BaseModel):
    """Container of entities affected by a code change."""

    target_symbol: str
    callers: List[NodeModel] = Field(default_factory=list)
    callees: List[NodeModel] = Field(default_factory=list)
    tests: List[NodeModel] = Field(default_factory=list)
    dependencies: List[NodeModel] = Field(default_factory=list)
    api_endpoints: List[NodeModel] = Field(default_factory=list)
    db_interactions: List[NodeModel] = Field(default_factory=list)
