"""Test LLMProvider protocol compliance and factory instantiation across all provider implementations."""

from backend.llm.factory import get_llm_provider
from backend.llm.protocol import LLMProvider
from backend.llm.providers.anthropic import AnthropicProvider
from backend.llm.providers.gemini import GeminiProvider
from backend.llm.providers.openai import OpenAIProvider
from backend.llm.providers.openai_compatible import OpenAICompatibleProvider
from backend.llm.routing import ModelRouter, TaskType


def test_all_providers_satisfy_protocol():
    providers = [
        OpenAIProvider(api_key="test-key"),
        GeminiProvider(api_key="test-key"),
        AnthropicProvider(api_key="test-key"),
        OpenAICompatibleProvider(
            provider_name="deepseek",
            base_url="https://api.deepseek.com",
            api_key="test-key",
        ),
        OpenAICompatibleProvider(
            provider_name="qwen",
            base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            api_key="test-key",
        ),
    ]

    for p in providers:
        assert isinstance(p, LLMProvider)
        assert p.provider_name
        assert p.default_model
        assert p.validate_model(p.default_model)
        caps = p.capabilities()
        assert isinstance(caps.json_mode, bool)
        assert isinstance(caps.max_context_tokens, int)


def test_custom_openrouter_factory_and_routing():
    provider = get_llm_provider(
        provider_name="custom",
        api_key="sk-or-v1-test",
        base_url="https://openrouter.ai/api/v1",
        default_model="z-ai/glm-5.2:free",
    )
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.provider_name == "custom"
    assert provider.base_url == "https://openrouter.ai/api/v1"

    router = ModelRouter(provider)
    # High risk -> routes to custom reasoning model
    routed_high = router.route_model(task_type=TaskType.REVIEW, risk_level="HIGH")
    assert "glm" in routed_high.lower() or routed_high == provider.default_model

    # Low risk -> routes to fast model
    routed_low = router.route_model(task_type=TaskType.REVIEW, risk_level="LOW")
    assert "qwen" in routed_low.lower() or routed_low == provider.default_model
