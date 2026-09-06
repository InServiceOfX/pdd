# Local llama.cpp prompt processing

PDD can process prompts using an existing llama.cpp `llama-server` without a
GitHub login, GitHub issue, paid-provider API key or placeholder key. This is
an exclusive endpoint route: connection/model/schema failures stop the command
instead of falling back to a cloud model. The model runs on the server, not
inside PDD. No weights are downloaded by PDD.

## Configure once per project

Save [this example](local_llm.example.json) as `.pdd/local_llm.json` in the
project root. The file is machine-local and ignored by Git in this fork and
claw-portfolio. It contains no secrets. Adjust context/output limits to your
loaded model. Alternatively place the same fields in top-level `local_llm:`
in `.pddrc` for versioned project settings.

```json
{
  "base_url": "http://127.0.0.1:8080/v1",
  "model": "auto",
  "max_tokens": 8192,
  "timeout": 600,
  "temperature": 0.6,
  "context_window": 262144,
  "enable_thinking": false
}
```

No `pdd setup`, credential scan or `--local` flag is necessary for this route.
The CLI selects local execution automatically and skips its auto-update check.
Use `PDD_LOCAL_ONLY=1` as well to forbid GitHub throughout other PDD workflows:

```bash
PDD_LOCAL_ONLY=1 pdd --no-core-dump generate example_python.prompt --output example.py
PDD_LOCAL_ONLY=1 pdd --no-core-dump test --manual example_python.prompt example.py --output test_example.py
PDD_LOCAL_ONLY=1 pdd --no-core-dump --estimate-json generate example_python.prompt --output example.py
```

All nested `llm_invoke` calls (including structured code extraction) stay on
the selected endpoint. Plain text, Pydantic and JSON-schema output are supported;
batch requests execute sequentially for single-slot servers. Reasoning is kept
separate from final content. Truncated or reasoning-only replies fail rather
than saving incomplete answers. Some models ignore `enable_thinking`; allow a
large enough output budget and timeout. Server queue time counts toward timeout.

Configuration precedence, highest first:

1. `PDD_LOCAL_LLM_BASE_URL`, `PDD_LOCAL_LLM_MODEL`, `PDD_LOCAL_LLM_MAX_TOKENS`,
   `PDD_LOCAL_LLM_TIMEOUT` override individual fields.
2. `PDD_LOCAL_LLM_CONFIG_JSON` pins non-secret settings inherited by child PDD
   commands; otherwise `PDD_LOCAL_LLM_CONFIG` selects an explicit JSON file.
3. Nearest project's `.pdd/local_llm.json`, then its `.pddrc` `local_llm` mapping.
4. `~/.pdd/local_llm.json` supplies an optional machine-wide default.

`PDD_LOCAL_LLM_ENABLED=0` explicitly disables the route. Use it for mocked
provider tests, not as a workaround for a failed local server. With no local
configuration, existing PDD model selection is unchanged.

`model: auto` queries `/v1/models` and requires exactly one advertised model.
Set an exact id to skip discovery or select among multiple models. The launch
profile name is not necessarily the API model id: the inspected Mac profile
`qwen38-9b-distill-q8` advertises `Qwen3.8-9B-Q8_0.gguf`.

## macOS and Linux

Both Monoclaw launchers expose the same `/v1/models` and `/v1/chat/completions`
protocol. The Mac launcher is native Metal; the Linux launcher uses a CUDA
container. PDD requires no OS-specific inference code:

- PDD and server on the same host: `http://127.0.0.1:8080/v1`.
- PDD on a different host: `http://<server-LAN-address>:8080/v1`.
- PDD inside a container: use the reachable host/service address, not that
  container's loopback unless the server is in the same network namespace.

`0.0.0.0`/`::` are server bind addresses, not client destinations. IPv6 loopback
uses `http://[::1]:8080/v1`. Keep a key-free server on a trusted network with
firewall restrictions. Remote plaintext HTTP does not encrypt prompts. PDD
does not change server exposure, start/stop servers or fetch GGUFs.

If your own server requires authentication, set `PDD_LOCAL_LLM_API_KEY` or name
another dedicated variable using `api_key_env`. Never put the key in JSON or
the URL. Ambient OpenAI/Anthropic keys, netrc credentials and proxies are not
used; redirects are rejected. An explicitly configured LAN server receives
the prompts you ask PDD to process, so choose its address deliberately.

## GitHub-free issues and capability boundary

`pdd work new|list|show|comment|close|reopen` stores requests in
`.pdd/work_items/` without networking. Run it from the repository root; these
are local files, not automatically synchronized through Git or a remote.
Steering comments and resumable workflow state also use this store for
`local:<number>` references. Back up the ignored store if it must survive
loss of the working directory. See [local work items](local_work_items.md).

A chat endpoint is **not a coding-agent harness**. Workflows that require
Claude/Codex/Gemini/OpenCode shell/browser/file tools fail before launching an
external agent when local endpoint mode is selected. This includes agentic
issue workflows and agentic repair stages of sync; their complete offline
execution is not claimed. Use prompt-file commands such as `generate` and
`test --manual`. An entire automated sync remains dependent on its configured
stages and repository adoption metadata.

`--estimate`/`--estimate-json` perform no inference or model discovery. Input
counts are approximate serialized UTF-8 byte bounds, not measurements from
the server tokenizer. API charge is zero; hardware/electricity are excluded.
Input/context admission uses the same conservative approximation and may reject
a request that the server tokenizer would fit.

## Validation

See [the validation record](local_llm_validation.md) for commands, test counts,
live-model evidence and explicit limits.

The regression suite uses synthetic loopback HTTP servers and mocked providers:

```bash
.venv/bin/python -m pytest -q tests/test_local_llm.py
PDD_LOCAL_ONLY=1 .venv/bin/python -m pytest -q tests/test_local_work_items.py tests/test_github_guard.py tests/story_regression/test_story_process_prompts_with_local_llama_cpp.py
```

On 2026-09-05, a real `pdd generate` command and its nested structured extraction
succeeded against the running Mac GGUF endpoint with provider keys removed.
The generated arithmetic-mean function passed independent numeric checks.
`pdd test --manual` also completed locally, including nested structured
extraction, but its first generated test file had a missing import and wrong
numeric expectations; it was rejected as a correctness result. A second
synthetic prompt specified five independent assertions explicitly. Invoking
`pdd generate` from claw-portfolio, using only its saved endpoint configuration,
produced that test module and all five checks passed against the generated
function. Model-generated tests are not an independent correctness oracle.
Linux launcher/protocol compatibility is covered by configuration and HTTP
tests; no live Linux GPU server was available for a second inference run.
