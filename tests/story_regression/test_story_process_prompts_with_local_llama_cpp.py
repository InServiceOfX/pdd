"""Independent behavioral evidence for the developer's local-server workflow."""
import importlib
import json
from pathlib import Path
from unittest.mock import Mock

from click.testing import CliRunner
import pytest
import requests

from pdd.commands.work import work
from pdd.story_test_generation import story_bundle_hash

PDD_STORY_ID = "process_prompts_with_local_llama_cpp"
PDD_STORY_HASH = "12d27c342e6de09d"
STORY_PATH = Path(__file__).resolve().parents[2] / (
    "user_stories/story__process_prompts_with_local_llama_cpp.md"
)


@pytest.mark.story(story_id=PDD_STORY_ID, story_hash=PDD_STORY_HASH)
def test_key_free_prompt_and_persistent_local_request(monkeypatch, tmp_path):
    """The local issue and model answer survive without GitHub or cloud auth."""
    assert story_bundle_hash(STORY_PATH) == PDD_STORY_HASH
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PDD_LOCAL_ONLY", "1")
    monkeypatch.setenv("PDD_LOCAL_LLM_ENABLED", "1")
    monkeypatch.setenv("PDD_LOCAL_LLM_CONFIG_JSON", json.dumps({
        "base_url": "http://linux-server.local:8080", "model": "my-gguf",
    }))
    monkeypatch.delenv("PDD_LOCAL_LLM_API_KEY", raising=False)
    calls = []

    def local_response(session, method, url, **kwargs):
        assert url == "http://linux-server.local:8080/v1/chat/completions"
        assert method == "POST"
        assert not session.trust_env
        assert "Authorization" not in session.headers
        assert kwargs["json"]["model"] == "my-gguf"
        calls.append(url)
        return Mock(status_code=200, json=lambda: {"choices": [{
            "finish_reason": "stop", "message": {
                "content": "def add(a, b):\n    return a + b\n",
                "reasoning_content": "private reasoning",
            },
        }]})

    monkeypatch.setattr(requests.Session, "request", local_response)
    runner = CliRunner()
    for args in (
        ["new", "--title", "Generate add", "--body", "Use my local model"],
        ["comment", "1", "--text", "Keep it key-free"],
        ["close", "1"], ["reopen", "1"],
    ):
        result = runner.invoke(work, args)
        assert result.exit_code == 0, result.output
    stored = json.loads(CliRunner().invoke(work, ["show", "1", "--json"]).output)
    assert stored["state"] == "open"
    assert stored["comments"][0]["body"] == "Keep it key-free"
    assert not calls  # Issue CRUD itself uses no model or GitHub.
    llm = importlib.import_module("pdd.llm_invoke")
    answer = llm.llm_invoke(prompt="Write add", input_json={}, use_cloud=True)
    assert answer["result"] == "def add(a, b):\n    return a + b\n"
    assert answer["thinking_output"] == "private reasoning"
    assert answer["cost"] == 0
    assert len(calls) == 1
