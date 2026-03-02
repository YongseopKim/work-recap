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


class TestFormatForTelegram:
    """TelegramNotifier._format_for_telegram 변환 테스트."""

    def test_strips_h1_title(self):
        from workrecap.scheduler.notifier import TelegramNotifier

        md = "# Daily Summary: 2026-03-01\n\n## 개요\n내용"
        result = TelegramNotifier._format_for_telegram(md)
        assert "# Daily Summary" not in result
        assert "📋 개요" in result
        assert "내용" in result

    def test_adds_emojis_to_known_headings(self):
        from workrecap.scheduler.notifier import TelegramNotifier

        md = "## 개요\n\n## 주요 활동\n\n### 커밋\n\n### PR\n\n### 이슈\n\n### 리뷰"
        result = TelegramNotifier._format_for_telegram(md)
        assert "📋 개요" in result
        assert "📌 주요 활동" in result
        assert "💻 커밋" in result
        assert "🔀 PR" in result
        assert "🎯 이슈" in result
        assert "👀 리뷰" in result

    def test_no_emoji_for_unknown_heading(self):
        from workrecap.scheduler.notifier import TelegramNotifier

        md = "## 기타 섹션"
        result = TelegramNotifier._format_for_telegram(md)
        assert result == "기타 섹션"

    def test_strips_heading_markers(self):
        from workrecap.scheduler.notifier import TelegramNotifier

        md = "## 주요 활동\n\n### 커밋\n\n#### 🔧 프로젝트 초기화"
        result = TelegramNotifier._format_for_telegram(md)
        assert "##" not in result
        assert "📌 주요 활동" in result
        assert "💻 커밋" in result
        assert "🔧 프로젝트 초기화" in result

    def test_strips_horizontal_rules(self):
        from workrecap.scheduler.notifier import TelegramNotifier

        md = "위\n\n---\n\n아래"
        result = TelegramNotifier._format_for_telegram(md)
        assert "---" not in result
        assert "위" in result
        assert "아래" in result

    def test_transforms_list_item_with_type_tag(self):
        from workrecap.scheduler.notifier import TelegramNotifier

        md = (
            "- [First commit](https://github.com/user/repo/commit/abc123): "
            "macOS 환경 설정 일괄 저장. **chore/setup**"
        )
        result = TelegramNotifier._format_for_telegram(md)
        assert result == "chore/setup: macOS 환경 설정 일괄 저장."

    def test_transforms_list_item_without_type_tag(self):
        from workrecap.scheduler.notifier import TelegramNotifier

        md = "- [Fix bug](https://github.com/user/repo/commit/abc): 버그 수정 내용"
        result = TelegramNotifier._format_for_telegram(md)
        assert result == "버그 수정 내용"

    def test_strips_bold_markers(self):
        from workrecap.scheduler.notifier import TelegramNotifier

        md = "이것은 **강조** 텍스트"
        result = TelegramNotifier._format_for_telegram(md)
        assert result == "이것은 강조 텍스트"

    def test_strips_markdown_links(self):
        from workrecap.scheduler.notifier import TelegramNotifier

        md = "[링크 텍스트](https://example.com) 설명"
        result = TelegramNotifier._format_for_telegram(md)
        assert result == "링크 텍스트 설명"

    def test_strips_parenthesized_urls(self):
        from workrecap.scheduler.notifier import TelegramNotifier

        md = "- [Tag] 설명 (https://github.com/org/repo/pull/123)"
        result = TelegramNotifier._format_for_telegram(md)
        assert "https://" not in result
        assert "Tag" in result

    def test_collapses_multiple_blank_lines(self):
        from workrecap.scheduler.notifier import TelegramNotifier

        md = "A\n\n\n\n\nB"
        result = TelegramNotifier._format_for_telegram(md)
        assert result == "A\n\nB"

    def test_full_daily_summary_conversion(self):
        from workrecap.scheduler.notifier import TelegramNotifier

        md = """\
# Daily Summary: 2026-03-01

## 개요
오늘은 집중적인 개발 세션이었다.

---

## 주요 활동

### 커밋

#### 🔧 my-setup 초기화

- [First commit](https://github.com/user/repo/commit/abc): macOS 환경 설정. **chore/setup**
- [Add config](https://github.com/user/repo/commit/def): 설정 추가. **feature**

#### 🚀 work-recap: 배포 자동화

- [feat: add launchd](https://github.com/user/repo/commit/ghi): launchd 추가. **feature**"""

        result = TelegramNotifier._format_for_telegram(md)

        # H1 제거됨
        assert "# Daily Summary" not in result
        # 이모지 추가됨
        assert "📋 개요" in result
        assert "📌 주요 활동" in result
        assert "💻 커밋" in result
        # H4 그대로 유지
        assert "🔧 my-setup 초기화" in result
        assert "🚀 work-recap: 배포 자동화" in result
        # 리스트 아이템 변환
        assert "chore/setup: macOS 환경 설정." in result
        assert "feature: 설정 추가." in result
        assert "feature: launchd 추가." in result
        # 마크다운 문법 없음
        assert "**" not in result
        assert "---" not in result
        assert "https://" not in result


class TestTrimToFit:
    """TelegramNotifier._trim_to_fit 비례 축소 테스트."""

    def test_short_body_unchanged(self):
        from workrecap.scheduler.notifier import TelegramNotifier

        body = "short content"
        assert TelegramNotifier._trim_to_fit(body, 1000) == body

    def test_trims_item_groups_proportionally(self):
        from workrecap.scheduler.notifier import TelegramNotifier

        # 그룹 A: 10 아이템, 그룹 B: 2 아이템 (충분히 긴 설명)
        group_a = "\n".join(f"feature: description of item number {i} with details" for i in range(10))
        group_b = "\n".join(f"bugfix: fix for issue number {i} with details" for i in range(2))
        body = f"📋 개요\n요약\n\n🔧 RepoA\n\n{group_a}\n\n🚀 RepoB\n\n{group_b}"

        # body보다 짧은 limit으로 trimming 유도
        result = TelegramNotifier._trim_to_fit(body, len(body) // 2)

        # 그룹 A가 더 많이 잘림 (원래 10 > 2)
        a_items = [ln for ln in result.split("\n") if ln.startswith("feature:")]
        b_items = [ln for ln in result.split("\n") if ln.startswith("bugfix:")]
        assert len(a_items) < 10
        assert len(b_items) >= 1
        # 비례 유지: A 아이템 >= B 아이템 (5:1 비율이니까)
        assert len(a_items) >= len(b_items)

    def test_adds_remaining_count(self):
        from workrecap.scheduler.notifier import TelegramNotifier

        items = "\n".join(f"feature: description number {i} for testing" for i in range(20))
        body = f"개요\n\n{items}"
        result = TelegramNotifier._trim_to_fit(body, 500)
        assert "...외" in result
        assert "건" in result

    def test_preserves_non_item_lines(self):
        from workrecap.scheduler.notifier import TelegramNotifier

        items = "\n".join(f"feature: item {i}" for i in range(20))
        body = f"📋 개요\n요약 텍스트\n\n📌 주요 활동\n\n💻 커밋\n\n🔧 제목\n\n{items}"
        result = TelegramNotifier._trim_to_fit(body, 400)
        assert "📋 개요" in result
        assert "📌 주요 활동" in result
        assert "💻 커밋" in result
        assert "🔧 제목" in result

    def test_fallback_truncation(self):
        from workrecap.scheduler.notifier import TelegramNotifier

        # 아이템 없는 긴 텍스트
        body = "가" * 5000
        result = TelegramNotifier._trim_to_fit(body, 500)
        assert len(result) <= 500
        assert result.endswith("...계속")


class TestBuildSingleMessage:
    """_build_single_message 단일 메시지 조립 테스트."""

    def _make_notifier(self, tmp_path):
        from workrecap.scheduler.notifier import TelegramNotifier

        config = MagicMock()
        return TelegramNotifier("token", "123", config)

    def test_no_body(self, tmp_path):
        notifier = self._make_notifier(tmp_path)
        result = notifier._build_single_message("header", None)
        assert result == "header"

    def test_short_body_fits(self, tmp_path):
        notifier = self._make_notifier(tmp_path)
        result = notifier._build_single_message("header", "body")
        assert "header" in result
        assert "body" in result
        assert "\u2500" in result  # separator

    def test_long_body_trimmed_to_fit(self, tmp_path):
        from workrecap.scheduler.notifier import TELEGRAM_MAX_LENGTH

        notifier = self._make_notifier(tmp_path)
        items = "\n".join(f"feature: long description item number {i}" for i in range(100))
        body = f"개요\n요약\n\n{items}"
        result = notifier._build_single_message("header", body)
        assert len(result) <= TELEGRAM_MAX_LENGTH


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

    @patch("workrecap.scheduler.notifier.httpx.AsyncClient")
    def test_notify_sends_single_message(self, mock_client_cls, tmp_path):
        notifier = self._make_notifier(tmp_path)
        summary_path = tmp_path / "daily.md"
        summary_path.write_text("# Daily Summary\n\n## 개요\nSome content")

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
        # 단일 메시지 전송
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
