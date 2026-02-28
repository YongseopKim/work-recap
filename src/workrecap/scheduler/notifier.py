"""알림 시스템 — Notifier ABC + LogNotifier."""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SchedulerEvent:
    job: str
    status: str  # "success" | "failed"
    triggered_at: str
    target: str
    completed_at: str | None = None
    error: str | None = None


class Notifier(ABC):
    @abstractmethod
    async def notify(self, event: SchedulerEvent) -> None: ...


class LogNotifier(Notifier):
    async def notify(self, event: SchedulerEvent) -> None:
        if event.status == "failed":
            logger.error(
                "Scheduler job '%s' failed (target=%s): %s",
                event.job,
                event.target,
                event.error,
            )
        else:
            logger.info(
                "Scheduler job '%s' %s (target=%s)",
                event.job,
                event.status,
                event.target,
            )
