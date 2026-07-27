"""`pdd intent` commands for local ordinary-language planning and application."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import click

from ..intent import build_intent_plan, intent_plan_to_dict, render_review_card
from ..intent_apply import (
    apply_intent,
    intent_apply_result_to_dict,
    render_apply_result,
)

_MAX_INTENT_CHARS = 100_000


def _read_intent_source(
    source: Optional[Path], inline_text: Optional[str]
) -> tuple[str, str, Optional[str]]:
    if source is not None and inline_text is not None:
        raise click.ClickException("Use either SOURCE or --text, not both.")

    if inline_text is not None:
        request = inline_text
        source_kind = "inline"
        source_ref = None
    elif source is not None:
        if not source.is_file():
            raise click.ClickException(f"Intent source is not a file: {source}")
        try:
            request = source.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise click.ClickException(f"Could not read intent source {source}: {exc}") from exc
        source_kind = "file"
        source_ref = str(source.resolve())
    elif not sys.stdin.isatty():
        request = click.get_text_stream("stdin").read()
        source_kind = "stdin"
        source_ref = "<stdin>"
    else:
        raise click.ClickException(
            "Provide a local SOURCE file, pass --text, or pipe the request on standard input."
        )

    if not request.strip():
        raise click.ClickException("Intent request must not be empty.")
    if len(request) > _MAX_INTENT_CHARS:
        raise click.ClickException(
            f"Intent request exceeds the {_MAX_INTENT_CHARS:,}-character planning limit."
        )
    return request, source_kind, source_ref


@click.group(name="intent")
def intent() -> None:
    """Accept ordinary product intent and determine the appropriate PDD route."""


@intent.command("plan")
@click.argument(
    "source",
    required=False,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option("--text", "inline_text", default=None, help="Ordinary-language request.")
@click.option("--title", default=None, help="Optional human-readable intent title.")
@click.option(
    "--project-root",
    default=".",
    type=click.Path(file_okay=False, path_type=Path),
    help="Existing or proposed project scope. Defaults to the current directory.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured agent output.")
def plan_intent(
    source: Optional[Path],
    inline_text: Optional[str],
    title: Optional[str],
    project_root: Path,
    as_json: bool,
) -> None:
    """Plan local product intent without GitHub, model calls, or file changes."""
    request, source_kind, source_ref = _read_intent_source(source, inline_text)
    try:
        plan = build_intent_plan(
            request,
            project_root,
            title=title,
            source_kind=source_kind,
            source_ref=source_ref,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    if as_json:
        click.echo(json.dumps(intent_plan_to_dict(plan), indent=2, sort_keys=True))
    else:
        click.echo(render_review_card(plan))


@intent.command("apply")
@click.argument(
    "source",
    required=False,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option("--text", "inline_text", default=None, help="Exact ordinary-language request.")
@click.option("--title", default=None, help="Optional human-readable intent title.")
@click.option(
    "--project-root",
    default=".",
    type=click.Path(file_okay=False, path_type=Path),
    help="Existing or proposed project scope. Defaults to the current directory.",
)
@click.option(
    "--approve",
    "approved_intent_id",
    required=True,
    help="Exact intent ID emitted by `pdd intent plan`.",
)
@click.option(
    "--kind",
    "intent_kind",
    type=click.Choice(["add", "clarify", "correct", "remove", "replace"]),
    default="add",
    show_default=True,
    help="Agent-classified relationship to accepted product intent.",
)
@click.option(
    "--supersedes",
    default=None,
    help="Prior local intent ID corrected, removed, or replaced by this event.",
)
@click.option(
    "--characterized",
    is_flag=True,
    help=(
        "Agent assertion that brownfield characterization and critical negative "
        "tests were actually run; this flag is not a substitute for those checks."
    ),
)
@click.option(
    "--approve-story",
    "approved_story_sha256",
    default=None,
    help="SHA-256 of the generated story wording reviewed by the human.",
)
@click.option("--no-story", is_flag=True, help="Agent override: skip selective story coverage.")
@click.option("--no-sync", is_flag=True, help="Agent override: stop after intent/prompt work.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured agent output.")
@click.pass_context
def apply_intent_command(
    ctx: click.Context,
    source: Optional[Path],
    inline_text: Optional[str],
    title: Optional[str],
    project_root: Path,
    approved_intent_id: str,
    intent_kind: str,
    supersedes: Optional[str],
    characterized: bool,
    approved_story_sha256: Optional[str],
    no_story: bool,
    no_sync: bool,
    as_json: bool,
) -> None:
    """Apply an exactly approved local plan; mutates scope without GitHub."""
    request, source_kind, source_ref = _read_intent_source(source, inline_text)
    try:
        plan = build_intent_plan(
            request,
            project_root,
            title=title,
            source_kind=source_kind,
            source_ref=source_ref,
        )
        result = apply_intent(
            plan,
            approved_intent_id=approved_intent_id,
            intent_kind=intent_kind,
            supersedes=supersedes,
            characterized=characterized,
            create_story=not no_story,
            run_sync=not no_sync,
            approved_story_sha256=approved_story_sha256,
            quiet=bool((ctx.obj or {}).get("quiet", False)),
            verbose=bool((ctx.obj or {}).get("verbose", False)),
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    if as_json:
        click.echo(
            json.dumps(intent_apply_result_to_dict(result), indent=2, sort_keys=True)
        )
    else:
        click.echo(render_apply_result(result))
    if result.status == "awaiting_story_approval":
        raise click.exceptions.Exit(2)
    if not result.success:
        raise click.exceptions.Exit(1)


intent_cli = intent
