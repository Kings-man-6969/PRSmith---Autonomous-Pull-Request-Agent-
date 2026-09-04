"""SQLAlchemy ORM models for PRSmith v2 core entities."""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import (
    Boolean,
    Column,
    DateTime as _SQLADateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON, TypeDecorator


class UTCDateTime(TypeDecorator):
    """DateTime type decorator that ensures timezone-aware Python datetimes
    are safely bound as UTC naive datetimes to avoid asyncpg TIMESTAMP WITHOUT TIME ZONE errors,
    while returning UTC timezone-aware datetimes to application code.
    """

    impl = _SQLADateTime
    cache_ok = True

    def process_bind_param(self, value: Optional[datetime], dialect: Any) -> Optional[datetime]:
        if value is not None:
            if value.tzinfo is not None:
                return value.astimezone(timezone.utc).replace(tzinfo=None)
            return value
        return None

    def process_result_value(self, value: Optional[datetime], dialect: Any) -> Optional[datetime]:
        if value is not None:
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value
        return None


DateTime = UTCDateTime


def utcnow() -> datetime:
    """Return UTC naive datetime for TIMESTAMP WITHOUT TIME ZONE compatibility."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    """Base declarative class for all SQLAlchemy ORM models."""

    type_annotation_map = {
        datetime: UTCDateTime,
    }


def generate_uuid() -> str:
    return str(uuid.uuid4())


class GitHubInstallation(Base):
    __tablename__ = "github_installations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    installation_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    account_login: Mapped[str] = mapped_column(String(255), index=True)
    account_type: Mapped[str] = mapped_column(String(50), default="Organization")
    app_slug: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    permissions: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    events: Mapped[List[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(50), default="active", index=True)  # active, suspended, deleted
    suspended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    repositories: Mapped[List["Repository"]] = relationship("Repository", back_populates="installation_record")


class Repository(Base):
    __tablename__ = "repositories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    github_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    owner: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255))
    default_branch: Mapped[str] = mapped_column(String(100), default="main")
    installation_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    github_installation_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("github_installations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    private: Mapped[bool] = mapped_column(Boolean, default=False)
    last_polled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    # Relationships
    installation_record: Mapped[Optional["GitHubInstallation"]] = relationship("GitHubInstallation", back_populates="repositories")
    snapshots: Mapped[List["RepositorySnapshot"]] = relationship("RepositorySnapshot", back_populates="repository", cascade="all, delete-orphan")
    jobs: Mapped[List["Job"]] = relationship("Job", back_populates="repository", cascade="all, delete-orphan")
    user_associations: Mapped[List["UserRepository"]] = relationship("UserRepository", back_populates="repository", cascade="all, delete-orphan")


class RepositorySnapshot(Base):
    __tablename__ = "repository_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    repository_id: Mapped[str] = mapped_column(String(36), ForeignKey("repositories.id", ondelete="CASCADE"), index=True)
    base_sha: Mapped[str] = mapped_column(String(40), index=True)
    head_sha: Mapped[str] = mapped_column(String(40), index=True)
    merge_base_sha: Mapped[str] = mapped_column(String(40))
    clone_path: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    repository: Mapped["Repository"] = relationship("Repository", back_populates="snapshots")
    graph_versions: Mapped[List["GraphVersion"]] = relationship("GraphVersion", back_populates="snapshot")


class GraphVersion(Base):
    __tablename__ = "graph_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    repository_id: Mapped[str] = mapped_column(String(36), ForeignKey("repositories.id", ondelete="CASCADE"), index=True)
    snapshot_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("repository_snapshots.id", ondelete="SET NULL"), nullable=True)
    commit_sha: Mapped[str] = mapped_column(String(40), index=True)
    is_incremental: Mapped[bool] = mapped_column(Boolean, default=False)
    node_count: Mapped[int] = mapped_column(Integer, default=0)
    edge_count: Mapped[int] = mapped_column(Integer, default=0)
    graph_content_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_verified: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    snapshot: Mapped[Optional["RepositorySnapshot"]] = relationship("RepositorySnapshot", back_populates="graph_versions")
    nodes: Mapped[List["GraphNode"]] = relationship("GraphNode", back_populates="graph_version", cascade="all, delete-orphan")
    edges: Mapped[List["GraphEdge"]] = relationship("GraphEdge", back_populates="graph_version", cascade="all, delete-orphan")


class GraphNode(Base):
    __tablename__ = "graph_nodes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    graph_version_id: Mapped[str] = mapped_column(String(36), ForeignKey("graph_versions.id", ondelete="CASCADE"), index=True)
    repository_id: Mapped[str] = mapped_column(String(36), index=True)
    node_type: Mapped[str] = mapped_column(String(50), index=True)  # File, Module, Class, Function, Test, etc.
    name: Mapped[str] = mapped_column(String(255), index=True)
    qualified_name: Mapped[str] = mapped_column(String(512), index=True)
    file_path: Mapped[str] = mapped_column(String(1024), index=True)
    start_line: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    end_line: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source_commit: Mapped[str] = mapped_column(String(40), index=True)
    parser_version: Mapped[str] = mapped_column(String(50), default="python_ast")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    properties: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    graph_version: Mapped["GraphVersion"] = relationship("GraphVersion", back_populates="nodes")


class GraphEdge(Base):
    __tablename__ = "graph_edges"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    graph_version_id: Mapped[str] = mapped_column(String(36), ForeignKey("graph_versions.id", ondelete="CASCADE"), index=True)
    source_node_id: Mapped[str] = mapped_column(String(36), ForeignKey("graph_nodes.id", ondelete="CASCADE"), index=True)
    target_node_id: Mapped[str] = mapped_column(String(36), ForeignKey("graph_nodes.id", ondelete="CASCADE"), index=True)
    relationship_type: Mapped[str] = mapped_column(String(50), index=True)  # calls, imports, tests, inherits, etc.
    edge_provenance: Mapped[str] = mapped_column(String(50), default="DIRECT_STATIC", index=True)  # DIRECT_STATIC, INFERRED, UNKNOWN
    provenance_weight: Mapped[float] = mapped_column(Float, default=1.0)
    source_commit: Mapped[str] = mapped_column(String(40))
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    derived_by: Mapped[str] = mapped_column(String(50), default="python_ast")
    properties: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    graph_version: Mapped["GraphVersion"] = relationship("GraphVersion", back_populates="edges")


class CodeEmbedding(Base):
    __tablename__ = "code_embeddings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    repository_id: Mapped[str] = mapped_column(String(36), index=True)
    graph_version_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("graph_versions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    commit_sha: Mapped[str] = mapped_column(String(40), index=True)
    file_path: Mapped[str] = mapped_column(String(1024), index=True)
    symbol_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    language: Mapped[str] = mapped_column(String(50), default="python")
    chunk_text: Mapped[str] = mapped_column(Text)
    embedding_model: Mapped[str] = mapped_column(String(100), default="text-embedding-3-small")
    embedding_version: Mapped[str] = mapped_column(String(50), default="v1")
    content_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    embedding_json: Mapped[List[float]] = mapped_column(JSON)  # Vector fallback / json list
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    repository_id: Mapped[str] = mapped_column(String(36), ForeignKey("repositories.id", ondelete="CASCADE"), index=True)
    pr_number: Mapped[int] = mapped_column(Integer, index=True)
    pr_title: Mapped[str] = mapped_column(String(512), default="")
    base_sha: Mapped[str] = mapped_column(String(40))
    head_sha: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(50), default="PENDING", index=True)
    risk_level: Mapped[str] = mapped_column(String(20), default="LOW")
    risk_score: Mapped[int] = mapped_column(Integer, default=0)
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    worker_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    lease_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    graph_version_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("graph_versions.id", ondelete="SET NULL"), nullable=True, index=True)
    snapshot_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("repository_snapshots.id", ondelete="SET NULL"), nullable=True, index=True)
    superseded_by_job_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, index=True)
    # Machine-readable completion reason — NEVER encode state in error_message
    completion_reason: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # Dispatch idempotency key (manual dispatch: manual:{repo_id}:{pr}:{head_sha}; nullable for webhook jobs)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    triggered_by_user_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    repository: Mapped["Repository"] = relationship("Repository", back_populates="jobs")
    triggering_user: Mapped[Optional["User"]] = relationship("User", foreign_keys=[triggered_by_user_id])
    review_runs: Mapped[List["ReviewRun"]] = relationship("ReviewRun", back_populates="job", cascade="all, delete-orphan")
    repair_runs: Mapped[List["RepairRun"]] = relationship("RepairRun", back_populates="job", cascade="all, delete-orphan")
    validation_runs: Mapped[List["ValidationRun"]] = relationship("ValidationRun", back_populates="job", cascade="all, delete-orphan")
    artifacts: Mapped[List["Artifact"]] = relationship("Artifact", back_populates="job", cascade="all, delete-orphan")


class ReviewRun(Base):
    __tablename__ = "review_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(50), default="STARTED")
    findings_count: Mapped[int] = mapped_column(Integer, default=0)
    head_sha: Mapped[str] = mapped_column(String(40), default="", index=True)
    evidence_validation_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    model_name: Mapped[str] = mapped_column(String(100), default="gpt-4o")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    job: Mapped["Job"] = relationship("Job", back_populates="review_runs")
    findings: Mapped[List["ReviewFinding"]] = relationship("ReviewFinding", back_populates="review_run", cascade="all, delete-orphan")


class ReviewFinding(Base):
    __tablename__ = "review_findings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    finding_id: Mapped[str] = mapped_column(String(50), index=True)  # ISSUE-001
    review_run_id: Mapped[str] = mapped_column(String(36), ForeignKey("review_runs.id", ondelete="CASCADE"), index=True)
    severity: Mapped[str] = mapped_column(String(20), index=True)  # LOW, MEDIUM, HIGH, CRITICAL
    category: Mapped[str] = mapped_column(String(50), index=True)  # BUG, TYPE_ERROR, SECURITY, etc.
    content_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    file_path: Mapped[str] = mapped_column(String(1024))
    symbol_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    start_line: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    end_line: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    description: Mapped[str] = mapped_column(Text)
    evidence: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, default=list)
    affected_entities: Mapped[List[str]] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    repairability: Mapped[str] = mapped_column(String(20), default="HIGH")
    status: Mapped[str] = mapped_column(String(50), default="PENDING")  # PENDING, REPAIRED, ESCALATED, UNRESOLVED
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    review_run: Mapped["ReviewRun"] = relationship("ReviewRun", back_populates="findings")


class RepairRun(Base):
    __tablename__ = "repair_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    finding_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="STARTED")  # STARTED, SUCCESS, FAILED, ESCALATED, TIMEOUT
    iteration_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    job: Mapped["Job"] = relationship("Job", back_populates="repair_runs")
    iterations: Mapped[List["RepairIteration"]] = relationship("RepairIteration", back_populates="repair_run", cascade="all, delete-orphan")


class RepairIteration(Base):
    __tablename__ = "repair_iterations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    repair_run_id: Mapped[str] = mapped_column(String(36), ForeignKey("repair_runs.id", ondelete="CASCADE"), index=True)
    iteration_number: Mapped[int] = mapped_column(Integer)
    patch_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(50))  # APPLIED, FAILED, ROLLED_BACK
    validation_status: Mapped[str] = mapped_column(String(50))
    error_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    repair_run: Mapped["RepairRun"] = relationship("RepairRun", back_populates="iterations")


class Patch(Base):
    __tablename__ = "patches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    diff_content: Mapped[str] = mapped_column(Text)
    diff_hash: Mapped[str] = mapped_column(String(64), index=True)
    artifact_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    files_changed: Mapped[List[str]] = mapped_column(JSON, default=list)
    lines_added: Mapped[int] = mapped_column(Integer, default=0)
    lines_deleted: Mapped[int] = mapped_column(Integer, default=0)
    is_valid_syntax: Mapped[bool] = mapped_column(Boolean, default=False)
    is_within_scope: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ValidationRun(Base):
    __tablename__ = "validation_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    stage: Mapped[str] = mapped_column(String(50))  # BASELINE, ITERATION, FINAL
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    head_sha: Mapped[str] = mapped_column(String(40), default="", index=True)
    patch_artifact_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    result_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    job: Mapped["Job"] = relationship("Job", back_populates="validation_runs")
    results: Mapped[List["ValidationResult"]] = relationship("ValidationResult", back_populates="validation_run", cascade="all, delete-orphan")


class ValidationResult(Base):
    __tablename__ = "validation_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    validation_run_id: Mapped[str] = mapped_column(String(36), ForeignKey("validation_runs.id", ondelete="CASCADE"), index=True)
    layer_name: Mapped[str] = mapped_column(String(50))  # patch_syntax, lint, type_check, targeted_test, etc.
    command: Mapped[str] = mapped_column(String(512))
    passed: Mapped[bool] = mapped_column(Boolean)
    exit_code: Mapped[int] = mapped_column(Integer)
    stdout: Mapped[str] = mapped_column(Text, default="")
    stderr: Mapped[str] = mapped_column(Text, default="")
    failure_class: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    validation_run: Mapped["ValidationRun"] = relationship("ValidationRun", back_populates="results")


class ContextSnapshot(Base):
    __tablename__ = "context_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    step_name: Mapped[str] = mapped_column(String(50))  # REVIEW, REPAIR_ITERATION_1, etc.
    retrieved_symbols: Mapped[List[str]] = mapped_column(JSON, default=list)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ModelCall(Base):
    __tablename__ = "model_calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(50), default="openai")
    model_name: Mapped[str] = mapped_column(String(100))
    purpose: Mapped[str] = mapped_column(String(50))  # review, repair, embedding
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    artifact_type: Mapped[str] = mapped_column(String(50))  # diff, log, graph_snapshot
    file_name: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    job: Mapped["Job"] = relationship("Job", back_populates="artifacts")


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    delivery_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(50))
    repository_full_name: Mapped[str] = mapped_column(String(255))
    pr_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSON)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    github_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    role: Mapped[str] = mapped_column(String(50), default="viewer")
    session_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    # Relationships
    github_connection: Mapped[Optional["GitHubConnection"]] = relationship("GitHubConnection", back_populates="user", uselist=False, cascade="all, delete-orphan")
    user_repositories: Mapped[List["UserRepository"]] = relationship("UserRepository", back_populates="user", cascade="all, delete-orphan")


class GitHubConnection(Base):
    __tablename__ = "github_connections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    access_token_encrypted: Mapped[str] = mapped_column(Text)
    refresh_token_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    access_token_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    refresh_token_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    scopes: Mapped[str] = mapped_column(String(255), default="read:user user:email read:org")
    token_type: Mapped[str] = mapped_column(String(50), default="bearer")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    user: Mapped["User"] = relationship("User", back_populates="github_connection")


class UserRepository(Base):
    __tablename__ = "user_repositories"
    __table_args__ = (UniqueConstraint("user_id", "repository_id", name="uq_user_repository"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    repository_id: Mapped[str] = mapped_column(String(36), ForeignKey("repositories.id", ondelete="CASCADE"), index=True)
    monitoring_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_repair_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    watched_branches: Mapped[List[str]] = mapped_column(MutableList.as_mutable(JSON()), default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    user: Mapped["User"] = relationship("User", back_populates="user_repositories")
    repository: Mapped["Repository"] = relationship("Repository", back_populates="user_associations")


class OutboxEvent(Base):
    __tablename__ = "outbox_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    aggregate_id: Mapped[str] = mapped_column(String(36), index=True)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(50), default="PENDING", index=True)  # PENDING, CLAIMED, DISPATCHED, FAILED
    claimed_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class TaskExecution(Base):
    __tablename__ = "task_executions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    task_name: Mapped[str] = mapped_column(String(100), index=True)
    status: Mapped[str] = mapped_column(String(50), default="PENDING", index=True)  # PENDING, IN_PROGRESS, COMPLETED, FAILED
    current_attempt: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    attempts: Mapped[List["TaskExecutionAttempt"]] = relationship(
        "TaskExecutionAttempt", back_populates="task_execution", cascade="all, delete-orphan"
    )


class TaskExecutionAttempt(Base):
    __tablename__ = "task_execution_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    task_execution_id: Mapped[str] = mapped_column(String(36), ForeignKey("task_executions.id", ondelete="CASCADE"), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer)
    worker_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # Set when worker claims (status→CLAIMED)
    heartbeat_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # PENDING → CLAIMED (worker_id set) → COMPLETED | FAILED | ABANDONED
    # Recovery: stale CLAIMED (no heartbeat) → ABANDONED; new PENDING attempt created
    status: Mapped[str] = mapped_column(String(50), default="PENDING", index=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    task_execution: Mapped["TaskExecution"] = relationship("TaskExecution", back_populates="attempts")


class PublishedReview(Base):
    """Represents one publication attempt for a specific pipeline artifact lineage.

    Multiple PublishedReview rows may exist per (repository_id, pr_number) — one per
    distinct chain_hash / pipeline run. Only one row may have is_current=True at a time
    (enforced by partial unique index). All rows are retained for audit.

    publication_status lifecycle:
        CLAIMED → POSTING → POSTED        (happy path)
        CLAIMED → POSTING → FAILED_RETRYABLE → (recovery retry) → POSTING → POSTED
        CLAIMED → POSTING → FAILED_PERMANENT  (Job → FAILED)
        CLAIMED → CANCELLED                   (superseded by newer lineage before posting)
    """
    __tablename__ = "published_reviews"
    __table_args__ = (
        # At most one active claim per PR at a time (CLAIMED or POSTING).
        # CANCELLED, POSTED, FAILED_* do not block new claims.
        Index(
            "uq_published_review_active_pr",
            "repository_id", "pr_number",
            unique=True,
            postgresql_where=text("publication_status IN ('CLAIMED', 'POSTING')"),
            sqlite_where=text("publication_status IN ('CLAIMED', 'POSTING')"),
        ),
        # Exactly one current (POSTED) publication per PR visible to the API.
        Index(
            "uq_published_review_current",
            "repository_id", "pr_number",
            unique=True,
            postgresql_where=text("is_current = true"),
            sqlite_where=text("is_current = 1"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    # Denormalized for partial index (avoids join to jobs)
    repository_id: Mapped[str] = mapped_column(String(36), ForeignKey("repositories.id", ondelete="CASCADE"), index=True)
    pr_number: Mapped[int] = mapped_column(Integer, index=True)
    # Per-lineage unique key for audit trail
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    chain_hash: Mapped[str] = mapped_column(String(64), index=True)
    head_sha: Mapped[str] = mapped_column(String(40), index=True)
    patch_artifact_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    validation_run_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("validation_runs.id", ondelete="SET NULL"), nullable=True, index=True)

    review_status: Mapped[str] = mapped_column(String(50), default="CONFIRMED")  # CONFIRMED | ESCALATED
    patch_status: Mapped[str] = mapped_column(String(50), default="NONE")        # VALIDATED | REJECTED | NONE
    # CLAIMED | POSTING | POSTED | FAILED_RETRYABLE | FAILED_PERMANENT | CANCELLED
    publication_status: Mapped[str] = mapped_column(String(50), default="CLAIMED")

    # True for the one POSTED row currently displayed on the PR (atomic swap on POSTED)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    # Ownership and timing
    worker_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)

    # Retry accounting
    publication_attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_publication_attempts: Mapped[int] = mapped_column(Integer, default=5)

    github_comment_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    reviewed_commit_sha: Mapped[str] = mapped_column(String(40), default="")
    pipeline_version: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    posted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    job: Mapped["Job"] = relationship("Job")
    validation_run: Mapped[Optional["ValidationRun"]] = relationship("ValidationRun")


PatchArtifact = Patch
