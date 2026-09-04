# PRSmith Commands & Scripts Reference

This file documents the exact, verified CLI paths and invocations for this workspace environment.
The virtual environment is located at `.venv/` (created via UCRT64 Python 3.11 with binaries in `.venv/bin/`).

## Python Environment

| Tool | Exact Command |
|---|---|
| **Python** | `.venv\bin\python.exe` |
| **Pip** | `.venv\bin\python.exe -m pip` |
| **Pytest** | `.venv\bin\python.exe -m pytest` |
| **Alembic** | `.venv\bin\python.exe -m alembic` |
| **Uvicorn** | `.venv\bin\python.exe -m uvicorn` |
| **Celery** | `.venv\bin\python.exe -m celery` |

## Standard Workflows

### Run All Unit & Integration Tests
```powershell
.venv\bin\python.exe -m pytest tests/unit tests/integration -v
```

### Run Full Test Suite (136 Passing Tests)
```powershell
.venv\bin\python.exe -m pytest tests/ -v
```

### Run E2E Safety Invariants & Security Adversarial Suites
```powershell
.venv\bin\python.exe -m pytest tests/e2e tests/security -v
```

### Run Concurrency & Crash Recovery Adversarial Tests
```powershell
.venv\bin\python.exe -m pytest tests/e2e/test_publication_crash_windows.py tests/e2e/test_stale_worker_superseded_race.py tests/e2e/test_task_attempt_recovery.py -v
```

### Run Single Test File
```powershell
.venv\bin\python.exe -m pytest tests/unit/test_config.py -v
```

### Key Generation Utilities
```powershell
# Generate Fernet token encryption key (REQUIRED in .env as GITHUB_TOKEN_ENCRYPTION_KEY):
.venv\bin\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Generate JWT signing secret key (SECRET_KEY in .env):
.venv\bin\python.exe -c "import secrets; print(secrets.token_hex(32))"
```

### Run Database Migrations (Alembic)
```powershell
.venv\bin\python.exe -m alembic upgrade head
```

### Start FastAPI Backend Locally
```powershell
.venv\bin\python.exe -m uvicorn backend.app:app --host 0.0.0.0 --port 8000 --reload
```

### Start Celery Worker Locally
```powershell
.venv\bin\python.exe -m celery -A worker.celery_app worker --loglevel=info --concurrency=4 -Q celery,graph_build,review,repair,validation
```

### Docker Compose Multi-Container Orchestration
```powershell
# Start all 5 services in detached mode
docker-compose up -d

# Rebuild backend and worker containers with updated Dockerfile / packages
docker-compose build backend worker

# Restart backend and worker services
docker-compose restart backend worker

# View live container logs
docker-compose logs -f backend worker

# Verify backend health endpoint
curl http://localhost:8000/health

# Stop all containers
docker-compose down
```

### Frontend Development & Build
```powershell
cd frontend
npm install
npm run dev
npm run build
cd ..
```

### Expose Local Environment via Ngrok (for GitHub OAuth & Webhooks)
```powershell
# Expose Docker Compose frontend & backend reverse proxy (port 5173 maps to Nginx):
ngrok http 5173 --url https://carmaker-registry-senorita.ngrok-free.dev
```

