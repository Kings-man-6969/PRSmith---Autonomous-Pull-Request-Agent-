"""End-to-end mock generation tests across all providers."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from pydantic import BaseModel

from backend.llm.protocol import LLMRequest, StructuredLLMRequest
from backend.llm.providers.anthropic import AnthropicProvider
from backend.llm.providers.gemini import GeminiProvider
from backend.llm.providers.openai import OpenAIProvider
from backend.llm.providers.openai_compatible import OpenAICompatibleProvider


class MockFinding(BaseModel):
    title: str
    severity: str


def test_openai_mock_generate():
    pytest.importorskip("openai")
    provider = OpenAIProvider(api_key="test-sk")
    assert provider.client is not None

    mock_response = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = "All checks passed"
    mock_choice.finish_reason = "stop"
    mock_response.choices = [mock_choice]
    mock_response.usage.prompt_tokens = 100
    mock_response.usage.completion_tokens = 20

    with patch.object(provider.client.chat.completions, "create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = mock_response
        req = LLMRequest(system_prompt="System", user_prompt="User")
        res = asyncio.run(provider.generate(req))
        assert res.content == "All checks passed"
        assert res.input_tokens == 100
        assert res.output_tokens == 20
        assert res.model == "gpt-4o"
        assert res.provider == "openai"


def test_openai_compatible_mock_structured():
    pytest.importorskip("openai")
    provider = OpenAICompatibleProvider(
        provider_name="deepseek",
        base_url="https://api.deepseek.com",
        api_key="test-ds-key",
        default_model="deepseek-chat",
    )
    mock_response = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = '{"title": "SQL Injection", "severity": "HIGH"}'
    mock_choice.finish_reason = "stop"
    mock_response.choices = [mock_choice]
    mock_response.usage.prompt_tokens = 200
    mock_response.usage.completion_tokens = 40

    with patch.object(provider.client.chat.completions, "create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = mock_response
        req = StructuredLLMRequest(system_prompt="System", user_prompt="User")
        res = asyncio.run(provider.generate_structured(req, MockFinding))
        assert res.parsed is not None
        assert isinstance(res.parsed, MockFinding)
        assert res.parsed.title == "SQL Injection"
        assert res.parsed.severity == "HIGH"
        assert res.provider == "deepseek"


def test_gemini_mock_generate_structured():
    provider = GeminiProvider(api_key="test-gemini-key", default_model="gemini-1.5-flash")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value={
        "candidates": [
            {
                "content": {
                    "parts": [{"text": '{"title": "Buffer Overflow", "severity": "CRITICAL"}'}]
                },
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 150,
            "candidatesTokenCount": 35,
        },
    })

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        req = StructuredLLMRequest(system_prompt="System", user_prompt="User")
        res = asyncio.run(provider.generate_structured(req, MockFinding))
        assert res.parsed is not None
        assert isinstance(res.parsed, MockFinding)
        assert res.parsed.title == "Buffer Overflow"
        assert res.parsed.severity == "CRITICAL"
        assert res.provider == "gemini"
        assert res.input_tokens == 150


def test_anthropic_mock_generate_structured():
    provider = AnthropicProvider(api_key="test-anthropic-key", default_model="claude-3-5-sonnet-20241022")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value={
        "content": [
            {
                "type": "tool_use",
                "name": "MockFinding",
                "input": {"title": "XSS Vulnerability", "severity": "MEDIUM"},
            }
        ],
        "stop_reason": "tool_use",
        "usage": {
            "input_tokens": 300,
            "output_tokens": 50,
        },
    })

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        req = StructuredLLMRequest(system_prompt="System", user_prompt="User")
        res = asyncio.run(provider.generate_structured(req, MockFinding))
        assert res.parsed is not None
        assert isinstance(res.parsed, MockFinding)
        assert res.parsed.title == "XSS Vulnerability"
        assert res.parsed.severity == "MEDIUM"
        assert res.provider == "anthropic"
        assert res.input_tokens == 300
