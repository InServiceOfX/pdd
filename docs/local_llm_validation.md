# Local llama.cpp validation — 2026-09-05

Branch: `feat/local-only-no-github-auth`, continuing `4989fbddb`.
Intent: `local-llama-cpp-prompt-processing-without-github-757a6f28`.
Implementation follows the updated prompt sources and independent local-server
story. It was implemented in the current harness; automatic whole-repo PDD
sync or semantic baseline establishment is not claimed.

## Passing evidence

- Combined focused regression run: **1,216 passed, 1 skipped**, including
  62 local-adapter checks, existing LLM routing/cloud/CLI/agent harness tests,
  local work items, GitHub guards and both independent behavioral stories.
- New local transport statement coverage: **91%** (204/224 statements).
- Five affected prompts pass strict deterministic contract and prompt lint,
  with zero issues. Architecture dependency mappings include the new module.
- Fresh-process imports attempt no network catalog lookup, including when
  `LITELLM_LOCAL_MODEL_COST_MAP=false` was supplied before selecting local mode.
- Synthetic HTTP tests cover key-free access, dedicated optional server auth,
  ambient proxy/netrc/provider-key exclusion, redirect/error refusal, ambiguous
  model discovery, typed/schema output, batches, missing/truncated final output,
  no remote schema-reference retrieval, offline estimates and exclusive routing.
- Real CLI local issue creation, comment, close, reopen and show succeeded in
  separate processes. A missing-GitHub steering bug was fixed: local comments
  now drain with `PDD_LOCAL_ONLY=1`, without requiring a `gh` executable.

Reproduce the combined run (synthetic HTTP fixtures need loopback bind access):

```bash
env -u PDD_LOCAL_ONLY LITELLM_LOCAL_MODEL_COST_MAP=true .venv/bin/python -m pytest -q --tb=short \
  tests/test_local_llm.py tests/test_local_work_items.py tests/test_github_guard.py \
  tests/test_cloud_noninteractive_auth.py \
  tests/story_regression/test_story_pdd_local_workflows_never_authenticate_with_github.py \
  tests/story_regression/test_story_process_prompts_with_local_llama_cpp.py \
  tests/test_llm_invoke.py tests/test_llm_invoke_grounding.py \
  tests/test_llm_invoke_task_routing.py tests/core/test_cloud.py tests/test_cli.py \
  tests/test_agentic_common.py
```

The fixture disables machine endpoint discovery for other provider tests,
including tests that clear all environment variables. Dedicated local-adapter
tests use isolated temporary projects. No paid inference is required.

For coverage use directory source `--cov=pdd`, then filter the report to
`pdd/local_llm.py`. Using dotted `--cov=pdd.local_llm` here imported PDD before
pytest initialization and failed in NumPy/Pydantic dependency initialization;
the directory-source run passed.

## Real GGUF inference

The existing Mac server at `http://127.0.0.1:8080/v1` reported healthy and
advertised `Qwen3.8-9B-Q8_0.gguf` (262144-token context). No restart or download
was performed. Tests removed provider/GitHub/local-server key variables.

1. `pdd generate smoke_python.prompt --output smoke.py` generated an arithmetic
   mean function. Nested Pydantic code extraction also completed locally.
2. Independent numeric and invalid-input checks passed against this function.
3. `pdd test --manual smoke_python.prompt smoke.py --output test_smoke.py`
   completed with the same local model. Its initial model-written test file
   omitted an import and invented wrong arithmetic expectations; those 15
   failing tests are **not** counted as correctness evidence.
4. A new synthetic prompt specified five independent expected results and
   imports. The installed `pdd generate` invoked from **claw-portfolio** used
   that repo's saved local settings and generated `test_local_verified.py`;
   all five assertions passed against `smoke.py`.
5. An installed-CLI `--estimate-json generate` from claw-portfolio made no
   provider call and reported the configured local output ceiling/zero API cost.

Each inference command's summary named `Qwen3.8-9B-Q8_0.gguf` and reported
`$0.000000` API cost. Hardware/electricity cost is not measured. Only synthetic
inputs were used; no brokerage data was sent. Scratch evidence is under
`/private/tmp/pdd-llama-smoke.7AiXj8` on this machine (not a durable artifact).

## Explicit limits

- Linux/CUDA launchers were inspected and the shared HTTP/configuration path
  was tested, including LAN/IPv6 addresses. No live Linux GPU host was provided;
  native Linux inference has not been claimed.
- An HTTP model server is not a coding-agent harness. Agentic issue workflows
  and agent-only sync stages fail explicitly before launching a remote provider.
- Model-generated code/tests still need independent correctness checks. A
  successful transport result is not proof of semantic correctness.
- Pylint on the new module: 9.78/10, five complexity/design advisories, no
  errors. Error-only lint across affected production files also reports two
  pre-existing diagnostics in unchanged code (`df` in model CSV loading, `best`
  in local state selection). A globally clean pylint gate is not claimed.
- This is the focused suite, not every test in the large PDD repository.

Both repositories have ignored machine-local `.pdd/local_llm.json` settings;
tracked setup instructions recreate them on another machine. No changes were
made to the launcher repositories or claw-dj; nothing was pushed or merged.
