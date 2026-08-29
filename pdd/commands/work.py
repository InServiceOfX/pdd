"""`pdd work` commands for local, GitHub-free work items.

A work item is the local stand-in for a GitHub issue: it carries the request
the agentic workflows implement, holds their resumable state, and is the
channel a human uses to steer a run mid-flight. Nothing here touches the
network or the ``gh`` CLI.

Typical flow::

    pdd work new --title "Ring buffer drops a frame" --body-file repro.md
    pdd bug local:1
    pdd work comment 1 --text "Use a ring buffer, not a deque."
    pdd fix local:1
    pdd work show 1
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import click

from .. import local_work_items

_MAX_BODY_CHARS = 200_000


def _project_root() -> Path:
    """Return the project root a work item store belongs to."""
    return Path.cwd()


def _read_body(body: Optional[str], body_file: Optional[Path]) -> str:
    """Resolve the work item body from --body, --body-file, or stdin."""
    if body is not None and body_file is not None:
        raise click.ClickException("Use either --body or --body-file, not both.")

    if body is not None:
        text = body
    elif body_file is not None:
        if not body_file.is_file():
            raise click.ClickException(f"Body file is not a file: {body_file}")
        try:
            text = body_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise click.ClickException(f"Could not read {body_file}: {exc}") from exc
    elif not sys.stdin.isatty():
        text = click.get_text_stream("stdin").read()
    else:
        text = ""

    if len(text) > _MAX_BODY_CHARS:
        raise click.ClickException(
            f"Work item body exceeds the {_MAX_BODY_CHARS:,}-character limit."
        )
    return text


def _require_item(number: int) -> dict:
    """Load a work item or fail with a clear CLI error."""
    item = local_work_items.load_work_item(_project_root(), number)
    if item is None:
        raise click.ClickException(
            f"No local work item {number}. Run 'pdd work list' to see what exists."
        )
    return item


def _render_item(item: dict, *, with_comments: bool = True) -> str:
    """Render a work item for the terminal."""
    lines = [
        f"{local_work_items.format_local_ref(item['number'])}  [{item.get('state', 'open')}]",
        f"  {item.get('title', '')}",
        f"  created {item.get('created_at')}  updated {item.get('updated_at')}",
    ]
    labels = item.get("labels") or []
    if labels:
        lines.append(f"  labels: {', '.join(labels)}")
    body = str(item.get("body", "") or "").strip()
    if body:
        lines.append("")
        lines.extend(f"  {line}" for line in body.splitlines())
    comments = item.get("comments") or []
    if with_comments and comments:
        lines.append("")
        lines.append(f"  --- {len(comments)} comment(s) ---")
        for comment in comments:
            author = (comment.get("user") or {}).get("login", "unknown")
            lines.append(f"  [{comment.get('id')}] {author} at {comment.get('created_at')}")
            for line in str(comment.get("body", "") or "").splitlines():
                lines.append(f"      {line}")
    return "\n".join(lines)


@click.group(name="work")
def work() -> None:
    """Create and steer local work items (a GitHub-free replacement for issues).

    Reference a work item from any agentic command as ``local:<number>``, for
    example ``pdd bug local:3``. The store lives in ``.pdd/work_items/``.
    """


@work.command(name="new")
@click.option("--title", required=True, help="One-line summary of the request.")
@click.option("--body", default=None, help="Full request text.")
@click.option(
    "--body-file",
    type=click.Path(path_type=Path),
    default=None,
    help="Read the request text from a file.",
)
@click.option("--label", "labels", multiple=True, help="Label to attach (repeatable).")
@click.option("--json", "as_json", is_flag=True, help="Emit the work item as JSON.")
def work_new(
    title: str,
    body: Optional[str],
    body_file: Optional[Path],
    labels: tuple,
    as_json: bool,
) -> None:
    """Create a work item. Body may also be piped on standard input."""
    text = _read_body(body, body_file)
    try:
        item = local_work_items.create_work_item(
            _project_root(), title, text, labels=list(labels)
        )
    except local_work_items.LocalWorkItemError as exc:
        raise click.ClickException(str(exc)) from exc

    if as_json:
        click.echo(json.dumps(item, indent=2, sort_keys=True))
        return
    ref = local_work_items.format_local_ref(item["number"])
    click.echo(f"Created {ref}: {item['title']}")
    click.echo(f"Run a workflow against it, e.g.  pdd bug {ref}")


@work.command(name="list")
@click.option(
    "--state",
    type=click.Choice(["open", "closed", "all"]),
    default="all",
    show_default=True,
    help="Filter by state.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit the list as JSON.")
def work_list(state: str, as_json: bool) -> None:
    """List local work items."""
    items = local_work_items.list_work_items(_project_root())
    if state != "all":
        items = [item for item in items if item.get("state") == state]

    if as_json:
        click.echo(json.dumps(items, indent=2, sort_keys=True))
        return
    if not items:
        click.echo("No local work items. Create one with 'pdd work new --title ...'.")
        return
    for item in items:
        ref = local_work_items.format_local_ref(item["number"])
        comment_count = len(item.get("comments") or [])
        suffix = f"  ({comment_count} comment(s))" if comment_count else ""
        click.echo(f"{ref:<12} [{item.get('state', 'open'):<6}] {item.get('title', '')}{suffix}")


@work.command(name="show")
@click.argument("number", type=int)
@click.option("--json", "as_json", is_flag=True, help="Emit the work item as JSON.")
def work_show(number: int, as_json: bool) -> None:
    """Show one work item and its comments."""
    item = _require_item(number)
    if as_json:
        click.echo(json.dumps(item, indent=2, sort_keys=True))
        return
    click.echo(_render_item(item))


@work.command(name="comment")
@click.argument("number", type=int)
@click.option("--text", default=None, help="Comment body.")
@click.option(
    "--body-file",
    type=click.Path(path_type=Path),
    default=None,
    help="Read the comment body from a file.",
)
@click.option("--author", default="local", show_default=True, help="Comment author.")
def work_comment(
    number: int,
    text: Optional[str],
    body_file: Optional[Path],
    author: str,
) -> None:
    """Add a comment to a work item.

    A running workflow drains new comments as mid-run steering, so this is how
    you redirect an agent without restarting it.
    """
    _require_item(number)
    body = _read_body(text, body_file)
    if not body.strip():
        raise click.ClickException("Comment body must not be empty.")
    comment = local_work_items.add_comment(
        _project_root(), number, body, author=author
    )
    if comment is None:
        raise click.ClickException(f"Could not add a comment to work item {number}.")
    click.echo(
        f"Added comment {comment['id']} to "
        f"{local_work_items.format_local_ref(number)}."
    )


@work.command(name="close")
@click.argument("number", type=int)
def work_close(number: int) -> None:
    """Mark a work item closed."""
    _require_item(number)
    local_work_items.update_work_item(_project_root(), number, state="closed")
    click.echo(f"Closed {local_work_items.format_local_ref(number)}.")


@work.command(name="reopen")
@click.argument("number", type=int)
def work_reopen(number: int) -> None:
    """Mark a work item open again."""
    _require_item(number)
    local_work_items.update_work_item(_project_root(), number, state="open")
    click.echo(f"Reopened {local_work_items.format_local_ref(number)}.")


work_cli = work
