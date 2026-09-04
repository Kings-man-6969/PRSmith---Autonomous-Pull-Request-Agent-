"""Token encryption utilities using Fernet symmetric encryption."""

from cryptography.fernet import Fernet

from backend.config import settings


def _get_fernet() -> Fernet:
    """Instantiate and return Fernet cipher with GITHUB_TOKEN_ENCRYPTION_KEY."""
    key = settings.GITHUB_TOKEN_ENCRYPTION_KEY
    if not key:
        raise RuntimeError(
            "GITHUB_TOKEN_ENCRYPTION_KEY is not configured. "
            'Generate one with: python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        )
    return Fernet(key.strip().encode())


def validate_encryption_key() -> None:
    """Validate that GITHUB_TOKEN_ENCRYPTION_KEY is valid and non-empty.

    Called during application startup to fail fast if misconfigured.
    """
    _get_fernet()


def encrypt_token(plain_token: str) -> str:
    """Encrypt a plaintext token string using Fernet at rest."""
    if not plain_token:
        return ""
    fernet = _get_fernet()
    return fernet.encrypt(plain_token.encode("utf-8")).decode("utf-8")


def decrypt_token(encrypted_token: str) -> str:
    """Decrypt an encrypted token string into plaintext."""
    if not encrypted_token:
        return ""
    fernet = _get_fernet()
    return fernet.decrypt(encrypted_token.encode("utf-8")).decode("utf-8")
