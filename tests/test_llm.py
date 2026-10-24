"""Mock transport contract tests; not claims of a live model run."""
import json
import httpx
import pytest
from pipeline.judge import Judge


def configured(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://mock.invalid/v1")
    monkeypatch.setenv("LLM_API_KEY", "unit-test-only")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    return Judge()


def test_compatible_request_and_feedback(monkeypatch):
    judge = configured(monkeypatch)
    client_class = httpx.Client
    def handler(request):
        assert str(request.url) == "https://mock.invalid/v1/chat/completions"
        body = json.loads(request.content)
        context = json.loads(body["messages"][1]["content"])
        assert context["violations"][0]["code"] == "evidence"
        assert body["model"] == "test-model"
        assert request.headers["Authorization"] == "Bearer unit-test-only"
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"grids": []}'}}]})
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client_class(transport=httpx.MockTransport(handler), **kwargs))
    assert judge.decide("L1", {"violations": [{"code": "evidence"}]}, lambda: pytest.fail("rules must not run")) == {"grids": []}
    assert judge.label == "llm:test-model"


@pytest.mark.parametrize("status,content", [(500, {}), (200, {"choices": [{"message": {"content": "not json"}}]})])
def test_llm_error_never_falls_back_or_leaks_key(monkeypatch, status, content):
    judge = configured(monkeypatch)
    client_class = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client_class(transport=httpx.MockTransport(lambda request: httpx.Response(status, json=content)), **kwargs))
    with pytest.raises(RuntimeError) as error:
        judge.decide("L1", {}, lambda: pytest.fail("must not fallback"))
    assert "unit-test-only" not in str(error.value)
