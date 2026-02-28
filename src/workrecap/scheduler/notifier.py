"""알림 시스템 — Notifier ABC + LogNotifier + TelegramNotifier."""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

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


TELEGRAM_MAX_LENGTH = 4096


class TelegramNotifier(Notifier):
    """Telegram Bot API sendMessage로 스케줄 결과 전송."""

    def __init__(self, bot_token: str, chat_id: str, config) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._config = config
        self._base_url = f"https://api.telegram.org/bot{bot_token}"

    def _format_header(self, event: SchedulerEvent) -> str:
        icon = "\u2705" if event.status == "success" else "\u274c"
        status_text = "완료" if event.status == "success" else "실패"
        header = f"{icon} [{event.job}] {status_text} \u2014 {event.target}"
        if event.triggered_at and event.completed_at:
            header += f"\n\n\u23f1 {event.triggered_at} \u2192 {event.completed_at}"
        if event.error:
            header += f"\n\nError: {event.error}"
        return header

    def _resolve_summary_path(self, job: str, target: str):
        if job == "daily":
            return self._config.daily_summary_path(target)
        elif job == "weekly":
            parts = target.split("-W")
            return self._config.weekly_summary_path(int(parts[0]), int(parts[1]))
        elif job == "monthly":
            parts = target.split("-")
            return self._config.monthly_summary_path(int(parts[0]), int(parts[1]))
        elif job == "yearly":
            return self._config.yearly_summary_path(int(target))
        return None

    def _read_summary(self, event: SchedulerEvent) -> str | None:
        if event.status != "success":
            return None
        try:
            path = self._resolve_summary_path(event.job, event.target)
            if path and path.exists():
                return path.read_text(encoding="utf-8")
        except Exception:
            logger.warning("Failed to read summary for Telegram", exc_info=True)
        return None

    def _split_messages(self, header: str, body: str | None) -> list[str]:
        if not body:
            return [header]
        separator = "\n\n" + "\u2500" * 20 + "\n"
        full = header + separator + body
        if len(full) <= TELEGRAM_MAX_LENGTH:
            return [full]
        messages = [header]
        while body:
            chunk = body[:TELEGRAM_MAX_LENGTH]
            messages.append(chunk)
            body = body[TELEGRAM_MAX_LENGTH:]
        return messages

    async def notify(self, event: SchedulerEvent) -> None:
        summary = self._read_summary(event)
        header = self._format_header(event)
        messages = self._split_messages(header, summary)
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                for msg in messages:
                    await client.post(
                        f"{self._base_url}/sendMessage",
                        json={"chat_id": self._chat_id, "text": msg},
                    )
        except Exception:
            logger.warning("Telegram notification failed for job '%s'", event.job, exc_info=True)
