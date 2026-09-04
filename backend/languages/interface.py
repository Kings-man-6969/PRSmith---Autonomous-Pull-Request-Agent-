"""Abstract base class for programming language code analyzers."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ExtractedSymbol(BaseModel):
    """Represents a code symbol extracted via static parsing."""

    name: str
    qualified_name: str
    symbol_type: str  # Function, Method, Class, Module, Test, APIEndpoint
    file_path: str
    start_line: int
    end_line: int
    docstring: Optional[str] = None
    parameters: List[str] = Field(default_factory=list)
    return_type: Optional[str] = None
    decorators: List[str] = Field(default_factory=list)
    properties: Dict[str, Any] = Field(default_factory=dict)


class ExtractedRelation(BaseModel):
    """Represents a relationship between symbols or modules."""

    source_qualified_name: str
    target_qualified_name: str
    relation_type: str  # calls, imports, tests, inherits, defines, reads_db, writes_db
    file_path: str
    line_number: Optional[int] = None
    confidence: float = 1.0
    derived_by: str = "ast"
    edge_provenance: str = "DIRECT_STATIC"
    provenance_weight: float = 1.0
    properties: Dict[str, Any] = Field(default_factory=dict)


class LanguageAnalyzer(ABC):
    """Abstract interface for language-specific static AST analysis."""

    @abstractmethod
    def supports(self, file_path: str) -> bool:
        """Check if this analyzer supports the given file path."""
        pass

    @abstractmethod
    def parse_file(
        self, file_path: str, source_code: str
    ) -> tuple[List[ExtractedSymbol], List[ExtractedRelation]]:
        """Parse source code to extract symbols and internal/external relations."""
        pass
