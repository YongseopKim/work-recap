"""Notifier ABC + LogNotifier + TelegramNotifier 테스트."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

from workrecap.scheduler.notifier import LogNotifier, Notifier, SchedulerEvent


class TestSchedulerEvent:
    def test_event_creation(self):
        event = SchedulerEvent(
            job="daily",
            status="success",
            triggered_at="2026-02-28T02:00:00+09:00",
            completed_at="2026-02-28T02:05:00+09:00",
            target="2026-02-27",
        )
        assert event.job == "daily"
        assert event.error is None

    def test_event_with_error(self):
        event = SchedulerEvent(
            job="daily",
            status="failed",
            triggered_at="2026-02-28T02:00:00+09:00",
            target="2026-02-27",
            error="FetchError: timeout",
        )
        assert event.status == "failed"
        assert event.error == "FetchError: timeout"


class TestLogNotifier:
    def test_is_notifier_subclass(self):
        assert issubclass(LogNotifier, Notifier)

    def test_notify_success(self, caplog):
        notifier = LogNotifier()
        event = SchedulerEvent(
            job="daily",
            status="success",
            triggered_at="t1",
            target="2026-02-27",
        )
        with caplog.at_level(logging.INFO, logger="workrecap.scheduler.notifier"):
            asyncio.run(notifier.notify(event))
        assert "daily" in caplog.text
        assert "success" in caplog.text

    def test_notify_failure(self, caplog):
        notifier = LogNotifier()
        event = SchedulerEvent(
            job="daily",
            status="failed",
            triggered_at="t1",
            target="2026-02-27",
            error="boom",
        )
        with caplog.at_level(logging.ERROR, logger="workrecap.scheduler.notifier"):
            asyncio.run(notifier.notify(event))
        assert "failed" in caplog.text
        assert "boom" in caplog.text


class TestCompositeNotifier:
    def test_is_notifier_subclass(self):
        from workrecap.scheduler.notifier import CompositeNotifier

        assert issubclass(CompositeNotifier, Notifier)

    def test_calls_all_notifiers(self):
        from workrecap.scheduler.notifier import CompositeNotifier

        n1 = AsyncMock(spec=Notifier)
        n2 = AsyncMock(spec=Notifier)
        composite = CompositeNotifier([n1, n2])
        event = SchedulerEvent(
            job="daily", status="success", triggered_at="t1", target="2026-02-27"
        )
        asyncio.run(composite.notify(event))
        n1.notify.assert_awaited_once_with(event)
        n2.notify.assert_awaited_once_with(event)

    def test_continues_on_failure(self, caplog):
        from workrecap.scheduler.notifier import CompositeNotifier

        n1 = AsyncMock(spec=Notifier)
        n1.notify.side_effect = RuntimeError("boom")
        n2 = AsyncMock(spec=Notifier)
        composite = CompositeNotifier([n1, n2])
        event = SchedulerEvent(job="daily", status="failed", triggered_at="t1", target="2026-02-27")
        with caplog.at_level(logging.WARNING):
            asyncio.run(composite.notify(event))
        n2.notify.assert_awaited_once_with(event)


class TestTelegramNotifier:
    def _make_notifier(self, tmp_path):
        from workrecap.scheduler.notifier import TelegramNotifier

        config = MagicMock()
        config.daily_telegram_path.return_value = tmp_path / "daily.telegram.txt"
        config.weekly_telegram_path.return_value = tmp_path / "weekly.telegram.txt"
        config.monthly_telegram_path.return_value = tmp_path / "monthly.telegram.txt"
        config.yearly_telegram_path.return_value = tmp_path / "yearly.telegram.txt"
        return TelegramNotifier("fake-token", "12345", config)

    def test_is_notifier_subclass(self):
        from workrecap.scheduler.notifier import TelegramNotifier

        assert issubclass(TelegramNotifier, Notifier)

    def test_format_header_success(self, tmp_path):
        notifier = self._make_notifier(tmp_path)
        event = SchedulerEvent(
            job="daily",
            status="success",
            triggered_at="2026-02-28T02:00:00",
            completed_at="2026-02-28T02:05:23",
            target="2026-02-27",
        )
        header = notifier._format_header(event)
        assert "daily" in header
        assert "2026-02-27" in header
        assert "\u2705" in header

    def test_format_header_failure(self, tmp_path):
        notifier = self._make_notifier(tmp_path)
        event = SchedulerEvent(
            job="daily",
            status="failed",
            triggered_at="2026-02-28T02:00:00",
            completed_at="2026-02-28T02:05:23",
            target="2026-02-27",
            error="FetchError: timeout",
        )
        header = notifier._format_header(event)
        assert "\u274c" in header
        assert "FetchError: timeout" in header

    def test_resolve_telegram_path_daily(self, tmp_path):
        notifier = self._make_notifier(tmp_path)
        path = notifier._resolve_telegram_path("daily", "2026-02-27")
        assert path == tmp_path / "daily.telegram.txt"

    def test_resolve_telegram_path_weekly(self, tmp_path):
        notifier = self._make_notifier(tmp_path)
        path = notifier._resolve_telegram_path("weekly", "2026-W09")
        assert path == tmp_path / "weekly.telegram.txt"

    def test_resolve_telegram_path_monthly(self, tmp_path):
        notifier = self._make_notifier(tmp_path)
        path = notifier._resolve_telegram_path("monthly", "2026-02")
        assert path == tmp_path / "monthly.telegram.txt"

    def test_resolve_telegram_path_yearly(self, tmp_path):
        notifier = self._make_notifier(tmp_path)
        path = notifier._resolve_telegram_path("yearly", "2026")
        assert path == tmp_path / "yearly.telegram.txt"

    def test_read_summary_reads_telegram_txt(self, tmp_path):
        notifier = self._make_notifier(tmp_path)
        tg_path = tmp_path / "daily.telegram.txt"
        tg_path.write_text("📋 개요\n텔레그램 요약 내용")

        event = SchedulerEvent(
            job="daily", status="success", triggered_at="t1", target="2026-02-27"
        )
        result = notifier._read_summary(event)
        assert result == "📋 개요\n텔레그램 요약 내용"

    def test_read_summary_returns_none_when_missing(self, tmp_path):
        notifier = self._make_notifier(tmp_path)
        event = SchedulerEvent(
            job="daily", status="success", triggered_at="t1", target="2026-02-27"
        )
        result = notifier._read_summary(event)
        assert result is None

    def test_read_summary_returns_none_on_failure_event(self, tmp_path):
        notifier = self._make_notifier(tmp_path)
        event = SchedulerEvent(
            job="daily",
            status="failed",
            triggered_at="t1",
            target="2026-02-27",
            error="boom",
        )
        result = notifier._read_summary(event)
        assert result is None

    def test_build_message_no_body(self, tmp_path):
        notifier = self._make_notifier(tmp_path)
        result = notifier._build_message("header", None)
        assert result == "header"

    def test_build_message_with_body(self, tmp_path):
        notifier = self._make_notifier(tmp_path)
        result = notifier._build_message("header", "body content")
        assert "header" in result
        assert "body content" in result
        assert "\u2500" in result

    def test_build_message_trims_long_body(self, tmp_path):
        from workrecap.scheduler.notifier import TELEGRAM_MAX_LENGTH

        notifier = self._make_notifier(tmp_path)
        long_body = "가" * 5000
        result = notifier._build_message("header", long_body)
        assert len(result) <= TELEGRAM_MAX_LENGTH

    @patch("workrecap.scheduler.notifier.httpx.AsyncClient")
    def test_notify_sends_single_message(self, mock_client_cls, tmp_path):
        notifier = self._make_notifier(tmp_path)
        tg_path = tmp_path / "daily.telegram.txt"
        tg_path.write_text("📋 개요\nTelegram summary content")

        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        event = SchedulerEvent(
            job="daily",
            status="success",
            triggered_at="2026-02-28T02:00:00",
            completed_at="2026-02-28T02:05:23",
            target="2026-02-27",
        )
        asyncio.run(notifier.notify(event))
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert "sendMessage" in call_args[0][0]

    @patch("workrecap.scheduler.notifier.httpx.AsyncClient")
    def test_notify_graceful_on_http_error(self, mock_client_cls, tmp_path, caplog):
        notifier = self._make_notifier(tmp_path)
        mock_client = AsyncMock()
        mock_client.post.side_effect = Exception("network error")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        event = SchedulerEvent(
            job="daily",
            status="success",
            triggered_at="t1",
            target="2026-02-27",
        )
        with caplog.at_level(logging.WARNING):
            asyncio.run(notifier.notify(event))
        assert "Telegram" in caplog.text
