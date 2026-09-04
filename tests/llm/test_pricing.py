"""Tests for central ModelPricingRegistry."""

from backend.llm.pricing import ModelPricingRegistry


def test_pricing_calculation_known_models():
    # gpt-4o: $2.50 prompt / $10.00 completion per 1M
    cost = ModelPricingRegistry.calculate_cost("gpt-4o", 100_000, 10_000)
    # (100k / 1M * 2.50) + (10k / 1M * 10.00) = 0.25 + 0.10 = 0.35
    assert round(cost, 2) == 0.35

    # deepseek-chat: $0.14 prompt / $0.28 completion per 1M
    cost_ds = ModelPricingRegistry.calculate_cost("deepseek-chat", 1_000_000, 1_000_000)
    assert round(cost_ds, 2) == 0.42


def test_dynamic_pricing_registration():
    ModelPricingRegistry.register_model("custom-fast-model", 0.50, 1.50)
    cost = ModelPricingRegistry.calculate_cost("custom-fast-model", 1_000_000, 1_000_000)
    assert round(cost, 2) == 2.00
