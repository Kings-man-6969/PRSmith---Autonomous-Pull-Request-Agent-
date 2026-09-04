import pytest
from backend.config import Settings


def test_settings_defaults():
    settings = Settings()
    assert settings.ENVIRONMENT in ["development", "staging", "production", "test"]
    assert settings.MAX_REPAIR_ITERATIONS == 5
    assert settings.MAX_REPAIR_TIME_SECONDS == 600
    assert settings.OPENAI_MODEL == "gpt-4o"
    assert settings.PR_RISK_HIGH_THRESHOLD == 70
