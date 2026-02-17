"""Activity/Stats 데이터와 하위 summary를 LLM에 전달하여 markdown summary를 생성."""

import json
import logging
from datetime import date, timedelta
from pathlib import Path

from jinja2 import Template

from git_recap.config import AppConfig
from git_recap.exceptions import SummarizeError
from git_recap.infra.llm_client import LLMClient
from git_recap.models import load_json, load_jsonl
from git_recap.services.date_utils import date_range

logger = logging.getLogger(__name__)


class SummarizerService:
    def __init__(self, config: AppConfig, llm_client: LLMClient) -> None:
        self._config = config
        self._llm = llm_client

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

    def weekly(self, year: int, week: int) -> Path:
        """Weekly summary 생성."""
        daily_contents = self._collect_daily_for_week(year, week)
        if not daily_contents:
            raise SummarizeError(f"No daily summaries found for {year}-W{week:02d}")

        system_prompt = self._render_prompt("weekly.md", year=year, week=week)
        user_content = "\n\n---\n\n".join(daily_contents)

        response = self._llm.chat(system_prompt, user_content)

        output_path = self._config.weekly_summary_path(year, week)
        self._save_markdown(output_path, response)

        logger.info("Generated weekly summary: %s", output_path)
        return output_path

    def monthly(self, year: int, month: int) -> Path:
        """Monthly summary 생성."""
        weekly_contents = self._collect_weekly_for_month(year, month)
        if not weekly_contents:
            raise SummarizeError(f"No weekly summaries found for {year}-{month:02d}")

        system_prompt = self._render_prompt("monthly.md", year=year, month=month)
        user_content = "\n\n---\n\n".join(weekly_contents)

        response = self._llm.chat(system_prompt, user_content)

        output_path = self._config.monthly_summary_path(year, month)
        self._save_markdown(output_path, response)

        logger.info("Generated monthly summary: %s", output_path)
        return output_path

    def yearly(self, year: int) -> Path:
        """Yearly summary 생성."""
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

        output_path = self._config.yearly_summary_path(year)
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
        """daily summary 파일이 존재하면 True."""
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
                file_list = ", ".join(act["files"][:5])
                line += f"\n  Files: {file_list}"
                if len(act["files"]) > 5:
                    line += f" 외 {len(act['files']) - 5}개"
            if act.get("body"):
                body = act["body"][:500]
                if len(act["body"]) > 500:
                    body += "..."
                line += f"\n  Body: {body}"
            if act.get("review_bodies"):
                parts = [rb[:200] + ("..." if len(rb) > 200 else "") for rb in act["review_bodies"]]
                line += f"\n  Reviews: {' | '.join(parts)}"
            if act.get("comment_bodies"):
                parts = [cb[:200] + ("..." if len(cb) > 200 else "") for cb in act["comment_bodies"]]
                line += f"\n  Comments: {' | '.join(parts)}"
            lines.append(line)

        return "\n".join(lines)

    @staticmethod
    def _save_markdown(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
