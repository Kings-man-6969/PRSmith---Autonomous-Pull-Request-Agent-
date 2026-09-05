# Changelog

All notable changes to the **PRSmith** project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
- Comprehensive CI pipeline (`.github/workflows/ci.yml`) enforcing linting (`ruff`), strict type checking (`mypy`), dependency audits (`pip-audit`), 60% coverage threshold (`pytest-cov`), and frontend TypeScript builds.
- Scheduled Dependabot configuration (`.github/dependabot.yml`) for automated weekly dependency updates across `pip`, `npm`, and `github-actions`.
- Reproducible Python lockfile (`requirements.lock.txt`) pinning all 27 direct and 100+ transitive dependencies.
- Dedicated security policy and formal threat model documentation in `SECURITY.md` detailing trust boundaries, AST escalation policies, and vulnerability disclosure SLAs.
- Developer onboarding and contribution guidelines in `CONTRIBUTING.md`.
- Explicit `GITHUB_TOKEN` definition in `.env.example`.

### Fixed
- Reconciled publication claim arguments in `backend/review/publisher.py` to allow optional `repository_id` and `pr_number` with fallback to `job` attributes.
- Resolved mock GitHub client keyword argument mismatches in `ConcurrentMockGitHubClient` and resilient fallback in `publish_review_comment()`.
- Fixed OAuth redirect handling in `backend/api/auth.py` to respect `settings.FRONTEND_URL` in test environments.
- Corrected state machine backward-compatible alias handling for `STATUS_SUPERSEDED` mapping to `STATUS_STALE_SNAPSHOT`.
- Resolved `MissingGreenlet` exception in concurrent publication claims using nested savepoints (`session.begin_nested()`).
- Fixed stable review marker assertion in safety invariant end-to-end tests.

---

## [0.2.0] - 2026-08-01

### Added
- Multi-provider LLM gateway supporting OpenAI, Anthropic Claude, Google Gemini, DeepSeek, Qwen, and custom OpenAI-compatible inference servers.
- AST-based dangerous code escalation in `backend/repair/patch_policy.py` (`eval`, `exec`, `subprocess.Popen`, socket operations).
- SSRF validation and automated credential masking in `backend/llm/security.py`.
- Fernet AES-128-CBC + HMAC-SHA256 encryption at rest for GitHub OAuth credentials.
- Optimistic locking and explicit state machine for PR review and repair job lifecycles.
- Dual-path review publication with idempotent GitHub HTML marker reconciliation.

### Changed
- Refactored Celery workers to use `NullPool` for asyncpg connection isolation.
- Enhanced dependency projection with deterministic cache keys.

---

## [0.1.0] - 2026-07-15

### Added
- Initial release of PRSmith autonomous pull request agent.
- FastAPI REST backend with JWT session authentication.
- Repository symbol and dependency graph extraction for Python and JavaScript.
- Celery task queue integration with Redis broker.
- Docker sandbox execution harness for sandboxed test validation.
- Interactive React/Vite dashboard frontend.
