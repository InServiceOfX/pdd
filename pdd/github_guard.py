"""Process-wide policy for explicit GitHub access and authentication."""

from __future__ import annotations

import os
import re
import shutil
from typing import Optional


_TRUTHY = frozenset({"1", "true", "yes", "on"})
_LOCAL_POLICY_MARKER = "PDD_LOCAL_GITHUB_POLICY_V1"
_LOCAL_SOURCE_PATTERNS = (
    re.compile(r"(?i)(?:^|\s)(?:local|local-intent|work|work-item|workitem)[#:/][^\s]+"),
    re.compile(r"(?i)pdd-local://"),
    re.compile(r"(?im)^\s*(?:repository|repo_owner)\s*:\s*(?:`?local(?:/|`?$))"),
)


class GitHubAccessDisabled(RuntimeError):
    """Raised when an operation crosses the local-only GitHub boundary."""


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().casefold() in _TRUTHY


def local_only_enabled() -> bool:
    """Return whether this process has a hard prohibition on GitHub access."""
    return _env_truthy("PDD_LOCAL_ONLY")


def github_auth_opted_in() -> bool:
    """Return whether ordinary commands may initiate GitHub authentication."""
    return not local_only_enabled() and _env_truthy("PDD_ALLOW_GITHUB_AUTH")


def github_access_allowed() -> bool:
    """Return whether this process may access GitHub at all."""
    return not local_only_enabled()


def require_github_access(purpose: str = "GitHub operation") -> None:
    """Fail before GitHub access when local-only mode is active."""
    if not github_access_allowed():
        raise GitHubAccessDisabled(
            f"{purpose} is disabled by PDD_LOCAL_ONLY=1. "
            "Use a local:<n> work item or unset PDD_LOCAL_ONLY for an "
            "explicit remote GitHub workflow."
        )


def find_gh() -> Optional[str]:
    """Locate GitHub CLI only when the process policy permits GitHub access."""
    if not github_access_allowed():
        return None
    return shutil.which("gh")


def instruction_is_local(instruction: str) -> bool:
    """Return whether an agent instruction describes a local PDD source."""
    if not isinstance(instruction, str):
        return False
    return any(pattern.search(instruction) for pattern in _LOCAL_SOURCE_PATTERNS)


def localize_agent_instruction(instruction: str) -> str:
    """Append the authoritative no-GitHub policy to local agent instructions."""
    if not (local_only_enabled() or instruction_is_local(instruction)):
        return instruction
    if _LOCAL_POLICY_MARKER in instruction:
        return instruction
    return (
        f"{instruction.rstrip()}\n\n"
        f"## {_LOCAL_POLICY_MARKER} — highest-priority final override\n\n"
        "This is a local PDD workflow. Do not run `gh` or access GitHub APIs, "
        "issues, comments, pull requests, OAuth, or device-login endpoints. "
        "Any earlier GitHub posting instruction in this shared template is "
        "inapplicable and is superseded by this section. Return the requested "
        "report in your final response; the PDD orchestrator persists it "
        "locally. Do not publish anything.\n"
    )


__all__ = [
    "GitHubAccessDisabled",
    "find_gh",
    "github_access_allowed",
    "github_auth_opted_in",
    "instruction_is_local",
    "local_only_enabled",
    "localize_agent_instruction",
    "require_github_access",
]
