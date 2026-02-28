"""schedule.toml 파싱 — 스케줄 설정 모델."""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DailySchedule:
    time: str = "02:00"
    enrich: bool = True
    batch: bool = False
    workers: int = 5

    @property
    def hour(self) -> int:
        return int(self.time.split(":")[0])

    @property
    def minute(self) -> int:
        return int(self.time.split(":")[1])


@dataclass
class WeeklySchedule:
    day: str = "mon"
    time: str = "03:00"

    @property
    def hour(self) -> int:
        return int(self.time.split(":")[0])

    @property
    def minute(self) -> int:
        return int(self.time.split(":")[1])


@dataclass
class MonthlySchedule:
    day: int = 1
    time: str = "04:00"

    @property
    def hour(self) -> int:
        return int(self.time.split(":")[0])

    @property
    def minute(self) -> int:
        return int(self.time.split(":")[1])


@dataclass
class YearlySchedule:
    month: int = 1
    day: int = 1
    time: str = "05:00"

    @property
    def hour(self) -> int:
        return int(self.time.split(":")[0])

    @property
    def minute(self) -> int:
        return int(self.time.split(":")[1])


@dataclass
class NotificationConfig:
    on_failure: bool = True
    on_success: bool = False


@dataclass
class ScheduleConfig:
    enabled: bool = False
    timezone: str = "Asia/Seoul"
    daily: DailySchedule = field(default_factory=DailySchedule)
    weekly: WeeklySchedule = field(default_factory=WeeklySchedule)
    monthly: MonthlySchedule = field(default_factory=MonthlySchedule)
    yearly: YearlySchedule = field(default_factory=YearlySchedule)
    notification: NotificationConfig = field(default_factory=NotificationConfig)

    @classmethod
    def from_toml(cls, path: Path) -> "ScheduleConfig":
        if not path.exists():
            return cls()
        with open(path, "rb") as f:
            data = tomllib.load(f)
        sched = data.get("scheduler", {})
        return cls(
            enabled=sched.get("enabled", False),
            timezone=sched.get("timezone", "Asia/Seoul"),
            daily=DailySchedule(**sched.get("daily", {})),
            weekly=WeeklySchedule(**sched.get("weekly", {})),
            monthly=MonthlySchedule(**sched.get("monthly", {})),
            yearly=YearlySchedule(**sched.get("yearly", {})),
            notification=NotificationConfig(**sched.get("notification", {})),
        )
