"""Batch processing abstractions for LLM providers.

Defines the data models and ABC mixin for providers that support batch API.
Providers implement BatchCapable alongside LLMProvider to enable batch mode.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

from workrecap.models import TokenUsage


@dataclass
class BatchRequest:
    """A single request within a batch job."""

    custom_id: str
    model: str
    system_prompt: str
    user_content: str
    json_mode: bool = False
    max_tokens: int | None = None
    cache_system_prompt: bool = False


@dataclass
class BatchResult:
    """Result of a single request within a completed batch."""

    custom_id: str
    content: str | None = None
    usage: TokenUsage | None = None
    error: str | None = None


class BatchStatus(str, Enum):
    """Batch job lifecycle status."""

    SUBMITTED = "submitted"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"

    @property
    def is_terminal(self) -> bool:
        """Whether this status represents a final state."""
        return self in (BatchStatus.COMPLETED, BatchStatus.FAILED, BatchStatus.EXPIRED)


class BatchCapable(ABC):
    """Mixin for providers that support batch processing.

    Usage:
        class MyProvider(LLMProvider, BatchCapable): ...

    Check at runtime:
        if isinstance(provider, BatchCapable):
            provider.submit_batch(requests)
    """

    @abstractmethod
    def submit_batch(self, requests: list[BatchRequest]) -> str:
        """Submit a batch of requests. Returns batch_id."""

    @abstractmethod
    def get_batch_status(self, batch_id: str) -> BatchStatus:
        """Get the current status of a batch job."""

    @abstractmethod
    def get_batch_results(self, batch_id: str) -> list[BatchResult]:
        """Retrieve results from a completed batch. Raises if not completed."""
