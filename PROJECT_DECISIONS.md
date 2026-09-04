# PRSmith — Complete Project Decision Log

> A full chronological record of every architectural decision, problem encountered, solution chosen, and alternative rejected throughout the PRSmith project. Written so any developer — including future-you — can understand *why* the system is built the way it is.

---

## Table of Contents

1. [Milestone 1 — Core Engine](#milestone-1--core-autonomous-review--repair-engine)
2. [Milestone 2 — Distributed Execution & Dashboard](#milestone-2--distributed-execution--frontend-dashboard)
3. [Milestone 3 — Multi-Tenant GitHub OAuth](#milestone-3--multi-tenant-github-oauth-20)
4. [Milestone 4 — Hardening Pass (P0/P1 Systems Reliability, Concurrency & Security)](#milestone-4--hardening-pass-p0p1-systems-reliability-concurrency--security)
5. [Milestone 5 — Production Architecture & Concurrency Hardening (v3.0 Decisions across 3 Review Rounds)](#milestone-5--production-architecture--concurrency-hardening-v30-decisions-across-3-review-rounds)
6. [Environment & Tooling Decisions](#environment--tooling-decisions)
7. [Rejected Alternatives Log](#rejected-alternatives-log)

---

## Milestone 1 — Core Autonomous Review & Repair Engine

### Decision 1.1 — Retrieval-Augmented Generation (RAG) over pure LLM reasoning

**Problem:** LLMs hallucinate when asked to review code they haven't seen. A model can confidently claim a function is called from three places when it is called from zero.

**Solution:** Enforce a two-step pipeline — first retrieve all relevant code context, then pass only grounded evidence to the LLM. No LLM call is made without a corresponding retrieval step.

**Consequence:** Every reviewer finding must cite a `GraphNode` or `CodeEmbedding` that actually exists in the database. The `evidence.py` module performs this validation and blocks findings that cannot be grounded.

---

### Decision 1.2 — Deterministic AST Knowledge Graph over LLM-generated summaries

**Problem:** We need to know what a codebase looks like structurally — what calls what, what tests cover what functions, what imports depend on what modules. LLM-generated summaries of code structure are non-deterministic and cannot be relied upon for graph traversal.

**Solution:** Use Python's built-in `ast` module to perform a deterministic static analysis pass over every Python file in the repository. Extract:
- Module-level and class-level function definitions.
- Import relationships between modules.
- Call relationships between functions.
- Test file→function coverage mappings (using test naming conventions).

**Consequence:** The `python_analyzer.py` module produces a `GraphVersion` with `GraphNode` and `GraphEdge` rows in PostgreSQL for every repository build. This is the ground truth used for all retrieval and evidence validation.

**Alternative Rejected:** Tree-sitter or Semgrep for parsing. Both are more powerful but add binary dependencies that complicate the sandbox and Docker build. `ast` is part of the Python standard library, zero external dependencies.

---

### Decision 1.3 — Hybrid Retrieval: structural graph + semantic embeddings

**Problem:** Graph traversal alone misses semantically related code that isn't structurally linked (e.g., a utility function in a different module that does the same thing). Vector embeddings alone lose structural precision (e.g., not knowing that `foo()` is directly called by `bar()`).

**Solution:** Implement a two-channel hybrid retriever:
1. **Graph Retriever (`graph_retriever.py`):** BFS traversal of the knowledge graph, gathering callers, callees, and test nodes up to a configurable depth.
2. **Semantic Retriever (`semantic_retriever.py`):** OpenAI `text-embedding-3-small` embeddings stored in pgvector, cosine similarity search.

Both channels feed results into the **11-priority Ranker** (`ranker.py`), which packs the highest-priority context into the LLM's token budget.

**Ranker Priority Levels:**
- P1: The directly changed function/class from the diff.
- P2: Direct callers of the changed function.
- P3: Direct callees.
- P4: Tests that cover the changed function.
- P5: Sibling functions in the same class/module.
- P6-P11: Semantic neighbours, transitive callers, import dependants...

**Consequence:** Context quality is predictable, reproducible, and bounded.

---

### Decision 1.4 — Read-only Reviewer with structured output schema

**Problem:** If the Reviewer agent can write code, it will. LLMs drift toward action; we want pure analysis.

**Solution:** The `reviewer.py` module passes only the diff plus retrieved context to the LLM. The system prompt explicitly prohibits code generation. The output is parsed against a strict Pydantic schema (`ReviewFinding`) that has no field for "suggested fix". Structured output parsing is enforced via OpenAI's JSON mode.

**Consequence:** Every finding has: `finding_id`, `severity`, `category`, `title`, `description`, `evidence` (list of `GraphNode` references), and `rationale`. No free-form code or patch suggestions leak from the review phase.

---

### Decision 1.5 — Repair Agent with surgical patch scope policy

**Problem:** LLMs, when asked to fix a finding, tend to refactor surrounding code, rename variables, add imports, and make unrelated improvements. This violates the principle of least modification and makes patches hard to review.

**Solution:** The `patch_policy.py` module enforces pre-application scope constraints before any patch is applied:
- The patch must only touch files that were part of the original PR diff.
- It must not change function signatures in a way that would break callers (checked via AST before/after diff).
- It must not add or remove import statements outside the directly patched module.
- It must not exceed a configurable maximum line change delta.

**Consequence:** Even if the LLM produces a wide patch, `patch_policy.py` rejects it and forces a narrower re-generation.

---

### Decision 1.6 — Isolated git worktree per repair iteration

**Problem:** Repair iterations must be rolled back cleanly if validation fails. Mutating the main working tree risks leaving it in a broken state.

**Solution:** The `worktree.py` module creates an isolated `git worktree` for each repair attempt. If validation fails, the worktree is deleted. If it passes, the worktree's commit is cherry-picked or squash-merged.

**Consequence:** The main repository clone is never in a broken intermediate state. Failed repair iterations are invisible.

---

### Decision 1.7 — 7-layer validation plan before accepting any patch

**Problem:** "The tests pass" is not good enough. A patch might pass all tests while introducing a new import cycle, breaking type annotations, or regressing performance benchmarks.

**Solution:** Implement a 7-layer waterfall validation plan in `runner.py`:

| Layer | Check | Tool |
|---|---|---|
| 1 | Syntax validity | `python -m ast` / `py_compile` |
| 2 | Lint conformance | `flake8` / `ruff` |
| 3 | Type annotation correctness | `mypy` |
| 4 | Graph-guided targeted tests | `pytest` (selected by `test_selector.py`) |
| 5 | Full test baseline (zero new regressions) | `pytest` (differential comparison) |
| 6 | Import cycle detection | Custom AST traversal |
| 7 | Security scan | Secret scanner (`secrets.py`) |

Each layer runs in an isolated Docker sandbox (`network=none`, non-root user, CPU/memory limits).

**Consequence:** A patch only reaches PUBLISHED status after passing all 7 layers. If any layer fails, `classifiers.py` categorises the failure (code bug vs environment/OOM) before deciding whether to retry or escalate.

---

### Decision 1.8 — Differential validation (zero new regressions guarantee)

**Problem:** If the codebase already had 3 failing tests before the PR, the repair agent should not be penalised for those. But it must not introduce any *new* failures.

**Solution:** `baseline.py` captures the full test result baseline (pass/fail per test) *before* the repair is applied. `differential.py` compares post-patch results against this baseline. A patch is rejected only if a test that *previously passed* now *fails*.

**Consequence:** PRSmith can make progress on codebases with pre-existing test debt without being blocked by unrelated failures.

---

### Decision 1.9 — Docker sandbox with subprocess fallback

**Problem:** Running arbitrary code from a PR is a remote code execution risk. The sandbox must be hardened.

**Solution:** All validation runs inside Docker containers configured by `policy.py`:
- Non-root user.
- `network=none` (no exfiltration).
- Read-only filesystem (except a single tmpfs for output).
- CPU and memory resource limits.
- 30-second execution timeout.

**Fallback:** If Docker is unavailable (e.g., CI environment without Docker-in-Docker), `docker.py` falls back to a subprocess with a timeout. The fallback is explicitly logged as a security downgrade.

---

## Milestone 2 — Distributed Execution & Frontend Dashboard

### Decision 2.1 — Celery + Redis for asynchronous task execution

**Problem:** Graph builds on large repositories can take 5+ minutes. PR review pipelines can take 10+ minutes with repair loops. Blocking FastAPI request handlers for this duration would exhaust the server's thread pool.

**Solution:** Celery workers pull jobs from Redis queues. FastAPI immediately returns a `job_id` and the work happens asynchronously. The frontend polls job status.

**Four named queues:**
- `graph_build`: Repository indexing tasks.
- `review`: Full PR review pipeline (review + repair + validation + publish).
- `repair`: Individual repair iteration subtasks.
- `validation`: Validation subtasks (can be parallelised per layer).

**Alternative Rejected:** FastAPI `BackgroundTasks`. Too lightweight — no retry logic, no distributed workers, no queue inspection.

---

### Decision 2.2 — React 19 + TypeScript + Vite frontend (glassmorphism dark mode)

**Problem:** The dashboard needs to surface complex multi-step job states, graph evidence, unified diffs, and validation layers in a readable way. A plain HTML table is not sufficient.

**Solution:** Build a React 19 SPA with TypeScript and Vite:
- Dark mode glassmorphism design system (`index.css` with CSS custom properties).
- `RepositoryHub.tsx`: Tracked repositories, discovery modal, monitoring/repair toggles.
- `JobDetail.tsx`: Full state machine timeline, findings list, patch diffs, validation reports.
- `RepositoryGraph.tsx`: Interactive knowledge graph browser.
- All components typed with TypeScript interfaces mirroring the FastAPI response schemas.

**Alternative Rejected:** Next.js SSR. Adds server-side complexity that is unnecessary for an internal dashboard. Vite + SPA is simpler to deploy alongside the FastAPI backend.

---

### Decision 2.3 — GitHub App for webhook ingestion and PR comments

**Problem:** Posting automated review comments on PRs and receiving webhook events requires a GitHub identity with repo access. Personal access tokens are tied to individual users and cannot be shared across teams.

**Solution:** Register a GitHub App with:
- Webhook subscriptions to `pull_request.opened` and `pull_request.synchronize` events.
- Permissions: `pull_requests: write`, `contents: read`, `checks: write`.
- RS256 JWT authentication using the App's private key for API calls.
- Installation tokens scoped per repository installation.

**Consequence:** The App identity is separate from any user identity. It can post to PRs it has been installed on regardless of which user triggered the review.

---

### Decision 2.4 — HMAC SHA-256 webhook signature verification with delivery deduplication

**Problem:** GitHub webhook endpoints receive unauthenticated HTTP requests from the public internet. A malicious actor could POST fake PR events to trigger unauthorized reviews.

**Solution:** Every inbound webhook is verified using HMAC SHA-256 against the `X-Hub-Signature-256` header using the webhook secret configured on the GitHub App. Verification happens before any database write.

**Deduplication:** Every delivery carries a unique `X-GitHub-Delivery` header. This ID is stored in the `webhook_deliveries` table. Duplicate deliveries (GitHub retries on 5xx) are silently dropped with 200 OK (to prevent GitHub from retrying indefinitely).

---

## Milestone 3 — Multi-Tenant GitHub OAuth 2.0

This milestone went through six iterative design review rounds before implementation. Every round identified contradictions and hardening gaps that were corrected before writing code.

### Design Review Rounds

#### Round 1 (v1 Plan) — Initial OAuth Proposal
Initial proposal: add GitHub OAuth for dashboard login. Basic flow: redirect → callback → JWT cookie. No database model changes.

**Problems identified:**
- No encryption of stored OAuth tokens at rest.
- No CSRF state protection on the OAuth redirect.
- Existing `Repository` model had `monitoring_enabled`, `auto_repair_enabled`, `watched_branches` — would conflict with multi-user access to the same repo.

---

#### Round 2 (v2 Plan) — Multi-User Model Added
Proposed `user_repositories` join table. Proposed `repositories.github_id UNIQUE`.

**Problem identified:**
- **Fatal contradiction:** `repositories.github_id UNIQUE` simultaneously claimed to be unique *and* allowed multiple users to track the same repository. These two requirements cannot coexist as written.
- Two valid solutions presented:
  1. If a GitHub repo belongs to exactly one PRSmith tenant → keep `github_id UNIQUE` on `repositories`.
  2. If multiple PRSmith users can independently track the same GitHub repo → canonical `repositories` row (with `github_id UNIQUE`) plus per-user `user_repositories` rows.

**Decision:** Option 2. PRSmith is a multi-tenant SaaS where any user can track any repository they have access to. A shared canonical `Repository` row + separate `UserRepository` association is the correct normalisation.

---

#### Round 3 (v3 Plan) — Repository Config Field Contradiction
v3 said both: "remove `monitoring_enabled`, `auto_repair_enabled`, `watched_branches` from `Repository`" **and** "keep them as repo-level defaults."

**Problem:** These are contradictory statements in the same plan.

**Decision:** Remove all three fields from `Repository` entirely. There is no meaningful repository-level value for these fields when multiple users with different preferences track the same repo. `UserRepository` is the sole source of truth for per-user configuration.

**Why no "defaults"?** Adding defaults back would re-introduce the same dual-source drift problem. Any code reading `repo.monitoring_enabled` as a fallback would produce unpredictable results when user A wants `True` and user B wants `False`. The solution is to require explicit `UserRepository` records and enforce 403 if none exists.

---

#### Round 4 (v4 Plan) — OAuth Scope Breadth

**Problem:** The v4 plan described `repo read:user user:email read:org` as "minimum permissions." This is incorrect — GitHub's `repo` scope grants **full read/write access to all public and private repositories**, including code, settings, and hooks.

**Decision:** Reframe scope documentation honestly:
- `read:user` — Required for profile identity.
- `user:email` — Required for email lookup.
- `read:org` — Required for organization membership discovery.
- `repo` — Required **only** if OAuth tokens are used for private repository clone/fetch operations.

Open question recorded: If all repository operations can be performed exclusively through GitHub App installation tokens, `repo` scope can be dropped from OAuth. Review when implementing repository cloning.

---

#### Round 5 (v5 Plan) — Five Remaining Issues

1. **`registered_by_user_id` was doing too much.** A field called "registered by" conflated (a) audit trail and (b) credential resolution. These are different concerns. Renamed to `triggered_by_user_id` on `Job` only. Credential resolution reads from `triggered_by_user_id`, not from a vague "who first registered this repo" field.

2. **Non-deterministic `_get_first_user_repository()` fallback.** The original plan had a fallback to grab the "first" user who registered a repository to resolve credentials for webhook jobs. "First" is non-deterministic — it depends on insertion order. Webhook jobs are user-agnostic; they use the GitHub App installation token. Removed `_get_first_user_repository()` entirely.

3. **`MutableList.as_mutable(JSON)` required for `watched_branches`.** SQLAlchemy does not detect in-place mutations of plain JSON columns (e.g., `repo.watched_branches.append("main")`). The ORM will not mark the column dirty, and the change will not be persisted. Solution: `from sqlalchemy.ext.mutable import MutableList` and declare `watched_branches = mapped_column(MutableList.as_mutable(JSON), default=list)`.

4. **`AuthCallback.tsx` frontend page is unnecessary.** The backend already completes the full OAuth handshake — it exchanges the code, upserts the user, sets the `prsmith_session` httpOnly cookie, and issues a 302 redirect to `/repos`. No frontend page is needed to "complete" the OAuth callback because the browser is already redirected before any JavaScript runs. Removed `AuthCallback.tsx` from the plan.

5. **`COOKIE_SECURE` must be environment-driven.** Hard-coding `Secure=True` on the session cookie breaks localhost development (browsers silently reject Secure cookies on HTTP). Added `COOKIE_SECURE` property to Settings that returns `True` in production/staging and `False` in development.

---

#### Round 6 (v6 Plan) — Hardening Checklist

Final checklist confirmed before implementation:

| Item | Decision |
|---|---|
| `GITHUB_TOKEN_ENCRYPTION_KEY` validation | Fail at application startup (`lifespan`), not on first request |
| OAuth scopes | Configurable via `GITHUB_OAUTH_SCOPES` env var, not hard-coded |
| Token refresh concurrency | `SELECT FOR UPDATE` on `github_connections` row; skip on SQLite (dialect check) |
| Per-user repo configuration source | `UserRepository` only — no repo-level fallback defaults |
| Credential resolution for jobs | `triggered_by_user_id` FK on `Job`; NULL for webhook-triggered jobs |
| Webhook jobs | Always use App installation token; `triggered_by_user_id = NULL` |
| GitHub App vs OAuth | Kept as separate identity planes; App tokens never mixed with OAuth sessions |
| PAT fallback | Disabled by default (`ENABLE_PAT_FALLBACK = False`); gated by env var |
| Tenant authorization | All API routes enforce `UserRepository` membership; 403 if no row exists |
| Schema migrations | Alembic only; no `Base.metadata.create_all()` in production startup |

---

### Implementation Decisions (Milestone 3 Execution)

#### Decision 3.1 — Fernet symmetric encryption for OAuth tokens at rest

**Problem:** OAuth access tokens and refresh tokens stored in the database would be plaintext in the event of a database dump or SQL injection.

**Solution:** Python `cryptography` library's `Fernet` symmetric cipher. Fernet provides:
- AES-128-CBC encryption with PKCS7 padding.
- HMAC-SHA256 authentication (tamper-detection).
- Timestamped tokens (supports built-in expiry checking, though we use our own `access_token_expires_at` column).

**Key management:** `GITHUB_TOKEN_ENCRYPTION_KEY` in `.env`. Generated with `Fernet.generate_key()`. Must be a 32-byte URL-safe base64 string.

**Startup validation:** `validate_encryption_key()` is called in the FastAPI `lifespan` context manager. If the key is missing or invalid, the application refuses to start and logs a clear error. This prevents a misconfigured production deployment from silently storing tokens unencrypted.

---

#### Decision 3.2 — HS256 JWT in HttpOnly cookie (not Bearer header)

**Problem:** If the session token is stored in `localStorage` or sent as a `Bearer` header from JavaScript, it is vulnerable to XSS attacks — any injected script can read and exfiltrate it.

**Solution:** Session JWT stored exclusively in an `HttpOnly; SameSite=Lax; Path=/` cookie. This means:
- JavaScript cannot access the cookie value (XSS cannot steal it).
- The browser automatically attaches the cookie to same-origin requests.
- Frontend sets `withCredentials: true` on Axios to include the cookie in API calls.

**Why HS256 and not RS256?** RS256 (asymmetric) is appropriate when multiple services need to independently verify tokens without sharing a secret. PRSmith is a monorepo where the FastAPI backend is the only token issuer and verifier. HS256 is simpler and sufficient.

**Why not a session store (Redis/database)?** Stateless JWT allows horizontal scaling without sticky sessions. If revocation is needed in the future, a `token_version` field on `User` can be added and checked during verification.

---

#### Decision 3.3 — CSRF state parameter as HttpOnly cookie (double-submit cookie pattern)

**Problem:** The OAuth redirect includes a `state` parameter. Without CSRF protection, an attacker can craft a link that initiates OAuth login and captures the user's tokens through a redirect to the attacker's callback URL.

**Solution:** On `GET /api/auth/github/login`:
1. Generate a cryptographically random state: `secrets.token_urlsafe(32)`.
2. Set it in a short-lived `HttpOnly; SameSite=Lax` cookie: `prsmith_oauth_state`.
3. Include the same value as the `state` query parameter in the GitHub OAuth redirect URL.

On `GET /api/auth/github/callback`:
1. Read `state` from query params.
2. Read expected state from the `prsmith_oauth_state` cookie.
3. Reject with 400 if either is missing or they do not match.
4. Delete the `prsmith_oauth_state` cookie immediately after validation.

**Why HttpOnly for the state cookie?** Prevents JavaScript from reading or forging it, even if XSS is present.

---

#### Decision 3.4 — Upsert semantics for User and GitHubConnection on OAuth callback

**Problem:** A returning user authenticates again (or switches scopes). Should we create a new user record, raise a conflict, or update the existing one?

**Solution:** Upsert with `ON CONFLICT DO UPDATE`:
- `User`: Match on `github_id`. Update `username`, `email`, `avatar_url`, and `updated_at` if they changed (e.g., user changed their GitHub display name).
- `GitHubConnection`: Match on `user_id`. Replace `access_token_encrypted`, `refresh_token_encrypted`, `access_token_expires_at`, `scopes` with the freshly-issued values.

**Consequence:** OAuth re-authentication is idempotent. The same user logging in twice ends up with exactly one `User` row and one `GitHubConnection` row, both up to date.

---

#### Decision 3.5 — `SELECT FOR UPDATE` on token refresh (with SQLite dialect bypass)

**Problem:** If two parallel requests both find the access token expired and both attempt to refresh it using the refresh token, GitHub will accept the first refresh and invalidate the refresh token. The second request will fail with an invalid grant error, logging the user out.

**Solution:** Wrap the refresh operation in `SELECT FOR UPDATE` on the `github_connections` row:
1. Lock the row.
2. Re-check token expiry after acquiring the lock (another request may have already refreshed it).
3. If still expired, perform the refresh call.
4. Update and commit.
5. Release the lock.

**SQLite Bypass:** SQLite does not support `SELECT FOR UPDATE` and raises an `OperationalError`. The credential provider checks `session.bind.dialect.name != "sqlite"` before applying `with_for_update()`. This makes unit tests against SQLite safe while production PostgreSQL gets the full protection.

---

#### Decision 3.6 — Tenant authorization via `UserRepository` membership on every route

**Problem:** Without explicit authorization checks, User B who knows User A's `repository_id` (a UUID, guessable by brute force or exposure in shared links) can call `PATCH /api/repositories/{id}/monitoring` to tamper with User A's settings.

**Solution:** Every state-mutating and state-reading endpoint in `repositories.py` calls `_get_user_repo_or_403(session, repo_id, current_user.id)` before doing anything:

```python
async def _get_user_repo_or_403(session, repo_id, user_id) -> UserRepository:
    ur = await session.execute(
        select(UserRepository)
        .where(UserRepository.repository_id == repo_id)
        .where(UserRepository.user_id == user_id)
    )
    user_repo = ur.scalars().first()
    if not user_repo:
        raise HTTPException(status_code=403, detail="Forbidden")
    return user_repo
```

The `Repository` row is only consulted after the `UserRepository` authorization check passes. This prevents even 404 leakage — an unauthorized user gets 403, not 404 (which would confirm the resource exists).

---

#### Decision 3.7 — Unregister deletes only `UserRepository`, not canonical `Repository`

**Problem:** If User A and User B both track `org/shared-repo`, and User A clicks "Remove Repository," should the canonical `repositories` row be deleted?

**Solution:** No. `unregister_repository` deletes only the `UserRepository` row (the per-user tracking record). The canonical `Repository` row is preserved as long as at least one other user still tracks it, and deleted only when the last `UserRepository` row for that canonical repo is removed.

**Consequence:** User B's tracking of `org/shared-repo` is completely unaffected by User A removing it from their dashboard. Validated by `test_shared_repository_independent_configuration` integration test.

---

#### Decision 3.8 — Frontend: No AuthCallback page, session resolved before redirect

**Problem:** Many OAuth implementations redirect to a frontend callback page (e.g., `/auth/callback`) where JavaScript reads the code from the URL and calls the backend. This exposes the authorization code in the browser's URL bar and history.

**Solution:** The backend handles the entire OAuth callback:
1. `GET /api/auth/github/callback` runs on the backend.
2. Backend exchanges code for tokens.
3. Backend upserts user and connection records.
4. Backend sets `prsmith_session` HttpOnly cookie in the response.
5. Backend issues `302 Redirect` to `${FRONTEND_URL}/repos`.
6. Browser loads `/repos` (React SPA), which calls `GET /api/auth/me` via `AuthProvider.refreshUser()`.

**Consequence:** The authorization code is never exposed to JavaScript. The redirect URL never contains a token. `AuthCallback.tsx` was explicitly removed from the implementation.

---

#### Decision 3.9 — GitHub SVG Icon component instead of lucide-react `Github`

**Problem:** The version of `lucide-react` installed in the project did not export a `Github` icon. The TypeScript compiler raised `error TS2305: Module 'lucide-react' has no exported member 'Github'`.

**Solution:** Created `frontend/src/components/GitHubIcon.tsx` — a purpose-built SVG component using GitHub's official Invertocat mark from their brand guidelines. This eliminates the version dependency entirely and allows full control over sizing and colour.

---

#### Decision 3.10 — Alembic initialized fresh (no prior migration history)

**Problem:** The project had no Alembic setup prior to Milestone 3. All table creation was likely happening via `Base.metadata.create_all()` calls, which cannot handle ALTER TABLE operations as the schema evolves.

**Solution:**
1. Initialized Alembic: `python -m alembic init alembic`.
2. Configured `alembic/env.py` to import `Base.metadata` from `backend.database.models`.
3. Added `SYNC_DATABASE_URL` to `Settings` as Alembic requires a synchronous database URL (SQLAlchemy's async engine cannot be used in `env.py` without additional adapter code).
4. Generated first autogenerate revision covering all existing + new tables: `alembic revision --autogenerate -m "add_github_oauth_multi_tenant"`.
5. Applied migration: `alembic upgrade head` (verified clean apply).

**Consequence:** All future schema changes must go through Alembic. `Base.metadata.create_all()` is no longer acceptable in production startup paths.

---

## Milestone 4 — Hardening Pass (P0/P1 Systems Reliability, Concurrency & Security)

### Decision 4.1 — Transactional Outbox Pattern & Two-Level TaskExecution Attempt Lifecycle

**Problem:** Webhook ingestion directly enqueuing to Celery can lose events if Celery/Redis crashes before acknowledgment, or can cause duplicate runs if Celery enqueues succeed but the DB commit fails. Furthermore, naive Celery idempotency based on `ON CONFLICT DO NOTHING` on a single task table permanently stalls jobs if a worker dies mid-execution.

**Solution:**
1. Ingest webhooks via atomic `INSERT INTO webhook_deliveries ... ON CONFLICT (delivery_id) DO NOTHING RETURNING id`. In the exact same database transaction, write a pending event to `outbox_events`.
2. A resilient Outbox Dispatcher polls `outbox_events` (`status='PENDING'`), marks them `DISPATCHED`, and publishes to Celery. It includes exponential backoff for transient failures and automatically recovers stale `DISPATCHED` events whose workers crashed.
3. Workers use a two-level execution model: `TaskExecution` (one per logical idempotency key) and `TaskExecutionAttempt` (one per worker run). Attempts track worker heartbeats. If a worker dies, a subsequent worker marks the expired attempt `ABANDONED` and claims a new attempt under the *same* `TaskExecution` identity.

**Consequence:** End-to-end at-least-once delivery with at-most-one active logical owner and idempotent/reconcilable execution semantics, guaranteed crash recovery, and zero lost webhooks (explicitly adhering to the frozen invariant: never claiming "exactly-once execution").

---

### Decision 4.2 — Fail-Closed Sandbox Boundary with Zero Subprocess Fallback in Production

**Problem:** Allowing local subprocess execution as a fallback when Docker is unavailable exposes the host machine to arbitrary remote code execution (RCE) via untrusted PR code, malicious setup scripts, or poisoned test harnesses.

**Solution:**
1. In production (`ENVIRONMENT != "development"`), `DockerBackend` strictly enforces containerized execution with hardened security flags: `network="none"`, `cap_drop=["ALL"]`, `pids_limit=100`, `mem_limit="2g"`, `nano_cpus=2000000000`, `read_only=True`, and non-root user (`user="1000:1000"`).
2. If Docker is unavailable or fails at runtime, PRSmith immediately raises `SandboxUnavailableError` and fails closed.
3. Local subprocess fallback is strictly gated behind an explicit `ALLOW_LOCAL_DEV_EXECUTION=True` flag, which is rejected at startup if `ENVIRONMENT="production"`.

**Consequence:** Untrusted PR code can never execute on the bare host in production environments.

---

### Decision 4.3 — Cryptographic Lineage Binding (`chain_hash`) & Canonical Serialization

**Problem:** Database timestamps, execution worker IDs, and line ending variations can cause artifact hashes to drift across retries, making review and patch provenance unverifiable and prone to duplicate publication.

**Solution:**
1. Implement `canonical_hash()` which normalizes nested dictionaries, strips transient database fields (`created_at`, `updated_at`, `worker_id`, `claimed_by`, etc.), normalizes line endings (`\r\n` to `\n`), and serializes with deterministic key ordering.
2. Compute an immutable `chain_hash` combining: `snapshot_sha`, `graph_content_hash`, `finding_content_hashes`, `patch_artifact_hash`, `validation_result_hash`, and `pipeline_version`.

**Consequence:** Any modification to code, AST graph, review findings, patches, or engine version yields a distinct hash. Lineage is mathematically auditable and tamper-proof.

---

### Decision 4.4 — Dual-Path Publication Prerequisites & GitHub Marker Reconciliation

**Problem:** Reviews without patches and reviews with patches have different completion criteria. A review-only run must not require a `patch_artifact_hash`. Additionally, concurrent workers could race to post duplicate review comments on GitHub.

**Solution:**
1. Create `assert_publication_prerequisites()` with two distinct paths:
   - **Review-Only Path:** Requires `ReviewRun.evidence_validation_passed=True` and matching `head_sha`.
   - **Repair Path:** Requires `ValidationRun.all_passed=True`, valid `patch_artifact_hash`, and matching `head_sha`.
2. Before posting to GitHub, workers atomically claim publication in the database (`INSERT INTO published_reviews ... ON CONFLICT DO NOTHING`).
3. Workers inspect existing PR comments for HTML metadata markers (`<!-- prsmith:published_review:{chain_hash} -->`). If the marker is present, the DB claim is updated to `POSTED` without posting a duplicate comment.

**Consequence:** Zero duplicate PR comments, deterministic idempotency, and clean separation of review vs repair requirements.

---

### Decision 4.5 — JWT Session Versioning, Double-Submit CSRF, and 4-Tier Auth Taxonomy

**Problem:** Stateless JWTs cannot be revoked upon logout without a distributed blocklist. Furthermore, cookie-based sessions are vulnerable to Cross-Site Request Forgery (CSRF). Credential failures were previously caught as generic exceptions.

**Solution:**
1. Add `User.session_version` integer column. The session JWT embeds `session_version`. On `POST /auth/logout`, `session_version` is atomically incremented, instantly invalidating all existing JWTs for that user across all devices.
2. Implement double-submit cookie CSRF: frontend receives an `HttpOnly=False` cookie (`prsmith_csrf_token`) and must send its value in the `X-CSRF-Token` header on state-changing requests.
3. Establish a 4-tier authentication exception taxonomy:
   - `AuthRequired`: Missing credentials or unauthenticated session.
   - `AuthRevoked`: Revoked or permanently expired tokens without refresh token.
   - `AuthInsufficientScope`: Token lacks required OAuth or App scopes.
   - `AuthTemporaryFailure`: Transient 5xx or network errors during token operations.

**Consequence:** O(1) instant session revocation, standard CSRF protection, and explicit credential failure classification.

---

### Decision 4.6 — Deterministic Concurrency via `JobMutationService` and Formal State Machine

**Problem:** Multiple asynchronous workers updating `jobs.status` without synchronization cause race conditions, illegal state transitions (e.g. `FAILED` -> `RUNNING`), and lost updates.

**Solution:**
1. Formalize the valid transition graph in `backend/orchestration/state_machine.py`.
2. Encapsulate all job status changes in `JobMutationService`, enforcing **Invariant 11**:
   - Status changes require `version = version + 1` optimistic concurrency checks (`UPDATE jobs WHERE id = :id AND version = :expected_version`).
   - If version mismatches, raises `StaleJobMutationError`.
   - Active execution requires an acquired worker lease with periodic heartbeats; stale leases (>300s) are reclaimed automatically.

**Consequence:** Linear, deterministic job state transitions without distributed locking deadlocks.

---

### Decision 4.7 — Categorized Denylist, Semantic YAML Classification, and AST Escalation

**Problem:** Naive denylists either block benign configuration changes (e.g. updating an application YAML config) or fail to block subtle CI attacks and dangerous runtime calls (e.g. `subprocess.Popen` in patched code).

**Solution:**
1. Segment denylists into explicit categories: `SECRETS`, `CI_WORKFLOWS`, `CONTAINER_INFRA`, `DEPENDENCIES`.
2. Implement `classify_yaml_path()` to inspect file paths and contents semantically — allowing application YAML while strictly blocking GitHub Actions / CI definitions.
3. Perform AST analysis on proposed patches. If a patch introduces sensitive system calls (`eval`, `exec`, `os.system`, `subprocess`), the patch policy marks it for **Human Escalation** (`AST_ESCALATION`) rather than silent rejection or unsafe application.

**Consequence:** Fine-grained security boundaries that protect infrastructure without hindering legitimate developer workflows.

---

### Decision 4.8 — Knowledge Graph Edge Provenance and Active Job-Protected GC

**Problem:** AST parsers infer relations with varying certainty (e.g. static direct calls vs dynamically resolved attributes). Additionally, deleting stale graph versions can orphan active jobs referencing those graphs.

**Solution:**
1. Annotate every `GraphEdge` with `edge_provenance` and `provenance_weight`:
   - `DIRECT_STATIC`: weight `1.0` (explicit AST call in the same file/module).
   - `INFERRED`: weight `0.7` (attribute or dynamically resolved reference).
2. Graph retrieval algorithms incorporate edge weights into priority scoring.
3. Graph garbage collection protects active jobs by asserting `~exists(active_jobs.where(jobs.snapshot_id == snapshot.id))`.

**Consequence:** Higher retrieval precision and zero foreign key violations during graph cleanup.

---

## Milestone 5 — Production Architecture & Concurrency Hardening (v3.0 Decisions across 3 Review Rounds)

### Decision 5.1 — Strict Guarantee Language: Removal of "Exactly-Once" Execution Claims

**Problem:** In distributed systems, claiming "exactly-once execution" across network and process boundaries (such as posting comments to GitHub API or distributed Celery workers) is mathematically impossible due to failure modes like crash-after-send before acknowledgment.

**Solution:** Systematically eliminate "exactly-once" claims from all codebase documentation, contracts, and code comments. Freeze the authoritative reliability contract:
> **At-least-once delivery + at-most-one active logical owner + idempotent/reconcilable side effects.**
> No code, comment, or document in this project may claim "exactly-once execution."

**Consequence:** The architecture focuses on deterministic idempotency, lock ownership, and state reconciliation rather than brittle assumptions of exactly-once execution.

---

### Decision 5.2 — Partial Unique Indexes on PublishedReview for Concurrency Race Defense

**Problem:** Two concurrent Celery workers evaluating different lineages (or racing on webhook delivery) could simultaneously discover no existing marker on GitHub and both call the GitHub API to POST, generating duplicate comments. The existing `UNIQUE(idempotency_key)` only serialized identical lineage keys, not different lineages for the same PR.

**Solution:** Introduce a PostgreSQL partial unique index on `published_reviews`:
```sql
CREATE UNIQUE INDEX uq_published_review_active_pr
ON published_reviews (repository_id, pr_number)
WHERE publication_status IN ('CLAIMED', 'POSTING');
```
Workers must acquire this DB claim via `claim_publication()` before making any GitHub REST API call.

**Consequence:** At most one worker can claim or post a review for a given PR at any moment, regardless of lineage differences.

---

### Decision 5.3 — Atomic Single-Transaction `is_current` Swap for Publication History

**Problem:** While multiple `PublishedReview` rows are retained for audit and lineage tracking, the system requires a definitive query for "what is the current published review for PR #X?". A non-atomic query or separate update statements introduce race windows where zero or multiple reviews appear "current".

**Solution:** Add `is_current` boolean with a partial unique index:
```sql
CREATE UNIQUE INDEX uq_published_review_current
ON published_reviews (repository_id, pr_number)
WHERE is_current = true;
```
When transitioning a publication to `POSTED`, execute the swap in a single database transaction:
1. `UPDATE published_reviews SET is_current = false WHERE repository_id = :rid AND pr_number = :pr AND is_current = true`
2. Set `current_review.is_current = true`

**Consequence:** An unambiguous, atomic single-source of truth for the active published review, with zero race conditions and zero duplicate current records.

---

### Decision 5.4 — Option B Publication Strategy (PATCH Existing vs POST New via HTML Marker)

**Problem:** When a new commit arrives on a PR or a review is updated, deleting existing GitHub comments destroys discussion threads and causes notification noise. Conversely, posting new comments creates comment clutter.

**Solution:** Adopt Option B publication reconciliation:
1. Parse PR comments for the PRSmith HTML marker: `<!-- prsmith:published_review:{chain_hash} -->`.
2. If an existing PRSmith comment is found, call GitHub API `PATCH /repos/{owner}/{repo}/issues/comments/{comment_id}` to update it in place.
3. If no existing comment is found, call `POST /repos/{owner}/{repo}/issues/{issue_number}/comments` to create a new comment.

**Consequence:** Seamless, in-place review updates that preserve comment history without polluting the pull request timeline.

---

### Decision 5.5 — Stale Publication Recovery & GitHub Error Classification

**Problem:** If a Celery worker crashes while in `POSTING` state, the publication claim remains locked indefinitely. Furthermore, GitHub rate limiting (HTTP 429) or transient 5xx errors previously caused jobs to fail permanently.

**Solution:**
1. Implement `recover_stale_posting_claims()`: reconciles `POSTING` rows older than the timeout threshold by checking GitHub markers; if posted, advances to `POSTED`, otherwise resets or retries.
2. Formulate explicit GitHub error taxonomy:
   - Transient errors (HTTP 429, 500, 502, 503, 504, connection timeouts) -> `PublicationRetryableError`. Set `publication_status = FAILED_RETRYABLE`, increment `publication_attempts`, and schedule `next_attempt_at` using exponential backoff (`min(60 * 2^(attempts-1), 3600)` seconds).
   - Permanent errors (HTTP 403, 404, 422, or attempts >= `max_publication_attempts`) -> `PublicationPermanentError`. Set `publication_status = FAILED_PERMANENT` and transition `Job` to `FAILED`.

**Consequence:** Resilient recovery from transient network interruptions and GitHub rate limits without human intervention.

---

### Decision 5.6 — Task Attempt Heartbeat Recovery with Clean Attempt Ownership

**Problem:** In the two-level `TaskExecution` model, if a worker died holding a `CLAIMED` attempt, previous recovery logic either left the attempt in an ownerless `CLAIMED` state or reused the expired attempt record.

**Solution:** `recover_stale_task_attempts()` in `backend/orchestration/attempt_recovery.py`:
1. Scans `TaskExecutionAttempt` rows where `status = 'CLAIMED'` and `heartbeat_at` is older than the lease threshold (300s).
2. Sets `status = 'ABANDONED'` on the expired attempt.
3. Creates a brand-new attempt row with `status = 'PENDING'` for the parent `TaskExecution`.

**Consequence:** Strict adherence to the owner contract: an attempt is never in `CLAIMED` without a live worker heartbeat, and newly queued work must be explicitly claimed.

---

### Decision 5.7 — Mandatory Sanitized Projections for All Repository-Derived Strings

**Problem:** Repository source code, diff text, file names, AST symbols, commit messages, and exceptions are untrusted external inputs. Allowing raw repository strings into persistent graph storage, vector embeddings, or LLM prompts creates severe prompt injection and secret leakage vulnerabilities.

**Solution:** Create `backend/retrieval/sanitized_projections.py` (`SanitizedProjection`):
- All file reads go through `read_file_sanitized()` / `read_lines_sanitized()`.
- Diffs pass through `sanitize_diff()`.
- File paths are verified against secret patterns (`scan_file_path()`).
- Symbol names are validated (`scan_symbol_name()`).
- Sensitive structlog log fields (`error`, `message`, `diff`, `content`) are redacted using a dedicated processor.

**Consequence:** A hard, non-bypassable security boundary isolating the core platform from malicious or sensitive repository data.

---

### Decision 5.8 — Mandatory Snapshot Immutability Checkpoints

**Problem:** If a contributor pushes new commits to a PR while PRSmith is cloning, analyzing, reviewing, or repairing, the agent risks producing findings or patches against stale code and posting outdated results.

**Solution:** Enforce three mandatory `assert_head_sha_current()` checkpoints:
1. Immediately before `reviewer.review_pr()` execution.
2. Immediately before each repair loop iteration.
3. Immediately before acquiring a publication claim in `claim_publication()`.
If the GitHub head SHA has drifted, `StaleSnapshotError` is raised and the job transitions immediately to `STALE_SNAPSHOT`.

**Consequence:** Guarantee that no review finding or repair patch is ever evaluated or published against a superseded commit.

---

### Decision 5.9 — Formalization of `STALE_SNAPSHOT` and `ESCALATED` as First-Class Terminal States

**Problem:** Previously, superseded jobs and jobs requiring human escalation were conflated with `FAILED`, requiring string parsing of `error_message` to understand termination cause.

**Solution:**
1. Update `backend/orchestration/state_machine.py`: formalize `STATUS_STALE_SNAPSHOT` and `STATUS_ESCALATED` alongside `COMPLETED` and `FAILED` in `TERMINAL_STATES`.
2. Add `completion_reason` enum column to `Job` model (`HUMAN_ESCALATION`, `REPAIR_BUDGET_EXHAUSTED`, `VALIDATION_FAILED`, `STALE_SNAPSHOT_DRIFT`, `UNRECOVERABLE_ERROR`, `NONE`).
3. Maintain `superseded_by_job_id` FK linking stale jobs to the replacement job.

**Consequence:** Clean, machine-readable pipeline states without fragile string parsing.

---

### Decision 5.10 — Centralized Repair Domain Service (`RepairService`)

**Problem:** Placing repair orchestration, `RepairRun` persistence, validation plan execution, and patch hashing inside worker task files (`worker/tasks/review.py`) created circular dependencies between workers and made repair logic untestable without a Celery execution context.

**Solution:** Create `backend/repair/service.py` (`RepairService`):
- Owns all repair domain logic: executing candidate fixes, persisting `RepairRun`, computing canonical patch artifact hashes, running 7-layer validation, and executing worktree rollback.
- Celery worker tasks (`worker/tasks/repair.py`, `worker/tasks/review.py`) act purely as thin asynchronous dispatchers that invoke `RepairService`.

**Consequence:** Clean separation of concerns, zero circular imports, and fully testable repair domain operations.

---

### Decision 5.11 — Optimistic Locking Enforcement in Worker Tasks

**Problem:** `worker/tasks/review.py` was directly modifying `job.status = "..."`, bypassing `JobMutationService.transition()`. If a job was marked `STALE_SNAPSHOT` concurrently, a lagging worker would overwrite the status, resurrecting a dead job.

**Solution:** Replace all direct status mutations with `await JobMutationService.transition(session, job, new_status, worker_id=worker_id)`. The conditional `WHERE version = :expected_version` ensures that any concurrent status mutation immediately raises `StaleJobMutationError` and aborts the lagging worker.

**Consequence:** Elimination of stale-worker job resurrection races.

---

## Environment & Tooling Decisions

### Environment Decision E.1 — MSYS64 UCRT64 Python with system-site-packages venv

**Problem:** Windows does not have precompiled PyPI binary wheels for Rust/C-extension packages like `cryptography` (required for Fernet) and `pydantic-core`. `pip install cryptography` on a standard Windows Python attempts to compile from source and fails without a C toolchain.

**Solution:** Use MSYS64's UCRT64 subsystem, which ships pre-compiled binary packages via `pacman`:
- `pacman -S mingw-w64-ucrt-x86_64-python-cryptography`
- `pacman -S mingw-w64-ucrt-x86_64-python-sqlalchemy`
- etc.

Created `.venv` with `--system-site-packages` flag so the virtual environment inherits all pacman-installed packages while still being isolated for pip-only packages.

**Gotcha:** PowerShell glob expansion. `pacman --overwrite "*"` expands `*` against the current directory's filenames. Must use `pacman --overwrite=*` (equals sign, no space) to pass the flag literally to pacman.

---

### Environment Decision E.2 — pydantic-settings version pinned to 2.1.0

**Problem:** The UCRT64 pacman repository carries `pydantic 2.5.3`. Installing the latest `pydantic-settings` (2.15.x) via pip causes `ModuleNotFoundError: No module named pydantic._internal._signature` because pydantic-settings 2.15 expects pydantic 2.8+.

**Solution:** Pin `pydantic-settings==2.1.0` which is compatible with pydantic 2.5.x. This is the maximum version that works without requiring a newer pydantic build.

---

### Environment Decision E.3 — Pure-Python packages installed with `--no-deps`

**Problem:** When pip resolves transitive dependencies for packages like `structlog` or `prometheus_client`, it may try to upgrade `pydantic` or `cryptography` to incompatible versions.

**Solution:** Install pure-Python packages that have no binary extension dependencies using `pip install --no-deps`. This prevents pip from resolving transitive dependency upgrades that would break the MSYS64 binary package versions.

Packages installed this way: `structlog`, `prometheus_client`, `PyGithub`, `GitPython`, `unidiff`, `smmap`, `gitdb`.

---

## Rejected Alternatives Log

| Feature Area | Alternative Considered | Why Rejected |
|---|---|---|
| Code parsing | Tree-sitter, Semgrep | Binary dependencies, complex build; Python AST is zero-dependency |
| Session storage | Redis-backed sessions | Adds stateful dependency for auth; stateless JWT sufficient for current scale |
| Auth token storage | AES-GCM manually | Fernet is authenticated encryption with simpler API; handles padding, IV, HMAC |
| Frontend framework | Next.js | SSR complexity unnecessary for internal dashboard SPA |
| Frontend auth callback | `AuthCallback.tsx` page | Backend handles full handshake; exposing code in URL is a security regression |
| Token delivery | `Authorization: Bearer` header | HttpOnly cookie blocks XSS token theft; Bearer headers require JS access to token |
| Repo uniqueness model | `repositories.github_id UNIQUE` + multiple owners | Contradictory — resolved with `user_repositories` join table |
| Repo config defaults | Keep `monitoring_enabled` on `Repository` as fallback | Creates dual-source truth; removed entirely in favor of `UserRepository`-only config |
| Credential resolution fallback | `_get_first_user_repository()` | Non-deterministic (insertion-order dependent); replaced with explicit `triggered_by_user_id` |
| SELECT FOR UPDATE on SQLite | Apply uniformly | SQLite doesn't support it; dialect check `!= "sqlite"` allows unit tests to run safely |
| OAuth state | URL query param only | Requires server-side state store; cookie double-submit is stateless and equally secure |
| Fernet key validation | Lazy (on first encrypt/decrypt) | Delay masks misconfiguration; eager startup failure is safer and clearer |
| GitHub icon | `lucide-react` `Github` export | Not exported in installed version; custom SVG component is version-independent |
| Database migrations | `Base.metadata.create_all()` | Cannot ALTER existing tables; Alembic is the correct migration tool |
| Production sandbox fallback | Subprocess fallback on host | Severe RCE security vulnerability; fail-closed `SandboxUnavailableError` strictly required |
| Task deduplication | Single-record task table | Worker death permanently suppresses retries; two-level `TaskExecution` + `TaskExecutionAttempt` adopted |
| Outbox delivery | Synchronous webhook dispatch to Celery | Network partitioning creates at-least-once loss window; transactional outbox guarantees durability |
| Lineage hashing | Direct dictionary `json.dumps()` | Key ordering and transient DB fields cause hash drift; `canonical_hash()` normalizes before hashing |
| Dangerous AST calls | Complete patch rejection | Rejection is overly rigid; `AST_ESCALATION` escalates to human reviewer with detailed context |
| Distributed exactly-once | Distributed 2PC/exactly-once claims | Impossible across network boundaries and API retries; replaced by at-least-once delivery + at-most-one active owner + idempotent side effects |
| Publication concurrency | Application-level check only | Prone to multi-worker race conditions; enforced via PostgreSQL partial unique index `uq_published_review_active_pr` |
| Worker status mutation | Direct `job.status = ...` assignment | Bypasses optimistic concurrency version checks; replaced by mandatory `JobMutationService.transition()` |
| Stale attempt recovery | Leave in ownerless `CLAIMED` state | Creates zombie claims; replaced by marking `ABANDONED` and creating a new `PENDING` attempt row |
| Raw code context retrieval | Unsanitized `open()` file reads | Bypasses secret scanning; replaced by mandatory `SanitizedProjection` boundary |
| Repair inlining | Embedding repair logic in worker tasks | Creates circular imports and breaks isolation; extracted into `RepairService` domain service |
| Stale comment handling | Delete-and-repost on GitHub | Destroys discussion threads and produces noise; replaced by Option B (PATCH existing if marker found, else POST) |
