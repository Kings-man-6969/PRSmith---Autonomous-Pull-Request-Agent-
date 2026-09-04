import pytest
from cryptography.fernet import Fernet, InvalidToken

from backend.auth.token_encryption import (
    _get_fernet,
    decrypt_token,
    encrypt_token,
    validate_encryption_key,
)
from backend.config import settings


@pytest.fixture(autouse=True)
def setup_encryption_key(monkeypatch):
    test_key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "GITHUB_TOKEN_ENCRYPTION_KEY", test_key)


def test_encrypt_token_produces_non_plaintext():
    token = "ghp_secretToken1234567890"
    encrypted = encrypt_token(token)
    assert encrypted != token
    assert isinstance(encrypted, str)
    assert len(encrypted) > len(token)


def test_decrypt_token_recovers_original():
    token = "ghp_anotherSecretToken987654321"
    encrypted = encrypt_token(token)
    decrypted = decrypt_token(encrypted)
    assert decrypted == token


def test_decrypt_token_empty_string():
    assert decrypt_token("") == ""
    assert encrypt_token("") == ""


def test_decrypt_token_raises_on_tampered():
    token = "ghp_secretToken"
    encrypted = encrypt_token(token)
    # Tamper with the ciphertext
    tampered = encrypted[:-4] + "AAAA"
    with pytest.raises(InvalidToken):
        decrypt_token(tampered)


def test_get_fernet_raises_runtime_error_when_key_missing(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_TOKEN_ENCRYPTION_KEY", "")
    with pytest.raises(RuntimeError, match="GITHUB_TOKEN_ENCRYPTION_KEY is not configured"):
        _get_fernet()


def test_validate_encryption_key_fails_fast_when_key_missing(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_TOKEN_ENCRYPTION_KEY", "")
    with pytest.raises(RuntimeError, match="GITHUB_TOKEN_ENCRYPTION_KEY is not configured"):
        validate_encryption_key()


def test_validate_encryption_key_succeeds_when_key_present():
    # fixture already sets valid key
    validate_encryption_key()
