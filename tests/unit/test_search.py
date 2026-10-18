from __future__ import annotations

import json
import math
from collections.abc import Sequence
from pathlib import Path

import httpx
import pytest

from d2d.agent.search import (
    EmbeddingError,
    HashingEmbedder,
    OllamaEmbedder,
    StorySearch,
    embedder_from_config,
    load_embeddings,
)

ROOT = Path(__file__).resolve().parents[2]


def _norm(v: list[float]) -> float:
    return math.sqrt(sum(x * x for x in v))


def test_hashing_embedder_is_deterministic_unit_length_and_lexical() -> None:
    e = HashingEmbedder()
    a, b = (
        e.embed(["Firmware rollout to smart plugs"], "document")[0],
        e.embed(["Firmware rollout to smart plugs"], "query")[0],
    )
    assert a == b and _norm(a) == pytest.approx(1.0)
    method, scores = StorySearch(e).rank(
        "firmware updates on the plugs",
        ["Washer schedule moved off-peak", "Firmware rollout to smart plugs", "Garage left open"],
    )
    assert method == "lexical"
    assert max(range(3), key=lambda i: scores[i]) == 1


def _ollama(handler: httpx.MockTransport) -> OllamaEmbedder:
    return OllamaEmbedder(
        "http://ollama",
        "nomic-embed-text",
        client=httpx.Client(base_url="http://ollama", transport=handler),
    )


def test_ollama_embedder_uses_task_prefixes_and_normalizes() -> None:
    seen: list[dict[str, object]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        return httpx.Response(200, json={"embeddings": [[3.0, 4.0] for _ in body["input"]]})

    e = _ollama(httpx.MockTransport(handle))
    assert e.method == "embedding:nomic-embed-text"
    vecs = e.embed(["a", "b"], "document")
    assert vecs == [[0.6, 0.8], [0.6, 0.8]]
    e.embed(["q"], "query")
    assert seen[0]["input"] == ["search_document: a", "search_document: b"]
    assert seen[1]["input"] == ["search_query: q"]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500),
        httpx.Response(200, json={"oops": 1}),
        httpx.Response(200, json={"embeddings": []}),
    ],
)
def test_ollama_embedder_errors(response: httpx.Response) -> None:
    e = _ollama(httpx.MockTransport(lambda _r: response))
    with pytest.raises(EmbeddingError):
        e.embed(["a"], "document")


class Counting:
    method = "counting"

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[int, str]] = []
        self.fail = fail
        self.inner = HashingEmbedder()

    def embed(self, texts: Sequence[str], kind: str) -> list[list[float]]:
        if self.fail:
            raise EmbeddingError("down")
        self.calls.append((len(texts), kind))
        return self.inner.embed(texts, kind)


def test_documents_are_embedded_once_and_cached() -> None:
    emb = Counting()
    search = StorySearch(emb)
    docs = ["alpha story", "beta story"]
    search.rank("alpha", docs)
    search.rank("beta", [*docs, "gamma story"])
    # first call: 2 docs + query; second: only the new doc + query
    assert emb.calls == [(2, "document"), (1, "query"), (1, "document"), (1, "query")]


def test_falls_back_to_lexical_when_embeddings_are_down() -> None:
    method, scores = StorySearch(Counting(fail=True)).rank("garage", ["Garage left open", "Washer"])
    assert method == "lexical (embedding unavailable)"
    assert scores[0] > scores[1]


def test_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = load_embeddings(ROOT / "config" / "llm.toml")
    assert (cfg.provider, cfg.model) == ("ollama", "nomic-embed-text")
    assert isinstance(embedder_from_config(cfg), OllamaEmbedder)
    monkeypatch.setenv("D2D_OLLAMA_URL", "http://elsewhere:11434")
    assert load_embeddings(ROOT / "config" / "llm.toml").base_url == "http://elsewhere:11434"
    bare = tmp_path / "llm.toml"
    bare.write_text('[llm]\nbase_url = "http://x"\nmodel = "m"\n', encoding="utf-8")
    assert isinstance(embedder_from_config(load_embeddings(bare)), HashingEmbedder)
    with pytest.raises(ValueError):
        embedder_from_config(load_embeddings(bare).__class__("nope", "m", "http://x", 1))
