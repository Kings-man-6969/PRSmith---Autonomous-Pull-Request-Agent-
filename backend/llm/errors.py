"""Normalized error taxonomy for all LLM providers and HTTP backends."""

from typing import Optional


class LLMError(Exception):
    """Base exception for all LLM-related errors."""

    def __init__(
        self,
        message: str,
        provider: str = "unknown",
        model: Optional[str] = None,
        status_code: Optional[int] = None,
        raw_error: Optional[Exception] = None,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.model = model
        self.status_code = status_code
        self.raw_error = raw_error
        self.retryable = retryable


class AuthenticationError(LLMError):
    """Invalid API key, expired credentials, or missing authentication."""

    def __init__(self, message: str, provider: str = "unknown", **kwargs):
        super().__init__(message, provider=provider, status_code=401, retryable=False, **kwargs)


class AuthorizationError(LLMError):
    """Permission denied or unauthorized model access."""

    def __init__(self, message: str, provider: str = "unknown", **kwargs):
        super().__init__(message, provider=provider, status_code=403, retryable=False, **kwargs)


class RateLimitError(LLMError):
    """Exceeded provider rate limit, concurrency limit, or quota."""

    def __init__(
        self,
        message: str,
        provider: str = "unknown",
        retry_after_seconds: Optional[float] = None,
        **kwargs,
    ):
        super().__init__(message, provider=provider, status_code=429, retryable=True, **kwargs)
        self.retry_after_seconds = retry_after_seconds


class TimeoutError(LLMError):
    """Request exceeded connection or generation timeout."""

    def __init__(self, message: str, provider: str = "unknown", **kwargs):
        super().__init__(message, provider=provider, status_code=408, retryable=True, **kwargs)


class ContextLengthError(LLMError):
    """Prompt tokens exceeded model context window limit."""

    def __init__(
        self,
        message: str,
        provider: str = "unknown",
        max_tokens: Optional[int] = None,
        prompt_tokens: Optional[int] = None,
        **kwargs,
    ):
        super().__init__(message, provider=provider, status_code=400, retryable=False, **kwargs)
        self.max_tokens = max_tokens
        self.prompt_tokens = prompt_tokens


class InvalidRequestError(LLMError):
    """Malformed request payload, invalid parameters, or bad schema."""

    def __init__(self, message: str, provider: str = "unknown", **kwargs):
        super().__init__(message, provider=provider, status_code=400, retryable=False, **kwargs)


class ModelNotFoundError(LLMError):
    """Requested model identifier does not exist on provider."""

    def __init__(self, message: str, provider: str = "unknown", **kwargs):
        super().__init__(message, provider=provider, status_code=404, retryable=False, **kwargs)


class ProviderUnavailableError(LLMError):
    """Provider API outage, bad gateway, or internal server error."""

    def __init__(self, message: str, provider: str = "unknown", status_code: int = 503, **kwargs):
        super().__init__(message, provider=provider, status_code=status_code, retryable=True, **kwargs)


class StructuredOutputError(LLMError):
    """Failed to extract or parse response adhering to expected Pydantic schema."""

    def __init__(self, message: str, raw_content: str = "", provider: str = "unknown", **kwargs):
        super().__init__(message, provider=provider, retryable=True, **kwargs)
        self.raw_content = raw_content


class ContentFilterError(LLMError):
    """Generation blocked by safety filters or policy violation."""

    def __init__(self, message: str, provider: str = "unknown", **kwargs):
        super().__init__(message, provider=provider, retryable=False, **kwargs)


class SSRFBlockedError(LLMError):
    """Custom base URL was rejected by SSRF security policy."""

    def __init__(self, message: str, url: str = "", **kwargs):
        super().__init__(message, provider="security", retryable=False, **kwargs)
        self.url = url


def normalize_exception(exc: Exception, provider: str = "unknown", model: Optional[str] = None) -> LLMError:
    """Normalize vendor-specific SDK or HTTP exceptions into standard LLMError."""
    if isinstance(exc, LLMError):
        return exc

    err_str = str(exc).lower()

    # Rate limits (429)
    if "rate limit" in err_str or "429" in err_str or "quota" in err_str or "resource_exhausted" in err_str:
        return RateLimitError(str(exc), provider=provider, model=model, raw_error=exc)

    # Authentication (401)
    if "unauthorized" in err_str or "401" in err_str or "invalid api key" in err_str or "auth" in err_str:
        return AuthenticationError(str(exc), provider=provider, model=model, raw_error=exc)

    # Authorization (403)
    if "forbidden" in err_str or "403" in err_str or "permission" in err_str:
        return AuthorizationError(str(exc), provider=provider, model=model, raw_error=exc)

    # Not found (404)
    if "not found" in err_str or "404" in err_str or "model_not_found" in err_str:
        return ModelNotFoundError(str(exc), provider=provider, model=model, raw_error=exc)

    # Context window / token overflow
    if "context_length_exceeded" in err_str or "maximum context length" in err_str or "too many tokens" in err_str:
        return ContextLengthError(str(exc), provider=provider, model=model, raw_error=exc)

    # Timeouts
    if "timeout" in err_str or "timed out" in err_str or "deadline_exceeded" in err_str:
        return TimeoutError(str(exc), provider=provider, model=model, raw_error=exc)

    # Server errors (500/502/503/504)
    if any(code in err_str for code in ["500", "502", "503", "504", "internal server error", "unavailable"]):
        return ProviderUnavailableError(str(exc), provider=provider, model=model, raw_error=exc)

    # Content safety
    if "safety" in err_str or "blocked" in err_str or "content filter" in err_str or "harmful" in err_str:
        return ContentFilterError(str(exc), provider=provider, model=model, raw_error=exc)

    return LLMError(str(exc), provider=provider, model=model, raw_error=exc)
