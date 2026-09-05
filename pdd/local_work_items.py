# pdd/local_work_items.py
"""Local, filesystem-backed work items: a GitHub-issue replacement.

PDD's agentic workflows (``bug``, ``fix``, ``change``, ``test``, ``sync``,
``architecture``) were originally keyed on a GitHub issue URL. The issue served
three distinct roles at once:

1. the **request** — title, body, and discussion the workflow implements;
2. the **state store** — long multi-step runs persist resumable state in a
   marker comment so a crashed run can resume;
3. the **steering channel** — a human comments mid-run and the orchestrator
   drains those comments as new instructions.

All three roles are satisfiable locally. This module provides a work item that
carries the same data and is stored under ``.pdd/work_items/`` in the project,
so a project can run the full agentic workflow set without ``gh``, without a
network call, and without publishing anything.

Design notes
------------
* **Shape compatibility.** :func:`github_shaped_issue` and
  :func:`github_shaped_comment` emit the exact field layout the existing
  callers already read (``title``, ``body``, ``user.login``, ``comments_url``,
  ``labels[].name``, ``state``, ``updated_at``, comment ``id``). Consumers need
  no rewrite — only a branch selecting the local source.
* **Routing discriminator.** Every deep call site in ``agentic_common`` already
  receives ``repo_owner``/``repo_name``/``issue_number``. A local work item is
  addressed with ``repo_owner == "local"`` (:data:`LOCAL_OWNER`), so routing
  needs no new parameters and no process-global state.
* **Monotonic comment ids.** Steering drains comments with ``id > cursor``, so
  local comment ids are allocated from a persisted counter and never reused.

See ``docs/local_work_items.md`` for the user-facing guide.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import json
import re

from .github_guard import local_only_enabled as _guard_local_only_enabled

__all__ = [
    "LOCAL_OWNER",
    "LocalWorkItemError",
    "add_comment",
    "create_work_item",
    "delete_comment",
    "delete_comment_by_id",
    "edit_comment",
    "edit_comment_by_id",
    "find_comment_item",
    "find_state_comments",
    "format_local_ref",
    "github_shaped_comment",
    "github_shaped_issue",
    "is_local_comments_url",
    "is_local_owner",
    "is_local_ref",
    "list_comments",
    "list_work_items",
    "load_work_item",
    "local_comments_url",
    "local_only_enabled",
    "local_repo_name",
    "parse_local_comments_url",
    "parse_local_ref",
    "resolve_local_ref",
    "serve_gh_api",
    "store_dir",
    "update_work_item",
]

# Sentinel owner marking a work item as local. Deep call sites branch on this
# instead of taking a new parameter.
LOCAL_OWNER = "local"

# ``local:12``, ``local#12``, ``local/12``, ``work:12``, ``work-item:12``.
_LOCAL_REF_RE = re.compile(
    r"^\s*(?:local|work|work-item|workitem)\s*[:#/]\s*(\d+)\s*$",
    re.IGNORECASE,
)

_COMMENTS_URL_PREFIX = "pdd-local://work-items/"
_COMMENTS_URL_RE = re.compile(r"^pdd-local://work-items/(\d+)/comments$")

_STORE_DIRNAME = "work_items"
_INDEX_FILENAME = "index.json"

_DEFAULT_AUTHOR = "local"


class LocalWorkItemError(RuntimeError):
    """Raised when a local work item cannot be read or written."""


# ---------------------------------------------------------------------------
# Reference parsing
# ---------------------------------------------------------------------------

def parse_local_ref(value: Any) -> Optional[int]:
    """Return the work item number for a local reference, else ``None``.

    Accepts ``local:12``, ``local#12``, ``local/12``, ``work:12``,
    ``work-item:12`` (case-insensitive, surrounding whitespace tolerated).
    """
    if not isinstance(value, str):
        return None
    match = _LOCAL_REF_RE.match(value)
    if not match:
        return None
    try:
        number = int(match.group(1))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def is_local_ref(value: Any) -> bool:
    """Return ``True`` when *value* is a local work item reference."""
    return parse_local_ref(value) is not None


def format_local_ref(number: int) -> str:
    """Return the canonical reference string for work item *number*."""
    return f"local:{int(number)}"


def is_local_owner(repo_owner: Any) -> bool:
    """Return ``True`` when *repo_owner* addresses the local work item store."""
    return isinstance(repo_owner, str) and repo_owner.strip().lower() == LOCAL_OWNER


def local_repo_name(project_root: Path) -> str:
    """Return the sentinel repo name used for local work items.

    The project directory name keeps log lines and state filenames readable;
    it is never used to reach the network.
    """
    name = Path(project_root).resolve().name or "project"
    return re.sub(r"[^A-Za-z0-9._-]", "-", name)


def local_comments_url(number: int) -> str:
    """Return the sentinel comments URL recorded on a local issue payload."""
    return f"{_COMMENTS_URL_PREFIX}{int(number)}/comments"


def parse_local_comments_url(url: Any) -> Optional[int]:
    """Return the work item number encoded in a local comments URL, else ``None``."""
    if not isinstance(url, str):
        return None
    match = _COMMENTS_URL_RE.match(url.strip())
    return int(match.group(1)) if match else None


def is_local_comments_url(url: Any) -> bool:
    """Return ``True`` when *url* is a local sentinel comments URL."""
    return parse_local_comments_url(url) is not None


def local_only_enabled() -> bool:
    """Return ``True`` when this process must never invoke ``gh``.

    Enabled by ``PDD_LOCAL_ONLY=1`` in the environment. When set, the ``gh``
    lookup in ``agentic_common._find_cli_binary`` reports the binary as
    missing, so every GitHub helper takes its existing "no gh" branch instead
    of shelling out.
    """
    return _guard_local_only_enabled()


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def store_dir(project_root: Path) -> Path:
    """Return the local work item directory for *project_root*."""
    return Path(project_root) / ".pdd" / _STORE_DIRNAME


def _item_path(project_root: Path, number: int) -> Path:
    return store_dir(project_root) / f"item_{int(number)}.json"


def _index_path(project_root: Path) -> Path:
    return store_dir(project_root) / _INDEX_FILENAME


def _now() -> str:
    """Return the current UTC time in GitHub's ISO-8601 ``Z`` form."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    """Write *payload* to *path* atomically (tmp file then rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        tmp_path.replace(path)
    except OSError as exc:
        raise LocalWorkItemError(f"Failed to write {path}: {exc}") from exc


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise LocalWorkItemError(f"Failed to read {path}: {exc}") from exc
    return data if isinstance(data, dict) else None


def _load_index(project_root: Path) -> Dict[str, Any]:
    index = _read_json(_index_path(project_root)) or {}
    index.setdefault("next_number", 1)
    index.setdefault("next_comment_id", 1)
    return index


def _save_index(project_root: Path, index: Dict[str, Any]) -> None:
    _write_json_atomic(_index_path(project_root), index)


def _allocate_number(project_root: Path) -> int:
    index = _load_index(project_root)
    number = int(index.get("next_number", 1))
    index["next_number"] = number + 1
    _save_index(project_root, index)
    return number


def _allocate_comment_id(project_root: Path, item_number: int) -> int:
    """Allocate a monotonically increasing comment id owned by *item_number*.

    Steering compares ``comment id > cursor``, so ids must never be reused
    even after a comment is deleted. The owning item number is recorded in the
    index because the state helpers address comments by id alone (GitHub's
    ``issues/comments/{id}`` endpoints take no issue number).
    """
    index = _load_index(project_root)
    comment_id = int(index.get("next_comment_id", 1))
    index["next_comment_id"] = comment_id + 1
    owners = index.setdefault("comment_owners", {})
    owners[str(comment_id)] = int(item_number)
    _save_index(project_root, index)
    return comment_id


def find_comment_item(project_root: Path, comment_id: int) -> Optional[int]:
    """Return the work item number owning *comment_id*, else ``None``.

    Falls back to scanning stored items when the index has no entry, so a
    hand-edited or partially migrated store still resolves.
    """
    index = _load_index(project_root)
    owner = (index.get("comment_owners") or {}).get(str(int(comment_id)))
    if owner is not None:
        return int(owner)
    for item in list_work_items(project_root):
        for comment in item.get("comments", []) or []:
            if int(comment.get("id", 0)) == int(comment_id):
                return int(item.get("number", 0))
    return None


# ---------------------------------------------------------------------------
# Work item CRUD
# ---------------------------------------------------------------------------

def create_work_item(
    project_root: Path,
    title: str,
    body: str = "",
    *,
    labels: Optional[List[str]] = None,
    author: str = _DEFAULT_AUTHOR,
) -> Dict[str, Any]:
    """Create a work item and return it.

    Args:
        project_root: Project root containing (or receiving) ``.pdd/``.
        title: One-line summary of the request.
        body: Full request text. May be empty.
        labels: Optional label names, mirroring GitHub issue labels.
        author: Recorded as the item's author.

    Returns:
        The stored work item dict.
    """
    clean_title = (title or "").strip()
    if not clean_title:
        raise LocalWorkItemError("A work item requires a non-empty title.")

    number = _allocate_number(project_root)
    timestamp = _now()
    item: Dict[str, Any] = {
        "number": number,
        "title": clean_title,
        "body": body or "",
        "state": "open",
        "labels": list(labels or []),
        "author": author or _DEFAULT_AUTHOR,
        "created_at": timestamp,
        "updated_at": timestamp,
        "comments": [],
    }
    _write_json_atomic(_item_path(project_root, number), item)
    return item


def load_work_item(project_root: Path, number: int) -> Optional[Dict[str, Any]]:
    """Return work item *number*, or ``None`` when it does not exist."""
    return _read_json(_item_path(project_root, number))


def list_work_items(project_root: Path) -> List[Dict[str, Any]]:
    """Return every work item, ordered by number."""
    directory = store_dir(project_root)
    if not directory.is_dir():
        return []
    items: List[Dict[str, Any]] = []
    for path in directory.glob("item_*.json"):
        item = _read_json(path)
        if item is not None:
            items.append(item)
    return sorted(items, key=lambda entry: int(entry.get("number", 0)))


def update_work_item(
    project_root: Path,
    number: int,
    *,
    title: Optional[str] = None,
    body: Optional[str] = None,
    state: Optional[str] = None,
    labels: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Update mutable fields on a work item and return it, or ``None``.

    Only fields passed as non-``None`` are changed. ``updated_at`` is refreshed
    whenever anything changes, because steering uses it as the ``since``
    baseline.
    """
    item = load_work_item(project_root, number)
    if item is None:
        return None

    changed = False
    if title is not None and title.strip() and title.strip() != item.get("title"):
        item["title"] = title.strip()
        changed = True
    if body is not None and body != item.get("body"):
        item["body"] = body
        changed = True
    if state is not None and state != item.get("state"):
        if state not in {"open", "closed"}:
            raise LocalWorkItemError(f"Invalid state '{state}'; expected 'open' or 'closed'.")
        item["state"] = state
        changed = True
    if labels is not None and list(labels) != item.get("labels"):
        item["labels"] = list(labels)
        changed = True

    if changed:
        item["updated_at"] = _now()
        _write_json_atomic(_item_path(project_root, number), item)
    return item


def add_comment(
    project_root: Path,
    number: int,
    body: str,
    *,
    author: str = _DEFAULT_AUTHOR,
) -> Optional[Dict[str, Any]]:
    """Append a comment to work item *number* and return the stored comment.

    Returns ``None`` when the work item does not exist. Comments are the local
    equivalent of GitHub issue comments and carry both the workflow state
    markers and human mid-run steering.
    """
    item = load_work_item(project_root, number)
    if item is None:
        return None

    timestamp = _now()
    comment = {
        "id": _allocate_comment_id(project_root, number),
        "user": {"login": author or _DEFAULT_AUTHOR},
        "body": body or "",
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    comments = item.setdefault("comments", [])
    comments.append(comment)
    item["updated_at"] = timestamp
    _write_json_atomic(_item_path(project_root, number), item)
    return comment


def edit_comment(
    project_root: Path,
    number: int,
    comment_id: int,
    body: str,
) -> bool:
    """Replace the body of a stored comment. Returns ``True`` on success."""
    item = load_work_item(project_root, number)
    if item is None:
        return False
    for comment in item.get("comments", []):
        if int(comment.get("id", 0)) == int(comment_id):
            comment["body"] = body or ""
            comment["updated_at"] = _now()
            item["updated_at"] = comment["updated_at"]
            _write_json_atomic(_item_path(project_root, number), item)
            return True
    return False


def delete_comment(project_root: Path, number: int, comment_id: int) -> bool:
    """Delete a stored comment. Returns ``True`` when it was removed."""
    item = load_work_item(project_root, number)
    if item is None:
        return False
    comments = item.get("comments", [])
    remaining = [c for c in comments if int(c.get("id", 0)) != int(comment_id)]
    if len(remaining) == len(comments):
        return False
    item["comments"] = remaining
    item["updated_at"] = _now()
    _write_json_atomic(_item_path(project_root, number), item)
    return True


def edit_comment_by_id(project_root: Path, comment_id: int, body: str) -> bool:
    """Replace a comment body addressed by id alone. ``True`` on success."""
    number = find_comment_item(project_root, comment_id)
    if number is None:
        return False
    return edit_comment(project_root, number, comment_id, body)


def delete_comment_by_id(project_root: Path, comment_id: int) -> bool:
    """Delete a comment addressed by id alone. ``True`` when removed."""
    number = find_comment_item(project_root, comment_id)
    if number is None:
        return False
    return delete_comment(project_root, number, comment_id)


def find_state_comments(
    project_root: Path,
    number: int,
    marker: str,
) -> List[Dict[str, Any]]:
    """Return stored comments whose body carries *marker*, oldest id first.

    Used by the workflow-state helpers to locate the marker comment that holds
    resumable state for a given workflow.
    """
    item = load_work_item(project_root, number)
    if item is None:
        return []
    matches = [
        comment
        for comment in item.get("comments", []) or []
        if isinstance(comment, dict) and marker in str(comment.get("body", "") or "")
    ]
    return sorted(matches, key=lambda comment: int(comment.get("id", 0)))


def list_comments(
    project_root: Path,
    number: int,
    *,
    since: Optional[str] = None,
) -> Optional[List[Dict[str, Any]]]:
    """Return comments for *number*, optionally filtered by ``since``.

    ``since`` mirrors the GitHub API parameter: only comments whose activity
    timestamp is strictly after it are returned. Returns ``None`` when the work
    item does not exist, so callers can distinguish "no such item" from
    "no comments".
    """
    item = load_work_item(project_root, number)
    if item is None:
        return None

    comments = [c for c in item.get("comments", []) if isinstance(c, dict)]
    if not since:
        return comments

    cutoff = _parse_timestamp(since)
    if cutoff is None:
        return comments

    filtered: List[Dict[str, Any]] = []
    for comment in comments:
        activity = comment.get("updated_at") or comment.get("created_at")
        parsed = _parse_timestamp(activity)
        if parsed is None or parsed > cutoff:
            filtered.append(comment)
    return filtered


def _parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# GitHub-shaped projections
# ---------------------------------------------------------------------------

def github_shaped_comment(comment: Dict[str, Any]) -> Dict[str, Any]:
    """Project a stored comment into the GitHub issue-comment shape."""
    return {
        "id": int(comment.get("id", 0)),
        "user": {"login": str((comment.get("user") or {}).get("login", _DEFAULT_AUTHOR))},
        "body": str(comment.get("body", "") or ""),
        "created_at": comment.get("created_at"),
        "updated_at": comment.get("updated_at") or comment.get("created_at"),
    }


def github_shaped_issue(item: Dict[str, Any]) -> Dict[str, Any]:
    """Project a stored work item into the GitHub issue payload shape.

    Emits exactly the fields existing callers read, so a local work item can be
    substituted for a ``gh api repos/{owner}/{repo}/issues/{n}`` response.
    """
    number = int(item.get("number", 0))
    return {
        "number": number,
        "title": str(item.get("title", "") or ""),
        "body": str(item.get("body", "") or ""),
        "state": str(item.get("state", "open") or "open"),
        "labels": [{"name": name} for name in item.get("labels", []) or []],
        "user": {"login": str(item.get("author", _DEFAULT_AUTHOR) or _DEFAULT_AUTHOR)},
        "comments": len(item.get("comments", []) or []),
        "comments_url": local_comments_url(number),
        "html_url": format_local_ref(number),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
    }


_GH_ISSUE_PATH_RE = re.compile(
    r"^/?repos/local/[^/]+/issues/(\d+)$", re.IGNORECASE
)
_GH_ISSUE_COMMENTS_PATH_RE = re.compile(
    r"^/?repos/local/[^/]+/issues/(\d+)/comments$", re.IGNORECASE
)


def serve_gh_api(project_root: Path, path: str) -> Optional[str]:
    """Answer a ``gh api``-shaped request from the local store.

    Modules that shell out through a generic ``["gh", "api", <path>]`` helper
    can call this first: it returns the JSON text ``gh`` would have printed for
    a local work item, or ``None`` when *path* addresses something that is not
    local (so the caller falls through to the real ``gh``).

    Recognised paths:
      * ``repos/local/<repo>/issues/<n>`` -> issue payload
      * ``repos/local/<repo>/issues/<n>/comments`` -> comment array
      * ``pdd-local://work-items/<n>/comments`` -> comment array
    """
    if not isinstance(path, str):
        return None
    candidate = path.strip()

    number = parse_local_comments_url(candidate)
    if number is None:
        match = _GH_ISSUE_COMMENTS_PATH_RE.match(candidate)
        if match:
            number = int(match.group(1))
    if number is not None:
        comments = list_comments(project_root, number)
        if comments is None:
            return None
        return json.dumps([github_shaped_comment(c) for c in comments])

    match = _GH_ISSUE_PATH_RE.match(candidate)
    if match:
        item = load_work_item(project_root, int(match.group(1)))
        if item is None:
            return None
        return json.dumps(github_shaped_issue(item))

    return None


def resolve_local_ref(
    project_root: Path,
    value: str,
) -> Optional[Dict[str, Any]]:
    """Resolve a local reference string to its stored work item.

    Returns ``None`` when *value* is not a local reference or the referenced
    item does not exist.
    """
    number = parse_local_ref(value)
    if number is None:
        return None
    return load_work_item(project_root, number)
