"""Native Anthropic Claude provider implementing LLMProvider protocol."""

import json
import time
from typing import Any, Dict, Optional, Type, TypeVar
import httpx
from pydantic import BaseModel

from backend.config import settings
from backend.llm.errors import AuthenticationError, normalize_exception
from backend.llm.pricing import ModelPricingRegistry
from backend.llm.protocol import LLMCapabilities, LLMProvider, LLMRequest, LLMResponse, StructuredLLMRequest
from backend.llm.retry import execute_with_retry
from backend.llm.structured import StructuredOutputParser
from backend.observability.logging import get_logger
from backend.observability.metrics import MetricsTracker

logger = get_logger(__name__)
T = TypeVar("T", bound=BaseModel)

_ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"


class AnthropicProvider(LLMProvider):
    """Native Anthropic API provider with Messages API and Tool-Calling structured output."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        default_model: Optional[str] = None,
    ):
        self.api_key = api_key or getattr(settings, "ANTHROPIC_API_KEY", "")
        self._default_model = default_model or getattr(settings, "ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")

    @property
    def provider_name(self) -> str:
        return "anthropic"

    @property
    def default_model(self) -> str:
        return self._default_model

    def validate_model(self, model_name: str) -> bool:
        return "claude" in model_name.lower()

    def capabilities(self, model: Optional[str] = None) -> LLMCapabilities:
        return LLMCapabilities(
            structured_output=True,
            json_mode=True,
            tool_calling=True,
            vision=True,
            reasoning=True,
            max_context_tokens=200000,
            token_usage_reporting=True,
        )

    def _headers(self) -> Dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    async def generate(self, request: LLMRequest) -> LLMResponse:
        """Execute standard completion over Anthropic Messages API."""
        if not self.api_key:
            raise AuthenticationError("Anthropic API key is missing. Set ANTHROPIC_API_KEY.", provider=self.provider_name)

        model = request.model or self._default_model
        start_time = time.time()

        payload: Dict[str, Any] = {
            "model": model,
            "system": request.system_prompt,
            "messages": [{"role": "user", "content": request.user_prompt}],
            "max_tokens": request.max_tokens or 4096,
            "temperature": request.temperature,
        }

        timeout_sec = float(getattr(settings, "LLM_TIMEOUT_SECONDS", 120))

        async def _call() -> LLMResponse:
            try:
                async with httpx.AsyncClient(timeout=timeout_sec) as client:
                    resp = await client.post(_ANTHROPIC_API_URL, headers=self._headers(), json=payload)
                    resp.raise_for_status()
                    data = resp.json()

                content_blocks = data.get("content", [])
                content = "".join(b.get("text", "") for b in content_blocks if b.get("type") == "text")
                finish_reason = data.get("stop_reason")

                usage = data.get("usage", {})
                input_tokens = usage.get("input_tokens", 0)
                output_tokens = usage.get("output_tokens", 0)
                latency_ms = int((time.time() - start_time) * 1000)
                cost = ModelPricingRegistry.calculate_cost(model, input_tokens, output_tokens)

                MetricsTracker.record_llm_usage(model, input_tokens, output_tokens, cost)

                return LLMResponse(
                    content=content,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=input_tokens + output_tokens,
                    usage_source="provider_reported" if usage else "unavailable",
                    model=model,
                    provider=self.provider_name,
                    finish_reason=finish_reason,
                    latency_ms=latency_ms,
                    cost_usd=cost,
                    request_id=request.request_id,
                )
            except Exception as e:
                raise normalize_exception(e, provider=self.provider_name, model=model) from e

        return await execute_with_retry(_call, provider_name=self.provider_name)

    async def generate_structured(self, request: StructuredLLMRequest, schema: Type[T]) -> LLMResponse:
        """Execute structured JSON completion using Anthropic tool-use schema enforcement."""
        if not self.api_key:
            raise AuthenticationError("Anthropic API key is missing. Set ANTHROPIC_API_KEY.", provider=self.provider_name)

        model = request.model or self._default_model
        start_time = time.time()

        tool_name = schema.__name__
        tool_def = {
            "name": tool_name,
            "description": f"Structured output adhering to {tool_name}",
            "input_schema": StructuredOutputParser.get_json_schema(schema),
        }

        payload: Dict[str, Any] = {
            "model": model,
            "system": request.system_prompt,
            "messages": [{"role": "user", "content": request.user_prompt}],
            "tools": [tool_def],
            "tool_choice": {"type": "tool", "name": tool_name},
            "max_tokens": request.max_tokens or 4096,
            "temperature": request.temperature,
        }

        timeout_sec = float(getattr(settings, "LLM_TIMEOUT_SECONDS", 120))

        async def _call() -> LLMResponse:
            try:
                async with httpx.AsyncClient(timeout=timeout_sec) as client:
                    resp = await client.post(_ANTHROPIC_API_URL, headers=self._headers(), json=payload)
                    resp.raise_for_status()
                    data = resp.json()

                content_blocks = data.get("content", [])
                parsed: Optional[T] = None
                content = ""

                for b in content_blocks:
                    if b.get("type") == "tool_use" and b.get("name") == tool_name:
                        tool_input = b.get("input", {})
                        content = json.dumps(tool_input)
                        parsed = StructuredOutputParser.parse_and_validate(
                            content, schema, provider=self.provider_name, model=model
                        )
                        break
                    elif b.get("type") == "text":
                        content += b.get("text", "")

                if parsed is None and content:
                    parsed = StructuredOutputParser.parse_and_validate(
                        content, schema, provider=self.provider_name, model=model
                    )

                latency_ms = int((time.time() - start_time) * 1000)
                usage = data.get("usage", {})
                input_tokens = usage.get("input_tokens", 0)
                output_tokens = usage.get("output_tokens", 0)
                cost = ModelPricingRegistry.calculate_cost(model, input_tokens, output_tokens)

                MetricsTracker.record_llm_usage(model, input_tokens, output_tokens, cost)

                return LLMResponse(
                    content=content,
                    parsed=parsed,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=input_tokens + output_tokens,
                    usage_source="provider_reported" if usage else "unavailable",
                    model=model,
                    provider=self.provider_name,
                    finish_reason=data.get("stop_reason"),
                    latency_ms=latency_ms,
                    cost_usd=cost,
                    request_id=request.request_id,
                )
            except Exception as e:
                raise normalize_exception(e, provider=self.provider_name, model=model) from e

        return await execute_with_retry(_call, provider_name=self.provider_name)
