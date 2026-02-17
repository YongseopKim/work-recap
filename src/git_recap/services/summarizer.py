"""Activity/Stats 데이터와 하위 summary를 LLM에 전달하여 markdown summary를 생성."""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from git_recap.services.daily_state import DailyStateStore

from jinja2 import Template

from git_recap.config import AppConfig
from git_recap.exceptions import SummarizeError
from git_recap.infra.llm_client import LLMClient
from git_recap.models import load_json, load_jsonl
from git_recap.services.date_utils import date_range

logger = logging.getLogger(__name__)


class SummarizerService:
    def __init__(
        self,
        config: AppConfig,
        llm_client: LLMClient,
        daily_state: DailyStateStore | None = None,
    ) -> None:
        self._config = config
        self._llm = llm_client
        self._daily_state = daily_state

    # ── Public API ──

    def daily(self, target_date: str) -> Path:
        """Daily summary 생성."""
        norm_dir = self._config.date_normalized_dir(target_date)
        activities_path = norm_dir / "activities.jsonl"
        stats_path = norm_dir / "stats.json"

        if not activities_path.exists():
            raise SummarizeError(f"Activities file not found: {activities_path}")
        if not stats_path.exists():
            raise SummarizeError(f"Stats file not found: {stats_path}")

        activities = load_jsonl(activities_path)
        stats = load_json(stats_path)

        system_prompt = self._render_prompt("daily.md", date=target_date, stats=stats)
        user_content = self._format_activities(activities)

        response = self._llm.chat(system_prompt, user_content)

        output_path = self._config.daily_summary_path(target_date)
        self._save_markdown(output_path, response)

        logger.info("Generated daily summary: %s", output_path)

        self._update_checkpoint(target_date)
        return output_path

    def daily_range(self, since: str, until: str, force: bool = False) -> list[dict]:
        """날짜 범위 순회하며 daily summary 생성. skip/force/resilience 지원."""
        dates = date_range(since, until)
        results: list[dict] = []

        for d in dates:
            try:
                if not force and self._is_date_summarized(d):
                    results.append({"date": d, "status": "skipped"})
                    continue

                self.daily(d)
                results.append({"date": d, "status": "success"})
            except Exception as e:
                logger.warning("Failed to summarize %s: %s", d, e)
                results.append({"date": d, "status": "failed", "error": str(e)})

        return results

    def weekly(self, year: int, week: int, force: bool = False) -> Path:
        """Weekly summary 생성."""
        output_path = self._config.weekly_summary_path(year, week)
        if not force and not self._is_stale(output_path, self._daily_paths_for_week(year, week)):
            logger.info("Weekly summary already exists, skipping: %s", output_path)
            return output_path

        daily_contents = self._collect_daily_for_week(year, week)
        if not daily_contents:
            raise SummarizeError(f"No daily summaries found for {year}-W{week:02d}")

        system_prompt = self._render_prompt("weekly.md", year=year, week=week)
        user_content = "\n\n---\n\n".join(daily_contents)

        response = self._llm.chat(system_prompt, user_content)

        self._save_markdown(output_path, response)

        logger.info("Generated weekly summary: %s", output_path)
        return output_path

    def monthly(self, year: int, month: int, force: bool = False) -> Path:
        """Monthly summary 생성."""
        output_path = self._config.monthly_summary_path(year, month)
        if not force and not self._is_stale(output_path, self._weekly_paths_for_month(year, month)):
            logger.info("Monthly summary already exists, skipping: %s", output_path)
            return output_path

        weekly_contents = self._collect_weekly_for_month(year, month)
        if not weekly_contents:
            raise SummarizeError(f"No weekly summaries found for {year}-{month:02d}")

        system_prompt = self._render_prompt("monthly.md", year=year, month=month)
        user_content = "\n\n---\n\n".join(weekly_contents)

        response = self._llm.chat(system_prompt, user_content)

        self._save_markdown(output_path, response)

        logger.info("Generated monthly summary: %s", output_path)
        return output_path

    def yearly(self, year: int, force: bool = False) -> Path:
        """Yearly summary 생성."""
        output_path = self._config.yearly_summary_path(year)
        if not force and not self._is_stale(output_path, self._monthly_paths_for_year(year)):
            logger.info("Yearly summary already exists, skipping: %s", output_path)
            return output_path

        monthly_contents = []
        for m in range(1, 13):
            path = self._config.monthly_summary_path(year, m)
            if path.exists():
                monthly_contents.append(path.read_text(encoding="utf-8"))

        if not monthly_contents:
            raise SummarizeError(f"No monthly summaries found for {year}")

        system_prompt = self._render_prompt("yearly.md", year=year)
        user_content = "\n\n---\n\n".join(monthly_contents)

        response = self._llm.chat(system_prompt, user_content)

        self._save_markdown(output_path, response)

        logger.info("Generated yearly summary: %s", output_path)
        return output_path

    def query(self, question: str, months_back: int = 3) -> str:
        """자유 질문 응답. 최근 N개월 monthly.md를 context로 사용."""
        context = self._collect_recent_context(months_back)
        if not context:
            raise SummarizeError("No summary data available for query context")

        system_prompt = self._render_prompt("query.md")
        user_content = f"## Context\n\n{context}\n\n## 질문\n\n{question}"

        return self._llm.chat(system_prompt, user_content)

    # ── Staleness 체크 ──

    @staticmethod
    def _is_stale(output_path: Path, input_paths: list[Path]) -> bool:
        """output이 없거나 input 중 하나라도 output보다 새로우면 stale."""
        if not output_path.exists():
            return True
        output_mtime = output_path.stat().st_mtime
        return any(p.exists() and p.stat().st_mtime > output_mtime for p in input_paths)

    def _daily_paths_for_week(self, year: int, week: int) -> list[Path]:
        """ISO week의 daily summary 경로 (존재하는 것만)."""
        monday = date.fromisocalendar(year, week, 1)
        paths = []
        for i in range(7):
            d = monday + timedelta(days=i)
            p = self._config.daily_summary_path(d.isoformat())
            if p.exists():
                paths.append(p)
        return paths

    def _weekly_paths_for_month(self, year: int, month: int) -> list[Path]:
        """해당 월에 걸치는 weekly summary 경로 (존재하는 것만)."""
        first_day = date(year, month, 1)
        if month == 12:
            last_day = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            last_day = date(year, month + 1, 1) - timedelta(days=1)

        paths = []
        seen_weeks: set[tuple[int, int]] = set()
        d = first_day
        while d <= last_day:
            iso_y, iso_w, _ = d.isocalendar()
            if (iso_y, iso_w) not in seen_weeks:
                seen_weeks.add((iso_y, iso_w))
                p = self._config.weekly_summary_path(iso_y, iso_w)
                if p.exists():
                    paths.append(p)
            d += timedelta(days=7)
        return paths

    def _monthly_paths_for_year(self, year: int) -> list[Path]:
        """1~12월 monthly summary 경로 (존재하는 것만)."""
        paths = []
        for m in range(1, 13):
            p = self._config.monthly_summary_path(year, m)
            if p.exists():
                paths.append(p)
        return paths

    # ── 파일 수집 ──

    def _collect_daily_for_week(self, year: int, week: int) -> list[str]:
        """ISO week 기준으로 해당 주의 daily.md 파일 내용 수집."""
        monday = date.fromisocalendar(year, week, 1)
        contents = []

        for i in range(7):
            d = monday + timedelta(days=i)
            date_str = d.isoformat()
            path = self._config.daily_summary_path(date_str)
            if path.exists():
                contents.append(path.read_text(encoding="utf-8"))

        return contents

    def _collect_weekly_for_month(self, year: int, month: int) -> list[str]:
        """해당 월에 걸치는 주의 weekly.md 수집."""
        first_day = date(year, month, 1)
        if month == 12:
            last_day = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            last_day = date(year, month + 1, 1) - timedelta(days=1)

        contents = []
        seen_weeks: set[tuple[int, int]] = set()
        d = first_day

        while d <= last_day:
            iso_y, iso_w, _ = d.isocalendar()
            if (iso_y, iso_w) not in seen_weeks:
                seen_weeks.add((iso_y, iso_w))
                path = self._config.weekly_summary_path(iso_y, iso_w)
                if path.exists():
                    contents.append(path.read_text(encoding="utf-8"))
            d += timedelta(days=7)

        return contents

    def _collect_recent_context(self, months_back: int) -> str:
        """최근 N개월 monthly summary 수집."""
        today = date.today()
        contents = []

        for i in range(months_back):
            target_month = today.month - i
            target_year = today.year
            while target_month <= 0:
                target_month += 12
                target_year -= 1

            path = self._config.monthly_summary_path(target_year, target_month)
            if path.exists():
                contents.append(path.read_text(encoding="utf-8"))

        return "\n\n---\n\n".join(contents)

    # ── Checkpoint / Skip ──

    def _is_date_summarized(self, date_str: str) -> bool:
        """daily_state 있으면 timestamp cascade 체크, 없으면 파일 존재 체크."""
        if self._daily_state is not None:
            return not self._daily_state.is_summarize_stale(date_str)
        return self._config.daily_summary_path(date_str).exists()

    def _update_checkpoint(self, target_date: str) -> None:
        """last_summarize_date 키 업데이트."""
        cp_path = self._config.checkpoints_path
        cp_path.parent.mkdir(parents=True, exist_ok=True)

        checkpoints = {}
        if cp_path.exists():
            checkpoints = load_json(cp_path)

        checkpoints["last_summarize_date"] = target_date

        with open(cp_path, "w", encoding="utf-8") as f:
            json.dump(checkpoints, f, indent=2)

        if self._daily_state is not None:
            self._daily_state.set_timestamp("summarize", target_date)

    # ── 유틸리티 ──

    def _render_prompt(self, template_name: str, **kwargs) -> str:
        """Jinja2 템플릿 렌더링."""
        template_path = self._config.prompts_dir / template_name
        if not template_path.exists():
            raise SummarizeError(f"Prompt template not found: {template_path}")

        template_text = template_path.read_text(encoding="utf-8")
        template = Template(template_text)
        return template.render(**kwargs)

    @staticmethod
    def _format_activities(activities: list[dict]) -> str:
        """activities dict 목록을 읽기 좋은 텍스트로 변환."""
        if not activities:
            return "(활동 없음)"

        lines = []
        for act in activities:
            line = (
                f"- [{act['kind']}] {act['title']} ({act['repo']}) "
                f"+{act.get('additions', 0)}/-{act.get('deletions', 0)} "
                f"URL: {act['url']}"
            )
            if act.get("files"):
                file_list = ", ".join(act["files"][:10])
                line += f"\n  Files: {file_list}"
                if len(act["files"]) > 10:
                    line += f" 외 {len(act['files']) - 10}개"
            if act.get("body"):
                body = act["body"][:1000]
                if len(act["body"]) > 1000:
                    body += "..."
                line += f"\n  Body: {body}"
            if act.get("review_bodies"):
                parts = [rb[:500] + ("..." if len(rb) > 500 else "") for rb in act["review_bodies"]]
                line += f"\n  Reviews: {' | '.join(parts)}"
            if act.get("comment_bodies"):
                parts = [
                    cb[:500] + ("..." if len(cb) > 500 else "") for cb in act["comment_bodies"]
                ]
                line += f"\n  Comments: {' | '.join(parts)}"
            # Patches section
            if act.get("file_patches"):
                patch_lines = []
                budget = 8000
                count = 0
                for fname, patch in act["file_patches"].items():
                    if count >= 8:
                        break
                    truncated = patch[:1000]
                    if len(patch) > 1000:
                        truncated += "..."
                    entry = f"    --- {fname} ---\n    {truncated}"
                    if budget - len(entry) < 0:
                        break
                    budget -= len(entry)
                    patch_lines.append(entry)
                    count += 1
                if patch_lines:
                    line += "\n  Patches:\n" + "\n".join(patch_lines)
            # Inline comments section
            if act.get("comment_contexts"):
                ctx_lines = []
                for ctx in act["comment_contexts"][:10]:
                    hunk = ctx.get("diff_hunk", "")
                    if len(hunk) > 300:
                        hunk = hunk[-300:]
                    comment_body = (ctx.get("body") or "")[:300]
                    ctx_lines.append(
                        f"    at {ctx.get('path', '')}:{ctx.get('line', 0)}\n"
                        f"    hunk: {hunk}\n"
                        f"    comment: {comment_body}"
                    )
                if ctx_lines:
                    line += "\n  Inline comments:\n" + "\n".join(ctx_lines)
            lines.append(line)

        return "\n".join(lines)

    @staticmethod
    def _save_markdown(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
