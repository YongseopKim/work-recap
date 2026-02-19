"""PricingTable tests."""

from workrecap.infra.pricing import PricingTable


class TestPricingTable:
    def test_known_model_cost(self):
        pt = PricingTable()
        cost = pt.estimate_cost(
            "openai", "gpt-4o-mini", prompt_tokens=1_000_000, completion_tokens=0
        )
        assert cost > 0

    def test_unknown_model_returns_zero(self):
        pt = PricingTable()
        cost = pt.estimate_cost(
            "openai", "unknown-model-xyz", prompt_tokens=1000, completion_tokens=500
        )
        assert cost == 0.0

    def test_prompt_and_completion_separate_rates(self):
        pt = PricingTable()
        prompt_cost = pt.estimate_cost(
            "openai", "gpt-4o", prompt_tokens=1_000_000, completion_tokens=0
        )
        completion_cost = pt.estimate_cost(
            "openai", "gpt-4o", prompt_tokens=0, completion_tokens=1_000_000
        )
        # Completion is typically more expensive
        assert completion_cost > prompt_cost

    def test_anthropic_model(self):
        pt = PricingTable()
        cost = pt.estimate_cost(
            "anthropic",
            "claude-haiku-4-5-20251001",
            prompt_tokens=500_000,
            completion_tokens=100_000,
        )
        assert cost > 0

    def test_gemini_model(self):
        pt = PricingTable()
        cost = pt.estimate_cost(
            "gemini", "gemini-2.0-flash", prompt_tokens=1_000_000, completion_tokens=500_000
        )
        assert cost > 0

    def test_custom_provider_returns_zero(self):
        """Custom/local models have no pricing."""
        pt = PricingTable()
        cost = pt.estimate_cost(
            "custom", "llama3", prompt_tokens=1_000_000, completion_tokens=500_000
        )
        assert cost == 0.0

    def test_get_rate_returns_none_for_unknown(self):
        pt = PricingTable()
        rate = pt.get_rate("openai", "nonexistent")
        assert rate is None

    def test_get_rate_returns_tuple_for_known(self):
        pt = PricingTable()
        rate = pt.get_rate("openai", "gpt-4o")
        assert rate is not None
        assert len(rate) == 2  # (prompt_rate, completion_rate)

    def test_model_name_prefix_matching(self):
        """Models with date suffixes should match base name."""
        pt = PricingTable()
        # claude-sonnet-4-5-20250929 should match a known pattern
        cost = pt.estimate_cost(
            "anthropic",
            "claude-sonnet-4-5-20250929",
            prompt_tokens=1_000_000,
            completion_tokens=0,
        )
        assert cost > 0

    def test_claude_sonnet_4_6_has_pricing(self):
        """claude-sonnet-4-6 must have pricing (was missing, causing $0 reports)."""
        pt = PricingTable()
        rate = pt.get_rate("anthropic", "claude-sonnet-4-6")
        assert rate is not None
        assert rate == (3.00, 15.00)

    def test_claude_opus_4_6_has_pricing(self):
        pt = PricingTable()
        rate = pt.get_rate("anthropic", "claude-opus-4-6")
        assert rate is not None
        assert rate == (5.00, 25.00)

    def test_gpt5_has_pricing(self):
        pt = PricingTable()
        rate = pt.get_rate("openai", "gpt-5")
        assert rate is not None
        assert rate == (1.25, 10.00)

    def test_gemini_3_pro_has_pricing(self):
        pt = PricingTable()
        rate = pt.get_rate("gemini", "gemini-3-pro")
        assert rate is not None
        assert rate == (2.00, 12.00)
