# PRSmith v2 — Final Implementation Plan (v3.0)
## Incorporating All 27 Corrections Across 3 Review Rounds

> **Status: Implementation-Ready**
> Three-pass architectural review complete. Proceed to execution.

---

## What the Code Audit Revealed (Undocumented Existing Infrastructure)

Before specifying changes, the following already exist and are **more complete than any previous plan stated**:

| Module | Actual State | Impact on Plan |
|---|---|---|
| `backend/review/snapshot_service.py` | `assert_head_sha_current()` + `StaleSnapshotError` + `mark_superseded()` with **PostgreSQL advisory locking** (`pg_advisory_xact_lock`) | Snapshot immutability is partially implemented; plan specifies mandatory call sites only |
| `Job.snapshot_id` FK | Already in `models.py:193` | Snapshot lineage link exists for Jobs |
| `GraphVersion.snapshot_id` FK | Already in `models.py:102` | Graph → Snapshot link exists |
| `JobMutationService.transition()` | Already does `WHERE version = :expected_version` conditional UPDATE | Optimistic lock is implemented; the bug is the worker **bypasses it** |
| `Job.status = "STALE_SNAPSHOT"` | Used in `snapshot_service.py:71` | Correct terminal state name (not `SUPERSEDED`) |
| `PublicationPrerequisiteError` | Already in `review/exceptions.py` | Used correctly in `publisher.py` |

---

## Corrected Guarantee Language (Frozen — Do Not Change)

> **At-least-once delivery + at-most-one active logical owner + idempotent/reconcilable side effects.**
> No code, comment, or document in this project may claim "exactly-once execution."

---

## Authoritative State Machine (v3.0)

Two changes from v2.0:
1. `STALE_SNAPSHOT` replaces `SUPERSEDED` (matches existing code)
2. `ESCALATED` is a distinct terminal state (not `FAILED` with parsed `error_message`)

| Current | Event | Next | Notes |
|---|---|---|---|
| `PENDING` | Worker acquires Job lease | `CLONING` | `JobMutationService.acquire_lease()` then `transition()` |
| `PENDING` | New commit detected | `STALE_SNAPSHOT` | `mark_superseded()` — advisory-locked |
| `PENDING` | Fatal error | `FAILED` | |
| `CLONING` | Snapshot created + validated | `ANALYZING` | `RepositorySnapshot` persisted, `job.snapshot_id` set |
| `CLONING` | Clone error | `FAILED` | |
| `CLONING` | New commit | `STALE_SNAPSHOT` | `mark_superseded()` |
| `ANALYZING` | Graph built + risk computed | `REVIEWING` | `GraphVersion` persisted |
| `ANALYZING` | Error | `FAILED` | |
| `ANALYZING` | New commit | `STALE_SNAPSHOT` | |
| `REVIEWING` | Findings validated, no repair path | `PUBLISHING` | `auto_repair=False` OR `risk=CRITICAL` OR no actionable findings |
| `REVIEWING` | Findings validated, repair path | `REPAIRING` | `auto_repair=True` AND actionable findings AND `risk≠CRITICAL` |
| `REVIEWING` | `StaleSnapshotError` raised | `STALE_SNAPSHOT` | |
| `REVIEWING` | Human escalation condition | `ESCALATED` | Sets `completion_reason=HUMAN_ESCALATION` |
| `REVIEWING` | Unrecoverable error | `FAILED` | |
| `REPAIRING` | Iteration complete, validation passed | `VALIDATING` | |
| `REPAIRING` | Budget exhausted / stall | `ESCALATED` | `completion_reason=REPAIR_BUDGET_EXHAUSTED` |
| `REPAIRING` | `StaleSnapshotError` | `STALE_SNAPSHOT` | |
| `VALIDATING` | Differential: zero new regressions | `PUBLISHING` | |
| `VALIDATING` | Differential fails, retry budget | `REPAIRING` | Iteration loop |
| `VALIDATING` | Differential fails, budget gone | `ESCALATED` | `completion_reason=VALIDATION_FAILED` |
| `VALIDATING` | `StaleSnapshotError` | `STALE_SNAPSHOT` | |
| `PUBLISHING` | Comment posted (POSTED) | `COMPLETED` | |
| `PUBLISHING` | Retryable error (timeout, 429, 5xx) | `PUBLISHING` | `PublishedReview.publication_status=FAILED_RETRYABLE`, recovery worker retries |
| `PUBLISHING` | Permanent error (403, 404, 422) | `FAILED` | `PublishedReview.publication_status=FAILED_PERMANENT` |
| `PUBLISHING` | `StaleSnapshotError` | `STALE_SNAPSHOT` | Assert SHA immediately before publication |
| `COMPLETED` | — | terminal | |
| `FAILED` | — | terminal | |
| `ESCALATED` | — | terminal | |
| `STALE_SNAPSHOT` | — | terminal | Links to `superseded_by_job_id` |

**`state_machine.py` must be updated to match this table exactly**, including renaming `STATUS_SUPERSEDED` to `STATUS_STALE_SNAPSHOT` and adding `STATUS_ESCALATED`.

---

## P0 Fix #1 — Atomic Publication Claiming (Concurrency Race)

**Problem**: Two workers can both see no existing marker on GitHub and both POST, creating duplicate comments.

**Root cause**: The DB `UNIQUE(idempotency_key)` on `PublishedReview` prevents duplicate *lineage* claims but does not prevent two workers from racing through `claim_publication()` with different lineage keys for the same PR at the same time.

**Fix — three-layer defense**:

### Layer 1: DB Uniqueness on (repository_id, pr_number) for Active Claims

Add a **partial unique index** to `PublishedReview`:
```sql
CREATE UNIQUE INDEX uq_published_review_active_pr
ON published_reviews (repository_id, pr_number)
WHERE publication_status IN ('CLAIMED', 'POSTING');
```

This prevents two concurrent CLAIMED or POSTING rows for the same PR. At most one worker owns the publication at any time. When a new lineage supersedes, the old row must first be advanced to POSTED/FAILED before a new claim is created.

In `models.py`:
```python
class PublishedReview(Base):
    __table_args__ = (
        Index(
            "uq_published_review_active_pr",
            "repository_id", "pr_number",
            unique=True,
            postgresql_where=text(
                "publication_status IN ('CLAIMED', 'POSTING')"
            ),
        ),
        # existing UNIQUE on idempotency_key stays
    )
    # Add new column:
    repository_id: Mapped[str]  # FK to repositories.id — ADD THIS
    pr_number: Mapped[int]       # Denormalized for the partial index — ADD THIS
```

### Layer 2: Single Owner Claims Before GitHub API Call

`claim_publication()` in `publisher.py` must succeed (not return `None`) before any GitHub API call is made. The existing `IntegrityError` catch on `UNIQUE(idempotency_key)` handles per-lineage racing. The partial unique index handles per-PR racing. No change to the calling code flow is needed beyond ensuring `claim_publication()` is always called before `publish_review_comment()`.

### Layer 3: SHA Assertion Before Publication

`assert_head_sha_current()` (already in `snapshot_service.py`) must be called immediately before the GitHub POST. If SHA has drifted, raise `StaleSnapshotError` → transition to `STALE_SNAPSHOT` instead of posting stale review.

---

## P0 Fix #2 — FAILED_RETRYABLE in Job State Machine

**Problem**: `FAILED_RETRYABLE` and `FAILED_PERMANENT` live only on `PublishedReview` but their effect on the `Job` state is not defined.

**Fix — Publication Retry via Recovery Worker (not Celery auto-retry)**:

```
Celery task
    ↓
claim PublishedReview (→ CLAIMED)
    ↓
advance to POSTING
    ↓
GitHub API
    ↓
success → POSTED → Job.transition(COMPLETED)
retryable → FAILED_RETRYABLE on PublishedReview, Job stays PUBLISHING
permanent → FAILED_PERMANENT on PublishedReview, Job.transition(FAILED)
```

The `Job` status stays `PUBLISHING` on retryable failure. A **publication recovery worker** (extending the existing `recover_stale_posting_claims()` in `publisher.py`) polls for `publication_status=FAILED_RETRYABLE` with exponential backoff and retries the POST. This survives Celery worker death because the retry state lives in the DB.

**Retryable vs Permanent classification**:
| GitHub HTTP Status | Classification |
|---|---|
| 429 (rate limit) | `FAILED_RETRYABLE` — respect `Retry-After` header |
| 500, 502, 503, 504 | `FAILED_RETRYABLE` |
| Connection timeout | `FAILED_RETRYABLE` |
| 403 (no permission) | `FAILED_PERMANENT` |
| 404 (repo/PR gone) | `FAILED_PERMANENT` |
| 422 (invalid comment body) | `FAILED_PERMANENT` |
| Token expired/revoked | `FAILED_RETRYABLE` after credential refresh attempt; `FAILED_PERMANENT` if refresh fails |

Add `PublicationErrorKind` enum to `review/exceptions.py`:
```python
class PublicationRetryableError(ReviewError):
    """Raised on transient GitHub API errors — recovery worker will retry."""

class PublicationPermanentError(ReviewError):
    """Raised on permanent GitHub API errors — job will be marked FAILED."""
```

Add to `PublishedReview`:
```python
publication_attempts: Mapped[int] = 0           # increments on each POST attempt
max_publication_attempts: Mapped[int] = 5       # upgrades FAILED_RETRYABLE → FAILED_PERMANENT
last_attempt_at: Mapped[Optional[datetime]]     # set at start of each attempt
next_attempt_at: Mapped[Optional[datetime]]     # set by recovery worker; recovery queries WHERE next_attempt_at <= NOW()
```

**Precise counter semantics**:
```
claim_publication() called
    publication_attempts += 1
    last_attempt_at = now()
POST attempt
    success → POSTED, next_attempt_at=None
    retryable AND attempts < max → FAILED_RETRYABLE, next_attempt_at = now + backoff(attempts)
    retryable AND attempts >= max → FAILED_PERMANENT → Job.transition(FAILED)
    permanent → FAILED_PERMANENT → Job.transition(FAILED)
```

Recovery worker query: `WHERE publication_status = 'FAILED_RETRYABLE' AND next_attempt_at <= NOW()`. Backoff: `min(60 * 2^(attempts-1), 3600)` seconds.

---

## P0 Fix #3 — Stale Worker Cannot Resurrect a Job

**Problem**: `worker/tasks/review.py` currently assigns `job.status = "SNAPSHOTTING"` (and other statuses) directly, bypassing `JobMutationService.transition()` which already enforces the conditional `WHERE version = :expected` optimistic lock.

**Fix**: Replace every direct `job.status = "..."` assignment in `worker/tasks/review.py` with `await JobMutationService.transition(session, job, new_status, worker_id=worker_id)`. The conditional UPDATE already in `JobMutationService` means: if `mark_superseded()` ran concurrently and incremented `job.version`, the worker's `transition()` call returns `rowcount=0` → raises `StaleJobError` → worker exits cleanly.

The fix is **primarily in `worker/tasks/review.py`** (and the new `worker/tasks/repair.py`). The `JobMutationService` itself does not need changes.

**Mandatory `assert_head_sha_current()` checkpoints** (calling the already-implemented function):
1. Immediately before `reviewer.review_pr()` is called
2. Immediately before each repair iteration begins
3. Immediately before `claim_publication()` is called

If `StaleSnapshotError` is raised at any checkpoint → call `JobMutationService.transition(job, "STALE_SNAPSHOT")`.

---

## P1 Fix #4 — Sanitization Boundary: All Repository-Derived Strings

The security invariant is:
> **No raw repository-derived string may enter persistent storage, vector embeddings, or an LLM prompt without passing the sanitization policy.**

**String classification table**:

| String type | Source | Treatment |
|---|---|---|
| File body content | Clone path | `SanitizedProjection.read_file_sanitized()` — full secret scan + redaction |
| Diff hunk content | `git diff` output | `SanitizedProjection.sanitize_diff()` — full secret scan + redaction |
| File paths | `git diff --name-only` | Policy scan: flag paths matching `*.pem`, `*.key`, `*secret*`, `*password*`, `*credential*` etc. — log warning, do not send to LLM |
| Symbol names | AST parser output | Policy scan: warn if matches secret-like patterns, otherwise pass through |
| Commit messages | `git log` | Policy scan + redaction before use in prompts |
| AST metadata (properties JSON) | Graph builder | File body already sanitized before AST parsing; properties contain no raw code |
| Exception messages | Any | Strip before logging/LLM using `redact_secrets()` on `str(exc)` |
| Retrieval context items | HybridRetriever output | Sanitized at source (file read → sanitized projection); no further pass needed |
| LLM tool/function call arguments | Prompt construction | Verify no raw paths or exception strings are interpolated |

**`SanitizedProjection` class** (the new `backend/retrieval/sanitized_projections.py`) adds `scan_file_path(path: str) -> ScanResult` and `scan_symbol_name(name: str) -> ScanResult` alongside the file content methods.

**Logging**: `structlog` processors must redact secrets from log fields. Add a `redact_log_field` processor wrapping the existing `redact_secrets()` function, applied to `error`, `message`, `diff`, `content` log key names.

---

## P1 Fix #5 — Separate Publication History from Current Publication

**Problem**: Multiple `PublishedReview` rows exist per PR (one per lineage/chain_hash). Queries for "what is the current published review for PR #42?" are ambiguous.

**Fix — `is_current` flag with partial unique index**:

```python
class PublishedReview(Base):
    # ... existing fields ...
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)
    
    __table_args__ = (
        # Partial unique: only one 'current' per (repo, pr) at a time
        Index(
            "uq_published_review_current",
            "repository_id", "pr_number",
            unique=True,
            postgresql_where=text("is_current = true"),
        ),
        # Active publication claim (from P0 Fix #1)
        Index(
            "uq_published_review_active_pr",
            "repository_id", "pr_number",
            unique=True,
            postgresql_where=text("publication_status IN ('CLAIMED', 'POSTING')"),
        ),
    )
```

**Lifecycle**:
- When a `PublishedReview` transitions to `POSTED`: inside **a single DB transaction**, first `UPDATE published_reviews SET is_current=False WHERE repository_id=:rid AND pr_number=:pr AND is_current=True`, then set `pub.is_current=True`. The partial unique index is the final invariant. The two-step swap within one transaction prevents both UNIQUE violation and the crash-between-unset-and-set gap.
- New helper: `get_current_published_review(session, repo_id, pr_number) → Optional[PublishedReview]` — `WHERE is_current=True AND publication_status='POSTED'`.
- Historical lineage is preserved — all previous rows remain with `is_current=False`.
- **Obsolete active claim cancellation**: if a newer lineage (newer `head_sha`) needs to claim publication and finds an existing `CLAIMED` or `POSTING` row for the same `(repository_id, pr_number)` belonging to a now-superseded lineage, set that old row's `publication_status='CANCELLED'` before creating the new claim. `CANCELLED` is a valid terminal publication status (distinct from Job `STALE_SNAPSHOT`). The partial unique index only covers `('CLAIMED', 'POSTING')` so a `CANCELLED` row does not block new claims.
- The API `GET /api/jobs/{id}` returns `current_publication` (the POSTED is_current row if it exists) separately from the full `publications` history array.

---

## Additional Fixes

### ESCALATED as Explicit Terminal State

Add to `state_machine.py`:
```python
STATUS_ESCALATED = "ESCALATED"
TERMINAL_STATES: Set[str] = {
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_ESCALATED,       # Human escalation — distinct from technical failure
    STATUS_STALE_SNAPSHOT,  # Superseded by new commit
}
```

Add to `Job` model:
```python
completion_reason: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
# Values: HUMAN_ESCALATION, REPAIR_BUDGET_EXHAUSTED, VALIDATION_FAILED,
#         STALE_SNAPSHOT_DRIFT, UNRECOVERABLE_ERROR, NONE
```

`error_message` remains for human-readable text. `completion_reason` is the machine-readable enum. No code parses `error_message` for branching logic.

### Snapshot Immutability — Mandatory Checkpoint Call Sites

`assert_head_sha_current()` already exists in `snapshot_service.py`. The plan mandates it is called (by the worker) at exactly three points per job lifecycle:
1. **Before `reviewer.review_pr()`** — pass current git HEAD for the PR
2. **Before each `repair_orchestrator.execute_repair()` call** — per-iteration
3. **Before `claim_publication()`** — final SHA check

All three checkpoints catch `StaleSnapshotError` and transition the Job to `STALE_SNAPSHOT`.

### ReviewFinding Name Collision Resolution

In every worker file that imports from both modules:
```python
from backend.review.schemas import ReviewFinding as ReviewFindingSchema
from backend.database.models import ReviewFinding as ReviewFindingORM
```

This applies to: `worker/tasks/review.py`, `backend/repair/service.py` (new).

---

## Final File Change Map

### Backend — Phase A (State Machine + Concurrency)

| Action | File | Change |
|---|---|---|
| MODIFY | [`backend/orchestration/state_machine.py`](file:///c:/Users/gungu/PRSmith---Autonomous-Pull-Request-Agent-/backend/orchestration/state_machine.py) | Add `STATUS_ESCALATED`, `STATUS_STALE_SNAPSHOT`; rename `STATUS_SUPERSEDED`; add `ESCALATED` + `STALE_SNAPSHOT` to `TERMINAL_STATES`; update `TRANSITION_GRAPH` to match v3.0 table |
| MODIFY | [`backend/database/models.py`](file:///c:/Users/gungu/PRSmith---Autonomous-Pull-Request-Agent-/backend/database/models.py) | Add `completion_reason` to `Job`; add `idempotency_key` (nullable UNIQUE) to `Job`; change `TaskExecutionAttempt.status` default `"CLAIMED"` → `"PENDING"`; add `repository_id`, `pr_number`, `is_current`, `last_attempt_at`, `publication_attempts`, `max_publication_attempts` to `PublishedReview`; add partial unique indexes |
| MODIFY | [`worker/tasks/review.py`](file:///c:/Users/gungu/PRSmith---Autonomous-Pull-Request-Agent-/worker/tasks/review.py) | Replace all direct `job.status = "..."` with `JobMutationService.transition()`; fix status string constants to match `state_machine.py`; add `assert_head_sha_current()` checkpoints; fix `reviewer.review_pr()` kwarg names |
| MODIFY | [`backend/review/exceptions.py`](file:///c:/Users/gungu/PRSmith---Autonomous-Pull-Request-Agent-/backend/review/exceptions.py) | Add `PublicationRetryableError`, `PublicationPermanentError` |

### Backend — Phase B (Snapshot Immutability)

| Action | File | Change |
|---|---|---|
| VERIFY | [`backend/review/snapshot_service.py`](file:///c:/Users/gungu/PRSmith---Autonomous-Pull-Request-Agent-/backend/review/snapshot_service.py) | No changes needed — `assert_head_sha_current()` and `mark_superseded()` already correct |
| MODIFY | Worker tasks | Add three mandatory `assert_head_sha_current()` call sites (documented above) |

### Backend — Phase C (Task Attempt Recovery)

| Action | File | Change |
|---|---|---|
| NEW | `backend/orchestration/attempt_recovery.py` | `recover_stale_task_attempts()`: scans `TaskExecutionAttempt` rows with `status=CLAIMED` and `heartbeat_at` older than threshold → set `ABANDONED`; create new `PENDING` attempt for parent `TaskExecution` |

### Backend — Phase D (Sanitized Projections)

| Action | File | Change |
|---|---|---|
| NEW | [`backend/retrieval/sanitized_projections.py`](file:///c:/Users/gungu/PRSmith---Autonomous-Pull-Request-Agent-/backend/retrieval/sanitized_projections.py) | `SanitizedProjection`: `read_file_sanitized()`, `read_lines_sanitized()`, `sanitize_diff()`, `scan_file_path()`, `scan_symbol_name()` |
| MODIFY | [`backend/retrieval/hybrid_retriever.py`](file:///c:/Users/gungu/PRSmith---Autonomous-Pull-Request-Agent-/backend/retrieval/hybrid_retriever.py) | Replace raw `open()` calls with `SanitizedProjection.read_lines_sanitized()` |
| MODIFY | `backend/graph/builder.py` | Sanitize code snippets before storing in `GraphNode.properties` |
| MODIFY | `backend/observability/logging.py` | Add `redact_log_field` structlog processor for sensitive log key names |

### Backend — Phase E (Hybrid Retrieval Wiring)

| Action | File | Change |
|---|---|---|
| MODIFY | [`worker/tasks/review.py`](file:///c:/Users/gungu/PRSmith---Autonomous-Pull-Request-Agent-/worker/tasks/review.py) | Call `HybridRetriever.retrieve_context()` before `reviewer.review_pr()`; pass `context_items=` kwarg |

### Backend — Phase F (Repair Service)

| Action | File | Change |
|---|---|---|
| NEW | [`backend/repair/service.py`](file:///c:/Users/gungu/PRSmith---Autonomous-Pull-Request-Agent-/backend/repair/service.py) | `RepairService.execute_finding_repair()` — domain logic extracted from `review.py` |
| MODIFY | [`worker/tasks/repair.py`](file:///c:/Users/gungu/PRSmith---Autonomous-Pull-Request-Agent-/worker/tasks/repair.py) | Replace stub with Celery task calling `RepairService.execute_finding_repair()` |
| MODIFY | [`worker/tasks/validation.py`](file:///c:/Users/gungu/PRSmith---Autonomous-Pull-Request-Agent-/worker/tasks/validation.py) | Replace stub with standalone 7-layer validation Celery task |
| MODIFY | [`worker/tasks/review.py`](file:///c:/Users/gungu/PRSmith---Autonomous-Pull-Request-Agent-/worker/tasks/review.py) | Remove `async_repair_single_finding`; delegate to `RepairService` |

### Backend — Phase G (Publication State Machine + Recovery)

| Action | File | Change |
|---|---|---|
| MODIFY | [`backend/review/publisher.py`](file:///c:/Users/gungu/PRSmith---Autonomous-Pull-Request-Agent-/backend/review/publisher.py) | Add `recover_stale_posting_claims()`; add `find_and_update_or_post()` (Option B: PATCH existing, else POST); add GitHub error taxonomy → `PublicationRetryableError` vs `PublicationPermanentError`; set `is_current=True` atomically on POSTED; add `repository_id`, `pr_number` to `claim_publication()` |
| MODIFY | [`backend/orchestration/idempotency.py`](file:///c:/Users/gungu/PRSmith---Autonomous-Pull-Request-Agent-/backend/orchestration/idempotency.py) | Add `make_publication_pr_key()` (stable, for GitHub marker dedup) and `make_publication_lineage_key()` |
| MODIFY | [`backend/auth/github_client.py`](file:///c:/Users/gungu/PRSmith---Autonomous-Pull-Request-Agent-/backend/auth/github_client.py) | Add `post_review_comment_with_token()` (token-agnostic); add `edit_review_comment_with_token()` (PATCH); add `find_comment_by_marker()` |
| MODIFY | [`worker/tasks/review.py`](file:///c:/Users/gungu/PRSmith---Autonomous-Pull-Request-Agent-/worker/tasks/review.py) | Use credential precedence chain (App → OAuth → PAT) for publication; add scope check |

### Backend — Phase H (API)

| Action | File | Change |
|---|---|---|
| NEW | [`backend/api/findings.py`](file:///c:/Users/gungu/PRSmith---Autonomous-Pull-Request-Agent-/backend/api/findings.py) | `GET /api/findings` — cursor-paginated, tenant-authorized, filterable |
| MODIFY | [`backend/api/jobs.py`](file:///c:/Users/gungu/PRSmith---Autonomous-Pull-Request-Agent-/backend/api/jobs.py) | Add `repo_id` filter; add `repair_summary`, `severity_counts`, `findings_count`, `current_publication`, `completion_reason` to responses; add `POST /{id}/publish`; add `GET /{id}/findings` |
| MODIFY | [`backend/api/repositories.py`](file:///c:/Users/gungu/PRSmith---Autonomous-Pull-Request-Agent-/backend/api/repositories.py) | Add `GET /{id}/jobs`; add `GET /{id}/stats`; add dispatch idempotency |
| MODIFY | [`backend/app.py`](file:///c:/Users/gungu/PRSmith---Autonomous-Pull-Request-Agent-/backend/app.py) | Register `findings` router |

### Frontend — Phase I

| Action | File | Change |
|---|---|---|
| MODIFY | `frontend/src/api/client.ts` | Add typed functions for new endpoints; update `JobDetailData` with `repair_summary`, `severity_counts`, `current_publication`, `completion_reason`, `superseded_by_job_id` |
| MODIFY | `frontend/src/pages/Dashboard.tsx` | Risk distribution bar; `findings_count` column; polling termination; View Repo link |
| MODIFY | `frontend/src/pages/JobDetail.tsx` | `repair_summary` panel; Publish button; STALE_SNAPSHOT banner; ESCALATED banner; severity breakdown; polling termination with terminal states |
| MODIFY | `frontend/src/pages/RepositoryHub.tsx` | Recent Jobs panel; dispatch idempotency feedback |
| NEW | `frontend/src/pages/FindingsExplorer.tsx` | Cursor-paginated findings, filters, repair button — at `/findings` |
| NEW | `frontend/src/components/RepairTimeline.tsx` | Per-iteration repair timeline |
| NEW | `frontend/src/components/RiskBar.tsx` | Horizontal stacked risk distribution bar |
| MODIFY | `frontend/src/App.tsx` | Add `/findings` route + nav item |

### Tests — Phase J

| Action | File |
|---|---|
| NEW | `tests/e2e/test_publication_crash_windows.py` |
| NEW | `tests/e2e/test_stale_worker_superseded_race.py` |
| NEW | `tests/e2e/test_task_attempt_recovery.py` |
| NEW | `tests/integration/test_retrieval_before_generation.py` |
| NEW | `tests/unit/test_sanitized_projections.py` |
| NEW | `tests/unit/test_publication_concurrency.py` |
| NEW | `tests/unit/test_state_machine_v3.py` |

### Documentation — Phase K

| Action | File |
|---|---|
| MODIFY | `MEMORY.md` | Update guarantee language, state machine table, sanitization boundary, publication semantics |
| MODIFY | `README.md` | Update architecture diagram, safety model |
| MODIFY | `PROJECT_DECISIONS.md` | Log all decisions from this plan (3 review rounds) |

---

## Alembic Migration Sequence

All `models.py` changes require a migration. In order:

```
1. Add completion_reason, idempotency_key to jobs
2. Change TaskExecutionAttempt.status default to 'PENDING'
3. Add repository_id, pr_number, is_current, last_attempt_at, 
   publication_attempts, max_publication_attempts to published_reviews
4. Add partial unique indexes (uq_published_review_active_pr, 
   uq_published_review_current) — PostgreSQL only
5. Rename SUPERSEDED → STALE_SNAPSHOT in state_machine constants
   (DB column values: UPDATE jobs SET status='STALE_SNAPSHOT' 
    WHERE status='SUPERSEDED')
```

SQLite supports partial indexes (`CREATE INDEX ... WHERE`) from version 3.8.9 (2015). Verify the project's minimum SQLite version and use the same partial-index DDL in tests as in production. Do not implement an application-only fallback — the constraint must be enforced at the DB level in both environments.

---

## Verification Plan

```bash
# Full regression — must pass ≥136 existing tests
pytest tests/ -v

# New tests
pytest tests/e2e/test_publication_crash_windows.py -v
pytest tests/e2e/test_stale_worker_superseded_race.py -v
pytest tests/e2e/test_task_attempt_recovery.py -v
pytest tests/integration/test_retrieval_before_generation.py -v
pytest tests/unit/test_sanitized_projections.py -v
pytest tests/unit/test_publication_concurrency.py -v
pytest tests/unit/test_state_machine_v3.py -v

# Frontend — zero TypeScript errors
cd frontend && npm run build
```

**Manual integration checklist** (after Phases A–H):
1. Dispatch review → job progresses `CLONING → ANALYZING → REVIEWING → PUBLISHING → COMPLETED` using `JobMutationService` at every transition
2. Post while worker is reviewing → confirm `mark_superseded()` advisory lock fires, old job → `STALE_SNAPSHOT`, new job created
3. Two workers race on publication → only one `CLAIMED` row in `published_reviews` (partial index enforces)
4. Simulate stale POSTING → recovery worker reconciles → no duplicate comment
5. Trigger repair via UI → `RepairService` called, patch appears in Patches panel
6. OAuth repo (no App installation) → publication uses user OAuth token, scope checked
7. `FindingsExplorer` at `/findings` → cursor pagination works, filter by severity

---

## Open Items (None Blocking Implementation)

> [!NOTE]
> **SQLite partial indexes**: The partial unique indexes for `PublishedReview` use PostgreSQL-specific syntax. In SQLite (used for local dev and the test suite), these are not enforced at DB level. Application-level enforcement (claim_publication's IntegrityError handling) remains the guard. Document this explicitly in migration comments.

> [!NOTE]
> **`find_comment_by_marker()` on GitHub**: Requires listing PR comments and scanning bodies. For large PRs with many comments this may be slow. Add a cache: after first reconciliation, store `github_comment_id` in `PublishedReview`. Subsequent reconciliations check the specific comment ID directly.
