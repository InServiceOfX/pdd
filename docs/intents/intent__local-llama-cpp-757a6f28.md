# Local llama.cpp prompt processing without GitHub or API keys

Date: 2026-09-05. Authorized: Ernest's request to implement and test through
completion, on `feat/local-only-no-github-auth`.
Planner ID: `local-llama-cpp-prompt-processing-without-github-757a6f28`.
Request SHA-256 (prose, excluding the pasted startup transcript):
`757a6f2812dd2e8178612ccce61ab81f1b72cf4f914fc5c2fb3f2456a1de2f4b`.

## Original request prose

> Ok go back to pdd /Users/ernestyeung/.openclaw/workspace/repos/PromptDrivenDevelopment/pdd
>  So we've tried to get rid of the dependency on github auth and any and all need for github issues, and then an alternative is locally keeping the issues there with the repo or somewhere local. So maake sure that's working: branch name: * feat/local-only-no-github-auth
>  next any time there's an ask for using a LLM to "process" a prompt file, any time it needs a LLM usually right now it needs an API key. Double check if we can point it to a llama-cpp llama server, like we have here: /Users/ernestyeung/.openclaw/workspace/repos/Monoclaw/Deployments/Scripts/llama-cpp-server-macos
>  check out how we launch it and then our .gguf is available on a local IP and port. Also like this is the Mac OS version, also make it work with llama cpp llama server on Linux, which we have here: /Users/ernestyeung/.openclaw/workspace/repos/Monoclaw/Deployments/Scripts/llama-cpp-server
>  and then of course test that pdd when it runs like some pdd <command> and it needs a LLM that once configured to use a locall llama server (for this Mac OS I have it running here:   qwen38-9b-distill-q8
> BTW claw-port that it can use it. Thanks! Please implement and test until completion

## Supplied and inspected deployment evidence

The user supplied a successful native Metal launch using the
`qwen38-9b-distill-q8` profile on port 8080. The running /health and /v1/models
endpoints confirm `Qwen3.8-9B-Q8_0.gguf`, with a 262144-token context. macOS and
Linux/CUDA launchers both expose OpenAI-compatible chat completions.
No server restart or model download is required.

## Accepted implementation meaning

Local work items stay under the repo's .pdd/work_items. Add explicit persistent
local LLM configuration and environment overrides. Prompt-processing calls use
only that endpoint without requiring a paid-provider key or GitHub login.
Support text, structured output, batch calls and non-networked estimates.
Endpoint errors, malformed/truncated responses or missing configuration must
not fall back to cloud/model catalogs or interactive credential acquisition.
Keep reasoning separate from final content. Prevent cloud dispatch at CLI and
library boundaries. Commands requiring a separate tool-capable coding-agent
harness must fail with a clear explanation rather than secretly launch a remote
agent when local endpoint mode is selected. That is a separate capability from
an HTTP model endpoint.

Configure this PDD checkout and claw-portfolio with machine-local settings, then
run actual PDD prompt commands against the already running server. Test GitHub
prohibitions, local issue persistence, endpoint failure and key-free operation.
Do not change or commit anything in claw-dj or the launcher repositories.
