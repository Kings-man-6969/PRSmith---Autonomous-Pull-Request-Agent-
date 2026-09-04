"""Redaction of secrets and PII before sending context to LLMs."""

import re
from backend.security.secrets import SECRET_PATTERNS


def redact_secrets(text: str) -> str:
    """Replace discovered secrets with safe placeholders."""
    redacted = text
    for pattern, secret_type in SECRET_PATTERNS:
        redacted = re.sub(pattern, f"[REDACTED:{secret_type}]", redacted)
    return redacted
