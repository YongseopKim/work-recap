"""EscalationHandler tests."""

import json
from unittest.mock import MagicMock


from workrecap.infra.escalation import EscalationHandler
from workrecap.infra.providers.base import LLMProvider
from workrecap.models import TokenUsage


def _make_provider(responses: list[str]) -> LLMProvider:
    """Create a mock provider that returns responses in sequence."""
    mock = MagicMock(spec=LLMProvider)
    mock.provider_name = "test"
    side_effects = [
        (resp, TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150, call_count=1))
        for resp in responses
    ]
    mock.chat.side_effect = side_effects
    return mock


class TestEscalationConfident:
    def test_high_confidence_no_escalation(self):
        """confidence >= 0.7 → use base model response, no escalation."""
        base_response = json.dumps(
            {
                "needs_escalation": False,
                "confidence": 0.9,
                "reason": "",
                "response": "This is the summary.",
            }
        )
        base = _make_provider([base_response])
        escalation = _make_provider([])  # Should not be called

        handler = EscalationHandler(
            base_provider=base,
            base_model="base-model",
            escalation_provider=escalation,
            escalation_model="premium-model",
        )
        text, usage = handler.chat("system", "user")

        assert text == "This is the summary."
        assert usage.call_count == 1
        base.chat.assert_called_once()
        escalation.chat.assert_not_called()


class TestEscalationNeeded:
    def test_low_confidence_triggers_escalation(self):
        """confidence < 0.7 → escalate to premium model."""
        base_response = json.dumps(
            {
                "needs_escalation": True,
                "confidence": 0.3,
                "reason": "Complex multi-repo analysis",
                "response": "Rough draft.",
            }
        )
        base = _make_provider([base_response])
        escalation = _make_provider(["Premium summary."])

        handler = EscalationHandler(
            base_provider=base,
            base_model="base-model",
            escalation_provider=escalation,
            escalation_model="premium-model",
        )
        text, usage = handler.chat("system", "user")

        assert text == "Premium summary."
        assert usage.call_count == 2  # base + escalation
        escalation.chat.assert_called_once()


class TestEscalationFallback:
    def test_invalid_json_uses_raw_response(self):
        """JSON 파싱 실패 → 원본 응답 그대로 사용 (graceful fallback)."""
        base = _make_provider(["This is not JSON at all."])
        escalation = _make_provider([])

        handler = EscalationHandler(
            base_provider=base,
            base_model="base-model",
            escalation_provider=escalation,
            escalation_model="premium-model",
        )
        text, usage = handler.chat("system", "user")

        assert text == "This is not JSON at all."
        assert usage.call_count == 1
        escalation.chat.assert_not_called()

    def test_missing_fields_uses_raw_response(self):
        """JSON은 유효하지만 필수 필드 누락 → 원본 그대로 사용."""
        base_response = json.dumps({"some_field": "value"})
        base = _make_provider([base_response])
        escalation = _make_provider([])

        handler = EscalationHandler(
            base_provider=base,
            base_model="base-model",
            escalation_provider=escalation,
            escalation_model="premium-model",
        )
        text, usage = handler.chat("system", "user")

        assert text == base_response
        escalation.chat.assert_not_called()

    def test_needs_escalation_false_but_low_confidence(self):
        """needs_escalation=false but confidence < 0.7 → still use base response."""
        base_response = json.dumps(
            {
                "needs_escalation": False,
                "confidence": 0.5,
                "reason": "somewhat uncertain",
                "response": "Best effort summary.",
            }
        )
        base = _make_provider([base_response])
        escalation = _make_provider([])

        handler = EscalationHandler(
            base_provider=base,
            base_model="base-model",
            escalation_provider=escalation,
            escalation_model="premium-model",
        )
        text, usage = handler.chat("system", "user")

        # confidence < 0.7 AND needs_escalation=True triggers escalation
        # needs_escalation=False → use response regardless of confidence
        assert text == "Best effort summary."
        escalation.chat.assert_not_called()


class TestEscalationJsonMode:
    def test_base_call_uses_json_mode(self):
        """Base model always called with json_mode=True for self-assessment."""
        base_response = json.dumps(
            {
                "needs_escalation": False,
                "confidence": 0.9,
                "reason": "",
                "response": "Summary.",
            }
        )
        base = _make_provider([base_response])
        escalation = _make_provider([])

        handler = EscalationHandler(
            base_provider=base,
            base_model="base-model",
            escalation_provider=escalation,
            escalation_model="premium-model",
        )
        handler.chat("system instructions", "user data")

        # Verify json_mode=True was passed to base call
        call_kwargs = base.chat.call_args.kwargs
        assert call_kwargs.get("json_mode") is True

    def test_escalation_forwards_json_mode(self):
        """When escalating, json_mode from caller is forwarded to escalation model."""
        base_response = json.dumps(
            {
                "needs_escalation": True,
                "confidence": 0.2,
                "reason": "too complex",
                "response": "draft",
            }
        )
        base = _make_provider([base_response])
        escalation = _make_provider(["Premium result."])

        handler = EscalationHandler(
            base_provider=base,
            base_model="base-model",
            escalation_provider=escalation,
            escalation_model="premium-model",
        )
        handler.chat("system", "user", json_mode=True)

        call_kwargs = escalation.chat.call_args.kwargs
        assert call_kwargs.get("json_mode") is True

    def test_lean_system_prompt(self):
        """Escalation uses a lean fixed system prompt, not embedding the original."""
        base_response = json.dumps(
            {
                "needs_escalation": False,
                "confidence": 0.9,
                "reason": "",
                "response": "Summary.",
            }
        )
        base = _make_provider([base_response])
        escalation = _make_provider([])

        handler = EscalationHandler(
            base_provider=base,
            base_model="base-model",
            escalation_provider=escalation,
            escalation_model="premium-model",
        )
        handler.chat("A very long system prompt with lots of instructions", "user data")

        # The system prompt sent to the base model should be the lean fixed one,
        # NOT contain the original system prompt
        call_args = base.chat.call_args
        system_prompt_sent = call_args.args[1]  # model, system, user
        assert "A very long system prompt" not in system_prompt_sent
        assert len(system_prompt_sent) < 200  # Lean system prompt

        # The original system prompt should be in the user content
        user_content_sent = call_args.args[2]
        assert "A very long system prompt" in user_content_sent
        assert "user data" in user_content_sent


class TestEscalationSameProvider:
    def test_same_provider_different_models(self):
        """Base and escalation can be same provider, different models."""
        base_response = json.dumps(
            {
                "needs_escalation": True,
                "confidence": 0.2,
                "reason": "too complex",
                "response": "draft",
            }
        )
        # Same provider used for both base and escalation
        provider = MagicMock(spec=LLMProvider)
        provider.provider_name = "anthropic"
        provider.chat.side_effect = [
            (base_response, TokenUsage(100, 50, 150, 1)),
            ("Premium result", TokenUsage(200, 100, 300, 1)),
        ]

        handler = EscalationHandler(
            base_provider=provider,
            base_model="claude-haiku-4-5-20251001",
            escalation_provider=provider,
            escalation_model="claude-sonnet-4-5-20250929",
        )
        text, usage = handler.chat("system", "user")

        assert text == "Premium result"
        assert provider.chat.call_count == 2
        # Verify different models were used
        calls = provider.chat.call_args_list
        assert calls[0].args[0] == "claude-haiku-4-5-20251001"
        assert calls[1].args[0] == "claude-sonnet-4-5-20250929"
