"""Pydantic schemas for structured Diff Review output."""

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class Evidence(BaseModel):
    """Concrete repository evidence supporting a reviewer finding."""

    entity_type: str  # Function, Class, File, Test, DatabaseTable
    entity_name: str
    file_path: str
    line_number: Optional[int] = None
    relationship: str  # calls, imports, tests, reads_db, writes_db
    verified_in_graph: bool = True
    snippet: Optional[str] = None


class ReviewFinding(BaseModel):
    """Structured actionable or informational finding on code diff."""

    id: str = Field(description="Finding identifier, e.g. ISSUE-001")
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    category: Literal[
        "BUG",
        "TYPE_ERROR",
        "SECURITY",
        "PERFORMANCE",
        "API_BREAK",
        "TEST_FAILURE",
        "CODE_SMELL",
        "STYLE",
        "ARCHITECTURE",
        "UNKNOWN",
    ]
    file: str
    symbol: Optional[str] = None
    lines: Optional[List[int]] = Field(default=None, description="Start and end line numbers [start, end]")
    description: str = Field(description="Clear explanation of WHAT is wrong and WHY")
    evidence: List[Evidence] = Field(default_factory=list, description="Repository facts supporting the claim")
    affected_entities: List[str] = Field(default_factory=list, description="Downstream symbols/tests impacted")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    repairability: Literal["HIGH", "MEDIUM", "LOW", "NONE"]


class ReviewResult(BaseModel):
    """Complete machine-readable output from the Diff Reviewer."""

    review_status: Literal["ACTIONABLE", "NOT_ACTIONABLE", "ESCALATED", "INSUFFICIENT_CONTEXT"]
    issues: List[ReviewFinding] = Field(default_factory=list)
    risk_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = "LOW"
    summary: str = ""
