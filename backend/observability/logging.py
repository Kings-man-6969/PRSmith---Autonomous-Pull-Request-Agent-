"""Structured JSON logging with event taxonomy for PRSmith v2."""

import logging
import sys
from typing import Any, Optional
import structlog


def configure_logging(log_level: str = "INFO") -> None:
    """Configure structlog for structured JSON logging in production and development."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: Optional[str] = None) -> structlog.BoundLogger:
    """Get a bound structured logger."""
    return structlog.get_logger(name or "prsmith")


# Configure default logging at import time
configure_logging()
logger = get_logger()


def log_event(event_name: str, **kwargs: Any) -> None:
    """Log a predefined lifecycle event with structured metadata."""
    if "event" in kwargs:
        kwargs["event_type"] = kwargs.pop("event")
    logger.info(event=event_name, **kwargs)
