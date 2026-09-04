"""Native Google Gemini provider implementing LLMProvider protocol."""

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

_GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiProvider(LLMProvider):
    """Native Google Gemini API provider using Generative Language REST interface."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        default_model: Optional[str] = None,
    ):
        self.api_key = api_key or getattr(settings, "GEMINI_API_KEY", "")
        self._default_model = default_model or getattr(settings, "GEMINI_MODEL", "gemini-1.5-pro")

    @property
    def provider_name(self) -> str:
        return "gemini"

    @property
    def default_model(self) -> str:
        return self._default_model

    def validate_model(self, model_name: str) -> bool:
        return "gemini" in model_name.lower()

    def capabilities(self, model: Optional[str] = None) -> LLMCapabilities:
        return LLMCapabilities(
            structured_output=True,
            json_mode=True,
            tool_calling=True,
            vision=True,
            reasoning="pro" in (model or self._default_model).lower(),
            max_context_tokens=1000000,
            token_usage_reporting=True,
        )

    async def generate(self, request: LLMRequest) -> LLMResponse:
        """Execute text completion over Gemini REST API."""
        if not self.api_key:
            raise AuthenticationError("Gemini API key is missing. Set GEMINI_API_KEY.", provider=self.provider_name)

        model = request.model or self._default_model
        start_time = time.time()
        url = f"{_GEMINI_API_BASE}/{model}:generateContent?key={self.api_key}"

        payload: Dict[str, Any] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": f"System Instruction:\n{request.system_prompt}\n\nTask:\n{request.user_prompt}"}],
                }
            ],
            "generationConfig": {
                "temperature": request.temperature,
            },
        }
        timeout_sec = float(getattr(settings, "LLM_TIMEOUT_SECONDS", 120))

        async def _call() -> LLMResponse:
            try:
                async with httpx.AsyncClient(timeout=timeout_sec) as client:
                    resp = await client.post(url, json=payload)
                    resp.raise_for_status()
                    data = resp.json()

                candidates = data.get("candidates", [])
                content = ""
                finish_reason = None
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    content = "".join(p.get("text", "") for p in parts)
                    finish_reason = candidates[0].get("finishReason")

                usage = data.get("usageMetadata", {})
                input_tokens = usage.get("promptTokenCount", 0)
                output_tokens = usage.get("candidatesTokenCount", 0)
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
        """Execute structured JSON completion using Gemini responseMimeType + Pydantic validation."""
        if not self.api_key:
            raise AuthenticationError("Gemini API key is missing. Set GEMINI_API_KEY.", provider=self.provider_name)

        model = request.model or self._default_model
        start_time = time.time()
        url = f"{_GEMINI_API_BASE}/{model}:generateContent?key={self.api_key}"

        json_schema = StructuredOutputParser.get_json_schema(schema)
        payload: Dict[str, Any] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": (
                                f"System Instruction:\n{request.system_prompt}\n\n"
                                f"CRITICAL: Output ONLY valid JSON adhering to this schema:\n"
                                f"{json.dumps(json_schema, indent=2)}\n\n"
                                f"Task:\n{request.user_prompt}"
                            )
                        }
                    ],
                }
            ],
            "generationConfig": {
                "temperature": request.temperature,
                "responseMimeType": "application/json",
            },
        }
        if request.max_tokens:
            payload["generationConfig"]["maxOutputTokens"] = request.max_tokens

        timeout_sec = float(getattr(settings, "LLM_TIMEOUT_SECONDS", 120))

        async def _call() -> LLMResponse:
            try:
                async with httpx.AsyncClient(timeout=timeout_sec) as client:
                    resp = await client.post(url, json=payload)
                    resp.raise_for_status()
                    data = resp.json()

                candidates = data.get("candidates", [])
                content = ""
                finish_reason = None
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    content = "".join(p.get("text", "") for p in parts)
                    finish_reason = candidates[0].get("finishReason")

                latency_ms = int((time.time() - start_time) * 1000)
                parsed = StructuredOutputParser.parse_and_validate(
                    content, schema, provider=self.provider_name, model=model
                )

                usage = data.get("usageMetadata", {})
                input_tokens = usage.get("promptTokenCount", 0)
                output_tokens = usage.get("candidatesTokenCount", 0)
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
                    finish_reason=finish_reason,
                    latency_ms=latency_ms,
                    cost_usd=cost,
                    request_id=request.request_id,
                )
            except Exception as e:
                raise normalize_exception(e, provider=self.provider_name, model=model) from e

        return await execute_with_retry(_call, provider_name=self.provider_name)
