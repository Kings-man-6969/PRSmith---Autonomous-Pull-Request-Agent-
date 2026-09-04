"""Native OpenAI provider implementing LLMProvider protocol."""

import time
from typing import Optional, Type, TypeVar
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
from backend.llm.structured import StructuredOutputParser
from backend.observability.logging import get_logger
from backend.observability.metrics import MetricsTracker

logger = get_logger(__name__)
T = TypeVar("T", bound=BaseModel)


class OpenAIProvider(LLMProvider):
    """Native OpenAI API integration with structured output support."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        default_model: Optional[str] = None,
        organization: Optional[str] = None,
    ):
        self.api_key = api_key or getattr(settings, "OPENAI_API_KEY", "")
        self._default_model = default_model or getattr(settings, "OPENAI_MODEL", "gpt-4o")
        self.organization = organization
        timeout = getattr(settings, "LLM_TIMEOUT_SECONDS", 120)
        self.client = (
            openai.AsyncOpenAI(api_key=self.api_key, organization=self.organization, timeout=float(timeout))
            if (self.api_key and openai is not None)
            else None
        )

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def default_model(self) -> str:
        return self._default_model

    def validate_model(self, model_name: str) -> bool:
        known = {"gpt-4o", "gpt-4o-mini", "o1", "o1-mini", "o1-preview", "o3-mini", "gpt-4-turbo"}
        return model_name in known or model_name.startswith("gpt-") or model_name.startswith("o")

    def capabilities(self, model: Optional[str] = None) -> LLMCapabilities:
        target_model = model or self._default_model
        is_reasoning = target_model.startswith("o1") or target_model.startswith("o3")
        return LLMCapabilities(
            structured_output=True,
            json_mode=True,
            tool_calling=True,
            reasoning=is_reasoning,
            max_context_tokens=128000 if not is_reasoning else 200000,
            token_usage_reporting=True,
        )

    async def generate(self, request: LLMRequest) -> LLMResponse:
        """Execute a text-based completion."""
        if not self.client:
            raise AuthenticationError("OpenAI API key is missing. Set OPENAI_API_KEY.", provider=self.provider_name)

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
        """Execute a structured completion using native OpenAI Beta Parse + Pydantic validation."""
        if not self.client:
            raise AuthenticationError("OpenAI API key is missing. Set OPENAI_API_KEY.", provider=self.provider_name)

        model = request.model or self._default_model
        start_time = time.time()

        async def _call() -> LLMResponse:
            try:
                completion = await self.client.beta.chat.completions.parse(
                    model=model,
                    messages=[
                        {"role": "system", "content": request.system_prompt},
                        {"role": "user", "content": request.user_prompt},
                    ],
                    response_format=schema,
                    temperature=request.temperature,
                )
                choice = completion.choices[0]
                parsed = choice.message.parsed
                content = choice.message.content or ""
                latency_ms = int((time.time() - start_time) * 1000)

                # Even with native parse, ensure semantic validation
                if parsed is None and content:
                    parsed = StructuredOutputParser.parse_and_validate(content, schema, provider=self.provider_name, model=model)

                input_tokens = completion.usage.prompt_tokens if completion.usage else 0
                output_tokens = completion.usage.completion_tokens if completion.usage else 0
                cost = ModelPricingRegistry.calculate_cost(model, input_tokens, output_tokens)

                MetricsTracker.record_llm_usage(model, input_tokens, output_tokens, cost)

                return LLMResponse(
                    content=content,
                    parsed=parsed,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=input_tokens + output_tokens,
                    usage_source="provider_reported" if completion.usage else "unavailable",
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
