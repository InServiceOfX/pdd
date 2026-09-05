"""Regression tests for the process-wide GitHub boundary."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from pdd.github_guard import (
    GitHubAccessDisabled,
    find_gh,
    github_access_allowed,
    github_auth_opted_in,
    instruction_is_local,
    local_only_enabled,
    localize_agent_instruction,
    require_github_access,
)


@pytest.mark.parametrize("value", ["1", " true ", "YES", "On"])
def test_local_only_truthy_values(monkeypatch, value):
    monkeypatch.setenv("PDD_LOCAL_ONLY", value)
    assert local_only_enabled() is True
    assert github_access_allowed() is False


def test_local_only_overrides_github_auth_opt_in(monkeypatch):
    monkeypatch.setenv("PDD_LOCAL_ONLY", "1")
    monkeypatch.setenv("PDD_ALLOW_GITHUB_AUTH", "1")
    assert github_auth_opted_in() is False
    with pytest.raises(GitHubAccessDisabled, match="local:<n>"):
        require_github_access("Issue fetch")


def test_find_gh_does_not_touch_path_in_local_only_mode(monkeypatch):
    monkeypatch.setenv("PDD_LOCAL_ONLY", "1")
    with patch("pdd.github_guard.shutil.which") as which:
        assert find_gh() is None
    which.assert_not_called()


def test_find_gh_uses_path_outside_local_only(monkeypatch):
    monkeypatch.delenv("PDD_LOCAL_ONLY", raising=False)
    with patch("pdd.github_guard.shutil.which", return_value="/usr/bin/gh") as which:
        assert find_gh() == "/usr/bin/gh"
    which.assert_called_once_with("gh")


def test_local_instruction_detection_does_not_match_remote_issue():
    assert instruction_is_local("Issue URL: local-intent:abc-123")
    assert instruction_is_local("Repository: local/project")
    assert instruction_is_local("comments: pdd-local://work-items/4/comments")
    assert not instruction_is_local("https://github.com/acme/widget/issues/4")


def test_localize_agent_instruction_is_final_and_idempotent():
    original = "Issue URL: local:4\nAlways run gh issue comment."
    localized = localize_agent_instruction(original)
    assert localized.startswith(original)
    assert "PDD_LOCAL_GITHUB_POLICY_V1" in localized
    assert "Do not run `gh`" in localized
    assert "Do not publish anything" in localized
    assert localize_agent_instruction(localized) == localized


def test_remote_instruction_is_unchanged(monkeypatch):
    monkeypatch.delenv("PDD_LOCAL_ONLY", raising=False)
    remote = "Issue URL: https://github.com/acme/widget/issues/4"
    assert localize_agent_instruction(remote) == remote


def test_local_only_blocks_legacy_github_subprocess_helpers(monkeypatch, tmp_path):
    """Older helper entry points fail closed before any gh subprocess."""
    from pdd import (
        agentic_architecture,
        agentic_bug,
        agentic_change,
        agentic_change_orchestrator,
        agentic_e2e_fix,
        agentic_sync_runner,
        agentic_test,
    )
    from pdd import ci_validation, user_story_tests
    from pdd.core import dump

    monkeypatch.setenv("PDD_LOCAL_ONLY", "1")

    def _explode(*_args, **_kwargs):
        raise AssertionError("a GitHub subprocess was started")

    monkeypatch.setattr("subprocess.run", _explode)

    assert agentic_change._run_gh_command(["issue", "view", "1"])[0] is False
    assert agentic_architecture._run_gh_command(["issue", "view", "1"])[0] is False
    assert agentic_sync_runner._run_gh_command(["issue", "view", "1"])[0] is False
    assert agentic_bug._fetch_issue_data("acme", "repo", 1)[0] is None
    assert agentic_bug._ensure_repo_context("acme", "repo", tmp_path, True) is False
    assert agentic_e2e_fix._fetch_issue_data("acme", "repo", 1)[0] is None
    assert agentic_e2e_fix._fetch_issue_comments("https://api.github.com/comments") == ""
    assert agentic_test._fetch_issue_data("acme", "repo", 1)[0] is None
    assert agentic_change_orchestrator._gh_pr_list_candidates(["gh", "pr", "list"]) == []
    assert user_story_tests._fetch_issue_via_gh("acme/repo", "1") is None
    assert ci_validation._run_gh("acme", "repo", tmp_path, ["pr", "view"]).returncode == 127
    assert dump._get_github_token() is None
