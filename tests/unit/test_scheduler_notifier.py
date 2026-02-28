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
        config.daily_summary_path.return_value = tmp_path / "daily.md"
        config.weekly_summary_path.return_value = tmp_path / "weekly.md"
        config.monthly_summary_path.return_value = tmp_path / "monthly.md"
        config.yearly_summary_path.return_value = tmp_path / "yearly.md"
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
        assert "\u2705" in header  # checkmark

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
        assert "\u274c" in header  # cross mark
        assert "FetchError: timeout" in header

    def test_resolve_summary_path_daily(self, tmp_path):
        notifier = self._make_notifier(tmp_path)
        path = notifier._resolve_summary_path("daily", "2026-02-27")
        assert path == tmp_path / "daily.md"

    def test_resolve_summary_path_weekly(self, tmp_path):
        notifier = self._make_notifier(tmp_path)
        path = notifier._resolve_summary_path("weekly", "2026-W09")
        assert path == tmp_path / "weekly.md"

    def test_resolve_summary_path_monthly(self, tmp_path):
        notifier = self._make_notifier(tmp_path)
        path = notifier._resolve_summary_path("monthly", "2026-02")
        assert path == tmp_path / "monthly.md"

    def test_resolve_summary_path_yearly(self, tmp_path):
        notifier = self._make_notifier(tmp_path)
        path = notifier._resolve_summary_path("yearly", "2026")
        assert path == tmp_path / "yearly.md"

    def test_split_messages_short(self, tmp_path):
        notifier = self._make_notifier(tmp_path)
        msgs = notifier._split_messages("header", "short body")
        assert len(msgs) == 1
        assert "header" in msgs[0]
        assert "short body" in msgs[0]

    def test_split_messages_long(self, tmp_path):
        notifier = self._make_notifier(tmp_path)
        long_body = "x" * 5000
        msgs = notifier._split_messages("header", long_body)
        assert len(msgs) >= 2
        assert "header" in msgs[0]

    @patch("workrecap.scheduler.notifier.httpx.AsyncClient")
    def test_notify_sends_message(self, mock_client_cls, tmp_path):
        notifier = self._make_notifier(tmp_path)
        summary_path = tmp_path / "daily.md"
        summary_path.write_text("# Daily Summary\nSome content")

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
        mock_client.post.assert_called()
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
