# PRSmith v2 — Project Memory & Architecture Guide

> **Repository-aware autonomous code review, repair, and multi-tenant management system**

---

## 🎯 System Philosophy & Core Design Principles

> **Frozen Guarantee Language**:
> **At-least-once delivery + at-most-one active logical owner + idempotent/reconcilable side effects.**
> No code, comment, or document in this project may claim "exactly-once execution."

1. **Retrieval Before Generation**: Never ask an LLM to reason about code without evidence.
2. **Evidence Before Claims**: Every reviewer finding must reference concrete repository entities.
3. **AI Proposes; Deterministic Systems Verify**: Tests, linters, and type-checkers determine whether a patch is acceptable.
4. **Least Context Necessary**: Smallest relevant subgraph is retrieved and packed within token budget.
5. **Least Modification Necessary**: The Repair Agent makes surgical fixes, not speculative refactors.
6. **Fail Closed**: If context or external services are missing: `ESCALATE` (never hallucinate).
7. **Never Trust Execution**: All validation runs in an isolated sandbox (`network=none`, non-root, resource limits).
8. **Never Accumulate Failed State**: Failed patches are rolled back; only validated patches are checkpointed.
9. **Auditable Lifecycle**: Every revision, graph version, finding, patch, and validation output is recorded in PostgreSQL.
10. **Target Branch Immutability**: Target branches are **never** modified directly by the agent. Human review remains the final authority.
11. **Dual Identity Planes**: GitHub App identity powers automated webhook ingestion and PR comments; GitHub OAuth identity powers user dashboard discovery, repo tracking, and manual dispatch.
12. **Sole Source of Truth**: Per-user repo configurations (`monitoring_enabled`, `auto_repair_enabled`, `watched_branches`) reside exclusively in `user_repositories`, never drifting in duplicate repo-level defaults.
13. **Fail-Closed Sandbox Boundary**: Subprocess fallback for untrusted PR code in production is an explicit architectural violation. Untrusted code runs in hardened Docker containers (`network=none`, `cap_drop=ALL`, `pids_limit=100`, read-only rootfs); if unavailable, PRSmith fails closed with `SandboxUnavailableError`.
14. **Transactional Outbox & Two-Level Task Attempts**: Webhook deliveries write to `outbox_events` in the same DB transaction. Workers claim jobs via `task_executions` (stable identity) and `task_execution_attempts` (per worker run). Stale heartbeats cause attempts to be marked `ABANDONED` and re-queued as `PENDING` (no ownerless `CLAIMED` states).
15. **Cryptographic Lineage Binding**: Every published artifact is bound to its complete pipeline lineage (`commit_sha`, `graph_content_hash`, `finding_content_hashes`, `patch_artifact_hash`, `validation_result_hash`, `pipeline_version`) via `canonical_hash`.
16. **Layered Publication Concurrency & Marker Reconciliation**: Active publication claims are serialized at the DB layer via a partial unique index on `(repository_id, pr_number)` for `CLAIMED` and `POSTING` states. A separate partial unique index ensures at most one `is_current=True` row per PR, updated via an atomic two-step swap in a single transaction. Publication reconciles against GitHub HTML markers (`<!-- prsmith:published_review:{chain_hash} -->`) via Option B (PATCH existing comment if found, else POST new comment). Stale `POSTING` rows and transient 429/5xx errors (`FAILED_RETRYABLE`) are recovered with exponential backoff.
17. **JWT Session Versioning & Double-Submit CSRF**: Logout invalidates all sessions immediately by incrementing `User.session_version` in DB; state-changing endpoints enforce double-submit cookie CSRF verification (`X-CSRF-Token`).
18. **Categorized Denylist & AST Escalation**: Sensitive paths (CI workflows, Dockerfiles, secrets) are rejected via categorized denylist; YAML files are semantically classified; AST calls to system APIs trigger human escalation (`ESCALATED`) rather than rejection.
19. **Sanitization Boundary (Mandatory Security Invariant)**: No raw repository-derived string may enter persistent graph/vector storage, vector embeddings, or an LLM prompt without passing through `SanitizedProjection`. Secret scanning and redaction cover file contents, diff hunks, file paths, symbol names, exception messages, and structlog log fields.
20. **Mandatory Snapshot Immutability Checkpoints**: Head SHA is validated against `job.head_sha` via `assert_head_sha_current()` at three mandatory checkpoints: (1) before review generation, (2) before each repair iteration, and (3) immediately before claiming publication. Any mismatch transitions the job to `STALE_SNAPSHOT`.
21. **Repair Domain Service Layering**: Repair domain logic is centralized in `backend.repair.service.RepairService` (patch generation, validation plan execution, `RepairRun` persistence, rollback). Celery tasks (`review.py`, `repair.py`, `validation.py`) act strictly as orchestrators, preventing circular imports.
22. **Optimistic Concurrency Control**: All job state mutations must use `JobMutationService.transition(session, job, new_status, ...)` enforcing `WHERE version = :expected_version`. Direct `job.status = ...` assignment is strictly prohibited.
23. **Explicit Terminal States**: `COMPLETED`, `FAILED`, `ESCALATED` (human escalation with structured `completion_reason`), and `STALE_SNAPSHOT` (superseded by a newer commit, linking `superseded_by_job_id`) are the formal terminal states.

---

## 📐 Frozen Architecture Decisions

| Concern | Decision | Extension / Hardening Point |
|---|---|---|
| **Reliability Guarantee** | At-least-once delivery + at-most-one active logical owner + idempotent side effects | Zero claims of "exactly-once execution" across system |
| **LLM Provider** | OpenAI (`gpt-4o`, `gpt-4o-mini`) | `LLMProvider` ABC → Anthropic, Gemini, OpenAI-Compatible |
| **Vector Store** | PostgreSQL + pgvector (or fallback JSON cosine) | Same DB as relational app data; sanitized code projections |
| **Language Analyzer** | Python AST (`ast` module) | Weighted edges (`DIRECT_STATIC=1.0`, `INFERRED=0.7`) + Graph GC |
| **Sandbox Backend** | Hardened Docker (`DockerBackend`) | Zero subprocess fallback in production; fail-closed `SandboxUnavailableError` |
| **Reliability / Outbox** | Transactional Outbox + TaskExecution Leases | Exponential backoff dispatcher + `attempt_recovery.py` |
| **Artifact Lineage** | Canonical JSON hashing (`chain_hash`) | Strips transient DB fields; binds full execution lineage |
| **Concurrency Model** | `JobMutationService` + Optimistic Locking | 10-state transition graph; Invariant 11 conditional update |
| **Publication Concurrency** | Partial unique DB indexes + Atomic `is_current` Swap | `uq_published_review_active_pr` + Option B PATCH/POST marker reconciliation |
| **Security Boundary** | `SanitizedProjection` on all repo strings | File content, diffs, paths, symbol names, exceptions, logs |
| **Repair Architecture** | `RepairService` domain service | Decoupled from Celery workers; no circular imports |
| **Frontend** | React 19 + TypeScript + Vite | Vercel-style clean dark UI, cookie-authenticated session |
| **Queue & Worker** | Celery + Redis | 4 named queues: `graph_build`, `review`, `repair`, `validation` |
| **Authentication** | GitHub OAuth 2.0 + HS256 JWT (`session_version`) | Double-submit CSRF cookie + 4-tier error taxonomy |
| **Token Encryption** | Fernet symmetric encryption at rest (`github_connections`) | Key validated on startup via `validate_encryption_key()` |
| **Multi-Tenancy** | `user_repositories` join table (`UNIQUE(user_id, repository_id)`) | Independent per-user repository configuration |
| **Database Migrations** | Alembic (Batch mode for SQLite) | Single source of truth for database schema evolutions |

---

## 🗂️ Complete Directory & Module Map

```
prsmith/
├── alembic/
│   ├── env.py                  # Alembic environment configured with Base.metadata
│   └── versions/               # Versioned migration scripts (OAuth multi-tenant & v2 hardening pass)
├── alembic.ini                 # Alembic configuration
│
├── backend/
│   ├── api/
│   │   ├── auth.py             # GET /login, GET /callback, GET /me, POST /logout
│   │   ├── webhook.py          # POST /webhook (HMAC SHA-256 + atomic delivery deduplication + Outbox)
│   │   ├── jobs.py             # GET /api/jobs, /api/jobs/{id}, /api/jobs/{id}/patches, POST /{id}/publish
│   │   ├── repositories.py     # User-scoped CRUD, discovery, graph trigger, manual dispatch
│   │   └── findings.py         # GET /api/findings (cursor-paginated, tenant-authorized, filterable)
│   │
│   ├── auth/
│   │   ├── github_app.py       # GitHub App RS256 JWT & installation token manager
│   │   ├── github_client.py    # GitHub REST client (PR fetch, diff extraction, review posting/patching)
│   │   ├── token_encryption.py # Fernet symmetric encryption & startup key validation
│   │   ├── security.py         # JWT session signing (session_version), double-submit CSRF, get_current_user
│   │   ├── credential_provider.py # Precedence provider: App Token -> User OAuth -> Dev PAT
│   │   └── exceptions.py       # 4-tier auth taxonomy: AuthRequired, AuthRevoked, AuthInsufficientScope, AuthTemporaryFailure
│   │
│   ├── delivery/
│   │   └── dispatcher.py       # Transactional Outbox Dispatcher (exponential backoff & stale event recovery)
│   │
│   ├── orchestration/
│   │   ├── job_mutations.py    # JobMutationService (optimistic concurrency versioning & worker leases)
│   │   ├── attempt_recovery.py # Stale TaskExecutionAttempt recovery service (ABANDONED -> new PENDING)
│   │   ├── idempotency.py      # Idempotency key builders (make_publication_pr_key, make_publication_lineage_key)
│   │   ├── leases.py           # Worker lease manager and heartbeat tracking
│   │   └── state_machine.py    # Formal PRSmith job state transition graph (10 states)
│   │
│   ├── repository/
│   │   ├── snapshot.py         # Immutable SHA tracking (RepositorySnapshot) & drift assertions
│   │   ├── git.py              # Git primitives (clone, fetch, diff, check_patch, apply, rollback)
│   │   └── worktree.py         # Isolated git worktree lifecycle manager
│   │
│   ├── languages/
│   │   ├── interface.py        # LanguageAnalyzer abstract base class
│   │   └── python_analyzer.py  # Deterministic AST symbol, import, call, & test extractor
│   │
│   ├── graph/
│   │   ├── models.py           # GraphNode, GraphEdge, ImpactSet Pydantic models
│   │   ├── builder.py          # Deterministic Knowledge Graph builder
│   │   ├── updater.py          # Incremental graph update for changed files
│   │   ├── queries.py          # get_callers, get_callees, get_tests, get_impact, check_claim_support
│   │   ├── freshness.py        # Staleness and drift detection
│   │   └── gc.py               # Active job-protected graph garbage collection
│   │
│   ├── retrieval/
│   │   ├── semantic_retriever.py # Vector embedding and cosine similarity search
│   │   ├── graph_retriever.py    # Structural graph traversal
│   │   ├── ranker.py             # 11-priority level context ranker & token budget packer
│   │   ├── hybrid_retriever.py   # Hybrid RAG orchestrator with sanitized file reads
│   │   └── sanitized_projections.py # Ephemeral read-only sanitized code projections & secret redaction
│   │
│   ├── review/
│   │   ├── schemas.py          # ReviewFinding, ReviewResult, Evidence Pydantic schemas
│   │   ├── evidence.py         # Evidence validator against Knowledge Graph (hallucination gate)
│   │   ├── impact.py           # Downstream blast radius analyzer
│   │   ├── reviewer.py         # Strictly read-only Diff Reviewer agent
│   │   ├── snapshot_service.py # assert_head_sha_current() & advisory-locked mark_superseded()
│   │   ├── canonical.py        # Deterministic canonical serialization & lineage hashing (chain_hash)
│   │   ├── publisher.py        # Atomic publication claim, Option B marker reconciliation, & recovery
│   │   └── exceptions.py       # PublicationRetryableError, PublicationPermanentError
│   │
│   ├── validation/
│   │   ├── baseline.py         # Pre-repair baseline capture
│   │   ├── test_selector.py    # Graph-guided targeted test selector
│   │   ├── differential.py     # Differential validation (zero new regressions guarantee)
│   │   ├── classifiers.py      # Failure classifier (code vs environment/OOM)
│   │   └── runner.py           # 7-layer validation plan runner
│   │
│   ├── repair/
│   │   ├── service.py          # Repair domain service (RepairRun persistence, hash, execution)
│   │   ├── patch_policy.py     # Pre-application patch scope & safety enforcer
│   │   ├── patcher.py          # Checkpoint patch application & rollback
│   │   ├── repairer.py         # Repair Agent (generates scoped unified diff)
│   │   └── loop.py             # Iterative loop (dynamic RAG, stall detection, budget limits)
│   │
│   ├── sandbox/
│   │   ├── interface.py        # SandboxBackend ABC & SandboxResult
│   │   ├── policy.py           # Security constraints & resource limits
│   │   └── docker.py           # Hardened Docker sandbox runner with fail-closed production boundary
│   │
│   ├── llm/
│   │   ├── interface.py        # LLMProvider ABC & LLMResponse
│   │   ├── openai.py           # OpenAIProvider with structured Pydantic schema parsing
│   │   ├── providers/          # Gemini, Anthropic, DeepSeek, Qwen, Custom providers
│   │   └── routing.py          # Model routing (gpt-4o / gpt-4o-mini)
│   │
│   ├── database/
│   │   ├── models.py           # SQLAlchemy ORM (User, GitHubConnection, UserRepository, Repository, Job, OutboxEvent, TaskExecution, PublishedReview...)
│   │   └── sessions.py         # Async/sync database session factories
│   │
│   ├── security/
│   │   ├── secrets.py          # Secret scanner for tokens/passwords/keys
│   │   ├── redaction.py        # Pre-LLM context redaction
│   │   ├── policies.py         # Prompt injection sanitizer & dependency policy
│   │   ├── denylist.py         # Categorized denylist & semantic YAML classifier
│   │   └── ast_escalation.py   # AST analysis flagging sensitive system calls for human escalation
│   │
│   ├── risk/
│   │   └── analyzer.py         # PR risk & blast radius classifier (LOW/MEDIUM/HIGH/CRITICAL)
│   │
│   ├── observability/
│   │   ├── logging.py          # structlog JSON logger with event taxonomy & sensitive field redaction
│   │   └── metrics.py          # Prometheus KPI metrics tracker
│   │
│   ├── app.py                  # FastAPI app factory (lifespan key validation, CORS, routers)
│   └── config.py               # Pydantic Settings (OAuth, encryption key, cookie security)
│
├── worker/
│   ├── celery.py               # Celery app configuration & queue routing
│   └── tasks/
│       ├── graph.py            # Graph build background task with credential precedence
│       ├── review.py           # Full state machine orchestration with JobMutationService & SHA checks
│       ├── repair.py           # Autonomous repair Celery task delegating to RepairService
│       └── validation.py       # Standalone 7-layer validation Celery task
│
├── frontend/
│   ├── src/
│   │   ├── api/client.ts       # Axios client with credentials, auth methods & interfaces
│   │   ├── context/
│   │   │   └── AuthContext.tsx # User profile provider, login redirect, logout, and session lifecycle
│   │   ├── components/
│   │   │   ├── UserMenu.tsx         # User avatar, @username, and dropdown sign-out
│   │   │   ├── GitHubIcon.tsx       # Authentic SVG GitHub mark component
│   │   │   ├── ConfidenceBar.tsx    # Evidence-backed confidence score component
│   │   │   ├── FindingCard.tsx      # Review finding card with graph evidence details
│   │   │   ├── PatchDiff.tsx        # Syntax-highlighted unified diff viewer
│   │   │   ├── ValidationReport.tsx # Layer-by-layer validation report
│   │   │   ├── ImpactGraph.tsx      # Subgraph impact visualizer
│   │   │   ├── RepairTimeline.tsx   # Per-iteration repair history and diffs
│   │   │   ├── RiskBar.tsx          # Stacked PR risk distribution bar
│   │   │   └── StatusBadge.tsx      # Formal state machine colored badge
│   │   ├── pages/
│   │   │   ├── Dashboard.tsx        # PR overview & metrics
│   │   │   ├── JobDetail.tsx        # Full job review & repair timeline with publish action
│   │   │   ├── RepositoryGraph.tsx  # Knowledge Graph browser
│   │   │   ├── RepositoryHub.tsx    # Multi-tenant tracked repos, AuthBanner, & discovery modal
│   │   │   └── FindingsExplorer.tsx # Cursor-paginated repository-wide findings browser
│   │   ├── App.tsx             # Main layout, router, Topbar & Sidebar with UserMenu
│   │   ├── main.tsx            # React DOM root
│   │   └── index.css           # Design tokens, user menu dropdown, and auth banner styles
│   ├── package.json
│   └── vite.config.ts
│
├── tests/
│   ├── unit/
│   │   ├── test_config.py              # Configuration defaults
│   │   ├── test_token_encryption.py    # Fernet encryption, decryption, tamper rejection, fail-fast
│   │   ├── test_auth_security.py       # JWT session signing, verification, CSRF state, 401 gates
│   │   ├── test_credential_provider.py # Precedence resolution, expiration buffer, token refresh
│   │   ├── test_sanitized_projections.py # Projections security boundary & redaction tests
│   │   ├── test_publication_concurrency.py # Publication race condition & claim tests
│   │   └── test_state_machine_v3.py    # Formal state machine transition graph tests
│   ├── integration/
│   │   ├── test_auth_endpoints.py      # OAuth login 302, state cookie, callback upsert, me, logout
│   │   ├── test_repository_authz.py   # Multi-user isolation, 403 enforcement, independent settings
│   │   └── test_retrieval_before_generation.py # Mandatory retrieval verification
│   ├── e2e/
│   │   ├── test_publication_crash_windows.py # Crash windows during posting recovery
│   │   ├── test_stale_worker_superseded_race.py # Stale worker cannot resurrect superseded job
│   │   └── test_task_attempt_recovery.py # Worker crash attempt recovery
│   ├── graph/test_python_analyzer.py
│   ├── repairer/test_classifiers.py
│   ├── repairer/test_differential.py
│   ├── repairer/test_policy.py
│   └── security/test_security.py
│
├── docker/
│   ├── sandbox/Dockerfile      # Hardened non-root sandbox image
│   └── analysis/               # Analysis worker container
├── docker-compose.yml          # Postgres(pgvector) + Redis + Backend + Worker + Frontend
├── Dockerfile                  # Production backend container
├── pyproject.toml              # Python dependencies and pytest configuration
└── .env.example                # Full environment variable template
```

---

## 🔐 Multi-Tenant GitHub OAuth & Authorization Model

```text
               ┌─────────────────────────────────────────────────────────────┐
               │                     User Browser                            │
               │  - HttpOnly Cookie: prsmith_session (HS256 JWT)             │
               │  - HttpOnly Cookie: oauth_state (CSRF 10-min window)        │
               └──────────────────────────────┬──────────────────────────────┘
                                              │ Requests (withCredentials: true)
                                              ▼
┌────────────────────────────────────────────────────────────────────────────────────────────┐
│ FastAPI Application (backend/app.py)                                                       │
│                                                                                            │
│  GET /api/auth/github/login    ──► Generate state, set oauth_state cookie, redirect 302    │
│  GET /api/auth/github/callback ──► Validate state, exchange code, upsert user & connection │
│  GET /api/auth/me              ──► Validate JWT, return user profile                       │
│  POST /api/auth/logout         ──► Clear prsmith_session cookie                            │
│                                                                                            │
│  FastAPI Dependency: get_current_user                                                      │
│   └── Extracts & verifies JWT ──► Resolves DB User or raises 401                           │
└─────────────────────────────────────────────┬──────────────────────────────────────────────┘
                                              │
                                              ▼
┌────────────────────────────────────────────────────────────────────────────────────────────┐
│ Relational PostgreSQL Database                                                             │
│                                                                                            │
│  ┌───────────────────────┐         ┌───────────────────────┐                               │
│  │         users         │ 1     1 │  github_connections   │                               │
│  │ id (PK)               ├─────────┤ user_id (FK, UNIQUE)  │ (Fernet Encrypted Tokens)     │
│  │ github_id (UNIQUE)    │         │ access_token_encrypted│                               │
│  │ username              │         │ refresh_token_enc     │                               │
│  │ role                  │         │ access_token_expires_at                               │
│  └───────────┬───────────┘         └───────────────────────┘                               │
│              │ 1                                                                           │
│              │                                                                             │
│              │ N                                                                           │
│  ┌───────────▼───────────┐         ┌───────────────────────┐                               │
│  │   user_repositories   │ N     1 │     repositories      │                               │
│  │ user_id (FK)          ├─────────┤ id (PK)               │ (Canonical GitHub Repo)       │
│  │ repository_id (FK)    │         │ github_id (UNIQUE)    │                               │
│  │ monitoring_enabled    │         │ full_name (UNIQUE)    │                               │
│  │ auto_repair_enabled   │         │ private               │                               │
│  │ watched_branches      │         └───────────┬───────────┘                               │
│  │ UNIQUE(user, repo)    │                     │ 1                                         │
│  └───────────────────────┘                     │                                           │
│                                                │ N                                         │
│                                    ┌───────────▼───────────┐                               │
│                                    │         jobs          │                               │
│                                    │ id (PK)               │                               │
│                                    │ repository_id (FK)    │                               │
│                                    │ triggered_by_user_id  │ ──► FK to users.id (nullable) │
│                                    │ status, pr_number     │                               │
│                                    └───────────────────────┘                               │
└────────────────────────────────────────────────────────────────────────────────────────────┘
```

### Credential Resolution Precedence
Whenever git clone/fetch or PR inspection executes:
1. **GitHub App Installation Token**: If `repository.installation_id` is set (automated webhooks).
2. **User OAuth Token**: If `job.triggered_by_user_id` is set or an authenticated user requests discovery. Automatically refreshes expired tokens using a `SELECT FOR UPDATE` row lock on `github_connections`.
3. **Development PAT**: Gated behind `ENABLE_PAT_FALLBACK=True` (disabled by default in production).

---

## 📜 Project Chronology & Milestone History

### Milestone 1: Core Autonomous Review & Repair Engine
- **AST Knowledge Graph:** Deterministic extraction of class hierarchies, call graphs, import dependencies, and test mappings for Python codebases using AST parsing.
- **Hybrid RAG:** 11-priority level ranking combining structural graph traversal and vector semantic embeddings within token budgets.
- **Diff Reviewer Agent:** Strictly read-only reviewer producing structured findings grounded by graph evidence (anti-hallucination gate).
- **Automated Repair Loop:** Sandboxed patch generation, scope validation (`patch_policy.py`), and 7-layer verification (syntax, lint, types, targeted tests, baseline comparisons).

### Milestone 2: Asynchronous Distributed Execution & Frontend Dashboard
- **Celery & Redis Architecture:** Decoupled background execution into 4 distinct queues (`graph_build`, `review`, `repair`, `validation`).
- **Web UI:** React 19 + TypeScript + Vite frontend with glassmorphism design, unified diff viewer, validation reports, and interactive impact graphs.
- **Webhook Ingestion:** Cryptographically verified GitHub App webhook handler (`POST /webhook`) supporting HMAC SHA-256 signatures and idempotency tracking.

### Milestone 3: Multi-Tenant Architecture & GitHub OAuth 2.0 (Completed)
- **v1-v4 Design Audits:** Identified and resolved 5 critical architectural contradictions:
  - Fixed `repositories.github_id UNIQUE` conflict by introducing `user_repositories` association table.
  - Eliminated dual-source configuration drift: per-user repo settings moved exclusively to `UserRepository`.
  - Prevented silent JSON mutation loss with `MutableList.as_mutable(JSON)`.
  - Replaced non-deterministic `_get_first_user_repository()` fallback with deterministic `job.triggered_by_user_id`.
  - Configured environment-driven `COOKIE_SECURE` to avoid breaking localhost development.
- **v5-v6 Hardening:**
  - Enforced eager encryption key validation during application startup (`lifespan`), failing fast on misconfiguration.
  - Configurable `GITHUB_OAUTH_SCOPES` (defaulting to minimal identity/discovery scopes).
  - Concurrency protection on token refresh via `SELECT FOR UPDATE`.
- **Implementation & Testing:**
  - Built `token_encryption.py` (Fernet), `security.py` (JWT & CSRF), `credential_provider.py` (3-tier resolution).
  - Created full Alembic migration suite (`936f4822902d_add_github_oauth_multi_tenant.py`).
  - Added frontend `AuthContext`, `UserMenu`, and `AuthBanner` components.
  - Authored and verified 32 comprehensive tests (100% pass rate).

### Milestone 4: Systems Reliability & Sandbox Hardening
- **Transactional Outbox:** Guaranteed webhook delivery via atomic DB transaction write to `outbox_events` and resilient dispatcher polling.
- **Two-Level TaskExecution Model:** Stable `TaskExecution` identity paired with per-worker-attempt `TaskExecutionAttempt` tracking.
- **Fail-Closed Sandbox:** Zero subprocess execution fallback in production; containerized Docker execution with strict limits or `SandboxUnavailableError`.
- **Cryptographic Lineage Binding:** `canonical_hash()` and `chain_hash` binding commit SHA, AST graph digest, finding hashes, patch diff, and validation results.
- **Categorized Denylist & AST Escalation:** Semantic YAML inspection and AST scanning flagging sensitive system calls for human escalation.

### Milestone 5: End-to-End v3.0 Production Architecture & Concurrency Hardening
- **Formal Guarantee Contract:** Guaranteed at-least-once delivery + at-most-one active owner + idempotent/reconcilable side effects (zero "exactly-once" claims).
- **Authoritative State Machine:** 10 discrete states with optimistic concurrency locking (`JobMutationService.transition`), first-class `ESCALATED` terminal state with `completion_reason` enum, and `STALE_SNAPSHOT` for superseded commits.
- **Layered Publication Concurrency:** Partial unique index on `(repository_id, pr_number)` for active claims (`CLAIMED`, `POSTING`) and partial unique index on `is_current=True` with single-transaction atomic swap.
- **Option B Publication Strategy:** Deterministic reconciliation against GitHub HTML marker comments (`<!-- prsmith:published_review:{chain_hash} -->`) — PATCH existing comment if marker is found, else POST new comment.
- **Stale Publication Recovery & Error Taxonomy:** 429/5xx/timeout mapped to `PublicationRetryableError` with exponential backoff (`next_attempt_at`); 403/404/422 mapped to `PublicationPermanentError`.
- **Task Attempt Recovery:** Stale heartbeats transitioned to `ABANDONED` and re-spawned as `PENDING` (never leaving an ownerless `CLAIMED` state).
- **Mandatory Sanitization Boundary:** Ephemeral `SanitizedProjection` applied to all repository-derived strings before persistent storage, vector embedding, or LLM prompt assembly.
- **Snapshot Immutability:** Mandatory `assert_head_sha_current()` checkpoints before review, before each repair iteration, and before publication claim.
- **Repair Domain Service Layering:** Centralized `RepairService` domain service decoupling Celery workers from repair/validation implementation.

---

## 🔄 End-to-End State Machine (v3.0)

### State Transition Graph

```text
                           ┌────────────────────────┐
                           │        PENDING         │
                           └───────────┬────────────┘
                                       │ Lease acquired
                                       ▼
                           ┌────────────────────────┐
                           │        CLONING         │
                           └───────────┬────────────┘
                                       │ Snapshot created & verified
                                       ▼
                           ┌────────────────────────┐
                           │       ANALYZING        │
                           └───────────┬────────────┘
                                       │ Graph built & risk assessed
                                       ▼
                           ┌────────────────────────┐
                           │       REVIEWING        │
                           └─────┬────────────┬─────┘
          No repair path or      │            │ Actionable findings &
          CRITICAL / auto_rep=F  │            │ auto_repair=True (risk!=CRIT)
                                 │            ▼
                                 │   ┌────────────────────────┐
                                 │   │       REPAIRING        │◄───────────┐
                                 │   └──────────┬─────────────┘            │
                                 │              │ Iteration patch applied  │ Regressions &
                                 │              ▼                          │ retry budget
                                 │   ┌────────────────────────┐            │
                                 │   │       VALIDATING       │────────────┘
                                 │   └──────────┬─────────────┘
                                 │              │ Differential passed (0 regressions)
                                 ▼              ▼
                           ┌────────────────────────┐
                           │       PUBLISHING       │
                           └───────────┬────────────┘
                                       │ GitHub comment posted / patched
                                       ▼
                           ┌────────────────────────┐
                           │       COMPLETED        │ (Terminal)
                           └────────────────────────┘

Non-Happy Terminal Paths:
  - Any State + Commit Drift (assert_head_sha_current fails) ──► STALE_SNAPSHOT
  - Unrecoverable Failure / Permanent Publication Error       ──► FAILED
  - Human Escalation / Budget Exhausted / AST Escalation      ──► ESCALATED
```

### Formal Transition Table

| Current State | Trigger Event | Next State | Completion Reason / Notes |
|---|---|---|---|
| `PENDING` | Worker acquires Job lease | `CLONING` | `JobMutationService.acquire_lease()` then `transition()` |
| `PENDING` | New commit detected | `STALE_SNAPSHOT` | `mark_superseded()` with advisory lock |
| `PENDING` | Fatal initialization error | `FAILED` | `completion_reason=UNRECOVERABLE_ERROR` |
| `CLONING` | Snapshot created & validated | `ANALYZING` | `RepositorySnapshot` persisted, `job.snapshot_id` set |
| `CLONING` | Git clone/fetch error | `FAILED` | Network failure or invalid credentials |
| `CLONING` | New commit detected | `STALE_SNAPSHOT` | Stale snapshot drift |
| `ANALYZING` | Graph built & risk computed | `REVIEWING` | `GraphVersion` persisted |
| `ANALYZING` | AST/analysis error | `FAILED` | Analysis crash |
| `ANALYZING` | New commit detected | `STALE_SNAPSHOT` | Stale snapshot drift |
| `REVIEWING` | Review validated, no repair needed | `PUBLISHING` | `auto_repair=False` OR `risk=CRITICAL` OR no actionable findings |
| `REVIEWING` | Review validated, repair candidate | `REPAIRING` | `auto_repair=True` AND actionable findings AND `risk!=CRITICAL` |
| `REVIEWING` | `StaleSnapshotError` raised | `STALE_SNAPSHOT` | `assert_head_sha_current()` before review |
| `REVIEWING` | Human escalation condition | `ESCALATED` | `completion_reason=HUMAN_ESCALATION` |
| `REVIEWING` | Reviewer agent failure | `FAILED` | LLM or evidence validation failure |
| `REPAIRING` | Iteration patch generated & applied | `VALIDATING` | Patch applied to isolated worktree |
| `REPAIRING` | Repair budget exhausted / loop stall | `ESCALATED` | `completion_reason=REPAIR_BUDGET_EXHAUSTED` |
| `REPAIRING` | `StaleSnapshotError` raised | `STALE_SNAPSHOT` | `assert_head_sha_current()` before iteration |
| `VALIDATING` | Differential check: zero regressions | `PUBLISHING` | All 7 validation layers passed |
| `VALIDATING` | Differential fails, retry budget > 0 | `REPAIRING` | Feedback injected into next repair iteration |
| `VALIDATING` | Differential fails, retry budget = 0 | `ESCALATED` | `completion_reason=VALIDATION_FAILED` |
| `VALIDATING` | `StaleSnapshotError` raised | `STALE_SNAPSHOT` | Drift detected during validation |
| `PUBLISHING` | GitHub comment posted/patched | `COMPLETED` | `PublishedReview.publication_status=POSTED`, `is_current=True` |
| `PUBLISHING` | Retryable error (429, 5xx, timeout) | `PUBLISHING` | `publication_status=FAILED_RETRYABLE`, backoff scheduled |
| `PUBLISHING` | Permanent error (403, 404, 422) | `FAILED` | `publication_status=FAILED_PERMANENT` |
| `PUBLISHING` | `StaleSnapshotError` raised | `STALE_SNAPSHOT` | Checkpoint immediately before API call |
| `COMPLETED` | — | *Terminal* | Successfully published to GitHub |
| `FAILED` | — | *Terminal* | Pipeline error occurred |
| `ESCALATED` | — | *Terminal* | Requires human intervention |
| `STALE_SNAPSHOT` | — | *Terminal* | Superseded by newer commit (`superseded_by_job_id`) |

---

## 🛠️ Developer Cheatsheet

### Running Backend Tests
```bash
# Run unit & integration test suites
pytest tests/unit tests/integration -v
```

### Running Database Migrations
```bash
# Apply all pending Alembic migrations
alembic upgrade head

# Generate a new migration revision
alembic revision --autogenerate -m "description_of_changes"
```

### Running Frontend Build
```bash
cd frontend
npm run build
```

### Generating Fernet Encryption Key
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### Starting Services via Docker Compose
```bash
docker-compose up --build
```

### Launching Local Backend
```bash
uvicorn backend.app:app --host 0.0.0.0 --port 8000 --reload
```

### Launching Local Celery Worker
```bash
celery -A worker.celery_app worker --loglevel=info --concurrency=4
```

### Launching Local Frontend Dev Server
```bash
cd frontend
npm run dev
```
