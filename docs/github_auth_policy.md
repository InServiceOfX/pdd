# GitHub access and authentication policy

PDD supports two explicit operating boundaries:

- Local sources (`--text`, local files, and `local:<n>` work items) keep their
  request, resumable state, steering, and reports on disk. They do not use
  GitHub as an issue tracker or output channel.
- Remote GitHub sources remain supported when the user deliberately supplies a
  GitHub issue or pull-request URL.

Ordinary commands never initiate interactive GitHub authentication. An
interactive GitHub device flow requires an explicit authentication command or
`PDD_ALLOW_GITHUB_AUTH=1`. Generic interactive-provider permission does not
silently grant GitHub authentication permission. Background conveniences such
as successful-run example auto-submit may reuse or silently refresh an existing
credential, but they cannot fall through into device authentication.

`PDD_LOCAL_ONLY=1` is stronger: it disables GitHub CLI/API access, PDD Cloud's
GitHub SSO device flow, GitHub Copilot model routing, and GitHub instructions
inside subprocess-agent tasks. Local operations either use already configured
non-GitHub providers or fail with an actionable, non-interactive error.

This policy is the security boundary behind
[`docs/local_work_items.md`](local_work_items.md) and the approved
[local-workflow story](../user_stories/story__pdd_local_workflows_never_authenticate_with_github.md).
