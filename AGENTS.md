# Repository Guidelines

## Project Structure & Module Organization
Core CLI logic lives in `pdd/`, where each module aligns with a prompt template in `prompts/` (for example, `pdd/code_generator.py` pairs with `prompts/code_generator_python.prompt`). Generated examples drop into `context/`, CSV data into `data/`, and packaged artifacts into `dist/`. Public examples live under `examples/`, and central documentation, diagrams, and onboarding notes reside in `docs/`.

## Build, Test, and Development Commands
Work in a virtual environment created for this checkout. Do not use conda.

```bash
python -m venv .venv && source .venv/bin/activate   # or: uv venv && source .venv/bin/activate
pip install -e '.[dev]'                             # or: uv pip install -e '.[dev]'
```

Invoke the tools directly so the interpreter in use is never ambiguous:
- `pytest -q tests/` runs the suite; `pytest -q tests/test_<module>.py` for one file and `pytest -k sync_main` while iterating.
- `pytest --cov=pdd --cov-report=term-missing tests/` reports coverage; review it before large merges.
- `pylint pdd tests` checks style; fix diagnostics instead of suppressing them.
- `bash tests/regression.sh` and `bash tests/sync_regression.sh` run the longer harnesses (both accept a test number). They expect API access.
- `pip install -e .` installs the CLI locally for smoke checks.

The `make` targets run through `$(PYTHON)`, which defaults to this checkout's `.venv`
and otherwise falls back to `python3` on PATH. Override it when needed:
`make test PYTHON=/path/to/python`. Dev dependencies install through `$(PIP_INSTALL)`,
which prefers `uv pip install` because uv-created environments ship no bundled pip.

One caveat that survives: `make test` exports `PDD_RUN_REAL_LLM_TESTS=1` and a Vertex
model, so it bills real provider calls. Plain `pytest` does not.

## Local, GitHub-Free Workflows
The workflow runs end to end with no GitHub issue, no `gh`, and no network call.
Prefer these over `gh` in agent-driven work.

`pdd intent plan --text "..."` is the ordinary-language front door. It is read-only:
no model call, no project-file change, and it works in a repository that has no PDD
artifacts yet. Phrase constraints as obligations (`MUST`, `MUST NOT`, `Never`) and give
one concrete example — the planner captures constraints only when they are stated that
way. Pass `--json` when a harness consumes the plan. `pdd intent apply --approve
<intent-id>` then applies the exact plan the human reviewed; a changed request produces
a different id, so an agent cannot show one interpretation and apply another. See
[docs/intent.md](docs/intent.md).

`pdd work new|list|show|comment|close|reopen` keeps issue-shaped requests in
`.pdd/work_items/`. Reference one as `local:<n>` wherever an issue URL went — `pdd bug
local:1`, `pdd fix local:1`, `pdd change local:1`, `pdd test local:1`, `pdd sync
local:1`, `pdd checkup local:1`. `pdd work comment <n> --text "..."` steers a run
mid-flight. Set `PDD_LOCAL_ONLY=1` when the requirement is that the project must never
talk to GitHub. See [docs/local_work_items.md](docs/local_work_items.md).

## Coding Style & Naming Conventions
Use Python 3.12+, four-space indentation, and type annotations for public functions. Match module and file names to their prompt identifiers (`snake_case`), keep classes in `PascalCase`, and reserve `UPPER_SNAKE_CASE` for constants. Favor small, composable functions with docstrings summarizing side effects. Run `pylint pdd tests` (or `pylint pdd/<module>.py`) before submitting to catch style regressions.

## Testing Guidelines
All automated tests live in `tests/test_*.py` and follow the pytest discovery rules pinned in `pytest.ini`. Name new tests `test_<module>_<behavior>` and colocate fixtures in the same file unless shared broadly. Use `pytest -k sync_main` for targeted runs while iterating, then `pytest --cov=pdd --cov-report=term-missing tests/` to confirm branch coverage holds. Regression shells (`tests/regression.sh`, `tests/sync_regression.sh`) expect API access—flag them as `real` with the provided marker when applicable.

## Commit & Pull Request Guidelines
Commitizen is configured for Conventional Commits; prefer messages like `feat: improve sync verification loop`. Keep commits focused and reference related prompt paths when the change spans generated assets. Before opening a PR, run `pytest tests/` and `pylint pdd tests`, describe the behavioral impact, link any tracked issues, and attach logs or screenshots for CLI UX tweaks.

## Configuration & Secrets
Run `pdd setup` once per machine to create `~/.pdd` credentials, and never commit keys or cache files (the repo `.gitignore` already excludes them). Use the public setup documentation to provision provider tokens. When recording logs for debugging, redact token strings and strip `litellm_cache.sqlite` before sharing artifacts.
