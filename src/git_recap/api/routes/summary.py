"""Summary 조회 엔드포인트 — 생성된 .md 파일 읽기."""

from fastapi import APIRouter, Depends, HTTPException

from git_recap.api.deps import get_config
from git_recap.config import AppConfig

router = APIRouter()


def _read_summary(path) -> dict:
    """Summary 파일 읽어서 반환. 없으면 404."""
    if not path.exists():
        raise HTTPException(status_code=404, detail="Summary not found")
    content = path.read_text(encoding="utf-8")
    return {"content": content, "path": str(path)}


@router.get("/daily/{date}")
def get_daily_summary(date: str, config: AppConfig = Depends(get_config)):
    return _read_summary(config.daily_summary_path(date))


@router.get("/weekly/{year}/{week}")
def get_weekly_summary(year: int, week: int, config: AppConfig = Depends(get_config)):
    return _read_summary(config.weekly_summary_path(year, week))


@router.get("/monthly/{year}/{month}")
def get_monthly_summary(year: int, month: int, config: AppConfig = Depends(get_config)):
    return _read_summary(config.monthly_summary_path(year, month))


@router.get("/yearly/{year}")
def get_yearly_summary(year: int, config: AppConfig = Depends(get_config)):
    return _read_summary(config.yearly_summary_path(year))
