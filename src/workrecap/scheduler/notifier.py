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


class CompositeNotifier(Notifier):
    """여러 Notifier를 묶어 순차 실행. 개별 실패 무시."""

    def __init__(self, notifiers: list[Notifier]) -> None:
        self._notifiers = notifiers

    async def notify(self, event: SchedulerEvent) -> None:
        for n in self._notifiers:
            try:
                await n.notify(event)
            except Exception:
                logger.warning(
                    "Notifier %s failed for job '%s'",
                    type(n).__name__,
                    event.job,
                    exc_info=True,
                )
