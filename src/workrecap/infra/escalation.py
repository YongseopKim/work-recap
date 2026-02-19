"""Adaptive escalation: lightweight model self-assesses, escalates if needed."""

from __future__ import annotations

import json
import logging

from workrecap.infra.providers.base import LLMProvider
from workrecap.models import TokenUsage

logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.7

_ESCALATION_SYSTEM = """\
Complete the user's task and self-assess. Respond with JSON:
{"needs_escalation": bool, "confidence": 0.0-1.0, "reason": "...", "response": "your answer"}\
"""

_ESCALATION_USER = """\
Instructions: {system_prompt}

---

{user_content}\
"""


class EscalationHandler:
    """Handles adaptive escalation from a base model to a premium model.

    Protocol:
    1. Send task + self-assessment wrapper to base model
    2. Parse JSON response for confidence and needs_escalation
    3. If needs_escalation=True AND confidence < threshold → escalate
    4. Otherwise use base model's response
    5. On JSON parse failure → use raw response as-is (graceful fallback)
    """

    def __init__(
        self,
        base_provider: LLMProvider,
        base_model: str,
        escalation_provider: LLMProvider,
        escalation_model: str,
    ) -> None:
        self._base_provider = base_provider
        self._base_model = base_model
        self._escalation_provider = escalation_provider
        self._escalation_model = escalation_model

    def chat(
        self,
        system_prompt: str,
        user_content: str,
        *,
        json_mode: bool = False,
        max_tokens: int | None = None,
        cache_system_prompt: bool = False,
    ) -> tuple[str, TokenUsage]:
        """Execute with possible escalation. Returns (text, total_usage)."""
        # Step 1: Call base model with lean system + merged user content
        wrapped_user = _ESCALATION_USER.format(
            system_prompt=system_prompt, user_content=user_content
        )
        base_text, base_usage = self._base_provider.chat(
            self._base_model, _ESCALATION_SYSTEM, wrapped_user, json_mode=True
        )

        # Step 2: Parse JSON response
        decision = self._parse_decision(base_text)
        if decision is None:
            # JSON parse failure → graceful fallback, use raw response
            logger.warning("Escalation JSON parse failed, using raw response")
            return base_text, base_usage

        # Step 3: Decide whether to escalate
        if decision["needs_escalation"] and decision["confidence"] < CONFIDENCE_THRESHOLD:
            logger.info(
                "Escalating: confidence=%.2f reason=%s",
                decision["confidence"],
                decision.get("reason", ""),
            )
            esc_text, esc_usage = self._escalation_provider.chat(
                self._escalation_model,
                system_prompt,
                user_content,
                json_mode=json_mode,
                max_tokens=max_tokens,
                cache_system_prompt=cache_system_prompt,
            )
            total_usage = base_usage + esc_usage
            return esc_text, total_usage

        # Step 4: Use base model's response
        return decision["response"], base_usage

    def _parse_decision(self, text: str) -> dict | None:
        """Parse the self-assessment JSON. Returns None on failure."""
        try:
            data = json.loads(text)
            # Validate required fields
            if not isinstance(data, dict):
                return None
            if "response" not in data or "confidence" not in data:
                return None
            data.setdefault("needs_escalation", False)
            data.setdefault("reason", "")
            return data
        except (json.JSONDecodeError, TypeError):
            return None
