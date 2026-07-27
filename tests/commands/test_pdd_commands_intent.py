"""Tests for the `pdd intent` planning command."""
from __future__ import annotations

import json
from pathlib import Path

import click
from click.testing import CliRunner

from pdd.commands import register_commands
from pdd.commands.intent import intent
from pdd.intent_apply import IntentApplyResult


def test_help_describes_local_planning() -> None:
    runner = CliRunner()

    group_help = runner.invoke(intent, ["--help"])
    plan_help = runner.invoke(intent, ["plan", "--help"])
    apply_help = runner.invoke(intent, ["apply", "--help"])

    assert group_help.exit_code == 0
    assert "ordinary product intent" in group_help.output
    assert plan_help.exit_code == 0
    assert "without GitHub" in plan_help.output
    assert "file changes" in plan_help.output
    assert apply_help.exit_code == 0
    assert "mutates scope without GitHub" in apply_help.output
    assert "substitute for those checks" in apply_help.output


def test_inline_text_renders_review_card_without_writes(tmp_path: Path) -> None:
    runner = CliRunner()
    before = set(tmp_path.iterdir())

    result = runner.invoke(
        intent,
        [
            "plan",
            "--text",
            "Add a calculator. Never send inputs over the network.",
            "--project-root",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "What I heard:" in result.output
    assert "Never send inputs over the network." in result.output
    assert "Planning only" in result.output
    assert set(tmp_path.iterdir()) == before


def test_json_output_is_machine_readable_and_has_no_status_prose(
    tmp_path: Path,
) -> None:
    runner = CliRunner()

    result = runner.invoke(
        intent,
        [
            "plan",
            "--text",
            "Create a Python calculator.",
            "--project-root",
            str(tmp_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema_version"] == "pdd.intent.plan.v1"
    assert payload["capabilities"]["apply"] is False
    assert "Planning only" not in result.output


def test_local_file_source_is_recorded(tmp_path: Path) -> None:
    runner = CliRunner()
    source = tmp_path / "request.md"
    source.write_text("# Export\n\nAdd PDF export.\n", encoding="utf-8")

    result = runner.invoke(
        intent,
        [
            "plan",
            str(source),
            "--project-root",
            str(tmp_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["source"] == {"kind": "file", "ref": str(source.resolve())}
    assert payload["original_request"] == "# Export\n\nAdd PDF export."


def test_piped_stdin_is_supported(tmp_path: Path) -> None:
    runner = CliRunner()

    result = runner.invoke(
        intent,
        ["plan", "--project-root", str(tmp_path), "--json"],
        input="Create an offline report viewer.\n",
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["source"] == {"kind": "stdin", "ref": "<stdin>"}


def test_source_and_text_are_mutually_exclusive(tmp_path: Path) -> None:
    runner = CliRunner()
    source = tmp_path / "request.md"
    source.write_text("Add export.", encoding="utf-8")

    result = runner.invoke(
        intent,
        [
            "plan",
            str(source),
            "--text",
            "Different request",
            "--project-root",
            str(tmp_path),
        ],
    )

    assert result.exit_code != 0
    assert "either SOURCE or --text" in result.output


def test_empty_input_is_rejected(tmp_path: Path) -> None:
    runner = CliRunner()

    result = runner.invoke(
        intent,
        ["plan", "--project-root", str(tmp_path)],
        input="",
    )

    assert result.exit_code != 0
    assert "must not be empty" in result.output


def test_oversized_input_is_rejected(tmp_path: Path) -> None:
    runner = CliRunner()

    result = runner.invoke(
        intent,
        [
            "plan",
            "--text",
            "x" * 100_001,
            "--project-root",
            str(tmp_path),
        ],
    )

    assert result.exit_code != 0
    assert "100,000-character" in result.output


def test_proposed_project_root_may_not_exist(tmp_path: Path) -> None:
    runner = CliRunner()
    proposed = tmp_path / "packages" / "new_tool"

    result = runner.invoke(
        intent,
        [
            "plan",
            "--text",
            "Create a new tool.",
            "--project-root",
            str(proposed),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["project"]["exists"] is False
    assert not proposed.exists()


def test_command_is_registered_at_top_level() -> None:
    cli = click.Group()

    register_commands(cli)

    assert "intent" in cli.commands


def test_apply_requires_exact_plan_approval_without_writes(tmp_path: Path) -> None:
    runner = CliRunner()

    result = runner.invoke(
        intent,
        [
            "apply",
            "--text",
            "Create a Python calculator.",
            "--project-root",
            str(tmp_path),
            "--approve",
            "wrong-id",
            "--no-story",
            "--no-sync",
        ],
    )

    assert result.exit_code != 0
    assert "Approval ID does not match" in result.output
    assert list(tmp_path.iterdir()) == []


def test_apply_json_is_machine_readable(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()

    def fake_apply(plan, **kwargs):
        assert kwargs["approved_intent_id"] == plan.intent_id
        assert kwargs["create_story"] is False
        assert kwargs["run_sync"] is False
        return IntentApplyResult(
            schema_version="pdd.intent.apply.v1",
            success=True,
            intent_id=plan.intent_id,
            route="local_full_architecture",
            status="applied",
            changed_files=("docs/PRODUCT_INTENT.md",),
            steps=(),
            message="Applied.",
            cost=0.0,
            model="",
        )

    monkeypatch.setattr("pdd.commands.intent.apply_intent", fake_apply)
    request = "Create a Python calculator."
    from pdd.intent import build_intent_plan

    intent_id = build_intent_plan(request, tmp_path).intent_id
    result = runner.invoke(
        intent,
        [
            "apply",
            "--text",
            request,
            "--project-root",
            str(tmp_path),
            "--approve",
            intent_id,
            "--no-story",
            "--no-sync",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema_version"] == "pdd.intent.apply.v1"
    assert payload["success"] is True
    assert "Intent apply:" not in result.output


def test_failed_apply_emits_report_and_exits_nonzero(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    request = "Create a Python calculator."
    from pdd.intent import build_intent_plan

    intent_id = build_intent_plan(request, tmp_path).intent_id

    def fake_apply(plan, **kwargs):
        return IntentApplyResult(
            schema_version="pdd.intent.apply.v1",
            success=False,
            intent_id=plan.intent_id,
            route="local_full_architecture",
            status="failed",
            changed_files=(),
            steps=(),
            message="Architecture failed.",
            cost=0.0,
            model="",
        )

    monkeypatch.setattr("pdd.commands.intent.apply_intent", fake_apply)
    result = runner.invoke(
        intent,
        [
            "apply",
            "--text",
            request,
            "--project-root",
            str(tmp_path),
            "--approve",
            intent_id,
        ],
    )

    assert result.exit_code == 1
    assert "Intent apply: FAILED" in result.output
    assert "Architecture failed." in result.output


def test_story_approval_gate_exits_two_with_structured_hash(
    monkeypatch, tmp_path: Path
) -> None:
    runner = CliRunner()
    request = "Create a Python calculator."
    from pdd.intent import build_intent_plan

    intent_id = build_intent_plan(request, tmp_path).intent_id

    def fake_apply(plan, **kwargs):
        return IntentApplyResult(
            schema_version="pdd.intent.apply.v1",
            success=False,
            intent_id=plan.intent_id,
            route="local_full_architecture",
            status="awaiting_story_approval",
            changed_files=("user_stories/story__calculator.md",),
            steps=(),
            message="Review the generated story.",
            cost=0.0,
            model="",
            approval_required={
                "kind": "story",
                "path": "user_stories/story__calculator.md",
                "sha256": "a" * 64,
            },
        )

    monkeypatch.setattr("pdd.commands.intent.apply_intent", fake_apply)
    result = runner.invoke(
        intent,
        [
            "apply",
            "--text",
            request,
            "--project-root",
            str(tmp_path),
            "--approve",
            intent_id,
            "--json",
        ],
    )

    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["status"] == "awaiting_story_approval"
    assert payload["approval_required"]["sha256"] == "a" * 64
