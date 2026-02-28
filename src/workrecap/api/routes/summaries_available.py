"""Summary 파일 존재 여부 조회 — 캘린더 뷰에서 사용."""

import calendar
from datetime import date

from fastapi import APIRouter, Depends, Query

from workrecap.api.deps import get_config
from workrecap.config import AppConfig

router = APIRouter()


def _weeks_overlapping_month(year: int, month: int) -> set[str]:
    """해당 월과 겹치는 모든 ISO week 번호(W06 형식)를 반환."""
    seen: set[str] = set()
    num_days = calendar.monthrange(year, month)[1]
    for day in range(1, num_days + 1):
        iso_y, iso_w, _ = date(year, month, day).isocalendar()
        if iso_y == year:
            seen.add(f"W{iso_w:02d}")
    return seen


@router.get("/available")
def get_available_summaries(
    year: int = Query(...),
    month: int = Query(...),
    config: AppConfig = Depends(get_config),
):
    summaries_year_dir = config.summaries_dir / str(year)
    month_str = f"{month:02d}"

    # Daily: data/summaries/{year}/daily/{MM}-{DD}.md
    daily: list[str] = []
    daily_dir = summaries_year_dir / "daily"
    if daily_dir.exists():
        for f in sorted(daily_dir.glob(f"{month_str}-*.md")):
            daily.append(f.stem)

    # Weekly: data/summaries/{year}/weekly/W{NN}.md — 해당 월과 겹치는 주차만
    weekly: list[str] = []
    weekly_dir = summaries_year_dir / "weekly"
    overlapping = _weeks_overlapping_month(year, month)
    if weekly_dir.exists():
        for f in sorted(weekly_dir.glob("W*.md")):
            if f.stem in overlapping:
                weekly.append(f.stem)

    # Monthly: data/summaries/{year}/monthly/{MM}.md
    monthly: list[str] = []
    monthly_path = summaries_year_dir / "monthly" / f"{month_str}.md"
    if monthly_path.exists():
        monthly.append(month_str)

    # Yearly: data/summaries/{year}/yearly.md
    yearly = (summaries_year_dir / "yearly.md").exists()

    return {
        "daily": daily,
        "weekly": weekly,
        "monthly": monthly,
        "yearly": yearly,
    }
