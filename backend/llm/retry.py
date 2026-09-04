"""Error-aware exponential backoff retries, concurrency limiting, and circuit breaking."""

import asyncio
import random
import time
from typing import Callable, Coroutine, Dict, Optional, TypeVar

from backend.config import settings
from backend.llm.errors import (
    AuthenticationError,
    AuthorizationError,
    ContextLengthError,
    InvalidRequestError,
    LLMError,
    ProviderUnavailableError,
    RateLimitError,
    SSRFBlockedError,
)
from backend.observability.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


class CircuitBreaker:
    """Protects against cascading provider outages by fast-failing once tripped."""

    def __init__(self, failure_threshold: int = 5, recovery_timeout_seconds: float = 60.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self.consecutive_failures = 0
        self.last_failure_time = 0.0
        self.state: str = "CLOSED"  # CLOSED, OPEN, HALF_OPEN

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.state = "CLOSED"

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        self.last_failure_time = time.time()
        if self.consecutive_failures >= self.failure_threshold:
            self.state = "OPEN"
            logger.warning(
                "Circuit breaker tripped OPEN",
                consecutive_failures=self.consecutive_failures,
            )

    def can_execute(self) -> bool:
        if self.state == "CLOSED":
            return True
        if self.state == "OPEN":
            if time.time() - self.last_failure_time > self.recovery_timeout_seconds:
                self.state = "HALF_OPEN"
                return True
            return False
        # HALF_OPEN allows a trial request
        return True


_GLOBAL_CIRCUIT_BREAKERS: Dict[str, CircuitBreaker] = {}
_GLOBAL_SEMAPHORES: Dict[str, asyncio.Semaphore] = {}
_LAST_REQUEST_TIME: Dict[str, float] = {}


def get_circuit_breaker(provider_name: str) -> CircuitBreaker:
    if provider_name not in _GLOBAL_CIRCUIT_BREAKERS:
        _GLOBAL_CIRCUIT_BREAKERS[provider_name] = CircuitBreaker()
    return _GLOBAL_CIRCUIT_BREAKERS[provider_name]


def get_semaphore(provider_name: str) -> asyncio.Semaphore:
    if provider_name not in _GLOBAL_SEMAPHORES:
        max_conc = getattr(settings, "LLM_MAX_CONCURRENCY", 2)
        _GLOBAL_SEMAPHORES[provider_name] = asyncio.Semaphore(max_conc)
    return _GLOBAL_SEMAPHORES[provider_name]


async def execute_with_retry(
    fn: Callable[[], Coroutine[None, None, T]],
    provider_name: str,
    max_retries: int = 4,
    initial_delay: float = 2.0,
    max_delay: float = 60.0,
    backoff_factor: float = 2.0,
) -> T:
    """
    Execute async function with concurrency limiting and intelligent error-aware exponential backoff.
    Never retries non-retryable errors (auth, validation, SSRF).
    """
    cb = get_circuit_breaker(provider_name)
    if not cb.can_execute():
        raise ProviderUnavailableError(
            f"Circuit breaker for provider '{provider_name}' is OPEN due to repeated failures. Request rejected.",
            provider=provider_name,
        )

    sem = get_semaphore(provider_name)
    delay = initial_delay
    last_error: Optional[Exception] = None

    async with sem:
        # Enforce minimum spacing between calls to prevent rate-limit bursts
        min_interval = getattr(settings, "LLM_MIN_REQUEST_INTERVAL", 0.3)
        last_t = _LAST_REQUEST_TIME.get(provider_name, 0.0)
        elapsed = time.time() - last_t
        if elapsed < min_interval:
            await asyncio.sleep(min_interval - elapsed)
        _LAST_REQUEST_TIME[provider_name] = time.time()

        for attempt in range(1, max_retries + 1):
            try:
                result = await fn()
                cb.record_success()
                return result
            except LLMError as exc:
                last_error = exc
                cb.record_failure()

                # Non-retryable errors: fail immediately
                if isinstance(exc, (AuthenticationError, AuthorizationError, InvalidRequestError, ContextLengthError, SSRFBlockedError)):
                    logger.error("Non-retryable LLM error encountered", error_type=type(exc).__name__, error=str(exc))
                    raise exc

                # If last attempt, don't sleep
                if attempt == max_retries:
                    logger.error("Max retries exceeded for LLM call", provider=provider_name, attempts=attempt, error=str(exc))
                    raise exc

                # Calculate sleep duration (adaptive for 429 rate limits)
                if isinstance(exc, RateLimitError) and exc.retry_after_seconds:
                    sleep_time = exc.retry_after_seconds
                elif isinstance(exc, RateLimitError):
                    # Rate limit encountered without retry-after header -> start with at least 3.0s delay
                    sleep_time = max(3.0, delay) * (1.0 + random.uniform(0.1, 0.4))
                    delay = max(delay * backoff_factor, 6.0)
                else:
                    # Exponential backoff with jitter
                    sleep_time = min(max_delay, delay * (1.0 + random.uniform(0.0, 0.2)))
                    delay *= backoff_factor

                logger.warning(
                    "Retryable LLM error, backing off",
                    provider=provider_name,
                    attempt=attempt,
                    next_delay=round(sleep_time, 2),
                    error=str(exc),
                )
                await asyncio.sleep(sleep_time)

            except Exception as exc:
                cb.record_failure()
                logger.error("Unexpected error in LLM execution", error=str(exc))
                raise exc

    if last_error:
        raise last_error
    raise ProviderUnavailableError("Operation failed without explicit exception.", provider=provider_name)
