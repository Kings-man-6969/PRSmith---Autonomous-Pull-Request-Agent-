"""Factory for creating LLMProvider instances with explicit configuration and security."""

from typing import Dict, Optional, TypedDict
from backend.config import settings
from backend.llm.errors import InvalidRequestError
from backend.llm.protocol import LLMProvider
from backend.llm.providers.anthropic import AnthropicProvider
from backend.llm.providers.gemini import GeminiProvider
from backend.llm.providers.openai import OpenAIProvider
from backend.llm.providers.openai_compatible import OpenAICompatibleProvider
from backend.observability.logging import get_logger

logger = get_logger(__name__)


class ProviderPreset(TypedDict):
    base_url: str
    default_model: str
    env_key: Optional[str]


# Predefined base URLs for OpenAI-compatible services
OPENAI_COMPATIBLE_DEFAULTS: Dict[str, ProviderPreset] = {
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-chat",
        "env_key": "DEEPSEEK_API_KEY",
    },
    "qwen": {
        "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
        "env_key": "QWEN_API_KEY",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "anthropic/claude-3.5-sonnet",
        "env_key": "OPENROUTER_API_KEY",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "llama-3.3-70b-versatile",
        "env_key": "GROQ_API_KEY",
    },
    "together": {
        "base_url": "https://api.together.xyz/v1",
        "default_model": "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo",
        "env_key": "TOGETHER_API_KEY",
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "default_model": "qwen2.5-coder:latest",
        "env_key": None,
    },
    "vllm": {
        "base_url": "http://localhost:8000/v1",
        "default_model": "default",
        "env_key": None,
    },
}


def get_llm_provider(
    provider_name: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    default_model: Optional[str] = None,
    allow_local_endpoints: bool = False,
    extra_headers: Optional[Dict[str, str]] = None,
) -> LLMProvider:
    """
    Instantiate and return the configured LLMProvider instance.
    Explicit provider selection is prioritized.
    """
    target_provider = (provider_name or getattr(settings, "LLM_PROVIDER", "openai")).lower().strip()

    # 1. Native OpenAI
    if target_provider == "openai":
        return OpenAIProvider(
            api_key=api_key or getattr(settings, "OPENAI_API_KEY", ""),
            default_model=default_model or getattr(settings, "OPENAI_MODEL", "gpt-4o"),
        )

    # 2. Native Gemini
    if target_provider == "gemini":
        return GeminiProvider(
            api_key=api_key or getattr(settings, "GEMINI_API_KEY", ""),
            default_model=default_model or getattr(settings, "GEMINI_MODEL", "gemini-1.5-pro"),
        )

    # 3. Native Anthropic
    if target_provider == "anthropic":
        return AnthropicProvider(
            api_key=api_key or getattr(settings, "ANTHROPIC_API_KEY", ""),
            default_model=default_model or getattr(settings, "ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"),
        )

    # 4. Known OpenAI-compatible providers (DeepSeek, Qwen, OpenRouter, Groq, Ollama, vLLM)
    if target_provider in OPENAI_COMPATIBLE_DEFAULTS:
        preset = OPENAI_COMPATIBLE_DEFAULTS[target_provider]
        env_key_name = preset["env_key"]
        resolved_key = api_key or (getattr(settings, env_key_name, "") if env_key_name else "")
        resolved_base_url = base_url or preset["base_url"]
        resolved_model = default_model or preset["default_model"]
        is_local = target_provider in ("ollama", "vllm") or allow_local_endpoints

        return OpenAICompatibleProvider(
            provider_name=target_provider,
            base_url=resolved_base_url,
            api_key=resolved_key,
            default_model=resolved_model,
            allow_local_endpoints=is_local,
            extra_headers=extra_headers,
        )

    # 5. Generic Custom OpenAI-compatible gateway
    if target_provider in ("custom", "openai_compatible", "generic"):
        resolved_base_url = base_url or getattr(settings, "CUSTOM_BASE_URL", "")
        if not resolved_base_url:
            raise InvalidRequestError("Custom provider selected but CUSTOM_BASE_URL is not set.")

        resolved_key = api_key or getattr(settings, "CUSTOM_API_KEY", "")
        resolved_model = default_model or getattr(settings, "CUSTOM_MODEL", "default")

        return OpenAICompatibleProvider(
            provider_name="custom",
            base_url=resolved_base_url,
            api_key=resolved_key,
            default_model=resolved_model,
            allow_local_endpoints=allow_local_endpoints or getattr(settings, "ALLOW_LOCAL_CUSTOM_ENDPOINTS", False),
            extra_headers=extra_headers,
        )

    raise InvalidRequestError(f"Unsupported LLM provider requested: '{target_provider}'.")
