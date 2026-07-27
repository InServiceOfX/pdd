"""Guarded local application of an exactly approved intent plan."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .architecture_registry import extract_modules
from .intent import IntentPlan, detected_technology_terms

INTENT_APPLY_SCHEMA_VERSION = "pdd.intent.apply.v1"
_INTENT_KINDS = {"add", "clarify", "correct", "remove", "replace"}
_SUPERSEDING_KINDS = {"correct", "remove", "replace"}
_MAX_CHILD_DETAIL = 4_000
_MAX_SNAPSHOT_FILES = 20_000
_INTENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,159}$")
_SNAPSHOT_EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "node_modules",
    "target",
    "vendor",
    "venv",
}


@dataclass(frozen=True)
class IntentApplyResult:
    """Stable result of an intent application attempt."""

    schema_version: str
    success: bool
    intent_id: str
    route: str
    status: str
    changed_files: Tuple[str, ...]
    steps: Tuple[Dict[str, Any], ...]
    message: str
    cost: float
    model: str
    approval_required: Optional[Dict[str, str]] = None


@dataclass(frozen=True)
class _WorkflowOutcome:
    success: bool
    message: str
    cost: float = 0.0
    model: str = ""
    changed_files: Tuple[str, ...] = ()


_ArchitectureRunner = Callable[[IntentPlan, Path, bool, bool], _WorkflowOutcome]
_CommandRunner = Callable[[Sequence[str], Path], _WorkflowOutcome]


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    _atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _blockquote(text: str) -> str:
    return "\n".join(f"> {line}" if line else ">" for line in text.splitlines())


def _bullets(values: Iterable[str]) -> str:
    items = list(values)
    return "\n".join(f"- {item}" for item in items) if items else "- None stated."


def _technology_decision(plan: IntentPlan, technology: Optional[str]) -> str:
    """Return the accepted technology, preferring the agent's explicit choice."""
    if technology and technology.strip():
        return technology.strip()
    stated = detected_technology_terms(plan.original_request)
    return ", ".join(stated) if stated else "not stated"


def _event_markdown(
    plan: IntentPlan,
    *,
    intent_kind: str,
    supersedes: Optional[str],
    approved_intent_id: str,
    technology: Optional[str] = None,
) -> str:
    supersedes_line = (
        f"- Supersedes: `{supersedes}`\n" if supersedes else "- Supersedes: none\n"
    )
    source_ref = _source_reference(plan)
    return (
        f"# Intent: {plan.title}\n\n"
        f"<!-- pdd-intent-id: {plan.intent_id} -->\n"
        f"<!-- pdd-intent-sha256: {plan.original_request_sha256} -->\n\n"
        "## Record\n\n"
        f"- Intent ID: `{plan.intent_id}`\n"
        f"- Kind: `{intent_kind}`\n"
        f"{supersedes_line}"
        f"- Approval ID: `{approved_intent_id}`\n"
        f"- Source kind: `{plan.source_kind}`\n"
        f"- Source reference: `{source_ref}`\n"
        f"- Request SHA-256: `{plan.original_request_sha256}`\n"
        f"- Project scope: `{plan.scope_kind}`\n"
        f"- Adoption scenario: `{plan.adoption_scenario}`\n\n"
        f"- Technology: `{_technology_decision(plan, technology)}`\n\n"
        "## Original Request\n\n"
        f"{_blockquote(plan.original_request)}\n\n"
        "## Must Stay Unchanged\n\n"
        f"{_bullets(plan.must_preserve)}\n\n"
        "## Examples\n\n"
        f"{_bullets(plan.examples)}\n\n"
        "## Candidate Product Areas\n\n"
        f"{_bullets(target.product_area for target in plan.candidate_targets)}\n"
    )


def _source_reference(plan: IntentPlan) -> str:
    if not plan.source_ref:
        return "not applicable"
    if plan.source_kind != "file":
        return plan.source_ref
    source_path = Path(plan.source_ref)
    root = Path(plan.project_root)
    try:
        return source_path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return f"external-local-file:{source_path.name}"


def _product_intent_entry(
    plan: IntentPlan,
    event_relative: str,
    intent_kind: str,
    supersedes: Optional[str],
    technology: Optional[str] = None,
) -> str:
    supersedes_line = (
        f"- Supersedes: `{supersedes}`\n" if supersedes else "- Supersedes: none\n"
    )
    return (
        f"\n<!-- pdd-intent-entry:{plan.intent_id}:start -->\n"
        f"## {plan.title}\n\n"
        f"- Intent event: [`{event_relative}`]({event_relative.removeprefix('docs/')})\n"
        f"- Change kind: `{intent_kind}`\n"
        f"{supersedes_line}"
        f"- Scope: `{plan.adoption_scenario}`\n"
        f"- Technology: `{_technology_decision(plan, technology)}`\n\n"
        f"{_blockquote(plan.original_request)}\n"
        f"<!-- pdd-intent-entry:{plan.intent_id}:end -->\n"
    )


def _ensure_durable_intent(
    plan: IntentPlan,
    root: Path,
    *,
    intent_kind: str,
    supersedes: Optional[str],
    approved_intent_id: str,
    technology: Optional[str] = None,
) -> Tuple[Path, Path, List[str]]:
    event_path = root / "docs" / "intents" / f"intent__{plan.intent_id}.md"
    product_intent_path = root / "docs" / "PRODUCT_INTENT.md"
    expected_event = _event_markdown(
        plan,
        intent_kind=intent_kind,
        supersedes=supersedes,
        approved_intent_id=approved_intent_id,
        technology=technology,
    )
    changed: List[str] = []

    if event_path.exists():
        existing = event_path.read_text(encoding="utf-8")
        digest_marker = f"<!-- pdd-intent-sha256: {plan.original_request_sha256} -->"
        kind_marker = f"- Kind: `{intent_kind}`"
        supersedes_marker = (
            f"- Supersedes: `{supersedes}`" if supersedes else "- Supersedes: none"
        )
        if (
            digest_marker not in existing
            or kind_marker not in existing
            or supersedes_marker not in existing
        ):
            raise ValueError(f"Existing intent event conflicts with this request: {event_path}")
    else:
        _atomic_write_text(event_path, expected_event)
        changed.append(_relative(event_path, root))

    if product_intent_path.exists():
        product_text = product_intent_path.read_text(encoding="utf-8")
    else:
        product_text = (
            "# Product Intent (PRD)\n\n"
            "This current product-intent record is maintained by `pdd intent apply`.\n"
            "Each accepted change links to an immutable intent event.\n"
        )
    marker = f"<!-- pdd-intent-entry:{plan.intent_id}:start -->"
    if marker not in product_text:
        product_text = product_text.rstrip() + "\n" + _product_intent_entry(
            plan,
            _relative(event_path, root),
            intent_kind,
            supersedes,
            technology,
        )
        _atomic_write_text(product_intent_path, product_text)
        changed.append(_relative(product_intent_path, root))

    return event_path, product_intent_path, changed


def _load_status(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_status(
    path: Path,
    plan: IntentPlan,
    *,
    status: str,
    route: str,
    steps: Sequence[Dict[str, Any]],
    changed_files: Sequence[str],
    message: str,
    cost: float,
    model: str,
    intent_kind: str,
    supersedes: Optional[str],
    approval_required: Optional[Dict[str, str]] = None,
) -> None:
    _atomic_write_json(
        path,
        {
            "schema_version": INTENT_APPLY_SCHEMA_VERSION,
            "intent_id": plan.intent_id,
            "original_request_sha256": plan.original_request_sha256,
            "status": status,
            "route": route,
            "intent_kind": intent_kind,
            "supersedes": supersedes,
            "steps": list(steps),
            "changed_files": sorted(dict.fromkeys(changed_files)),
            "message": message,
            "cost": cost,
            "model": model,
            "approval_required": approval_required,
        },
    )


def _usable_architecture(root: Path) -> bool:
    path = root / "architecture.json"
    if not path.is_file():
        return False
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(extract_modules(raw))


def _bounded_detail(text: str) -> str:
    cleaned = re.sub(
        r"(?i)(token|api[_-]?key|authorization)(\s*[:=]\s*)\S+",
        r"\1\2[REDACTED]",
        text.strip(),
    )
    if len(cleaned) <= _MAX_CHILD_DETAIL:
        return cleaned
    return cleaned[-_MAX_CHILD_DETAIL:]


def _normalize_changed_files(paths: Iterable[str], root: Path) -> Tuple[str, ...]:
    normalized: List[str] = []
    for raw in paths:
        path = Path(raw)
        if not path.is_absolute():
            path = root / path
        resolved = path.resolve()
        try:
            display = resolved.relative_to(root.resolve()).as_posix()
        except ValueError as exc:
            raise ValueError(
                f"PDD workflow reported an out-of-scope changed path: {resolved}"
            ) from exc
        if display not in normalized:
            normalized.append(display)
    return tuple(normalized)


def _default_architecture_runner(
    plan: IntentPlan, product_intent_path: Path, quiet: bool, verbose: bool
) -> _WorkflowOutcome:
    root = Path(plan.project_root).resolve()
    if plan.project_kind == "existing_pdd" and _usable_architecture(root):
        from .agentic_architecture import run_incremental_architecture

        success, message, cost, model, changed = run_incremental_architecture(
            str(product_intent_path),
            quiet=quiet,
            verbose=verbose,
            use_github_state=False,
            project_root=str(root),
        )
        return _WorkflowOutcome(
            success=success,
            message=message,
            cost=cost,
            model=model,
            changed_files=_normalize_changed_files(changed, root),
        )

    from .agentic_architecture_orchestrator import (
        run_agentic_architecture_orchestrator,
    )

    issue_number = int(hashlib.sha256(plan.intent_id.encode()).hexdigest()[:8], 16)
    existing_pddrc = (
        (root / ".pddrc").read_text(encoding="utf-8")
        if (root / ".pddrc").is_file()
        else ""
    )
    existing_architecture = (
        (root / "architecture.json").read_text(encoding="utf-8")
        if (root / "architecture.json").is_file()
        else ""
    )
    success, message, cost, model, changed = run_agentic_architecture_orchestrator(
        issue_url=f"local-intent:{plan.intent_id}",
        issue_content=product_intent_path.read_text(encoding="utf-8"),
        repo_owner="",
        repo_name="",
        issue_number=max(1, issue_number),
        issue_author="local-intent",
        issue_title=plan.title,
        cwd=root,
        verbose=verbose,
        quiet=quiet,
        use_github_state=False,
        force_single=True,
        existing_pddrc=existing_pddrc,
        existing_architecture=existing_architecture,
    )
    return _WorkflowOutcome(
        success=success,
        message=message,
        cost=cost,
        model=model,
        changed_files=_normalize_changed_files(changed, root),
    )


def _default_command_runner(args: Sequence[str], root: Path) -> _WorkflowOutcome:
    before = _snapshot_project_files(root)
    completed = subprocess.run(
        [sys.executable, "-m", "pdd", *args],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    after = _snapshot_project_files(root)
    changed_files = tuple(
        sorted(
            path
            for path in set(before) | set(after)
            if before.get(path) != after.get(path)
        )
    )
    detail = "\n".join(
        part for part in (completed.stdout.strip(), completed.stderr.strip()) if part
    )
    return _WorkflowOutcome(
        success=completed.returncode == 0,
        message=_bounded_detail(detail)
        or f"pdd {' '.join(args)} exited {completed.returncode}",
        changed_files=changed_files,
    )


def _snapshot_project_files(root: Path) -> Dict[str, Tuple[int, int]]:
    snapshot: Dict[str, Tuple[int, int]] = {}
    if not root.is_dir():
        return snapshot
    for current, directories, filenames in os.walk(root):
        directories[:] = sorted(
            name for name in directories if name not in _SNAPSHOT_EXCLUDED_DIRS
        )
        current_path = Path(current)
        for filename in sorted(filenames):
            path = current_path / filename
            if path.is_symlink():
                continue
            try:
                stat = path.stat()
                relative = path.relative_to(root).as_posix()
            except (OSError, ValueError):
                continue
            snapshot[relative] = (stat.st_size, stat.st_mtime_ns)
            if len(snapshot) >= _MAX_SNAPSHOT_FILES:
                return snapshot
    return snapshot


def _step(name: str, outcome: _WorkflowOutcome, *, skipped: bool = False) -> Dict[str, Any]:
    return {
        "name": name,
        "status": "skipped" if skipped else ("passed" if outcome.success else "failed"),
        "message": outcome.message,
        "changed_files": list(outcome.changed_files),
        "cost": outcome.cost,
        "model": outcome.model,
    }


def _candidate_prompt_paths(plan: IntentPlan, changed_files: Sequence[str]) -> List[str]:
    root = Path(plan.project_root).resolve()
    values: List[str] = []
    for item in changed_files:
        if item.endswith(".prompt"):
            path = Path(item)
            if not path.is_absolute():
                path = root / path
            if path.is_file():
                values.append(str(path))
    if not values:
        for target in plan.candidate_targets:
            if not target.prompt_path:
                continue
            path = root / target.prompt_path
            if path.is_file():
                values.append(str(path))
    return list(dict.fromkeys(values))


def _story_paths(plan: IntentPlan, root: Path) -> Tuple[Path, Path]:
    from .user_story_tests import (
        STORY_PREFIX,
        STORY_SUFFIX,
        _slugify_story_name,
    )

    slug = _slugify_story_name(_story_title(plan))
    story = root / "user_stories" / f"{STORY_PREFIX}{slug}{STORY_SUFFIX}"
    regression = root / "tests" / "story_regression" / f"test_story_{slug}.py"
    return story, regression


def _story_title(plan: IntentPlan) -> str:
    if (
        not Path(plan.title).is_absolute()
        and "/" not in plan.title
        and "\\" not in plan.title
        and ".." not in Path(plan.title).parts
    ):
        return plan.title
    return plan.intent_id.rsplit("-", 1)[0]


def _result(
    plan: IntentPlan,
    *,
    success: bool,
    route: str,
    status: str,
    changed_files: Sequence[str],
    steps: Sequence[Dict[str, Any]],
    message: str,
    cost: float,
    model: str,
    approval_required: Optional[Dict[str, str]] = None,
) -> IntentApplyResult:
    return IntentApplyResult(
        schema_version=INTENT_APPLY_SCHEMA_VERSION,
        success=success,
        intent_id=plan.intent_id,
        route=route,
        status=status,
        changed_files=tuple(sorted(dict.fromkeys(changed_files))),
        steps=tuple(dict(item) for item in steps),
        message=message,
        cost=cost,
        model=model,
        approval_required=approval_required,
    )


def apply_intent(
    plan: IntentPlan,
    *,
    approved_intent_id: str,
    intent_kind: str = "add",
    supersedes: Optional[str] = None,
    characterized: bool = False,
    technology: Optional[str] = None,
    create_story: bool = True,
    run_sync: bool = True,
    approved_story_sha256: Optional[str] = None,
    quiet: bool = False,
    verbose: bool = False,
    _architecture_runner: Optional[_ArchitectureRunner] = None,
    _command_runner: Optional[_CommandRunner] = None,
) -> IntentApplyResult:
    """Apply an exact approved intent plan through existing local PDD workflows."""
    if approved_intent_id != plan.intent_id:
        raise ValueError(
            f"Approval ID does not match this plan; expected {plan.intent_id}."
        )
    if intent_kind not in _INTENT_KINDS:
        raise ValueError(f"Unsupported intent kind: {intent_kind}")
    if supersedes and not _INTENT_ID_RE.fullmatch(supersedes):
        raise ValueError("Superseded intent ID has an invalid format.")
    if supersedes == plan.intent_id:
        raise ValueError("An intent cannot supersede itself.")
    if intent_kind in _SUPERSEDING_KINDS and not supersedes:
        raise ValueError(f"Intent kind '{intent_kind}' requires --supersedes.")
    if supersedes and intent_kind not in _SUPERSEDING_KINDS:
        raise ValueError("--supersedes is only valid with correct, remove, or replace.")
    if approved_story_sha256 and not re.fullmatch(
        r"[0-9a-f]{64}", approved_story_sha256
    ):
        raise ValueError("Approved story SHA-256 must be 64 lowercase hexadecimal characters.")
    if approved_story_sha256 and not create_story:
        raise ValueError("--approve-story cannot be combined with --no-story.")
    if plan.project_kind == "conventional_brownfield" and not characterized:
        raise ValueError(
            "Brownfield apply requires characterization evidence; run the existing "
            "behavior and critical negative tests, then pass --characterized."
        )
    if (
        plan.project_kind == "greenfield"
        and not (technology and technology.strip())
        and not detected_technology_terms(plan.original_request)
    ):
        raise ValueError(
            "Greenfield apply requires a technology decision; the architecture "
            "workflow cannot select a language or runtime on its own. State it in "
            "the request or pass --technology."
        )

    root = Path(plan.project_root).resolve()
    if root.exists() and not root.is_dir():
        raise ValueError(f"Project root is not a directory: {root}")
    if not root.exists():
        if plan.project_kind != "greenfield":
            raise ValueError("Only an approved greenfield plan may create a project root.")
        root.mkdir(parents=True)

    if supersedes:
        superseded_path = root / "docs" / "intents" / f"intent__{supersedes}.md"
        if not superseded_path.is_file():
            raise ValueError(f"Superseded local intent event was not found: {supersedes}")

    status_path = root / ".pdd" / "intents" / f"{plan.intent_id}.json"
    previous_status = _load_status(status_path)
    if previous_status and previous_status.get("status") == "applied":
        if previous_status.get("original_request_sha256") != plan.original_request_sha256:
            raise ValueError("Applied intent status conflicts with this request.")
        if previous_status.get("intent_kind") != intent_kind:
            raise ValueError("Applied intent kind conflicts with this request.")
        if previous_status.get("supersedes") != supersedes:
            raise ValueError("Applied supersedes metadata conflicts with this request.")
        return _result(
            plan,
            success=True,
            route=str(previous_status.get("route") or "idempotent"),
            status="applied",
            changed_files=(),
            steps=(
                {
                    "name": "idempotency",
                    "status": "skipped",
                    "message": "This exact intent was already applied.",
                    "changed_files": [],
                    "cost": 0.0,
                    "model": "",
                },
            ),
            message="This exact intent was already applied; no workflows were rerun.",
            cost=0.0,
            model="",
        )

    route = (
        "incremental_product_intent"
        if plan.project_kind == "existing_pdd" and _usable_architecture(root)
        else "local_full_architecture"
    )
    steps: List[Dict[str, Any]] = []
    changed_files: List[str] = [_relative(status_path, root)]
    total_cost = 0.0
    model = ""

    try:
        event_path, product_intent_path, durable_changed = _ensure_durable_intent(
            plan,
            root,
            intent_kind=intent_kind,
            supersedes=supersedes,
            approved_intent_id=approved_intent_id,
            technology=technology,
        )
        changed_files.extend(durable_changed)
        durable_outcome = _WorkflowOutcome(
            True,
            "Durable intent event and Product Intent are recorded.",
            changed_files=tuple(durable_changed),
        )
        steps.append(_step("durable_intent", durable_outcome))
        _write_status(
            status_path,
            plan,
            status="applying",
            route=route,
            steps=steps,
            changed_files=changed_files,
            message="Intent captured; applying architecture and prompt changes.",
            cost=total_cost,
            model=model,
            intent_kind=intent_kind,
            supersedes=supersedes,
        )

        architecture_runner = _architecture_runner or _default_architecture_runner
        architecture_outcome = architecture_runner(
            plan, product_intent_path, quiet, verbose
        )
        steps.append(_step("architecture_and_prompts", architecture_outcome))
        total_cost += architecture_outcome.cost
        model = architecture_outcome.model or model
        changed_files.extend(architecture_outcome.changed_files)
        if not architecture_outcome.success:
            raise RuntimeError(architecture_outcome.message)

        command_runner = _command_runner or _default_command_runner
        prompt_paths = _candidate_prompt_paths(plan, changed_files)
        if plan.story_recommended and create_story:
            if not prompt_paths:
                raise RuntimeError(
                    "Story coverage was recommended, but no changed or candidate prompt "
                    "could be linked."
                )
            story_path, regression_path = _story_paths(plan, root)
            if story_path.is_file():
                story_args: List[str] = [
                    "story",
                    "link",
                    str(story_path),
                ]
            else:
                story_args = [
                    "story",
                    "add",
                    str(event_path),
                    "--title",
                    _story_title(plan),
                ]
            for prompt_path in prompt_paths:
                story_args.extend(["--prompt", prompt_path])
            story_outcome = command_runner(story_args, root)
            steps.append(_step("story", story_outcome))
            changed_files.extend(story_outcome.changed_files)
            if not story_outcome.success:
                raise RuntimeError(story_outcome.message)
            if story_path.is_file():
                changed_files.append(_relative(story_path, root))

            story_sha256 = hashlib.sha256(story_path.read_bytes()).hexdigest()
            if approved_story_sha256 != story_sha256:
                approval_required = {
                    "kind": "story",
                    "path": _relative(story_path, root),
                    "sha256": story_sha256,
                }
                message = (
                    "Review the generated story meaning, then rerun with "
                    f"--approve-story {story_sha256}."
                )
                _write_status(
                    status_path,
                    plan,
                    status="awaiting_story_approval",
                    route=route,
                    steps=steps,
                    changed_files=changed_files,
                    message=message,
                    cost=total_cost,
                    model=model,
                    intent_kind=intent_kind,
                    supersedes=supersedes,
                    approval_required=approval_required,
                )
                return _result(
                    plan,
                    success=False,
                    route=route,
                    status="awaiting_story_approval",
                    changed_files=changed_files,
                    steps=steps,
                    message=message,
                    cost=total_cost,
                    model=model,
                    approval_required=approval_required,
                )

            if regression_path.is_file():
                regression_outcome = _WorkflowOutcome(
                    True,
                    "Story regression already exists.",
                    changed_files=(_relative(regression_path, root),),
                )
                steps.append(_step("story_regression", regression_outcome, skipped=True))
            else:
                regression_outcome = command_runner(
                    [
                        "test",
                        "--from-story",
                        str(story_path),
                        "--output",
                        str(regression_path),
                    ],
                    root,
                )
                steps.append(_step("story_regression", regression_outcome))
                changed_files.extend(regression_outcome.changed_files)
                if not regression_outcome.success:
                    raise RuntimeError(regression_outcome.message)
                if regression_path.is_file():
                    changed_files.append(_relative(regression_path, root))
        else:
            reason = (
                "Story coverage disabled by the agent."
                if not create_story
                else "Independent story coverage was not recommended for this intent."
            )
            steps.append(_step("story", _WorkflowOutcome(True, reason), skipped=True))

        if run_sync:
            sync_outcome = command_runner(
                ["--force", "sync", "--evidence", "--no-steer"],
                root,
            )
            steps.append(_step("sync", sync_outcome))
            changed_files.extend(sync_outcome.changed_files)
            if not sync_outcome.success:
                raise RuntimeError(sync_outcome.message)
        else:
            steps.append(
                _step(
                    "sync",
                    _WorkflowOutcome(True, "Scoped synchronization disabled by the agent."),
                    skipped=True,
                )
            )

        message = "Intent applied through durable source, PDD prompts, and verification."
        result = _result(
            plan,
            success=True,
            route=route,
            status="applied",
            changed_files=changed_files,
            steps=steps,
            message=message,
            cost=total_cost,
            model=model,
        )
        _write_status(
            status_path,
            plan,
            status=result.status,
            route=route,
            steps=steps,
            changed_files=changed_files,
            message=message,
            cost=total_cost,
            model=model,
            intent_kind=intent_kind,
            supersedes=supersedes,
        )
        return result
    except Exception as exc:
        message = str(exc)
        if not steps or steps[-1].get("status") != "failed":
            steps.append(
                _step("apply", _WorkflowOutcome(False, _bounded_detail(message)))
            )
        _write_status(
            status_path,
            plan,
            status="failed",
            route=route,
            steps=steps,
            changed_files=changed_files,
            message=message,
            cost=total_cost,
            model=model,
            intent_kind=intent_kind,
            supersedes=supersedes,
        )
        return _result(
            plan,
            success=False,
            route=route,
            status="failed",
            changed_files=changed_files,
            steps=steps,
            message=message,
            cost=total_cost,
            model=model,
        )


def intent_apply_result_to_dict(result: IntentApplyResult) -> Dict[str, Any]:
    """Return the stable JSON-serializable representation of an apply result."""
    return {
        "schema_version": result.schema_version,
        "success": result.success,
        "intent_id": result.intent_id,
        "route": result.route,
        "status": result.status,
        "changed_files": list(result.changed_files),
        "steps": [dict(item) for item in result.steps],
        "message": result.message,
        "cost": result.cost,
        "model": result.model,
        "approval_required": result.approval_required,
    }


def render_apply_result(result: IntentApplyResult) -> str:
    """Render a concise human-readable apply report."""
    lines = [
        f"Intent apply: {'SUCCESS' if result.success else 'FAILED'}",
        f"Intent ID: {result.intent_id}",
        f"Route: {result.route}",
        "",
        "Steps:",
    ]
    for step in result.steps:
        lines.append(
            f"- {step.get('name')}: {step.get('status')} — {step.get('message')}"
        )
    lines.extend(["", "Changed files:"])
    lines.extend(f"- {path}" for path in result.changed_files)
    if not result.changed_files:
        lines.append("- None")
    if result.cost:
        lines.extend(["", f"Reported model cost: ${result.cost:.4f}"])
    if result.model:
        lines.append(f"Model: {result.model}")
    if result.approval_required:
        lines.extend(
            [
                "",
                "Approval required:",
                f"- Kind: {result.approval_required['kind']}",
                f"- Path: {result.approval_required['path']}",
                f"- SHA-256: {result.approval_required['sha256']}",
            ]
        )
    lines.extend(["", result.message])
    return "\n".join(lines)
