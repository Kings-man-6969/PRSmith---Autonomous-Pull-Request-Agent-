# 🖥️ PRSmith Frontend — Autonomous PR Review Dashboard

A modern, responsive web application for monitoring repositories, viewing autonomous Pull Request review findings, inspecting surgical patches, and exploring AST knowledge graphs.

Built with **React 19**, **TypeScript**, **Vite**, **Tailwind CSS**, and **Recharts**, designed with a clean, dark, Vercel-inspired glassmorphism aesthetic.

---

## 🚀 Key Features

- **GitHub OAuth Login & Session Management**:
  - Secure authentication via `HttpOnly; SameSite=Lax` cookies with automatic CSRF double-submit protection (`X-CSRF-Token` header).
  - Real-time session validation with automatic redirect to `/login` when unauthenticated.
- **Repository Management Dashboard**:
  - Connected repository discovery and manual repository onboarding (`RepositoryHub`).
  - Per-user monitoring toggles, automated repair enablement, and watched branches configuration.
  - One-click triggers for AST Knowledge Graph generation and PR review dispatches.
- **Formal State Machine & Job Inspection**:
  - Real-time job lifecycle tracking across 10 formal states (`PENDING`, `CLONING`, `ANALYZING`, `REVIEWING`, `REPAIRING`, `VALIDATING`, `PUBLISHING`, `COMPLETED`, `FAILED`, `ESCALATED`, `STALE_SNAPSHOT`).
  - Machine-readable completion reasons (`HUMAN_ESCALATION`, `REPAIR_BUDGET_EXHAUSTED`, `VALIDATION_FAILED`, `STALE_SNAPSHOT_DRIFT`, `UNRECOVERABLE_ERROR`).
  - Explicit banner callouts for superseded lineages (`STALE_SNAPSHOT` with link to `superseded_by_job_id`) and escalation states (`ESCALATED`).
  - Manual review publication action (`POST /api/jobs/{id}/publish`).
- **Review Findings & Repository-Wide Explorer**:
  - Cursor-paginated Findings Explorer (`/findings`) with severity (`HIGH`, `MEDIUM`, `LOW`), category, and repository filters.
  - Structured evidence cards grounded in deterministic AST entities (`FindingCard`).
  - Visual stacked PR risk distribution bar (`RiskBar`).
- **Autonomous Repair & Differential Validation**:
  - Iteration-by-iteration repair history and diff inspector (`RepairTimeline`).
  - Interactive unified diff viewer (`PatchDiff`) showing surgical modifications before human approval.
  - 7-layer validation status cards (syntax, format, lint, typecheck, targeted tests, integration tests, build).
  - Confidence scoring gauge and regression-free verification guarantees.
- **AST Knowledge Graph Visualizer**:
  - Interactive structural graph of functions, callers, callees, and test mappings.

---

## 📂 Architecture & Directory Structure

```
frontend/
├── public/               # Static assets & favicon
├── src/
│   ├── api/
│   │   └── client.ts     # Axios instance with credentials, CSRF header interceptor, and API types
│   ├── components/       # Reusable UI components
│   │   ├── ConfidenceBar.tsx   # Evidence confidence score indicator
│   │   ├── FindingCard.tsx     # Grounded finding display with severity badges
│   │   ├── GitHubIcon.tsx      # Authentic SVG GitHub mark component
│   │   ├── ImpactGraph.tsx     # Blast radius & dependency visualization
│   │   ├── PatchDiff.tsx       # Unified diff syntax viewer
│   │   ├── RepairTimeline.tsx  # Iterative repair timeline and diff inspection
│   │   ├── RiskBar.tsx         # Stacked PR risk score distribution bar
│   │   ├── StatusBadge.tsx     # Formal state machine colored badge
│   │   ├── UserMenu.tsx        # User profile & sign-out dropdown
│   │   └── ValidationReport.tsx# Layered validation results & diff verification
│   ├── context/
│   │   └── AuthContext.tsx     # User authentication state, profile caching, and logout
│   ├── pages/            # Application views
│   │   ├── Dashboard.tsx       # Repositories overview, quick stats, and recent jobs
│   │   ├── JobDetail.tsx       # Full PR review breakdown, repair timeline, and publish action
│   │   ├── Login.tsx           # GitHub OAuth sign-in screen
│   │   ├── RepositoryGraph.tsx # Interactive AST knowledge graph explorer
│   │   ├── RepositoryHub.tsx   # Multi-tenant tracked repos, AuthBanner, & discovery modal
│   │   └── FindingsExplorer.tsx# Cursor-paginated repository-wide findings browser
│   ├── App.tsx           # Router configuration and layout shell
│   ├── main.tsx          # Application entry point
│   └── index.css         # Vercel-inspired design tokens and glassmorphism styling
├── Dockerfile            # Multi-stage production container build (Vite build -> Nginx alpine)
├── nginx.conf            # Nginx SPA fallback configuration & reverse proxy
├── package.json          # Dependencies & build scripts
├── tsconfig.json         # TypeScript strict configuration
└── vite.config.ts        # Vite configuration with API proxy
```

---

## 🛠️ Local Development

### 1. Install Dependencies
```bash
npm install
```

### 2. Start Vite Dev Server
```bash
npm run dev
```
The application will start on `http://localhost:5173` (or the next available port) with Hot Module Replacement (HMR).

### 3. API Proxy Configuration
In development, Vite proxies requests from `/api` to the FastAPI backend at `http://localhost:8000`:
```ts
// vite.config.ts
export default defineConfig({
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
});
```

---

## 📦 Production Build & Containerization

### Build Standalone Bundle
```bash
npm run build
```
Builds optimized production assets to `dist/`.

### Run via Docker
The frontend is containerized using a multi-stage Dockerfile:
1. **Stage 1 (Builder)**: Compiles TypeScript and builds assets via Vite.
2. **Stage 2 (Runner)**: Serves static files through lightweight Nginx on port `80`.

In Docker Compose, the frontend is accessible at:
```text
http://localhost:5173
```
Mapped to Nginx port 80 with SPA fallback routing configured in `nginx.conf`.

---

## 🔒 Security & Authentication

- **Cookie-Based JWT**: Session tokens are held in `HttpOnly` cookies and are never accessible to client-side JavaScript, mitigating Cross-Site Scripting (XSS) risks.
- **Double-Submit CSRF**: The frontend reads the non-HttpOnly `prsmith_csrf_token` cookie and automatically attaches it as the `X-CSRF-Token` header on all mutation requests (`POST`, `PUT`, `DELETE`).
- **Instant Logout**: Calling `/api/auth/logout` increments the database `session_version`, invalidating the session token immediately across all active instances.
