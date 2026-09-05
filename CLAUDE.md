# Claude Code Notes

This repository contains the public PDD CLI. Keep changes focused on the open-source package and avoid adding private deployment or credential-backed workflow details.

## Commands
Use a virtual environment for this checkout, not conda. The `make` targets run through
`$(PYTHON)`, which defaults to the checkout's `.venv`. Prefer the direct invocations
below for ordinary work — `make test` bills real LLM calls.

- Install dependencies: `pip install -e ".[dev]"` or `pip install -e .`
- Run all tests: `pytest -vv tests/`
- Run one test file: `pytest -vv tests/test_module_name.py`
- Test with coverage: `pytest --cov=pdd --cov-report=term-missing tests/`
- Lint check: `pylint pdd tests` or `pylint pdd/<module>.py`
- Regression tests: `bash tests/regression.sh` (accepts a test number)
- Generate module: `make generate MODULE=module_name`
- Fix module: `make fix MODULE=module_name`
- Crash fix: `make crash MODULE=module_name`

## Local, GitHub-Free Workflows
Neither of these needs a GitHub issue, the `gh` binary, or a network call. Reach for
them before reaching for `gh`.

- Plan intent (read-only; no model call, no file changes, works with no PDD artifacts
  present): `pdd intent plan --text "..."`, or `--json` for machine consumption. State
  constraints as `MUST` / `MUST NOT` / `Never` and give one concrete example — the
  planner only captures obligations phrased that way.
- Apply an approved plan: `pdd intent apply --text "..." --approve <intent-id>`. The id
  must be the one the human reviewed. Greenfield work refuses until the request names a
  technology (`--technology "Python 3.12, standard library only"`); conventional
  brownfield expects characterization tests first, then `--characterized`.
- Open a local work item: `pdd work new --title "..." --body "..."` prints `local:<n>`.
- Run a workflow against it: `pdd bug local:1`, `pdd fix local:1`, `pdd change local:1`,
  `pdd test local:1`, `pdd sync local:1`, `pdd checkup local:1`.
- Steer a run mid-flight: `pdd work comment <n> --text "..."`.
- Guarantee no GitHub CLI/API, cloud SSO, Copilot, or agent-instruction access:
  `export PDD_LOCAL_ONLY=1`. Ordinary commands never start GitHub device auth
  unless `PDD_ALLOW_GITHUB_AUTH=1`; `pdd auth login` is the explicit login path.

Details in [docs/intent.md](docs/intent.md) and
[docs/local_work_items.md](docs/local_work_items.md).

## Model Selection
- `llm_model.csv` has an `interactive_only` column. Rows marked `True` (e.g. `github_copilot/*`, `chatgpt/*`, `lm_studio/*`, `ollama/*`) require interactive human auth (device-flow OAuth or a ChatGPT subscription / `codex login` token) or a running local server and hang in non-interactive contexts (Cloud Run, CI, library import). `_select_model_candidates` skips them in the automatic candidate cascade by default; set `PDD_ALLOW_INTERACTIVE=1` from a terminal to opt in. An explicitly configured `PDD_MODEL_DEFAULT` is always honored.
- This bites `pdd intent apply` in particular, since it makes model calls: run it from a terminal rather than a non-interactive agent session. It can also stop at `awaiting_story_approval` (exit 2) for a SHA-256 confirmation, but only when `--require-story-approval` is passed or a story SHA is supplied; by default it generates a story selectively and continues.

## Code Style
- Python 3.12+, four-space indentation, and PEP 8 conventions.
- Use type hints for public functions.
- Keep imports grouped as standard library, third-party, then local.
- Functions should have docstrings when behavior or side effects are not obvious.
- Add tests for user-visible behavior and shared helper changes.
- Pydantic v2 is used for structured validation.
- The CLI is implemented with Click.
