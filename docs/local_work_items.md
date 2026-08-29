# Local work items: running PDD without GitHub

PDD's agentic workflows were keyed on a GitHub issue URL. Local work items
remove that requirement. A project can run `bug`, `fix`, `change`, `test`,
`sync`, and `architecture` with **no `gh`, no network call, and nothing
published** — the request, the workflow state, and the human's mid-run steering
all live in `.pdd/work_items/`.

This is the engineering-workflow counterpart to [`pdd intent`](intent.md),
which already required no GitHub issue for the product-intent front door.

## Why the issue was there in the first place

A GitHub issue was doing three jobs at once. Naming them separately is what
makes a local replacement possible:

| Role | What it did | Local replacement |
|---|---|---|
| **Request** | Title, body, and discussion the workflow implements | `item_<n>.json` title/body |
| **State store** | A marker comment holding resumable state, so a 13–18 step run survives a crash | A marker comment in the same file |
| **Steering channel** | A human comments mid-run; the orchestrator drains it as new instructions | `pdd work comment` |

The issue was never a ticketing requirement — it was durable state plus an
audit trail. Both are satisfiable on disk.

## Quick start

```bash
# 1. Open a work item (body may also be piped on stdin, or read with --body-file)
pdd work new --title "Ring buffer drops the oldest frame" \
             --body "When full, push() overwrites index 0 instead of advancing the tail."
# -> Created local:1

# 2. Run any agentic workflow against it
pdd bug local:1
pdd fix local:1

# 3. Steer the run mid-flight — the orchestrator drains new comments
pdd work comment 1 --text "Use a ring buffer, not a deque. Keep the CRC check."

# 4. Inspect what happened
pdd work show 1
pdd work list --state open
```

Every agentic command that accepts an issue URL accepts a local reference in
the same position:

```bash
pdd bug local:1        pdd change local:1      pdd sync local:1
pdd fix local:1        pdd test local:1        pdd checkup local:1
```

## Reference syntax

`local:12` is canonical. `local#12`, `local/12`, `work:12`, and
`work-item:12` are accepted too, case-insensitively. Anything that is not one
of those — a GitHub URL, a prompt filename, a bare path — routes exactly as it
did before, so nothing about existing usage changes.

## Guaranteeing no `gh`

Set `PDD_LOCAL_ONLY=1` and the `gh` binary is reported as absent to every
GitHub helper in the agentic layer, so each one takes its existing "no gh"
branch instead of shelling out:

```bash
export PDD_LOCAL_ONLY=1
pdd bug local:1
```

This is a **backstop, not the mechanism**. Local work items already avoid `gh`
by routing on the work item's owner; `PDD_LOCAL_ONLY` additionally covers code
paths that predate them, so an agent cannot silently reach for the GitHub CLI
on a path nobody thought to check. Use it when the requirement is "this project
must never talk to GitHub", rather than "this particular run is local".

Pull-request discovery is skipped entirely for a local work item — there is no
remote branch to open a PR against — instead of failing or querying GitHub.

## Storage layout

```
.pdd/work_items/
    index.json        # number + comment-id allocators, comment ownership map
    item_1.json       # title, body, state, labels, timestamps, comments[]
```

Writes are atomic (temp file then rename), so an interrupted run cannot leave a
half-written item. Comment ids come from a persisted counter and are **never
reused, even after deletion** — steering drains comments whose id exceeds a
stored cursor, so a reused id would replay an instruction the agent already
acted on.

Because the store is plain JSON inside the project, work items are diffable and
can be committed if a team wants them shared, or left in `.gitignore` if not.
That is a project decision, not something PDD forces.

## What this does not change

* GitHub workflows are untouched. Passing an issue URL behaves exactly as
  before, `gh` and all.
* `--no-github-state` and `PDD_NO_GITHUB_STATE=1` keep their existing meaning
  for GitHub-backed runs.
* Manual mode (`pdd bug --manual …`) is unaffected and is not a prerequisite —
  local work items are for the *agentic* path.
* CI validation still requires GitHub, because there is no local CI to
  validate against.

## Relationship to `pdd intent`

`pdd intent plan` / `pdd intent apply` are the ordinary-language front door for
product intent, and already require no GitHub issue. Local work items serve the
engineering workflows that are keyed on an issue-shaped request — a bug report
with a repro, a change with discussion. The two are complementary: intent
decides *what* should change and routes it; a work item carries an
issue-shaped request through `bug → fix` or `change → sync`.
