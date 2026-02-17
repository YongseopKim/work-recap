"""Raw PR/Commit/Issue 데이터를 정규화된 Activity + DailyStats로 변환하는 서비스."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from git_recap.infra.llm_client import LLMClient
    from git_recap.services.daily_state import DailyStateStore

from jinja2 import Template

from git_recap.config import AppConfig
from git_recap.exceptions import NormalizeError
from git_recap.services.date_utils import date_range
from git_recap.models import (
    Activity,
    ActivityKind,
    CommitRaw,
    DailyStats,
    IssueRaw,
    PRRaw,
    commit_raw_from_dict,
    issue_raw_from_dict,
    load_json,
    pr_raw_from_dict,
    save_json,
    save_jsonl,
)

logger = logging.getLogger(__name__)


class NormalizerService:
    def __init__(
        self,
        config: AppConfig,
        daily_state: DailyStateStore | None = None,
        llm: LLMClient | None = None,
    ) -> None:
        self._config = config
        self._username = config.username
        self._daily_state = daily_state
        self._llm = llm

    def normalize(
        self, target_date: str, progress: Callable[[str], None] | None = None
    ) -> tuple[Path, Path]:
        """
        Raw PR 데이터를 Activity 목록과 통계로 변환.

        Args:
            target_date: "YYYY-MM-DD"
            progress: 진행 상황 콜백.

        Returns:
            (activities_path, stats_path)

        Raises:
            NormalizeError: 입력 파일 없음 또는 파싱 실패
        """
        logger.info("Normalizing %s", target_date)
        if progress:
            progress(f"Normalizing {target_date}...")
        raw_path = self._config.date_raw_dir(target_date) / "prs.json"
        if not raw_path.exists():
            raise NormalizeError(f"Raw file not found: {raw_path}")

        try:
            raw_data = load_json(raw_path)
        except Exception as e:
            raise NormalizeError(f"Failed to parse {raw_path}: {e}") from e

        prs = [pr_raw_from_dict(d) for d in raw_data]
        logger.debug("Loaded %d PRs from %s", len(prs), raw_path)

        # Commit/Issue 로드 (optional — 없으면 빈 리스트, 하위 호환)
        raw_dir = self._config.date_raw_dir(target_date)

        commits_path = raw_dir / "commits.json"
        commits: list[CommitRaw] = []
        if commits_path.exists():
            try:
                commits = [commit_raw_from_dict(d) for d in load_json(commits_path)]
            except Exception:
                logger.warning("Failed to parse %s, skipping commits", commits_path)

        issues_path = raw_dir / "issues.json"
        issues: list[IssueRaw] = []
        if issues_path.exists():
            try:
                issues = [issue_raw_from_dict(d) for d in load_json(issues_path)]
            except Exception:
                logger.warning("Failed to parse %s, skipping issues", issues_path)

        pr_activities = self._convert_activities(prs, target_date)
        commit_activities = self._convert_commit_activities(commits, target_date)
        issue_activities = self._convert_issue_activities(issues, target_date)

        activities = pr_activities + commit_activities + issue_activities
        activities.sort(key=lambda a: a.ts)

        self._enrich_activities(activities)

        stats = self._compute_stats(activities, target_date)

        out_dir = self._config.date_normalized_dir(target_date)
        activities_path = out_dir / "activities.jsonl"
        stats_path = out_dir / "stats.json"

        save_jsonl(activities, activities_path)
        save_json(stats, stats_path)

        logger.info(
            "Normalized %d activities for %s → %s",
            len(activities),
            target_date,
            out_dir,
        )

        self._update_checkpoint(target_date)
        return activities_path, stats_path

    def normalize_range(
        self,
        since: str,
        until: str,
        force: bool = False,
        progress: Callable[[str], None] | None = None,
    ) -> list[dict]:
        """날짜 범위 순회하며 normalize. skip/force/resilience 지원."""
        dates = date_range(since, until)
        logger.info("normalize_range %s..%s (%d dates, force=%s)", since, until, len(dates), force)
        if progress:
            progress(f"Normalizing {since}..{until} ({len(dates)} dates)")
        results: list[dict] = []

        for d in dates:
            try:
                if not force and self._is_date_normalized(d):
                    results.append({"date": d, "status": "skipped"})
                    continue

                self.normalize(d, progress=progress)
                results.append({"date": d, "status": "success"})
            except Exception as e:
                logger.warning("Failed to normalize %s: %s", d, e)
                results.append({"date": d, "status": "failed", "error": str(e)})

        return results

    def _is_date_normalized(self, date_str: str) -> bool:
        """daily_state 있으면 timestamp cascade 체크, 없으면 파일 존재 체크."""
        if self._daily_state is not None:
            return not self._daily_state.is_normalize_stale(date_str)
        norm_dir = self._config.date_normalized_dir(date_str)
        return (norm_dir / "activities.jsonl").exists() and (norm_dir / "stats.json").exists()

    def _update_checkpoint(self, target_date: str) -> None:
        """last_normalize_date 키 업데이트."""
        from git_recap.services.checkpoint import update_checkpoint

        update_checkpoint(self._config.checkpoints_path, "last_normalize_date", target_date)

        if self._daily_state is not None:
            self._daily_state.set_timestamp("normalize", target_date)

    # ── LLM Enrichment ──

    def _enrich_activities(self, activities: list[Activity]) -> None:
        """LLM으로 change_summary/intent 분류. 실패 시 빈 필드로 계속."""
        if self._llm is None or not activities:
            return

        logger.info("Enriching %d activities with LLM", len(activities))
        try:
            template_path = self._config.prompts_dir / "enrich.md"
            if not template_path.exists():
                logger.warning("Enrich template not found: %s", template_path)
                return

            template_text = template_path.read_text(encoding="utf-8")
            template = Template(template_text)

            act_dicts = []
            for act in activities:
                act_dicts.append(
                    {
                        "kind": act.kind.value,
                        "title": act.title,
                        "repo": act.repo,
                        "body": act.body,
                        "files": act.files,
                        "file_patches": act.file_patches,
                        "review_bodies": act.review_bodies,
                        "comment_bodies": act.comment_bodies,
                    }
                )

            prompt = template.render(activities=act_dicts)
            response = self._llm.chat("You are a code change classifier.", prompt)

            # Parse JSON response
            text = response.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()

            enrichments = json.loads(text)
            for entry in enrichments:
                idx = entry.get("index")
                if idx is not None and 0 <= idx < len(activities):
                    activities[idx].change_summary = entry.get("change_summary", "")
                    activities[idx].intent = entry.get("intent", "")

            logger.info("Enrichment complete: %d activities enriched", len(enrichments))
        except Exception as e:
            logger.warning("LLM enrichment failed, continuing without enrichment: %s", e)

    # ── Activity 변환 ──

    def _convert_activities(self, prs: list[PRRaw], target_date: str) -> list[Activity]:
        """
        PR 목록에서 사용자의 활동을 추출.

        규칙:
          - author == username → PR_AUTHORED (ts = created_at)
          - reviews에 username → PR_REVIEWED (ts = submitted_at), self-review 제외
          - comments에 username → PR_COMMENTED (ts = earliest created_at)
          - 각 activity의 ts가 target_date에 해당하지 않으면 제외
        """
        activities: list[Activity] = []

        for pr in prs:
            is_author = pr.author.lower() == self._username.lower()

            # PR_AUTHORED
            if is_author and self._matches_date(pr.created_at, target_date):
                activities.append(self._make_activity(pr, ActivityKind.PR_AUTHORED, pr.created_at))

            # PR_REVIEWED (self-review 제외)
            if not is_author:
                for review in pr.reviews:
                    if review.author.lower() == self._username.lower() and self._matches_date(
                        review.submitted_at, target_date
                    ):
                        reviewer_inline = [
                            {
                                "path": c.path,
                                "line": c.line,
                                "diff_hunk": c.diff_hunk,
                                "body": c.body,
                            }
                            for c in pr.comments
                            if c.author.lower() == self._username.lower() and c.path
                        ]
                        activities.append(
                            self._make_activity(
                                pr,
                                ActivityKind.PR_REVIEWED,
                                review.submitted_at,
                                evidence_urls=[review.url],
                                review_bodies=[review.body],
                                comment_contexts=reviewer_inline,
                            )
                        )
                        break  # PR당 1개 review activity

            # PR_COMMENTED
            user_comments = [
                c
                for c in pr.comments
                if c.author.lower() == self._username.lower()
                and self._matches_date(c.created_at, target_date)
            ]
            if user_comments:
                earliest = min(user_comments, key=lambda c: c.created_at)
                contexts = [
                    {
                        "path": c.path,
                        "line": c.line,
                        "diff_hunk": c.diff_hunk,
                        "body": c.body,
                    }
                    for c in user_comments
                    if c.path
                ]
                activities.append(
                    self._make_activity(
                        pr,
                        ActivityKind.PR_COMMENTED,
                        earliest.created_at,
                        evidence_urls=[c.url for c in user_comments],
                        comment_bodies=[c.body for c in user_comments],
                        comment_contexts=contexts,
                    )
                )

        activities.sort(key=lambda a: a.ts)
        return activities

    # ── Commit → Activity 변환 ──

    def _convert_commit_activities(
        self, commits: list[CommitRaw], target_date: str
    ) -> list[Activity]:
        """Commit 목록에서 COMMIT Activity를 생성."""
        activities: list[Activity] = []
        for commit in commits:
            if not self._matches_date(commit.committed_at, target_date):
                continue

            # 제목: commit message 첫 줄 (truncation 없음)
            title = commit.message.split("\n", 1)[0]

            total_adds = sum(f.additions for f in commit.files)
            total_dels = sum(f.deletions for f in commit.files)
            file_names = [f.filename for f in commit.files]
            file_patches = {f.filename: f.patch for f in commit.files if f.patch}

            activities.append(
                Activity(
                    ts=commit.committed_at,
                    kind=ActivityKind.COMMIT,
                    repo=commit.repo,
                    pr_number=0,
                    title=title,
                    url=commit.url,
                    summary=f"commit: {title} ({commit.repo}) +{total_adds}/-{total_dels}",
                    sha=commit.sha,
                    body=commit.message,
                    files=file_names,
                    file_patches=file_patches,
                    additions=total_adds,
                    deletions=total_dels,
                )
            )
        return activities

    # ── Issue → Activity 변환 ──

    def _convert_issue_activities(self, issues: list[IssueRaw], target_date: str) -> list[Activity]:
        """Issue 목록에서 ISSUE_AUTHORED / ISSUE_COMMENTED Activity를 생성."""
        activities: list[Activity] = []
        for issue in issues:
            # ISSUE_AUTHORED
            if issue.author.lower() == self._username.lower() and self._matches_date(
                issue.created_at, target_date
            ):
                activities.append(
                    Activity(
                        ts=issue.created_at,
                        kind=ActivityKind.ISSUE_AUTHORED,
                        repo=issue.repo,
                        pr_number=issue.number,
                        title=issue.title,
                        url=issue.url,
                        summary=f"issue_authored: {issue.title} ({issue.repo})",
                        body=issue.body,
                        labels=issue.labels,
                    )
                )

            # ISSUE_COMMENTED
            user_comments = [
                c
                for c in issue.comments
                if c.author.lower() == self._username.lower()
                and self._matches_date(c.created_at, target_date)
            ]
            if user_comments:
                earliest = min(user_comments, key=lambda c: c.created_at)
                activities.append(
                    Activity(
                        ts=earliest.created_at,
                        kind=ActivityKind.ISSUE_COMMENTED,
                        repo=issue.repo,
                        pr_number=issue.number,
                        title=issue.title,
                        url=issue.url,
                        summary=f"issue_commented: {issue.title} ({issue.repo})",
                        body=issue.body,
                        comment_bodies=[c.body for c in user_comments],
                        labels=issue.labels,
                        evidence_urls=[c.url for c in user_comments],
                    )
                )
        return activities

    def _make_activity(
        self,
        pr: PRRaw,
        kind: ActivityKind,
        ts: str,
        evidence_urls: list[str] | None = None,
        review_bodies: list[str] | None = None,
        comment_bodies: list[str] | None = None,
        comment_contexts: list[dict] | None = None,
    ) -> Activity:
        total_adds = sum(f.additions for f in pr.files)
        total_dels = sum(f.deletions for f in pr.files)
        file_names = [f.filename for f in pr.files]
        file_patches = {f.filename: f.patch for f in pr.files if f.patch}

        return Activity(
            ts=ts,
            kind=kind,
            repo=pr.repo,
            pr_number=pr.number,
            title=pr.title,
            url=pr.url,
            summary=self._auto_summary(pr, kind, total_adds, total_dels),
            body=pr.body,
            review_bodies=review_bodies or [],
            comment_bodies=comment_bodies or [],
            files=file_names,
            file_patches=file_patches,
            additions=total_adds,
            deletions=total_dels,
            labels=pr.labels,
            evidence_urls=evidence_urls or [],
            comment_contexts=comment_contexts or [],
        )

    # ── Auto Summary ──

    @staticmethod
    def _auto_summary(pr: PRRaw, kind: ActivityKind, adds: int, dels: int) -> str:
        """1줄 자동 요약. body 없으면 파일 경로 기반 fallback (D-2)."""
        if pr.body and pr.body.strip():
            return f"{kind.value}: {pr.title} ({pr.repo}) +{adds}/-{dels}"

        dirs = set()
        for f in pr.files:
            parts = f.filename.split("/")
            if len(parts) > 1:
                dirs.add(parts[0])
            else:
                dirs.add(f.filename)

        dir_hint = ", ".join(sorted(dirs)[:3])
        if len(dirs) > 3:
            dir_hint += " 외"

        return f"{kind.value}: [{dir_hint}] {len(pr.files)}개 파일 변경 ({pr.repo}) +{adds}/-{dels}"

    # ── 날짜 필터링 ──

    @staticmethod
    def _matches_date(iso_timestamp: str, target_date: str) -> bool:
        """ISO 8601 타임스탬프의 날짜 부분이 target_date와 일치하는지 확인."""
        return iso_timestamp[:10] == target_date

    # ── DailyStats 계산 ──

    @staticmethod
    def _compute_stats(activities: list[Activity], target_date: str) -> DailyStats:
        authored = [a for a in activities if a.kind == ActivityKind.PR_AUTHORED]
        reviewed = [a for a in activities if a.kind == ActivityKind.PR_REVIEWED]
        commented = [a for a in activities if a.kind == ActivityKind.PR_COMMENTED]
        commits = [a for a in activities if a.kind == ActivityKind.COMMIT]
        issue_authored = [a for a in activities if a.kind == ActivityKind.ISSUE_AUTHORED]
        issue_commented = [a for a in activities if a.kind == ActivityKind.ISSUE_COMMENTED]

        # additions/deletions: authored PR + commit 합산
        total_adds = sum(a.additions for a in authored) + sum(a.additions for a in commits)
        total_dels = sum(a.deletions for a in authored) + sum(a.deletions for a in commits)

        repos = sorted(set(a.repo for a in activities))

        return DailyStats(
            date=target_date,
            authored_count=len(authored),
            reviewed_count=len(reviewed),
            commented_count=len(commented),
            total_additions=total_adds,
            total_deletions=total_dels,
            repos_touched=repos,
            authored_prs=[{"url": a.url, "title": a.title, "repo": a.repo} for a in authored],
            reviewed_prs=[{"url": a.url, "title": a.title, "repo": a.repo} for a in reviewed],
            commit_count=len(commits),
            issue_authored_count=len(issue_authored),
            issue_commented_count=len(issue_commented),
            commits=[
                {"url": a.url, "title": a.title, "repo": a.repo, "sha": a.sha} for a in commits
            ],
            authored_issues=[
                {"url": a.url, "title": a.title, "repo": a.repo} for a in issue_authored
            ],
        )
