"""알림 시스템 — Notifier ABC + LogNotifier + TelegramNotifier."""

import logging
import re
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

_HEADING_EMOJIS: dict[str, str] = {
    "개요": "📋",
    "주간 개요": "📋",
    "월간 개요": "📋",
    "연간 개요": "📋",
    "주요 활동": "📌",
    "주요 성과": "🏆",
    "커밋": "💻",
    "PR": "🔀",
    "이슈": "🎯",
    "리뷰": "👀",
}

_ITEM_RE = re.compile(r"^[a-zA-Z][\w/]*: ")


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

    @staticmethod
    def _format_for_telegram(text: str) -> str:
        """마크다운 요약을 텔레그램 평문 포맷으로 변환.

        - ``# 타이틀`` 행 제거 (헤더에 이미 날짜 정보 포함)
        - ``##``/``###``/``####`` 마커 제거, 알려진 헤딩에 이모지 추가
        - ``---`` 수평선 제거
        - ``- [link](url): desc **type**`` → ``type: desc``
        - ``- **[link](url)** (type): desc`` → ``type: desc``
        - 나머지 ``**bold**``, ``[text](url)`` 등 마크다운 문법 정리
        """
        lines: list[str] = []
        for raw in text.split("\n"):
            line = raw.rstrip()

            # 수평 구분선
            if re.match(r"^-{3,}$", line.strip()):
                continue

            # H1 타이틀 — 헤더가 이미 포함하므로 제거
            if re.match(r"^# ", line):
                continue

            # H2–H6 마커 제거, 이모지 추가
            hm = re.match(r"^(#{2,6})\s+(.*)", line)
            if hm:
                heading_text = hm.group(2)
                emoji = _HEADING_EMOJIS.get(heading_text, "")
                lines.append(f"{emoji} {heading_text}" if emoji else heading_text)
                continue

            # 리스트: - **[text](url)** (type): description (새 포맷)
            im_new = re.match(
                r"^-\s+\*\*\[.*?\]\(.*?\)\*\*\s*\(([\w/]+)\):\s*(.*)", line
            )
            if im_new:
                lines.append(f"{im_new.group(1)}: {im_new.group(2)}")
                continue

            # 리스트: - [text](url): description **type** (기존 포맷)
            im = re.match(r"^-\s+\[.*?\]\(.*?\):\s*(.*?)\s*\*\*([\w/]+)\*\*\s*$", line)
            if im:
                lines.append(f"{im.group(2)}: {im.group(1)}")
                continue

            # 리스트: - [text](url): description (타입 태그 없음)
            im2 = re.match(r"^-\s+\[.*?\]\(.*?\):\s*(.*)", line)
            if im2:
                lines.append(im2.group(1))
                continue

            # 일반 텍스트: 마크다운 잔여 문법 정리
            line = re.sub(r"\*\*(.*?)\*\*", r"\1", line)
            line = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", line)
            line = re.sub(r"\(https?://\S+\)", "", line)
            lines.append(line)

        result = "\n".join(lines)
        return re.sub(r"\n{3,}", "\n\n", result).strip()

    @staticmethod
    def _trim_to_fit(body: str, max_chars: int) -> str:
        """body를 max_chars 이내로 리포별 비례 축소.

        연속된 아이템 라인(``type: desc``) 그룹을 식별하고,
        동일 비율로 줄여 전체 길이를 맞춘다.
        """
        if len(body) <= max_chars:
            return body

        lines = body.split("\n")

        # 아이템 그룹 식별 (연속된 item 라인 인덱스 묶음)
        groups: list[list[int]] = []
        current: list[int] = []
        for i, line in enumerate(lines):
            if _ITEM_RE.match(line):
                current.append(i)
            else:
                if current:
                    groups.append(current)
                    current = []
        if current:
            groups.append(current)

        if not groups:
            return body[: max_chars - 10] + "\n...계속"

        # 점진적 비율로 축소 — 첫 번째로 맞는 비율 사용
        for ratio in (0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1):
            skip: set[int] = set()
            extras: dict[int, str] = {}

            for group in groups:
                n = len(group)
                keep_n = max(1, round(n * ratio))
                if keep_n >= n:
                    continue
                for idx in group[keep_n:]:
                    skip.add(idx)
                extras[group[keep_n - 1]] = f"  ...외 {n - keep_n}건"

            result_lines: list[str] = []
            for i, line in enumerate(lines):
                if i in skip:
                    continue
                result_lines.append(line)
                if i in extras:
                    result_lines.append(extras[i])

            result = "\n".join(result_lines)
            if len(result) <= max_chars:
                return result

        return body[: max_chars - 10] + "\n...계속"

    def _build_single_message(self, header: str, body: str | None) -> str:
        """헤더 + 본문을 단일 텔레그램 메시지로 조립 (필요 시 비례 축소)."""
        if not body:
            return header
        separator = "\n\n" + "\u2500" * 20 + "\n"
        max_body = TELEGRAM_MAX_LENGTH - len(header) - len(separator)
        trimmed = self._trim_to_fit(body, max_body)
        return header + separator + trimmed

    async def notify(self, event: SchedulerEvent) -> None:
        summary = self._read_summary(event)
        if summary:
            summary = self._format_for_telegram(summary)
        header = self._format_header(event)
        message = self._build_single_message(header, summary)
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                await client.post(
                    f"{self._base_url}/sendMessage",
                    json={"chat_id": self._chat_id, "text": message},
                )
        except Exception:
            logger.warning("Telegram notification failed for job '%s'", event.job, exc_info=True)
