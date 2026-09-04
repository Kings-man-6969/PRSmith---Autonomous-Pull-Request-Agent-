"""Abstract base class for LLM providers."""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Type, TypeVar
from pydantic import BaseModel, Field

T = TypeVar("T", bound=BaseModel)


class LLMResponse(BaseModel):
    """Container for model output with execution metadata."""

    model_config = {"protected_namespaces": ()}

    content: str
    parsed: Optional[Any] = None
    model_name: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0


class LLMProvider(ABC):
    """Abstract interface for interacting with large language models."""

    @abstractmethod
    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: Optional[Type[T]] = None,
        model: Optional[str] = None,
        temperature: float = 0.1,
    ) -> LLMResponse:
        """Execute a completion request with optional structured JSON schema enforcement."""
        pass

    @abstractmethod
    def estimate_tokens(self, text: str) -> int:
        """Estimate token count for a given text string."""
        pass

    @property
    @abstractmethod
    def default_model(self) -> str:
        """Get the default model name."""
        pass
