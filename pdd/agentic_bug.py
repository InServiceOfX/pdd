from __future__ import annotations

"""
Agentic bug investigation entry point.

This module serves as the CLI entry point for the agentic bug investigation workflow.
It parses a GitHub issue URL, fetches the issue content and comments using the `gh` CLI,
sets up the environment, and invokes the orchestrator to run the investigation process.
It also supports a legacy manual mode via argument inspection.
"""

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from rich.console import Console

# Internal imports
from . import local_work_items
from .agentic_bug_orchestrator import run_agentic_bug_orchestrator
from .bug_main import bug_main

# Optional globals from package root
try:  # pragma: no cover
    from . import DEFAULT_STRENGTH
except Exception:  # pragma: no cover
    DEFAULT_STRENGTH = None

console = Console()

__all__ = ["run_agentic_bug"]


def _check_gh_cli(issue_ref: str = "") -> bool:
    """Check that the tooling needed to resolve *issue_ref* is available.

    A local work item reference (``local:12``) needs no GitHub CLI at all, so
    the check passes unconditionally for those.
    """
    if local_work_items.is_local_ref(issue_ref):
        return True
    from pdd.github_guard import local_only_enabled
    return not local_only_enabled() and shutil.which("gh") is not None


def _parse_github_url(url: str) -> Optional[Tuple[str, str, int]]:
    """
    Parse a GitHub issue URL to extract owner, repo, and issue number.

    Supported formats:
    - https://github.com/{owner}/{repo}/issues/{number}
    - https://www.github.com/{owner}/{repo}/issues/{number}
    - github.com/{owner}/{repo}/issues/{number}

    Args:
        url: The URL string to parse.

    Returns:
        Tuple of (owner, repo, issue_number) if successful, else None.
    """
    # Local work item reference (``local:12``) — no network identity needed.
    local_number = local_work_items.parse_local_ref(url)
    if local_number is not None:
        return (
            local_work_items.LOCAL_OWNER,
            local_work_items.local_repo_name(Path.cwd()),
            local_number,
        )

    # Remove protocol and www if present
    clean_url = url.replace("https://", "").replace("http://", "").replace("www.", "")
    
    # Regex for github.com/owner/repo/issues/number
    # Allows for optional trailing slash or query params (though query params usually follow ?)
    pattern = r"^github\.com/([^/]+)/([^/]+)/issues/(\d+)"
    match = re.match(pattern, clean_url)
    
    if match:
        owner, repo, number_str = match.groups()
        try:
            return owner, repo, int(number_str)
        except ValueError:
            return None
    return None


def _fetch_issue_data(owner: str, repo: str, number: int) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Fetch issue metadata and content using `gh api`.

    Args:
        owner: Repository owner.
        repo: Repository name.
        number: Issue number.

    Returns:
        (data_dict, error_message)
        - data_dict: JSON response from GitHub API if successful.
        - error_message: Error string if failed.
    """
    if local_work_items.is_local_owner(owner):
        try:
            item = local_work_items.load_work_item(Path.cwd(), number)
        except Exception as exc:
            return None, f"Failed to read local work item: {exc}"
        if item is None:
            return None, f"Local work item {number} not found"
        return local_work_items.github_shaped_issue(item), None

    from pdd.github_guard import find_gh
    if not find_gh():
        return None, "GitHub CLI unavailable or disabled by PDD_LOCAL_ONLY=1"

    cmd = [
        "gh", "api",
        f"repos/{owner}/{repo}/issues/{number}",
        "--header", "Accept: application/vnd.github+json"
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return json.loads(result.stdout), None
    except subprocess.CalledProcessError as e:
        err_msg = e.stderr.strip() or str(e)
        return None, f"Failed to fetch issue: {err_msg}"
    except json.JSONDecodeError:
        return None, "Failed to parse GitHub API response"
    except Exception as e:
        return None, str(e)


def _fetch_comments(comments_url: str) -> str:
    """
    Fetch comments for an issue to provide full context.

    Args:
        comments_url: API URL for comments (provided in issue metadata).

    Returns:
        Concatenated string of comments formatted as "User: Comment".
    """
    local_number = local_work_items.parse_local_comments_url(comments_url)
    if local_number is not None:
        try:
            comments = local_work_items.list_comments(Path.cwd(), local_number) or []
        except Exception:
            return ""
        return "\n".join(
            f"--- Comment by {(c.get('user') or {}).get('login', 'Unknown')} ---\n"
            f"{c.get('body', '')}\n"
            for c in comments
        )

    # The comments_url from API is full URL like https://api.github.com/repos/...
    # gh api expects path relative to api root or full URL.
    from pdd.github_guard import find_gh
    gh = find_gh()
    if not gh:
        return ""
    cmd = ["gh", "api", comments_url, "--paginate"]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        comments_data = json.loads(result.stdout)
        
        formatted_comments = []
        for comment in comments_data:
            user = comment.get("user", {}).get("login", "Unknown")
            body = comment.get("body", "")
            formatted_comments.append(f"--- Comment by {user} ---\n{body}\n")
            
        return "\n".join(formatted_comments)
    except Exception:
        # If comments fail, we proceed with just the issue body
        return ""


def _ensure_repo_context(owner: str, repo: str, cwd: Path, quiet: bool = False) -> bool:
    """
    Ensure the current working directory contains the repository.
    If not, clone it into the current directory.
    
    Args:
        owner: Repo owner.
        repo: Repo name.
        cwd: Current working directory.
        quiet: Suppress output.
        
    Returns:
        True if successful (exists or cloned), False otherwise.
    """
    # Check if .git exists
    if (cwd / ".git").exists():
        return True

    # A local work item names no remote to clone from; the project on disk is
    # the only repository involved.
    if local_work_items.is_local_owner(owner):
        return True

    from pdd.github_guard import github_access_allowed
    if not github_access_allowed():
        return False

    # Attempt clone
    repo_url = f"https://github.com/{owner}/{repo}.git"
    if not quiet:
        console.print(f"[blue]Cloning {repo_url} into {cwd}...[/blue]")
        
    try:
        # Clone into current directory (.)
        subprocess.run(["git", "clone", repo_url, "."], cwd=cwd, check=True, capture_output=True, text=True)
        return True
    except subprocess.CalledProcessError as e:
        if not quiet:
            err = e.stderr.strip() if e.stderr else str(e)
            console.print(f"[red]Failed to clone repository: {err}[/red]")
        return False
    except Exception as e:
        if not quiet:
            console.print(f"[red]Error during clone: {e}[/red]")
        return False


def run_agentic_bug(
    issue_url: str,
    *,
    verbose: bool = False,
    quiet: bool = False,
    timeout_adder: float = 0.0,
    use_github_state: bool = True,
    reasoning_time: Optional[float] = None,
    clean_restart: bool = False,
    # Legacy/Manual mode arguments (handled via *args in a real CLI, but here explicit for type safety if called directly)
    manual_args: Optional[Tuple[str, str, str, str, str]] = None
) -> Tuple[bool, str, float, str, List[str]]:
    """
    Entry point for the agentic bug investigation.

    Parses the GitHub issue, fetches context, and invokes the orchestrator.
    
    If `manual_args` is provided (simulating the --manual flag logic from a CLI wrapper),
    it delegates to the legacy `bug_main` logic.

    Args:
        issue_url: The GitHub issue URL to investigate.
        verbose: Enable verbose logging.
        quiet: Suppress informational logging.
        timeout_adder: Additional time to add to step timeouts.
        use_github_state: Whether to use GitHub state (comments, PRs) during orchestration.
        clean_restart: Discard saved workflow state before starting.
        manual_args: Optional tuple of (prompt_file, code_file, program_file, current_out, desired_out)
                     to trigger legacy manual mode.

    Returns:
        (success, message, total_cost, model_used, changed_files)
    """
    # 1. Handle Legacy Manual Mode
    if manual_args:
        if not quiet:
            console.print("[blue]Running in manual mode (legacy)...[/blue]")
        
        prompt_file, code_file, program_file, current_out, desired_out = manual_args
        
        # Mock context for bug_main
        class MockContext:
            obj = {
                'force': True,
                'quiet': quiet,
                'strength': DEFAULT_STRENGTH,
                'temperature': 0
            }
        
        try:
            # bug_main returns (unit_test_content, cost, model)
            # It writes the test file to disk as a side effect if 'output' arg is used,
            # but here we just capture the return.
            # We need to adapt the return signature to match run_agentic_bug.
            unit_test, cost, model = bug_main(
                ctx=MockContext(),  # type: ignore
                prompt_file=prompt_file,
                code_file=code_file,
                program_file=program_file,
                current_output=current_out,
                desired_output=desired_out,
                language='Python'
            )
            return True, "Manual test generation successful", cost, model, []
        except Exception as e:
            return False, f"Manual mode failed: {e}", 0.0, "", []

    # 2. Validate Environment
    if not _check_gh_cli(issue_url):
        msg = "gh CLI not found. Please install GitHub CLI."
        if not quiet:
            console.print(f"[red]{msg}[/red]")
        return False, msg, 0.0, "", []

    # 3. Parse URL
    parsed = _parse_github_url(issue_url)
    if not parsed:
        msg = f"Invalid GitHub URL: {issue_url}"
        if not quiet:
            console.print(f"[red]{msg}[/red]")
        return False, msg, 0.0, "", []

    owner, repo, issue_number = parsed
    if not quiet:
        console.print(f"[blue]Investigating {owner}/{repo} issue #{issue_number}[/blue]")

    # 4. Fetch Issue Data
    issue_data, error = _fetch_issue_data(owner, repo, issue_number)
    if error or not issue_data:
        msg = f"Issue not found or API error: {error}"
        if not quiet:
            console.print(f"[red]{msg}[/red]")
        return False, msg, 0.0, "", []

    # 5. Extract Metadata
    try:
        issue_title = issue_data.get("title", "")
        issue_body = issue_data.get("body", "") or ""
        issue_author = issue_data.get("user", {}).get("login", "unknown")
        comments_url = issue_data.get("comments_url", "")
        
        # Fetch comments for context
        comments_text = _fetch_comments(comments_url) if comments_url else ""
        
        full_content = (
            f"Title: {issue_title}\n"
            f"Author: {issue_author}\n"
            f"Description:\n{issue_body}\n\n"
            f"Comments:\n{comments_text}"
        )
    except Exception as e:
        msg = f"Failed to process issue data: {e}"
        if not quiet:
            console.print(f"[red]{msg}[/red]")
        return False, msg, 0.0, "", []

    # 6. Prepare Workspace (Repo Context)
    cwd = Path.cwd()
    if not _ensure_repo_context(owner, repo, cwd, quiet=quiet):
        msg = "Failed to clone repository"
        if not quiet:
            console.print(f"[red]{msg}[/red]")
        return False, msg, 0.0, "", []

    # 7. Invoke Orchestrator
    if not quiet:
        console.print(f"[green]Starting agentic workflow for: '{issue_title}'[/green]")

    try:
        success, message, cost, model, changed_files = run_agentic_bug_orchestrator(
            issue_url=issue_url,
            issue_content=full_content,
            repo_owner=owner,
            repo_name=repo,
            issue_number=issue_number,
            issue_author=issue_author,
            issue_title=issue_title,
            issue_updated_at=issue_data.get("updated_at", "") or "",
            cwd=cwd,
            verbose=verbose,
            quiet=quiet,
            timeout_adder=timeout_adder,
            use_github_state=use_github_state,
            reasoning_time=reasoning_time,
            clean_restart=clean_restart,
        )
        return success, message, cost, model, changed_files

    except Exception as e:
        msg = f"Orchestrator failed: {e}"
        if not quiet:
            console.print(f"[red]{msg}[/red]")
            if verbose:
                import traceback
                console.print(traceback.format_exc())
        return False, msg, 0.0, "", []
