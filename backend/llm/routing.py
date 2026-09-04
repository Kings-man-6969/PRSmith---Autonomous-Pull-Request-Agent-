"""Capability-aware and Task-based Model Routing for PRSmith."""

from enum import Enum
from typing import Dict, Optional
from backend.config import settings
from backend.llm.factory import get_llm_provider
from backend.llm.protocol import LLMProvider


class TaskType(str, Enum):
    REVIEW = "review"
    REPAIR = "repair"
    FAILURE_ANALYSIS = "failure_analysis"
    CONTEXT_SUMMARIZATION = "context_summarization"
    STRUCTURED_EXTRACTION = "structured_extraction"


class ModelRouter:
    """Selects the optimal model profile and provider for a given task."""

    def __init__(self, default_provider: Optional[LLMProvider] = None):
        self.provider = default_provider or get_llm_provider()

    def route_model(
        self,
        task_type: TaskType = TaskType.REVIEW,
        risk_level: str = "LOW",
        is_complex: bool = False,
    ) -> str:
        """
        Route to appropriate model identifier based on task complexity,
        risk class, and provider capabilities.
        """
        provider_name = self.provider.provider_name.lower()

        # High-capability reasoning tasks (CRITICAL/HIGH risk or complex repair)
        requires_reasoning = risk_level in ("HIGH", "CRITICAL") or is_complex or task_type == TaskType.REPAIR

        if provider_name == "openai":
            return getattr(settings, "OPENAI_MODEL", "gpt-4o") if requires_reasoning else getattr(settings, "OPENAI_FAST_MODEL", "gpt-4o-mini")

        if provider_name == "gemini":
            return getattr(settings, "GEMINI_MODEL", "gemini-1.5-pro") if requires_reasoning else getattr(settings, "GEMINI_FAST_MODEL", "gemini-1.5-flash")

        if provider_name == "anthropic":
            return getattr(settings, "ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022") if requires_reasoning else getattr(settings, "ANTHROPIC_FAST_MODEL", "claude-3-5-haiku-20241022")

        if provider_name == "deepseek":
            return getattr(settings, "DEEPSEEK_MODEL", "deepseek-chat")

        if provider_name == "qwen":
            return getattr(settings, "QWEN_MODEL", "qwen-plus") if requires_reasoning else getattr(settings, "QWEN_FAST_MODEL", "qwen-turbo")

        if provider_name in ("custom", "openai_compatible", "generic", "openrouter", "groq", "together", "ollama", "vllm"):
            custom_model = getattr(settings, "CUSTOM_MODEL", "")
            custom_fast = getattr(settings, "CUSTOM_FAST_MODEL", "")
            if requires_reasoning and custom_model and custom_model != "default":
                return custom_model
            if not requires_reasoning and custom_fast and custom_fast != "default":
                return custom_fast
            if custom_model and custom_model != "default":
                return custom_model

        return self.provider.default_model
