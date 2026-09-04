"""PRSmith LLM Abstraction Layer."""

from backend.llm.errors import (
    AuthenticationError,
    AuthorizationError,
    ContextLengthError,
    InvalidRequestError,
    LLMError,
    ModelNotFoundError,
    ProviderUnavailableError,
    RateLimitError,
    SSRFBlockedError,
    StructuredOutputError,
    TimeoutError,
    normalize_exception,
)
from backend.llm.factory import get_llm_provider
from backend.llm.pricing import ModelPricingRegistry
from backend.llm.protocol import (
    LLMCapabilities,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    StructuredLLMRequest,
)
from backend.llm.providers import (
    AnthropicProvider,
    GeminiProvider,
    OpenAICompatibleProvider,
    OpenAIProvider,
)
from backend.llm.retry import CircuitBreaker, execute_with_retry, get_circuit_breaker
from backend.llm.routing import ModelRouter, TaskType
from backend.llm.security import SSRFValidator, mask_secret, sanitize_headers
from backend.llm.structured import StructuredOutputParser

__all__ = [
    "LLMProvider",
    "LLMCapabilities",
    "LLMRequest",
    "StructuredLLMRequest",
    "LLMResponse",
    "LLMError",
    "AuthenticationError",
    "AuthorizationError",
    "RateLimitError",
    "TimeoutError",
    "ContextLengthError",
    "InvalidRequestError",
    "ModelNotFoundError",
    "ProviderUnavailableError",
    "StructuredOutputError",
    "SSRFBlockedError",
    "normalize_exception",
    "OpenAIProvider",
    "GeminiProvider",
    "AnthropicProvider",
    "OpenAICompatibleProvider",
    "get_llm_provider",
    "ModelPricingRegistry",
    "StructuredOutputParser",
    "ModelRouter",
    "TaskType",
    "CircuitBreaker",
    "get_circuit_breaker",
    "execute_with_retry",
    "SSRFValidator",
    "mask_secret",
    "sanitize_headers",
]
