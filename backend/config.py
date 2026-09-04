"""Configuration management for PRSmith v2 using Pydantic Settings."""

from pathlib import Path
from typing import Literal
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """PRSmith configuration loaded from environment or .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # General
    ENVIRONMENT: Literal["development", "staging", "production", "test"] = "development"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"
    API_PREFIX: str = "/api"
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    SECRET_KEY: str = "prsmith-super-secret-key-change-in-production"

    # GitHub App & OAuth
    GITHUB_APP_ID: str = ""
    GITHUB_APP_PRIVATE_KEY_PATH: str = "prsmith69.2026-08-06.private-key.pem"
    GITHUB_APP_PRIVATE_KEY: str = ""
    GITHUB_WEBHOOK_SECRET: str = ""
    GITHUB_CLIENT_ID: str = ""
    GITHUB_CLIENT_SECRET: str = ""
    # Personal Access Token for dashboard repo discovery (optional fallback)
    GITHUB_PAT: str = ""
    ENABLE_PAT_FALLBACK: bool = False

    # OAuth & Session Configuration
    FRONTEND_URL: str = "https://carmaker-registry-senorita.ngrok-free.dev"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 10080  # 7 days
    OAUTH_STATE_EXPIRE_SECONDS: int = 600  # 10 min CSRF window
    GITHUB_OAUTH_SCOPES: str = "read:user user:email read:org"

    # Token Encryption at Rest (Fernet key - REQUIRED)
    GITHUB_TOKEN_ENCRYPTION_KEY: str = ""

    @property
    def COOKIE_SECURE(self) -> bool:
        """Enforce Secure cookie flag in production, allow HTTP in dev/test."""
        return self.ENVIRONMENT == "production"

    # Database & Redis
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/prsmith"
    SYNC_DATABASE_URL: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/prsmith"
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/1"

    # LLM Settings
    LLM_PROVIDER: str = "openai"  # openai | gemini | anthropic | deepseek | qwen | custom

    # OpenAI
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o"
    OPENAI_FAST_MODEL: str = "gpt-4o-mini"
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"

    # Google Gemini
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-1.5-pro"
    GEMINI_FAST_MODEL: str = "gemini-1.5-flash"

    # Anthropic
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-3-5-sonnet-20241022"
    ANTHROPIC_FAST_MODEL: str = "claude-3-5-haiku-20241022"

    # DeepSeek
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_MODEL: str = "deepseek-chat"
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"

    # Qwen (Alibaba DashScope)
    QWEN_API_KEY: str = ""
    QWEN_MODEL: str = "qwen-plus"
    QWEN_FAST_MODEL: str = "qwen-turbo"
    QWEN_BASE_URL: str = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"

    # Custom / OpenAI-Compatible
    CUSTOM_API_KEY: str = ""
    CUSTOM_BASE_URL: str = ""
    CUSTOM_MODEL: str = "default"
    CUSTOM_FAST_MODEL: str = "default"
    ALLOW_LOCAL_CUSTOM_ENDPOINTS: bool = False

    # Timeout & Rate Limiting Controls
    LLM_TIMEOUT_SECONDS: int = 120
    LLM_MAX_CONCURRENCY: int = 2
    LLM_MIN_REQUEST_INTERVAL: float = 0.5

    # Budget & Resource Limits
    MAX_COST_USD: float = 5.0
    MAX_TOKEN_BUDGET: int = 16000
    MAX_MODEL_CALLS: int = 15

    # Pipeline Version
    PIPELINE_VERSION: str = "20260903.1"

    # Sandbox Configuration
    ALLOW_LOCAL_DEV_EXECUTION: bool = False
    SANDBOX_BACKEND: Literal["docker", "dev_subprocess"] = "docker"
    SANDBOX_DOCKER_IMAGE: str = "prsmith-sandbox:latest"
    SANDBOX_CPU_LIMIT: str = "2.0"
    SANDBOX_MEMORY_LIMIT: str = "2g"
    SANDBOX_TIMEOUT_SECONDS: int = 180
    SANDBOX_NETWORK_ENABLED: bool = False
    SANDBOX_USER: str = "sandboxuser"

    @model_validator(mode="after")
    def validate_production_safety(self) -> "Settings":
        """Disallow dangerous execution configurations in production."""
        if self.ENVIRONMENT == "production" and self.ALLOW_LOCAL_DEV_EXECUTION:
            raise ValueError(
                "ALLOW_LOCAL_DEV_EXECUTION must never be True in production. "
                "Untrusted repository code must run inside the Docker sandbox."
            )
        if self.ENVIRONMENT == "production" and self.SANDBOX_BACKEND == "dev_subprocess":
            raise ValueError(
                "SANDBOX_BACKEND cannot be 'dev_subprocess' in production."
            )
        return self

    # Repair Policy & Limits
    MAX_REPAIR_ITERATIONS: int = 5
    MAX_REPAIR_TIME_SECONDS: int = 600
    MAX_FILES_CHANGED: int = 5
    MAX_LINES_ADDED: int = 200
    MAX_LINES_DELETED: int = 200
    MAX_PATCH_BYTES: int = 32768

    # Risk Scoring Thresholds
    PR_RISK_HIGH_THRESHOLD: int = 70
    PR_RISK_CRITICAL_THRESHOLD: int = 90

    # Worktree Storage
    WORKTREE_BASE_DIR: Path = Path("/tmp/prsmith_worktrees")


settings = Settings()
