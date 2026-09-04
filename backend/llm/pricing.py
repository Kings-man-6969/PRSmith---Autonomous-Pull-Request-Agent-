"""Central Model Pricing Registry decoupling pricing tables from provider code."""

from typing import Dict, Optional, Tuple


class ModelPricingRegistry:
    """Registry maintaining current token pricing per model across providers."""

    # Default rates in USD per 1 Million tokens: (prompt_rate_usd, completion_rate_usd)
    _RATES: Dict[str, Tuple[float, float]] = {
        # OpenAI
        "gpt-4o": (2.50, 10.00),
        "gpt-4o-mini": (0.15, 0.60),
        "o1": (15.00, 60.00),
        "o3-mini": (1.10, 4.40),
        "text-embedding-3-small": (0.02, 0.00),

        # Google Gemini
        "gemini-1.5-pro": (1.25, 5.00),
        "gemini-1.5-flash": (0.075, 0.30),
        "gemini-2.0-flash": (0.10, 0.40),
        "text-embedding-004": (0.00, 0.00),

        # Anthropic
        "claude-3-5-sonnet-20241022": (3.00, 15.00),
        "claude-3-5-haiku-20241022": (0.80, 4.00),
        "claude-3-opus-20240229": (15.00, 75.00),

        # DeepSeek
        "deepseek-chat": (0.14, 0.28),
        "deepseek-coder": (0.14, 0.28),
        "deepseek-reasoner": (0.55, 2.19),

        # Qwen
        "qwen-max": (2.80, 8.40),
        "qwen-plus": (0.40, 1.20),
        "qwen-turbo": (0.10, 0.30),
        "qwen-2.5-coder-32b-instruct": (0.20, 0.60),
    }

    @classmethod
    def register_model(cls, model_name: str, prompt_rate_per_1m: float, completion_rate_per_1m: float) -> None:
        """Dynamically register or update pricing for a model."""
        cls._RATES[model_name.lower()] = (prompt_rate_per_1m, completion_rate_per_1m)

    @classmethod
    def get_rates(cls, model_name: str) -> Tuple[float, float]:
        """Look up prompt and completion rates per 1M tokens. Returns default fallback if unlisted."""
        normalized = model_name.lower()
        if normalized in cls._RATES:
            return cls._RATES[normalized]
        # Match by prefix / family
        for known_model, rates in cls._RATES.items():
            if known_model in normalized or normalized in known_model:
                return rates
        # Default conservative estimation ($1.00 / $3.00 per 1M)
        return (1.00, 3.00)

    @classmethod
    def calculate_cost(
        cls,
        model_name: str,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        """Calculate total USD cost based on token counts."""
        prompt_rate, completion_rate = cls.get_rates(model_name)
        input_cost = (input_tokens / 1_000_000.0) * prompt_rate
        output_cost = (output_tokens / 1_000_000.0) * completion_rate
        return round(input_cost + output_cost, 6)
