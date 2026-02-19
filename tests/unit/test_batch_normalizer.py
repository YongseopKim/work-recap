"""Normalizer batch 모드 단위 테스트."""

import json
from unittest.mock import MagicMock

import pytest

from workrecap.infra.providers.batch_mixin import BatchResult
from workrecap.models import TokenUsage
from workrecap.services.normalizer import NormalizerService


@pytest.fixture
def setup_data(tmp_path, test_config):
    """2일치 raw 데이터 + enrich 템플릿 + normalizer 설정."""
    config = test_config

    # enrich template
    prompts_dir = config.prompts_dir
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / "enrich.md").write_text(
        "You classify code changes.\n<!-- SPLIT -->\n"
        "{% for act in activities %}"
        "- {{ act.title }}\n"
        "{% endfor %}"
    )

    # 2일치 raw 데이터 생성
    for date_str in ["2026-01-01", "2026-01-02"]:
        raw_dir = config.date_raw_dir(date_str)
        raw_dir.mkdir(parents=True, exist_ok=True)
        prs = [
            {
                "url": "https://github.com/org/repo/pull/1",
                "api_url": "https://api.github.com/repos/org/repo/pulls/1",
                "number": 1,
                "title": f"PR for {date_str}",
                "body": "test body",
                "state": "closed",
                "is_merged": True,
                "created_at": f"{date_str}T10:00:00Z",
                "updated_at": f"{date_str}T10:00:00Z",
                "merged_at": f"{date_str}T10:00:00Z",
                "repo": "org/repo",
                "author": config.username,
                "files": [
                    {
                        "filename": "main.py",
                        "additions": 10,
                        "deletions": 2,
                        "status": "modified",
                        "patch": "",
                    }
                ],
                "comments": [],
                "reviews": [],
            }
        ]
        (raw_dir / "prs.json").write_text(json.dumps(prs, ensure_ascii=False), encoding="utf-8")
        (raw_dir / "commits.json").write_text("[]", encoding="utf-8")
        (raw_dir / "issues.json").write_text("[]", encoding="utf-8")

    return config


class TestNormalizerBatchMode:
    def test_batch_false_uses_sequential(self, setup_data):
        """batch=False (기본값)는 기존 sequential 경로 사용."""
        mock_llm = MagicMock()
        # 각 날짜의 enrich 호출에 응답
        mock_llm.chat.return_value = json.dumps(
            [{"index": 0, "change_summary": "test change", "intent": "feature"}]
        )
        normalizer = NormalizerService(setup_data, llm=mock_llm)

        results = normalizer.normalize_range("2026-01-01", "2026-01-02", force=True)
        assert len(results) == 2
        assert all(r["status"] == "success" for r in results)
        # chat이 날짜별로 호출됨 (batch=False이므로)
        assert mock_llm.chat.call_count == 2

    def test_batch_true_submits_batch(self, setup_data):
        """batch=True → submit_batch + wait_for_batch 경로."""
        mock_llm = MagicMock()
        mock_llm.submit_batch.return_value = "batch-enrich-123"
        mock_llm.wait_for_batch.return_value = [
            BatchResult(
                custom_id="enrich-2026-01-01",
                content=json.dumps(
                    [{"index": 0, "change_summary": "batch change 1", "intent": "bugfix"}]
                ),
                usage=TokenUsage(
                    prompt_tokens=100, completion_tokens=50, total_tokens=150, call_count=1
                ),
            ),
            BatchResult(
                custom_id="enrich-2026-01-02",
                content=json.dumps(
                    [{"index": 0, "change_summary": "batch change 2", "intent": "feature"}]
                ),
                usage=TokenUsage(
                    prompt_tokens=100, completion_tokens=50, total_tokens=150, call_count=1
                ),
            ),
        ]
        normalizer = NormalizerService(setup_data, llm=mock_llm)

        results = normalizer.normalize_range("2026-01-01", "2026-01-02", force=True, batch=True)
        assert len(results) == 2
        assert all(r["status"] == "success" for r in results)

        # submit_batch가 1번 호출됨 (모든 날짜를 한 번에)
        mock_llm.submit_batch.assert_called_once()
        mock_llm.wait_for_batch.assert_called_once()
        # chat은 호출되지 않음 (batch 모드이므로)
        mock_llm.chat.assert_not_called()

        # Enrichment이 적용되었는지 확인
        norm_dir = setup_data.date_normalized_dir("2026-01-01")
        activities_path = norm_dir / "activities.jsonl"
        assert activities_path.exists()
        lines = activities_path.read_text().strip().split("\n")
        act = json.loads(lines[0])
        assert act["change_summary"] == "batch change 1"
        assert act["intent"] == "bugfix"

    def test_batch_with_error_result(self, setup_data):
        """batch 결과에 error가 있는 날짜는 enrichment 없이 진행."""
        mock_llm = MagicMock()
        mock_llm.submit_batch.return_value = "batch-123"
        mock_llm.wait_for_batch.return_value = [
            BatchResult(
                custom_id="enrich-2026-01-01",
                content=json.dumps([{"index": 0, "change_summary": "ok", "intent": "feature"}]),
                usage=TokenUsage(call_count=1),
            ),
            BatchResult(
                custom_id="enrich-2026-01-02",
                error="Rate limit exceeded",
            ),
        ]
        normalizer = NormalizerService(setup_data, llm=mock_llm)

        results = normalizer.normalize_range("2026-01-01", "2026-01-02", force=True, batch=True)
        # Both should still succeed (error in batch → enrichment skipped)
        assert len(results) == 2
        assert all(r["status"] == "success" for r in results)

        # First date should be enriched, second should not
        act1 = json.loads(
            (setup_data.date_normalized_dir("2026-01-01") / "activities.jsonl")
            .read_text()
            .strip()
            .split("\n")[0]
        )
        assert act1["change_summary"] == "ok"

        act2 = json.loads(
            (setup_data.date_normalized_dir("2026-01-02") / "activities.jsonl")
            .read_text()
            .strip()
            .split("\n")[0]
        )
        assert act2["change_summary"] == ""  # No enrichment

    def test_batch_no_llm_falls_back_to_sequential(self, setup_data):
        """LLM이 없으면 batch=True여도 sequential로 (enrichment 없이)."""
        normalizer = NormalizerService(setup_data, llm=None)

        results = normalizer.normalize_range("2026-01-01", "2026-01-02", force=True, batch=True)
        assert len(results) == 2
        assert all(r["status"] == "success" for r in results)

    def test_batch_skip_force_logic(self, setup_data):
        """batch 모드에서도 skip/force 로직 동작."""
        mock_llm = MagicMock()
        # 먼저 sequential로 normalize
        mock_llm.chat.return_value = json.dumps([])
        normalizer = NormalizerService(setup_data, llm=mock_llm)
        normalizer.normalize_range("2026-01-01", "2026-01-02", force=True)

        # 이제 batch=True, force=False → 이미 처리된 날짜는 skip
        mock_llm.reset_mock()
        results = normalizer.normalize_range("2026-01-01", "2026-01-02", force=False, batch=True)
        assert all(r["status"] == "skipped" for r in results)
        mock_llm.submit_batch.assert_not_called()
