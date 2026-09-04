"""Test provider-neutral structured output extraction and validation."""

import pytest
from pydantic import BaseModel, Field

from backend.llm.errors import StructuredOutputError
from backend.llm.structured import StructuredOutputParser


class SampleOutput(BaseModel):
    name: str
    count: int = Field(ge=0)
    tags: list[str] = Field(default_factory=list)


def test_extract_json_from_clean_string():
    raw = '{"name": "test", "count": 5, "tags": ["a", "b"]}'
    res = StructuredOutputParser.parse_and_validate(raw, SampleOutput)
    assert res.name == "test"
    assert res.count == 5
    assert res.tags == ["a", "b"]


def test_extract_json_from_markdown_fences():
    raw = """Here is your JSON response:
```json
{
  "name": "fenced_test",
  "count": 10,
  "tags": ["frontend", "backend"]
}
```
Hope this helps!"""
    res = StructuredOutputParser.parse_and_validate(raw, SampleOutput)
    assert res.name == "fenced_test"
    assert res.count == 10
    assert len(res.tags) == 2


def test_extract_json_with_surrounding_commentary():
    raw = """The analysis completed successfully.
    {"name": "inline_test", "count": 2, "tags": ["hotfix"]}
    Please review the patch above."""
    res = StructuredOutputParser.parse_and_validate(raw, SampleOutput)
    assert res.name == "inline_test"
    assert res.count == 2


def test_invalid_json_raises_structured_output_error():
    raw = "This is not json at all"
    with pytest.raises(StructuredOutputError):
        StructuredOutputParser.parse_and_validate(raw, SampleOutput)


def test_schema_mismatch_raises_structured_output_error():
    raw = '{"name": "test", "count": -10}'  # count must be ge=0
    with pytest.raises(StructuredOutputError):
        StructuredOutputParser.parse_and_validate(raw, SampleOutput)
