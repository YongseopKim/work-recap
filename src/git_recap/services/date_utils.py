"""날짜 범위 유틸리티 함수."""

import calendar
from datetime import date, timedelta


def date_range(since: str, until: str) -> list[str]:
    """Inclusive 날짜 리스트 반환."""
    start = date.fromisoformat(since)
    end = date.fromisoformat(until)
    result: list[str] = []
    current = start
    while current <= end:
        result.append(current.isoformat())
        current += timedelta(days=1)
    return result


def weekly_range(year: int, week: int) -> tuple[str, str]:
    """ISO 주 번호 → (월요일, 일요일) 날짜."""
    monday = date.fromisocalendar(year, week, 1)
    sunday = monday + timedelta(days=6)
    return monday.isoformat(), sunday.isoformat()


def monthly_range(year: int, month: int) -> tuple[str, str]:
    """월 → (1일, 말일) 날짜."""
    first = date(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    last = date(year, month, last_day)
    return first.isoformat(), last.isoformat()


def yearly_range(year: int) -> tuple[str, str]:
    """연도 → (1/1, 12/31) 날짜."""
    return date(year, 1, 1).isoformat(), date(year, 12, 31).isoformat()


def monthly_chunks(since: str, until: str) -> list[tuple[str, str]]:
    """날짜 범위를 월 단위 (start, end) 쌍으로 분할."""
    start = date.fromisoformat(since)
    end = date.fromisoformat(until)
    if start > end:
        return []

    chunks: list[tuple[str, str]] = []
    current = start
    while current <= end:
        last_day = calendar.monthrange(current.year, current.month)[1]
        month_end = date(current.year, current.month, last_day)
        chunk_end = min(month_end, end)
        chunks.append((current.isoformat(), chunk_end.isoformat()))
        current = chunk_end + timedelta(days=1)
    return chunks


def catchup_range(last_fetch_date: str) -> tuple[str, str]:
    """Checkpoint 다음 날 ~ 오늘."""
    last = date.fromisoformat(last_fetch_date)
    today = date.today()
    since = last + timedelta(days=1)
    return since.isoformat(), today.isoformat()
