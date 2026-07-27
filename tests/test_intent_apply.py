"""Tests for guarded local intent application."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pdd.intent import build_intent_plan
from pdd.intent_apply import (
    INTENT_APPLY_SCHEMA_VERSION,
    _WorkflowOutcome,
    _default_architecture_runner,
    _default_command_runner,
    apply_intent,
    intent_apply_result_to_dict,
)


def _successful_architecture(
    plan, product_intent_path: Path, quiet: bool, verbose: bool
) -> _WorkflowOutcome:
    del quiet, verbose
    root = Path(plan.project_root)
    prompt = root / "prompts" / "calculator_python.prompt"
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text("Calculator contract.", encoding="utf-8")
    return _WorkflowOutcome(
        True,
        f"Applied {product_intent_path.name}.",
        cost=0.25,
        model="test-model",
        changed_files=("prompts/calculator_python.prompt",),
    )


def test_exact_approval_is_required_before_any_write(tmp_path: Path) -> None:
    plan = build_intent_plan("Create a calculator.", tmp_path)

    with pytest.raises(ValueError, match="Approval ID does not match"):
        apply_intent(
            plan,
            approved_intent_id="wrong-id",
            create_story=False,
            run_sync=False,
            _architecture_runner=_successful_architecture,
        )

    assert list(tmp_path.iterdir()) == []


def test_brownfield_requires_characterization_assertion(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('existing')\n", encoding="utf-8")
    plan = build_intent_plan("Add CSV export.", tmp_path)

    with pytest.raises(ValueError, match="requires characterization evidence"):
        apply_intent(
            plan,
            approved_intent_id=plan.intent_id,
            create_story=False,
            run_sync=False,
            _architecture_runner=_successful_architecture,
        )

    assert not (tmp_path / "docs" / "PRODUCT_INTENT.md").exists()


def test_greenfield_apply_records_intent_and_status(tmp_path: Path) -> None:
    proposed = tmp_path / "new_project"
    plan = build_intent_plan("Create a calculator.", proposed)

    result = apply_intent(
        plan,
        approved_intent_id=plan.intent_id,
        create_story=False,
        run_sync=False,
        _architecture_runner=_successful_architecture,
    )

    assert result.success is True
    assert result.schema_version == INTENT_APPLY_SCHEMA_VERSION
    assert result.route == "local_full_architecture"
    event = proposed / "docs" / "intents" / f"intent__{plan.intent_id}.md"
    assert plan.original_request in event.read_text(encoding="utf-8")
    product_intent = (proposed / "docs" / "PRODUCT_INTENT.md").read_text(
        encoding="utf-8"
    )
    assert product_intent.count(f"pdd-intent-entry:{plan.intent_id}:start") == 1
    status = json.loads(
        (proposed / ".pdd" / "intents" / f"{plan.intent_id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert status["status"] == "applied"
    assert status["original_request_sha256"] == plan.original_request_sha256


def test_successful_reapply_is_idempotent(tmp_path: Path) -> None:
    plan = build_intent_plan("Create a calculator.", tmp_path)
    calls = 0

    def architecture_runner(*args) -> _WorkflowOutcome:
        nonlocal calls
        calls += 1
        return _successful_architecture(*args)

    first = apply_intent(
        plan,
        approved_intent_id=plan.intent_id,
        create_story=False,
        run_sync=False,
        _architecture_runner=architecture_runner,
    )
    second = apply_intent(
        plan,
        approved_intent_id=plan.intent_id,
        create_story=False,
        run_sync=False,
        _architecture_runner=architecture_runner,
    )

    assert first.success and second.success
    assert calls == 1
    assert second.steps[0]["name"] == "idempotency"
    product_intent = (tmp_path / "docs" / "PRODUCT_INTENT.md").read_text(
        encoding="utf-8"
    )
    assert product_intent.count(f"pdd-intent-entry:{plan.intent_id}:start") == 1


def test_existing_pdd_with_architecture_uses_incremental_route(tmp_path: Path) -> None:
    (tmp_path / ".pddrc").write_text("version: '1.0'\n", encoding="utf-8")
    (tmp_path / "architecture.json").write_text(
        json.dumps(
            [
                {
                    "filename": "calculator_python.prompt",
                    "filepath": "calculator.py",
                    "description": "Calculator",
                    "reason": "Calculate",
                    "dependencies": [],
                    "priority": 1,
                }
            ]
        ),
        encoding="utf-8",
    )
    plan = build_intent_plan("Improve the calculator.", tmp_path)

    result = apply_intent(
        plan,
        approved_intent_id=plan.intent_id,
        create_story=False,
        run_sync=False,
        _architecture_runner=_successful_architecture,
    )

    assert result.success
    assert result.route == "incremental_product_intent"


@pytest.mark.parametrize("kind", ["correct", "remove", "replace"])
def test_superseding_kinds_require_existing_event(tmp_path: Path, kind: str) -> None:
    plan = build_intent_plan("Change calculator rounding.", tmp_path)

    with pytest.raises(ValueError, match="requires --supersedes"):
        apply_intent(
            plan,
            approved_intent_id=plan.intent_id,
            intent_kind=kind,
            create_story=False,
            run_sync=False,
            _architecture_runner=_successful_architecture,
        )

    with pytest.raises(ValueError, match="was not found"):
        apply_intent(
            plan,
            approved_intent_id=plan.intent_id,
            intent_kind=kind,
            supersedes="prior-intent-12345678",
            create_story=False,
            run_sync=False,
            _architecture_runner=_successful_architecture,
        )


def test_correction_can_reference_prior_local_event(tmp_path: Path) -> None:
    original = build_intent_plan("Create a calculator.", tmp_path)
    original_result = apply_intent(
        original,
        approved_intent_id=original.intent_id,
        create_story=False,
        run_sync=False,
        _architecture_runner=_successful_architecture,
    )
    assert original_result.success
    correction = build_intent_plan("Use banker's rounding instead.", tmp_path)

    result = apply_intent(
        correction,
        approved_intent_id=correction.intent_id,
        intent_kind="correct",
        supersedes=original.intent_id,
        create_story=False,
        run_sync=False,
        _architecture_runner=_successful_architecture,
    )

    assert result.success
    event = (
        tmp_path
        / "docs"
        / "intents"
        / f"intent__{correction.intent_id}.md"
    ).read_text(encoding="utf-8")
    assert f"- Supersedes: `{original.intent_id}`" in event


def test_architecture_failure_is_recorded_without_false_success(tmp_path: Path) -> None:
    plan = build_intent_plan("Create a calculator.", tmp_path)

    result = apply_intent(
        plan,
        approved_intent_id=plan.intent_id,
        create_story=False,
        run_sync=False,
        _architecture_runner=lambda *_: _WorkflowOutcome(
            False, "Architecture validation failed."
        ),
    )

    assert result.success is False
    assert result.status == "failed"
    assert "Architecture validation failed" in result.message
    status = json.loads(
        (tmp_path / ".pdd" / "intents" / f"{plan.intent_id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert status["status"] == "failed"
    assert any(step["status"] == "failed" for step in status["steps"])


def test_recommended_story_regression_and_sync_use_existing_commands(
    tmp_path: Path,
) -> None:
    request = (
        "Create a calculator. Never send operands over the network. "
        "For example, two plus two returns four."
    )
    plan = build_intent_plan(request, tmp_path)
    commands: list[list[str]] = []

    def command_runner(args, root: Path) -> _WorkflowOutcome:
        command = list(args)
        commands.append(command)
        if command[:2] == ["story", "add"]:
            from pdd.intent_apply import _story_paths

            story_path, _ = _story_paths(plan, root)
            story_path.parent.mkdir(parents=True, exist_ok=True)
            story_path.write_text("# Story\n", encoding="utf-8")
        elif command[0] == "test":
            output = Path(command[command.index("--output") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("def test_story(): pass\n", encoding="utf-8")
        return _WorkflowOutcome(True, "ok")

    awaiting = apply_intent(
        plan,
        approved_intent_id=plan.intent_id,
        _architecture_runner=_successful_architecture,
        _command_runner=command_runner,
    )

    assert awaiting.success is False
    assert awaiting.status == "awaiting_story_approval"
    assert awaiting.approval_required
    assert commands[0][:2] == ["story", "add"]
    assert len(commands) == 1

    result = apply_intent(
        plan,
        approved_intent_id=plan.intent_id,
        approved_story_sha256=awaiting.approval_required["sha256"],
        _architecture_runner=_successful_architecture,
        _command_runner=command_runner,
    )

    assert result.success
    assert commands[1][:2] == ["story", "link"]
    assert commands[2][:2] == ["test", "--from-story"]
    assert commands[3] == ["--force", "sync", "--evidence", "--no-steer"]
    assert [step["name"] for step in result.steps] == [
        "durable_intent",
        "architecture_and_prompts",
        "story",
        "story_regression",
        "sync",
    ]


def test_structured_result_is_stable(tmp_path: Path) -> None:
    plan = build_intent_plan("Create a calculator.", tmp_path)
    result = apply_intent(
        plan,
        approved_intent_id=plan.intent_id,
        create_story=False,
        run_sync=False,
        _architecture_runner=_successful_architecture,
    )

    payload = intent_apply_result_to_dict(result)

    assert payload["schema_version"] == "pdd.intent.apply.v1"
    assert payload["success"] is True
    assert payload["status"] == "applied"
    assert payload["steps"][-1]["status"] == "skipped"


def test_full_architecture_runner_disables_github_state(
    monkeypatch, tmp_path: Path
) -> None:
    plan = build_intent_plan("Create a calculator.", tmp_path)
    product_intent = tmp_path / "docs" / "PRODUCT_INTENT.md"
    product_intent.parent.mkdir(parents=True)
    product_intent.write_text("# Product Intent\n", encoding="utf-8")
    captured = {}

    def fake_orchestrator(**kwargs):
        captured.update(kwargs)
        return True, "generated", 1.0, "model", ["architecture.json"]

    monkeypatch.setattr(
        "pdd.agentic_architecture_orchestrator."
        "run_agentic_architecture_orchestrator",
        fake_orchestrator,
    )

    outcome = _default_architecture_runner(plan, product_intent, True, False)

    assert outcome.success
    assert captured["cwd"] == tmp_path.resolve()
    assert captured["repo_owner"] == ""
    assert captured["repo_name"] == ""
    assert captured["use_github_state"] is False
    assert captured["force_single"] is True


def test_incremental_runner_uses_local_product_intent(
    monkeypatch, tmp_path: Path
) -> None:
    (tmp_path / ".pddrc").write_text("version: '1.0'\n", encoding="utf-8")
    (tmp_path / "architecture.json").write_text(
        json.dumps(
            [
                {
                    "filename": "calculator_python.prompt",
                    "filepath": "calculator.py",
                    "description": "Calculator",
                    "reason": "Calculate",
                    "dependencies": [],
                    "priority": 1,
                }
            ]
        ),
        encoding="utf-8",
    )
    product_intent = tmp_path / "docs" / "PRODUCT_INTENT.md"
    product_intent.parent.mkdir(parents=True)
    product_intent.write_text("# Product Intent\n", encoding="utf-8")
    plan = build_intent_plan("Improve the calculator.", tmp_path)
    captured = {}

    def fake_incremental(source, **kwargs):
        captured["source"] = source
        captured.update(kwargs)
        return True, "updated", 0.5, "model", ["prompts/calculator_python.prompt"]

    monkeypatch.setattr(
        "pdd.agentic_architecture.run_incremental_architecture",
        fake_incremental,
    )

    outcome = _default_architecture_runner(plan, product_intent, True, False)

    assert outcome.success
    assert captured["source"] == str(product_intent)
    assert captured["project_root"] == str(tmp_path.resolve())
    assert captured["use_github_state"] is False


def test_child_command_runner_reports_files_changed_by_sync(
    monkeypatch, tmp_path: Path
) -> None:
    class Completed:
        returncode = 0
        stdout = "synced"
        stderr = ""

    def fake_run(*args, **kwargs):
        del args, kwargs
        generated = tmp_path / "src" / "generated.py"
        generated.parent.mkdir()
        generated.write_text("VALUE = 1\n", encoding="utf-8")
        return Completed()

    monkeypatch.setattr("pdd.intent_apply.subprocess.run", fake_run)

    outcome = _default_command_runner(["--force", "sync"], tmp_path)

    assert outcome.success
    assert outcome.changed_files == ("src/generated.py",)


def test_external_source_path_is_not_persisted_verbatim(tmp_path: Path) -> None:
    source = tmp_path / "private-request.md"
    source.write_text("Create a calculator.", encoding="utf-8")
    project = tmp_path / "project"
    plan = build_intent_plan(
        source.read_text(encoding="utf-8"),
        project,
        source_kind="file",
        source_ref=str(source.resolve()),
    )

    result = apply_intent(
        plan,
        approved_intent_id=plan.intent_id,
        create_story=False,
        run_sync=False,
        _architecture_runner=_successful_architecture,
    )

    assert result.success
    event = (
        project / "docs" / "intents" / f"intent__{plan.intent_id}.md"
    ).read_text(encoding="utf-8")
    assert str(source.resolve()) not in event
    assert "external-local-file:private-request.md" in event
