<!-- pdd-story-status: implementation-authorized-2026-09-05 -->
<!-- pdd-story-prompts: pdd/prompts/local_llm_python.prompt, pdd/prompts/llm_invoke_python.prompt, pdd/prompts/core/cloud_python.prompt, pdd/prompts/core/cli_python.prompt, pdd/prompts/agentic_common_python.prompt -->

# Process PDD prompts with a local llama.cpp server

As a developer, I want to configure my existing local or LAN llama.cpp server
once and have PDD use it for prompt processing without GitHub issues, GitHub
authentication or a paid-provider API key, so my workflow stays on infrastructure
I selected on macOS or Linux.

- Requests use only the configured endpoint and model, including structured
  outputs and nested model calls. Persistent project settings work from child
  directories; explicit environment settings override them.
- No server key is required by default. If my own server requires a key, I can
  supply it through a dedicated environment variable. Other provider credentials
  are never forwarded.
- A server error or incomplete output cannot silently select a cloud provider,
  request credentials or be accepted as complete generated code.
- Model reasoning is distinct from the final answer. A server returning only
  reasoning does not count as successful prompt processing.
- Local issues, comments and workflow state remain available in the repository
  without GitHub. PDD_LOCAL_ONLY=1 continues to prohibit GitHub access.
- An HTTP model endpoint alone does not supply filesystem/browser tools. A
  command requiring an external coding-agent harness reports that limitation
  before any remote agent is launched.
- A real PDD generation command produces runnable code using the running local
  server, and its reported model/provenance identifies that server's model.

Source: [original request](../docs/intents/intent__local-llama-cpp-757a6f28.md).
