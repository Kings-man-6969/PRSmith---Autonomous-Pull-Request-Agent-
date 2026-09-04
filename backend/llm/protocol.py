"""Core LLM Provider Protocol and normalized Data Transfer Objects."""

from typing import Any, Dict, List, Literal, Optional, Protocol, Type, TypeVar, runtime_checkable
from pydantic import BaseModel, Field

T = TypeVar("T", bound=BaseModel)

UsageSource = Literal["provider_reported", "estimated", "unavailable"]


class LLMCapabilities(BaseModel):
    """Declarative capability matrix for an LLM provider and model."""

    structured_output: bool = False
    json_mode: bool = True
    tool_calling: bool = False
    vision: bool = False
    streaming: bool = False
    reasoning: bool = False
    max_context_tokens: int = 128000
    token_usage_reporting: bool = True


class LLMRequest(BaseModel):
    """Normalized input request for standard completions."""

    system_prompt: str
    user_prompt: str
    model: Optional[str] = None
    temperature: float = 0.1
    max_tokens: Optional[int] = None
    stop_sequences: List[str] = Field(default_factory=list)
    request_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class StructuredLLMRequest(LLMRequest):
    """Normalized input request requiring structured schema output."""

    schema_name: Optional[str] = None
    schema_description: Optional[str] = None


class LLMResponse(BaseModel):
    """Normalized output container across all LLM providers."""

    model_config = {"protected_namespaces": ()}

    content: str
    parsed: Optional[Any] = None

    # Token Accounting
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    usage_source: UsageSource = "unavailable"

    # Execution Metadata
    model: str
    provider: str
    finish_reason: Optional[str] = None
    latency_ms: int = 0
    cost_usd: float = 0.0
    request_id: Optional[str] = None
    retries_attempted: int = 0
    fallback_used: bool = False


@runtime_checkable
class LLMProvider(Protocol):
    """Unified, provider-agnostic protocol for all LLM backends."""

    async def generate(self, request: LLMRequest) -> LLMResponse:
        """Execute a text-based completion request."""
        ...

    async def generate_structured(
        self, request: StructuredLLMRequest, schema: Type[T]
    ) -> LLMResponse:
        """Execute a structured completion request validated against a Pydantic schema."""
        ...

    def capabilities(self, model: Optional[str] = None) -> LLMCapabilities:
        """Return the capability matrix for this provider/model."""
        ...

    def validate_model(self, model_name: str) -> bool:
        """Verify if a model identifier is supported by this provider."""
        ...

    @property
    def provider_name(self) -> str:
        """Unique identifier of the provider (e.g. 'openai', 'gemini', 'anthropic', 'openai_compatible')."""
        ...

    @property
    def default_model(self) -> str:
        """Default model name configured for this provider."""
        ...
