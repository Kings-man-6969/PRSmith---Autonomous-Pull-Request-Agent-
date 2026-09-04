"""Tests for error-aware retry mechanism and circuit breaker."""

import asyncio
import pytest
from backend.llm.errors import AuthenticationError, RateLimitError
from backend.llm.retry import CircuitBreaker, execute_with_retry


def test_non_retryable_errors_fail_immediately():
    call_count = 0

    async def _failing():
        nonlocal call_count
        call_count += 1
        raise AuthenticationError("Invalid API key", provider="test")

    with pytest.raises(AuthenticationError):
        asyncio.run(execute_with_retry(_failing, provider_name="test_auth_fail", max_retries=3))

    assert call_count == 1  # Did NOT retry


def test_retryable_error_succeeds_on_subsequent_attempt():
    call_count = 0

    async def _eventual_success():
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise RateLimitError("Too many requests", provider="test", retry_after_seconds=0.01)
        return "success"

    res = asyncio.run(
        execute_with_retry(
            _eventual_success,
            provider_name="test_ratelimit_success",
            max_retries=3,
            initial_delay=0.01,
        )
    )
    assert res == "success"
    assert call_count == 2


def test_circuit_breaker_trips_open_after_consecutive_failures():
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout_seconds=60.0)
    assert cb.can_execute()

    cb.record_failure()
    cb.record_failure()
    assert cb.state == "CLOSED"
    assert cb.can_execute()

    cb.record_failure()
    assert cb.state == "OPEN"
    assert not cb.can_execute()

    cb.record_success()
    assert cb.state == "CLOSED"
    assert cb.can_execute()
