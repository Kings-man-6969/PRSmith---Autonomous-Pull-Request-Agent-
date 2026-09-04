"""Provider-neutral structured output extraction and Pydantic validation pipeline."""

import json
import re
from typing import Any, Dict, Type, TypeVar, cast
from pydantic import BaseModel, ValidationError

from backend.llm.errors import StructuredOutputError

T = TypeVar("T", bound=BaseModel)

_JSON_FENCE_REGEX = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)


class StructuredOutputParser:
    """Extracts, cleans, and validates structured payloads against Pydantic schemas."""

    @staticmethod
    def extract_json_payload(raw_content: str) -> str:
        """Extract a clean JSON string from raw LLM output."""
        if not raw_content or not raw_content.strip():
            raise StructuredOutputError("Empty response content received from LLM.", raw_content=raw_content)

        text = raw_content.strip()

        # 1. Check for markdown code fences (```json ... ```)
        fence_match = _JSON_FENCE_REGEX.search(text)
        if fence_match:
            candidate = fence_match.group(1).strip()
            if candidate:
                return candidate

        # 2. Extract substring between first '{' or '[' and last '}' or ']'
        start_obj = text.find("{")
        start_arr = text.find("[")

        if start_obj != -1 and (start_arr == -1 or start_obj < start_arr):
            end_obj = text.rfind("}")
            if end_obj > start_obj:
                return text[start_obj : end_obj + 1].strip()

        if start_arr != -1:
            end_arr = text.rfind("]")
            if end_arr > start_arr:
                return text[start_arr : end_arr + 1].strip()

        # 3. Fallback: return as-is
        return text

    @classmethod
    def parse_and_validate(
        cls,
        raw_content: str,
        schema: Type[T],
        provider: str = "unknown",
        model: str = "unknown",
    ) -> T:
        """Parse raw content into validated Pydantic model with normalized error raising."""
        clean_json = cls.extract_json_payload(raw_content)

        try:
            data = json.loads(clean_json)
        except json.JSONDecodeError as exc:
            raise StructuredOutputError(
                f"Failed to decode JSON from {provider}/{model}: {exc}",
                raw_content=raw_content,
                provider=provider,
            ) from exc

        try:
            if hasattr(schema, "model_validate"):
                result = getattr(schema, "model_validate")(data)
                return cast(T, result)
            result = schema(**data)
            return cast(T, result)
        except ValidationError as exc:
            raise StructuredOutputError(
                f"Schema validation failed for {schema.__name__} on {provider}/{model}: {exc}",
                raw_content=raw_content,
                provider=provider,
            ) from exc
        except Exception as exc:
            raise StructuredOutputError(
                f"Instantiation failed for {schema.__name__}: {exc}",
                raw_content=raw_content,
                provider=provider,
            ) from exc

    @staticmethod
    def get_json_schema(schema: Type[BaseModel]) -> Dict[str, Any]:
        """Generate JSON Schema dictionary from Pydantic model."""
        if hasattr(schema, "model_json_schema"):
            return cast(Dict[str, Any], getattr(schema, "model_json_schema")())
        if hasattr(schema, "schema"):
            return cast(Dict[str, Any], getattr(schema, "schema")())
        return {}
