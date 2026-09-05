# PRSmith Security Policy & Threat Model

PRSmith is an autonomous pull request review and repair agent that interacts with GitHub repositories, calls Large Language Model (LLM) inference gateways, and validates patches in isolated sandboxes. Because the system has access to code, repository tokens, and automated publishing capabilities, security invariants and trust boundaries are strictly defined and enforced.

---

## 1. Security Architecture & Trust Boundaries

PRSmith operates across five explicit trust boundaries:

```
┌─────────────────────────┐
│     GitHub Webhooks     │ (Untrusted Ingress)
└────────────┬────────────┘
             │ HMAC-SHA256 Signature Verification (X-Hub-Signature-256)
             ▼
┌─────────────────────────┐
│    PRSmith Backend      │ ──[Fernet AES-128-CBC + HMAC]──► Encrypted DB Tokens
│ (FastAPI / Celery Core) │
└────────────┬────────────┘
             │
   ┌─────────┴─────────┐
   │                   │
   ▼                   ▼
┌──────────────┐ ┌──────────────┐
│  LLM Gateway │ │ Docker/Env   │
│  (Filtered)  │ │ Sandbox      │
└──────────────┘ └──────────────┘
   (SSRF Guard)   (Resource-capped,
  (Secret Mask)    no-network)
```

### Trust Boundary 1: Webhook Ingress
- **Threat**: Forged or replay webhook events triggering unauthorized analysis or resource exhaustion.
- **Mitigation**: Every inbound webhook is authenticated via HMAC-SHA256 signature verification (`X-Hub-Signature-256`) against `GITHUB_WEBHOOK_SECRET`. Unsigned or invalid requests are rejected with `401 Unauthorized` before parsing payload bodies.
- **Deduplication**: Webhooks are registered in an atomic idempotency outbox (`WebhookDelivery`) to protect against duplicate delivery and replay attacks.

### Trust Boundary 2: GitHub Credentials & Token Storage
- **Threat**: Exposure of GitHub OAuth access tokens or Personal Access Tokens (PATs) at rest in the database or during logging.
- **Mitigation**:
  - OAuth tokens are encrypted at rest using AES-128-CBC with PKCS7 padding and HMAC-SHA256 authenticated encryption via Fernet (`GITHUB_TOKEN_ENCRYPTION_KEY`).
  - Tokens are only decrypted in-memory immediately prior to GitHub API calls and never persisted in plaintext.
  - Logging pipelines (`backend/observability/logging.py`) and secret projection layers filter and redact authorization headers and credentials matching regex patterns (`ghp_[a-zA-Z0-9]{36}`, `Bearer ...`, `sk-...`).

### Trust Boundary 3: LLM Provider Gateway
- **Threat**:
  - Server-Side Request Forgery (SSRF) when custom or local model gateway endpoints are configured.
  - Accidental leakage of secrets embedded within source code sent to external LLM providers.
  - Prompt injection embedded in PR diffs attempting to hijack the review agent instructions.
- **Mitigation**:
  - **SSRF Guard**: Custom LLM gateway URLs are strictly validated using `backend/llm/security.py`. Private IP ranges (RFC 1918), link-local addresses, and cloud instance metadata services (`169.254.169.254`) are blocked unless `ALLOW_LOCAL_CUSTOM_ENDPOINTS=true` is explicitly configured for offline local testing.
  - **Pre-LLM Secret Redaction**: Diff chunks and context files are scanned with `backend/security/secrets.py` to mask high-entropy tokens and credentials before LLM prompt assembly.
  - **Prompt Injection Defense**: Untrusted user inputs (PR titles, descriptions, comments, and patch diffs) are demarcated within structured delimiters and sanitized to suppress instruction overrides.

### Trust Boundary 4: Sandboxed Patch Execution
- **Threat**: Malicious code executed during automated test runs escaping the worker environment or accessing host resources.
- **Mitigation**:
  - Validation runs execute inside isolated Docker containers (`prsmith-sandbox`).
  - Strict cgroup resource limits: CPU (`SANDBOX_CPU_LIMIT=2.0`), Memory (`SANDBOX_MEMORY_LIMIT=2g`), and wall-clock timeout (`SANDBOX_TIMEOUT_SECONDS=180`).
  - Network isolation: `SANDBOX_NETWORK_ENABLED=false` blocks outbound egress during patch verification.
  - Unprivileged execution: Containers run as a non-root user (`sandboxuser`).

### Trust Boundary 5: Autonomous Review & Publication Concurrency
- **Threat**: Concurrent workers racing on the same PR posting duplicate review comments or overwriting confirmed states.
- **Mitigation**:
  - Publication claims are atomically gated via PostgreSQL partial unique index on `(repository_id, pr_number)` for active states (`CLAIMED`, `POSTING`).
  - Marker reconciliation checks for stable PR markers (`<!-- prsmith:review:{repo_id}:{pr_number} -->`) to PATCH existing comments rather than posting duplicates.
  - Optimistic locking (`Job.version`) protects state transitions against lost updates.

---

## 2. Autonomous Repair Patch Policies

Autonomous repair generates patches to fix detected defects. To prevent malicious or destabilizing modifications, patches must pass deterministic policy gates in `backend/repair/patch_policy.py`:

1. **Categorized Denylist Enforcement**:
   - Patches touching CI/CD workflows (`.github/workflows/`), security configurations, authentication code, or Docker definitions are **strictly rejected**.
   - Package manager manifests (`pyproject.toml`, `package.json`, `Cargo.toml`) and environment configuration files are denylisted from autonomous modification.
2. **AST-Based Escalation (Not Silent Execution)**:
   - Patches are analyzed at the Python Abstract Syntax Tree (AST) level.
   - Any introduction of dangerous system calls (`eval`, `exec`, `__import__`, `os.system`, `subprocess.Popen`, `socket.*`, `shutil.rmtree`) automatically halts autonomous repair and triggers **Human Escalation (`ESCALATED`)**, requiring explicit human review before any code is approved.
3. **Differential Regression Gate**:
   - Patches must produce zero new test regressions compared against base execution snapshots. Any new test failure aborts publication.

---

## 3. Out-of-Scope Threats

The following threat scenarios are beyond the trust perimeter of PRSmith and must be secured by host infrastructure:
- **Compromise of GitHub App Private Key**: If `prsmith.private-key.pem` or GitHub App secrets are leaked outside the deployment environment.
- **Host Compromise**: Direct root access to the Docker host or Kubernetes cluster running PRSmith.
- **Compromised LLM Provider**: Malicious manipulation or total breach of upstream LLM API providers (e.g., OpenAI, Anthropic, Google).
- **Physical/OS-Level Memory Dumps**: Extraction of in-flight decryption keys from raw system memory on untrusted hosts.

---

## 4. Reporting a Vulnerability

We take the security of PRSmith seriously. If you discover a security vulnerability, please follow responsible disclosure:

1. **Do not open public GitHub issues** for security vulnerabilities.
2. Send report details to **`security@prsmith.dev`** (or open a private security advisory on GitHub under the **Security** tab -> **Advisories**).
3. Include:
   - Detailed description of the vulnerability and affected components
   - Proof-of-concept (PoC) code or reproduction steps
   - Potential security impact and suggested mitigations if available
4. **Response Timelines**:
   - **Initial Acknowledgement**: Within 24 hours.
   - **Triage and Status Update**: Within 72 hours.
   - **Remediation Release**: Aimed within 7 business days for high or critical severity issues.

We appreciate the efforts of security researchers and engineers in keeping open source software secure.
