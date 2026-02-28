"""ScheduleConfig — schedule.toml 파싱 테스트."""

import textwrap

import pytest

from workrecap.scheduler.config import ScheduleConfig


@pytest.fixture()
def schedule_toml(tmp_path):
    p = tmp_path / "schedule.toml"
    p.write_text(
        textwrap.dedent("""\
        [scheduler]
        enabled = true
        timezone = "Asia/Seoul"

        [scheduler.daily]
        time = "02:00"
        enrich = true
        batch = false
        workers = 5

        [scheduler.weekly]
        day = "mon"
        time = "03:00"

        [scheduler.monthly]
        day = 1
        time = "04:00"

        [scheduler.yearly]
        month = 1
        day = 1
        time = "05:00"

        [scheduler.notification]
        on_failure = true
        on_success = false
        """)
    )
    return p


class TestScheduleConfig:
    def test_load_full_config(self, schedule_toml):
        cfg = ScheduleConfig.from_toml(schedule_toml)
        assert cfg.enabled is True
        assert cfg.timezone == "Asia/Seoul"

    def test_daily_config(self, schedule_toml):
        cfg = ScheduleConfig.from_toml(schedule_toml)
        assert cfg.daily.time == "02:00"
        assert cfg.daily.enrich is True
        assert cfg.daily.batch is False
        assert cfg.daily.workers == 5

    def test_weekly_config(self, schedule_toml):
        cfg = ScheduleConfig.from_toml(schedule_toml)
        assert cfg.weekly.day == "mon"
        assert cfg.weekly.time == "03:00"

    def test_monthly_config(self, schedule_toml):
        cfg = ScheduleConfig.from_toml(schedule_toml)
        assert cfg.monthly.day == 1
        assert cfg.monthly.time == "04:00"

    def test_yearly_config(self, schedule_toml):
        cfg = ScheduleConfig.from_toml(schedule_toml)
        assert cfg.yearly.month == 1
        assert cfg.yearly.day == 1
        assert cfg.yearly.time == "05:00"

    def test_notification_config(self, schedule_toml):
        cfg = ScheduleConfig.from_toml(schedule_toml)
        assert cfg.notification.on_failure is True
        assert cfg.notification.on_success is False

    def test_defaults_when_minimal(self, tmp_path):
        p = tmp_path / "schedule.toml"
        p.write_text("[scheduler]\n")
        cfg = ScheduleConfig.from_toml(p)
        assert cfg.enabled is False
        assert cfg.timezone == "Asia/Seoul"
        assert cfg.daily.time == "02:00"

    def test_missing_file_returns_defaults(self, tmp_path):
        cfg = ScheduleConfig.from_toml(tmp_path / "nonexistent.toml")
        assert cfg.enabled is False

    def test_daily_hour_minute(self, schedule_toml):
        cfg = ScheduleConfig.from_toml(schedule_toml)
        assert cfg.daily.hour == 2
        assert cfg.daily.minute == 0
