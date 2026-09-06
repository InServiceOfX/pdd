"""Tests for local, filesystem-backed work items.

These cover the three roles a GitHub issue played in the agentic workflows —
the request, the resumable state store, and the mid-run steering channel —
and assert that satisfying them locally never invokes ``gh``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pdd import agentic_common, local_work_items


# ---------------------------------------------------------------------------
# Reference parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value,expected",
    [
        ("local:1", 1),
        ("local#12", 12),
        ("local/7", 7),
        ("work:3", 3),
        ("work-item:44", 44),
        ("LOCAL:5", 5),
        ("  local:9  ", 9),
    ],
)
def test_parse_local_ref_accepts_supported_forms(value, expected):
    assert local_work_items.parse_local_ref(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "https://github.com/owner/repo/issues/5",
        "foo_python.prompt",
        "local:",
        "local:0",
        "local:-3",
        "locally:3",
        "",
        None,
        12,
    ],
)
def test_parse_local_ref_rejects_other_values(value):
    assert local_work_items.parse_local_ref(value) is None


def test_format_and_is_local_owner_round_trip():
    assert local_work_items.format_local_ref(4) == "local:4"
    assert local_work_items.is_local_owner("local")
    assert local_work_items.is_local_owner("LOCAL")
    assert not local_work_items.is_local_owner("promptdriven")
    assert not local_work_items.is_local_owner(None)


# ---------------------------------------------------------------------------
# Store CRUD
# ---------------------------------------------------------------------------

def test_create_and_load_work_item(tmp_path: Path):
    item = local_work_items.create_work_item(
        tmp_path, "Ring buffer drops a frame", "Overwrites index 0 when full.", labels=["bug"]
    )
    assert item["number"] == 1
    assert item["state"] == "open"
    assert item["labels"] == ["bug"]

    loaded = local_work_items.load_work_item(tmp_path, 1)
    assert loaded == item
    assert local_work_items.load_work_item(tmp_path, 99) is None


def test_create_work_item_requires_title(tmp_path: Path):
    with pytest.raises(local_work_items.LocalWorkItemError):
        local_work_items.create_work_item(tmp_path, "   ", "body")


def test_numbers_increment_across_items(tmp_path: Path):
    first = local_work_items.create_work_item(tmp_path, "one")
    second = local_work_items.create_work_item(tmp_path, "two")
    assert (first["number"], second["number"]) == (1, 2)
    assert [i["number"] for i in local_work_items.list_work_items(tmp_path)] == [1, 2]


def test_list_work_items_is_empty_without_a_store(tmp_path: Path):
    assert local_work_items.list_work_items(tmp_path) == []


def test_update_work_item_changes_state_and_touches_timestamp(tmp_path: Path):
    local_work_items.create_work_item(tmp_path, "one", "body")
    updated = local_work_items.update_work_item(tmp_path, 1, state="closed")
    assert updated["state"] == "closed"
    with pytest.raises(local_work_items.LocalWorkItemError):
        local_work_items.update_work_item(tmp_path, 1, state="banana")
    assert local_work_items.update_work_item(tmp_path, 99, state="closed") is None


def test_comment_ids_are_monotonic_and_survive_deletion(tmp_path: Path):
    """Steering compares ``id > cursor``, so ids must never be reused."""
    local_work_items.create_work_item(tmp_path, "one")
    first = local_work_items.add_comment(tmp_path, 1, "a")
    second = local_work_items.add_comment(tmp_path, 1, "b")
    assert second["id"] > first["id"]

    assert local_work_items.delete_comment_by_id(tmp_path, second["id"])
    third = local_work_items.add_comment(tmp_path, 1, "c")
    assert third["id"] > second["id"], "a reused id would replay drained steers"


def test_add_comment_to_missing_item_returns_none(tmp_path: Path):
    assert local_work_items.add_comment(tmp_path, 42, "orphan") is None


def test_find_comment_item_resolves_owner(tmp_path: Path):
    local_work_items.create_work_item(tmp_path, "one")
    local_work_items.create_work_item(tmp_path, "two")
    comment = local_work_items.add_comment(tmp_path, 2, "hello")
    assert local_work_items.find_comment_item(tmp_path, comment["id"]) == 2


def test_list_comments_filters_by_since(tmp_path: Path):
    local_work_items.create_work_item(tmp_path, "one")
    local_work_items.add_comment(tmp_path, 1, "early")
    all_comments = local_work_items.list_comments(tmp_path, 1)
    assert len(all_comments) == 1

    future = "2999-01-01T00:00:00Z"
    assert local_work_items.list_comments(tmp_path, 1, since=future) == []
    assert local_work_items.list_comments(tmp_path, 99) is None


# ---------------------------------------------------------------------------
# GitHub-shaped projections
# ---------------------------------------------------------------------------

def test_github_shaped_issue_carries_every_consumed_field(tmp_path: Path):
    """Callers read these keys off a ``gh api`` payload; all must be present."""
    local_work_items.create_work_item(tmp_path, "Title here", "Body here", labels=["bug", "p1"])
    payload = local_work_items.github_shaped_issue(
        local_work_items.load_work_item(tmp_path, 1)
    )
    for key in (
        "number", "title", "body", "state", "labels", "user",
        "comments_url", "created_at", "updated_at",
    ):
        assert key in payload, f"missing {key}"
    assert payload["labels"] == [{"name": "bug"}, {"name": "p1"}]
    assert payload["user"]["login"]
    assert local_work_items.is_local_comments_url(payload["comments_url"])


def test_serve_gh_api_answers_issue_and_comment_paths(tmp_path: Path):
    local_work_items.create_work_item(tmp_path, "Title", "Body")
    local_work_items.add_comment(tmp_path, 1, "a comment", author="ernest")

    issue = json.loads(local_work_items.serve_gh_api(tmp_path, "repos/local/proj/issues/1"))
    assert issue["title"] == "Title"

    comments = json.loads(
        local_work_items.serve_gh_api(tmp_path, "repos/local/proj/issues/1/comments")
    )
    assert [c["user"]["login"] for c in comments] == ["ernest"]

    via_url = json.loads(
        local_work_items.serve_gh_api(tmp_path, "pdd-local://work-items/1/comments")
    )
    assert via_url == comments


def test_serve_gh_api_declines_non_local_paths(tmp_path: Path):
    assert local_work_items.serve_gh_api(tmp_path, "repos/octocat/hello/issues/1") is None
    assert local_work_items.serve_gh_api(tmp_path, "repos/local/proj/issues/999") is None
    assert local_work_items.serve_gh_api(tmp_path, None) is None


# ---------------------------------------------------------------------------
# Role 2: the state store
# ---------------------------------------------------------------------------

def test_workflow_state_round_trips_through_local_store(tmp_path: Path):
    local_work_items.create_work_item(tmp_path, "Fix the parser")

    comment_id = agentic_common.github_save_state(
        "local", "proj", 1, "bug", {"last_completed_step": 3}, tmp_path
    )
    assert comment_id is not None

    state, loaded_id = agentic_common.github_load_state("local", "proj", 1, "bug", tmp_path)
    assert state == {"last_completed_step": 3}
    assert loaded_id == comment_id


def test_workflow_state_updates_in_place(tmp_path: Path):
    local_work_items.create_work_item(tmp_path, "Fix the parser")
    first = agentic_common.github_save_state(
        "local", "proj", 1, "bug", {"last_completed_step": 1}, tmp_path
    )
    second = agentic_common.github_save_state(
        "local", "proj", 1, "bug", {"last_completed_step": 9}, tmp_path, first
    )
    assert second == first, "an update must not create a second state comment"

    state, _ = agentic_common.github_load_state("local", "proj", 1, "bug", tmp_path)
    assert state == {"last_completed_step": 9}

    item = local_work_items.load_work_item(tmp_path, 1)
    assert len(item["comments"]) == 1


def test_workflow_state_clear_removes_state(tmp_path: Path):
    local_work_items.create_work_item(tmp_path, "Fix the parser")
    agentic_common.github_save_state("local", "proj", 1, "bug", {"step": 1}, tmp_path)
    assert agentic_common.github_clear_state("local", "proj", 1, "bug", tmp_path)
    assert agentic_common.github_load_state("local", "proj", 1, "bug", tmp_path) == (None, None)


def test_workflow_state_is_isolated_per_workflow_type(tmp_path: Path):
    local_work_items.create_work_item(tmp_path, "Fix the parser")
    agentic_common.github_save_state("local", "proj", 1, "bug", {"who": "bug"}, tmp_path)
    agentic_common.github_save_state("local", "proj", 1, "change", {"who": "change"}, tmp_path)

    bug_state, _ = agentic_common.github_load_state("local", "proj", 1, "bug", tmp_path)
    change_state, _ = agentic_common.github_load_state("local", "proj", 1, "change", tmp_path)
    assert bug_state == {"who": "bug"}
    assert change_state == {"who": "change"}


def test_save_state_dedupes_duplicate_markers(tmp_path: Path):
    """Two racing first-saves must converge to exactly one state comment."""
    local_work_items.create_work_item(tmp_path, "Fix the parser")
    agentic_common.github_save_state("local", "proj", 1, "bug", {"n": 1}, tmp_path)
    agentic_common.github_save_state("local", "proj", 1, "bug", {"n": 2}, tmp_path)

    kept = agentic_common.github_save_state(
        "local", "proj", 1, "bug", {"n": 3}, tmp_path, None, dedupe=True
    )
    assert kept is not None
    marker = agentic_common._build_state_marker("bug", 1)
    remaining = local_work_items.find_state_comments(tmp_path, 1, marker)
    assert len(remaining) == 1
    state, _ = agentic_common.github_load_state("local", "proj", 1, "bug", tmp_path)
    assert state == {"n": 3}


# ---------------------------------------------------------------------------
# Role 3: the steering channel
# ---------------------------------------------------------------------------

def test_steers_drain_once_and_advance_the_cursor(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PDD_LOCAL_ONLY", "1")
    local_work_items.create_work_item(tmp_path, "Fix the parser")
    state: dict = {}
    assert agentic_common.ensure_issue_steer_cursor_seeded(
        "local", "proj", 1, state, cwd=tmp_path
    )

    local_work_items.add_comment(tmp_path, 1, "Use a ring buffer.", author="ernest")
    local_work_items.add_comment(tmp_path, 1, "Keep the CRC check.", author="ernest")

    steers = agentic_common.drain_issue_steers("local", "proj", 1, state, cwd=tmp_path)
    assert [s.author for s in steers] == ["ernest", "ernest"]
    assert "ring buffer" in steers[0].body.lower()

    assert agentic_common.drain_issue_steers("local", "proj", 1, state, cwd=tmp_path) == []


def test_state_marker_comments_are_not_drained_as_steers(tmp_path: Path):
    """The orchestrator's own state comment must not read as human steering."""
    local_work_items.create_work_item(tmp_path, "Fix the parser")
    state: dict = {}
    agentic_common.ensure_issue_steer_cursor_seeded("local", "proj", 1, state, cwd=tmp_path)
    agentic_common.github_save_state("local", "proj", 1, "bug", {"step": 1}, tmp_path)

    assert agentic_common.drain_issue_steers("local", "proj", 1, state, cwd=tmp_path) == []


def test_fetch_issue_updated_at_reads_the_local_item(tmp_path: Path):
    item = local_work_items.create_work_item(tmp_path, "Fix the parser")
    assert agentic_common.fetch_issue_updated_at(
        "local", "proj", 1, cwd=tmp_path
    ) == item["updated_at"]
    assert agentic_common.fetch_issue_updated_at("local", "proj", 99, cwd=tmp_path) == ""


# ---------------------------------------------------------------------------
# Progress comments
# ---------------------------------------------------------------------------

def test_step_and_final_comments_land_in_the_local_item(tmp_path: Path):
    local_work_items.create_work_item(tmp_path, "Fix the parser")
    posted: set = set()

    assert agentic_common.post_step_comment_once(
        repo_owner="local", repo_name="proj", issue_number=1,
        step_num=2, body="step two done", posted_steps=posted, cwd=tmp_path,
    )
    assert 2 in posted
    # Idempotent: the same step does not post twice.
    assert agentic_common.post_step_comment_once(
        repo_owner="local", repo_name="proj", issue_number=1,
        step_num=2, body="step two done", posted_steps=posted, cwd=tmp_path,
    )

    assert agentic_common.post_final_comment(
        "local", "proj", 1, "NOT_A_BUG", 0.12, 3, 13, tmp_path
    )

    bodies = [c["body"] for c in local_work_items.load_work_item(tmp_path, 1)["comments"]]
    assert len(bodies) == 2
    assert any("step two done" in b for b in bodies)
    assert any("NOT_A_BUG" in b for b in bodies)


# ---------------------------------------------------------------------------
# The guarantee: no gh
# ---------------------------------------------------------------------------

def test_local_only_hides_the_gh_binary(monkeypatch):
    """``PDD_LOCAL_ONLY`` makes every GitHub helper take its "no gh" branch."""
    monkeypatch.setenv("PDD_LOCAL_ONLY", "1")
    assert agentic_common._find_cli_binary("gh") is None

    monkeypatch.delenv("PDD_LOCAL_ONLY", raising=False)
    assert local_work_items.local_only_enabled() is False


def test_full_local_workflow_never_invokes_gh(tmp_path: Path, monkeypatch):
    """End-to-end proof: exercising every role runs zero ``gh`` subprocesses."""
    calls: list = []

    def _explode(cmd, *args, **kwargs):
        calls.append(cmd)
        raise AssertionError(f"local workflow shelled out to: {cmd}")

    monkeypatch.setattr(agentic_common.subprocess, "run", _explode)
    monkeypatch.setattr(agentic_common, "_subprocess_run", _explode)

    local_work_items.create_work_item(tmp_path, "Fix the parser", "It drops frames.")

    state: dict = {}
    agentic_common.ensure_issue_steer_cursor_seeded("local", "proj", 1, state, cwd=tmp_path)
    cid = agentic_common.github_save_state("local", "proj", 1, "bug", {"step": 1}, tmp_path)
    agentic_common.github_save_state("local", "proj", 1, "bug", {"step": 2}, tmp_path, cid)
    agentic_common.github_load_state("local", "proj", 1, "bug", tmp_path)
    local_work_items.add_comment(tmp_path, 1, "steer me", author="ernest")
    agentic_common.drain_issue_steers("local", "proj", 1, state, cwd=tmp_path)
    agentic_common.fetch_issue_updated_at("local", "proj", 1, cwd=tmp_path)
    agentic_common.post_step_comment_once(
        repo_owner="local", repo_name="proj", issue_number=1,
        step_num=1, body="done", posted_steps=set(), cwd=tmp_path,
    )
    agentic_common.github_clear_state("local", "proj", 1, "bug", tmp_path)

    assert calls == []


def test_routing_predicates_accept_local_refs():
    """``pdd bug local:1`` must dispatch to the agentic route, not manual mode."""
    from pdd.agentic_sync import _is_github_issue_url as sync_pred
    from pdd.commands.analysis import _is_github_issue_url as analysis_pred
    from pdd.commands.fix import _is_issue_url as fix_pred
    from pdd.commands.modify import _is_github_issue_url as modify_pred

    for predicate in (sync_pred, analysis_pred, fix_pred, modify_pred):
        assert predicate("local:1") is True
        assert predicate("https://github.com/o/r/issues/5") is True
        assert predicate("some_module_python.prompt") is False
