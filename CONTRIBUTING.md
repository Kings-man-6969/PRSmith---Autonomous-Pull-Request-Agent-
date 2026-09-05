# Contributing to PRSmith

Thank you for your interest in contributing to **PRSmith**! We welcome contributions from the community. Please review this guide before opening issues or submitting pull requests.

---

## 1. Development Setup

### Prerequisites
- **Python**: 3.11 or higher
- **Node.js**: 20+ and npm (for frontend dashboard development)
- **Docker**: For running sandboxed test execution
- **Git**

### Fresh Clone Setup

1. **Clone the repository**:
   ```bash
   git clone https://github.com/Kings-man-6969/PRSmith---Autonomous-Pull-Request-Agent-.git
   cd PRSmith---Autonomous-Pull-Request-Agent-
   ```

2. **Create and activate a virtual environment**:
   ```bash
   python -m venv .venv
   # On Linux / macOS:
   source .venv/bin/activate
   # On Windows (PowerShell):
   .venv\Scripts\Activate.ps1
   ```

3. **Install dependencies from the reproducible lockfile**:
   ```bash
   pip install -r requirements.lock.txt
   pip install -e ".[dev]"
   ```

4. **Frontend setup (Optional if working on backend only)**:
   ```bash
   cd frontend
   npm ci
   cd ..
   ```

5. **Environment Configuration**:
   ```bash
   cp .env.example .env
   ```
   Generate required secrets:
   ```bash
   python -c "from cryptography.fernet import Fernet; print('GITHUB_TOKEN_ENCRYPTION_KEY=' + Fernet.generate_key().decode())"
   python -c "import secrets; print('SECRET_KEY=' + secrets.token_hex(32))"
   ```

---

## 2. Running Tests & Quality Checks

PRSmith's test suite is designed to run **standalone and offline** without requiring running PostgreSQL, Redis, or external LLM API credentials.

### Run the Full Test Suite
```bash
pytest tests/ -v
```

### Run Tests with Coverage Enforcement (Fail Under 60%)
```bash
pytest tests/ -v --cov=backend --cov=worker --cov-report=term-missing --cov-fail-under=60
```

### Code Formatting & Linting
We use **Ruff** for linting and **Black** for code formatting:
```bash
# Check linting
ruff check .

# Auto-fix linting issues where possible
ruff check --fix .

# Code formatting
black --check .
```

### Type Checking
We enforce strict typing with **mypy**:
```bash
mypy backend worker
```

### Dependency Security Audit
```bash
pip-audit
```

### Frontend Build & Typecheck
```bash
cd frontend
npm run build
```

---

## 3. Pull Request Guidelines

### Commit Convention
We follow the [Conventional Commits](https://www.conventionalcommits.org/) specification:
- `feat:` A new feature
- `fix:` A bug fix
- `test:` Adding or updating tests
- `docs:` Documentation updates
- `ci:` Changes to CI configuration or scripts
- `chore:` Dependency bumps, tooling maintenance

### Commit Granularity
- **Ship features and fixes together with their tests**: Each commit should be self-contained and pair code modifications with test cases verifying the invariant or behavior.
- Avoid large, unrelated batch commits that mix refactors, feature code, and formatting changes.

### Automated CI Gate
Every pull request is automatically verified against:
- Ruff linting
- Mypy strict type checking
- Pip-audit vulnerability scanning
- Full Pytest test suite with a 60% code coverage threshold
- Frontend TypeScript build

---

## 4. Security Disclosures
Please do not report security vulnerabilities through public GitHub issues. Refer to [SECURITY.md](SECURITY.md) for our coordinated vulnerability disclosure policy and contact procedures.
