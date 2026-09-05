"""Story regression for GitHub-free local and inline PDD workflows."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from pdd.agentic_common import build_agentic_task_instruction
from pdd.core.cloud import CloudConfig
from pdd.core.dump import _create_gist_with_files, _post_issue_to_github
from pdd.llm_invoke import _select_model_candidates
from pdd.sync_core.global_sync_ledger import GitHubPromotionVerifier, LedgerError
from pdd.story_test_generation import story_bundle_hash


PDD_STORY_ID = "pdd_local_workflows_never_authenticate_with_github"
PDD_STORY_HASH = "62b27619ecb90573"
STORY_PATH = (
    Path(__file__).resolve().parents[2]
    / "user_stories/story__pdd_local_workflows_never_authenticate_with_github.md"
)


@pytest.mark.story(story_id=PDD_STORY_ID, story_hash=PDD_STORY_HASH)
def test_local_inline_workflow_has_no_github_auth_path(monkeypatch):
    assert story_bundle_hash(STORY_PATH) == PDD_STORY_HASH
    monkeypatch.setenv("PDD_LOCAL_ONLY", "1")
    monkeypatch.setenv("PDD_ALLOW_GITHUB_AUTH", "1")
    monkeypatch.setenv("PDD_ALLOW_INTERACTIVE", "1")
    monkeypatch.setenv("PDD_JWT_TOKEN", "would-otherwise-enable-cloud")

    cached = patch("pdd.core.cloud._get_cached_jwt")
    device = patch("pdd.core.cloud.device_flow_get_token")
    with cached as cached_jwt, device as device_flow:
        assert CloudConfig.is_cloud_enabled() is False
        assert CloudConfig.get_jwt_token() is None
        cached_jwt.assert_not_called()
        device_flow.assert_not_called()

    instruction = build_agentic_task_instruction(
        "Issue URL: local-intent:approved\nAlways run gh issue comment."
    )
    assert instruction.rstrip().endswith("Do not publish anything.")

    rows = pd.DataFrame(
        [
            {
                "provider": "OpenAI",
                "model": "gpt-4",
                "api_key": "OPENAI_API_KEY",
                "coding_arena_elo": 1200,
                "model_rank_score": 1200,
                "input": 1.0,
                "output": 1.0,
                "interactive_only": False,
            },
            {
                "provider": "Github Copilot",
                "model": "github_copilot/gpt-5",
                "api_key": "",
                "coding_arena_elo": 1400,
                "model_rank_score": 1400,
                "input": 0.0,
                "output": 0.0,
                "interactive_only": True,
            },
        ]
    )
    selected = _select_model_candidates(0.5, "gpt-4", rows)
    assert all(not row["model"].startswith("github_copilot/") for row in selected)

    with patch("pdd.core.dump.requests.post") as github_post:
        assert _create_gist_with_files("token", {}, STORY_PATH) is None
        assert _post_issue_to_github("token", "owner/repo", "title", "body") is None
    github_post.assert_not_called()

    with pytest.raises(LedgerError, match="disabled by PDD_LOCAL_ONLY=1"):
        GitHubPromotionVerifier()._get("/repos/owner/repo/pulls/1")
