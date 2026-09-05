# Intent: Local workflows never authenticate with GitHub unexpectedly

<!-- pdd-intent-id: local-and-inline-pdd-workflows-must-never-start--20529718 -->
<!-- pdd-intent-sha256: 205297183f722ae7a64ee6d2fa52749594120bcb1f320d9114dc33ff5376f5fc -->

## Record

- Kind: `add`
- Approved by Ernest: 2026-09-05
- Adoption scenario: `existing_pdd_change`
- Implementation branch: `feat/local-only-no-github-auth`

## Original request

> make a note, in general for PDD ... that we want to have a git branch ... to get rid of all the unexpected github authentications, and if in order to get rid of the github authentications we have to "rewrite" just how pdd does the issue tracking to be moved locally, then so be it ... implement it ... until completion

## Accepted interpretation

Local and inline PDD workflows must use local work items, state, and steering
and must neither invoke nor instruct an agent to invoke GitHub. Ordinary
commands must never begin a GitHub OAuth or Copilot device flow implicitly.
Remote GitHub workflows remain available only when explicitly selected, and
interactive GitHub authentication remains available only through an explicit
authentication action or opt-in. `PDD_LOCAL_ONLY=1` is a hard deny across
GitHub CLI/API access, cloud GitHub SSO, GitHub Copilot, and subprocess-agent
instructions.

## Observable example

`pdd intent apply --text ...` and `pdd story add --text ...` either complete
using configured non-GitHub providers or fail with a non-interactive provider
configuration error. They never print a `github.com/login/device` instruction
and never run `gh`.

## Implementation

Implemented on `feat/local-only-no-github-auth` with a shared process guard,
local agent-instruction override, explicit device-flow permission, guarded
GitHub CLI/API helpers, and story regression coverage.
