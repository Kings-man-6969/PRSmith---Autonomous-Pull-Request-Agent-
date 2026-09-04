"""Universal OpenAI-compatible provider for DeepSeek, Qwen, Ollama, vLLM, OpenRouter, and Custom Gateways."""

import json
import time
from typing import Any, Dict, Optional, Type, TypeVar
try:
    import openai
except ImportError:
    openai = None
from pydantic import BaseModel

from backend.config import settings
from backend.llm.errors import AuthenticationError, normalize_exception
from backend.llm.pricing import ModelPricingRegistry
from backend.llm.protocol import LLMCapabilities, LLMProvider, LLMRequest, LLMResponse, StructuredLLMRequest
from backend.llm.retry import execute_with_retry
from backend.llm.security import SSRFValidator
from backend.llm.structured import StructuredOutputParser
from backend.observability.logging import get_logger
from backend.observability.metrics import MetricsTracker

logger = get_logger(__name__)
T = TypeVar("T", bound=BaseModel)


class OpenAICompatibleProvider(LLMProvider):
    """
    Standardized adapter for any OpenAI-compatible API endpoint.
    Handles DeepSeek, Qwen, Ollama, vLLM, OpenRouter, Groq, and custom gateways.
    """

    def __init__(
        self,
        provider_name: str = "openai_compatible",
        base_url: str = "https://api.deepseek.com",
        api_key: Optional[str] = None,
        default_model: str = "deepseek-chat",
        allow_local_endpoints: bool = False,
        extra_headers: Optional[Dict[str, str]] = None,
    ):
        self._provider_name = provider_name
        self._default_model = default_model
        self.api_key = api_key or ""

        # Validate URL against SSRF policy
        self.base_url = SSRFValidator.validate_url(base_url, allow_local=allow_local_endpoints)
        self.extra_headers = extra_headers or {}

        timeout = getattr(settings, "LLM_TIMEOUT_SECONDS", 120)
        self.client = (
            openai.AsyncOpenAI(
                api_key=self.api_key or "sk-placeholder",
                base_url=self.base_url,
                default_headers=self.extra_headers,
                timeout=float(timeout),
            )
            if openai is not None
            else None
        )

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def default_model(self) -> str:
        return self._default_model

    def validate_model(self, model_name: str) -> bool:
        return bool(model_name and model_name.strip())

    def capabilities(self, model: Optional[str] = None) -> LLMCapabilities:
        target = (model or self._default_model).lower()
        is_reasoning = "reasoner" in target or "r1" in target or "o1" in target
        return LLMCapabilities(
            structured_output=False,  # Use robust JSON extraction fallback pipeline
            json_mode=True,
            tool_calling=True,
            reasoning=is_reasoning,
            max_context_tokens=128000,
            token_usage_reporting=True,
        )

    async def generate(self, request: LLMRequest) -> LLMResponse:
        """Execute standard completion over OpenAI-compatible endpoint."""
        model = request.model or self._default_model
        start_time = time.time()

        async def _call() -> LLMResponse:
            try:
                response = await self.client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": request.system_prompt},
                        {"role": "user", "content": request.user_prompt},
                    ],
                    temperature=request.temperature,
                    max_tokens=request.max_tokens,
                )
                choice = response.choices[0]
                content = choice.message.content or ""
                latency_ms = int((time.time() - start_time) * 1000)

                input_tokens = response.usage.prompt_tokens if response.usage else 0
                output_tokens = response.usage.completion_tokens if response.usage else 0
                cost = ModelPricingRegistry.calculate_cost(model, input_tokens, output_tokens)

                MetricsTracker.record_llm_usage(model, input_tokens, output_tokens, cost)

                return LLMResponse(
                    content=content,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=input_tokens + output_tokens,
                    usage_source="provider_reported" if response.usage else "unavailable",
                    model=model,
                    provider=self.provider_name,
                    finish_reason=choice.finish_reason,
                    latency_ms=latency_ms,
                    cost_usd=cost,
                    request_id=request.request_id,
                )
            except Exception as e:
                raise normalize_exception(e, provider=self.provider_name, model=model) from e

        return await execute_with_retry(_call, provider_name=self.provider_name)

    async def generate_structured(self, request: StructuredLLMRequest, schema: Type[T]) -> LLMResponse:
        """
        Execute structured completion with JSON Schema instruction injection
        and multi-stage Pydantic validation fallback.
        """
        model = request.model or self._default_model
        start_time = time.time()

        schema_json = json.dumps(StructuredOutputParser.get_json_schema(schema), indent=2)
        system_instruction = (
            f"{request.system_prompt}\n\n"
            f"CRITICAL: You must format your response strictly as valid JSON adhering to this JSON Schema:\n"
            f"```json\n{schema_json}\n```\n"
            f"Output ONLY the JSON object. Do not include introductory text or trailing commentary."
        )

        async def _call() -> LLMResponse:
            try:
                # Try json_object response format if supported
                try:
                    response = await self.client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": system_instruction},
                            {"role": "user", "content": request.user_prompt},
                        ],
                        response_format={"type": "json_object"},
                        temperature=request.temperature,
                    )
                except Exception:
                    # Fallback to standard request without response_format flag
                    response = await self.client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": system_instruction},
                            {"role": "user", "content": request.user_prompt},
                        ],
                        temperature=request.temperature,
                    )

                choice = response.choices[0]
                content = choice.message.content or ""
                latency_ms = int((time.time() - start_time) * 1000)

                # Strict Pydantic Validation via provider-neutral pipeline
                parsed = StructuredOutputParser.parse_and_validate(
                    content, schema, provider=self.provider_name, model=model
                )

                input_tokens = response.usage.prompt_tokens if response.usage else 0
                output_tokens = response.usage.completion_tokens if response.usage else 0
                cost = ModelPricingRegistry.calculate_cost(model, input_tokens, output_tokens)

                MetricsTracker.record_llm_usage(model, input_tokens, output_tokens, cost)

                return LLMResponse(
                    content=content,
                    parsed=parsed,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=input_tokens + output_tokens,
                    usage_source="provider_reported" if response.usage else "unavailable",
                    model=model,
                    provider=self.provider_name,
                    finish_reason=choice.finish_reason,
                    latency_ms=latency_ms,
                    cost_usd=cost,
                    request_id=request.request_id,
                )
            except Exception as e:
                raise normalize_exception(e, provider=self.provider_name, model=model) from e

        return await execute_with_retry(_call, provider_name=self.provider_name)
