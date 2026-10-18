"""Semantic search over change stories and category playbooks (the ``search_stories`` tool).

Embeddings come from Ollama (``nomic-embed-text`` by default, see ``[embeddings]`` in llm.toml).
Vectors are cached per text hash, so each story or playbook is embedded once per process. If the
embedding service is unreachable the request falls back to a lexical hashing embedder and says so
in ``method``. Results are always limited to what the caller's scope may see.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import threading
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Protocol

import httpx

from d2d.config import load_toml

if TYPE_CHECKING:
    from d2d.api.repo import Ontology
    from d2d.api.scope import Scope

_TOKEN = re.compile(r"[a-z0-9]+")


class EmbeddingError(RuntimeError):
    """The embedding service could not be reached or returned something unusable."""


class Embedder(Protocol):
    method: str

    def embed(self, texts: Sequence[str], kind: str) -> list[list[float]]:
        """Unit vectors for ``texts``; ``kind`` is "query" or "document"."""
        ...


def _normalize(v: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in v))
    return [x / norm for x in v] if norm else v


class HashingEmbedder:
    """Lexical fallback: hashed word, word-pair and character-trigram counts (no model needed)."""

    method = "lexical"

    def __init__(self, dim: int = 1024) -> None:
        self.dim = dim

    def _features(self, text: str) -> Counter[str]:
        words = _TOKEN.findall(text.lower())
        feats: Counter[str] = Counter(f"w:{w}" for w in words)
        feats.update(f"b:{a}_{b}" for a, b in pairwise(words))
        for w in words:
            padded = f"#{w}#"
            feats.update(f"c:{padded[i : i + 3]}" for i in range(len(padded) - 2))
        return feats

    def embed(self, texts: Sequence[str], kind: str) -> list[list[float]]:
        out = []
        for text in texts:
            v = [0.0] * self.dim
            for feat, count in self._features(text).items():
                h = int.from_bytes(hashlib.blake2b(feat.encode(), digest_size=8).digest(), "big")
                sign = 1.0 if h & 1 else -1.0
                v[(h >> 1) % self.dim] += sign * (1.0 + math.log(count))
            out.append(_normalize(v))
        return out


class OllamaEmbedder:
    """``/api/embed`` with the task prefixes nomic-embed-text was trained with."""

    PREFIX: ClassVar[dict[str, str]] = {"query": "search_query: ", "document": "search_document: "}

    def __init__(
        self, base_url: str, model: str, timeout_s: float = 30, client: httpx.Client | None = None
    ) -> None:
        self.model = model
        self.method = f"embedding:{model}"
        self._client = client or httpx.Client(base_url=base_url, timeout=timeout_s)

    def embed(self, texts: Sequence[str], kind: str) -> list[list[float]]:
        prefix = self.PREFIX.get(kind, "") if "nomic" in self.model else ""
        try:
            resp = self._client.post(
                "/api/embed", json={"model": self.model, "input": [prefix + t for t in texts]}
            )
            resp.raise_for_status()
            vectors = resp.json()["embeddings"]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise EmbeddingError(f"ollama embed: {exc}") from exc
        if len(vectors) != len(texts):
            raise EmbeddingError("ollama embed: wrong number of vectors")
        return [_normalize([float(x) for x in v]) for v in vectors]


@dataclass(frozen=True)
class EmbeddingConfig:
    provider: str
    model: str
    base_url: str
    timeout_s: float


def load_embeddings(path: Path) -> EmbeddingConfig:
    """``[embeddings]`` in llm.toml; without it, the lexical embedder is used."""
    raw = load_toml(path)
    emb = raw.get("embeddings", {})
    llm = raw.get("llm", {})
    return EmbeddingConfig(
        provider=str(emb.get("provider", "hashing")),
        model=str(emb.get("model", "nomic-embed-text")),
        base_url=os.environ.get("D2D_OLLAMA_URL")
        or str(emb.get("base_url") or llm.get("base_url") or "http://127.0.0.1:11434"),
        timeout_s=float(emb.get("timeout_s", 30)),
    )


def embedder_from_config(cfg: EmbeddingConfig) -> Embedder:
    if cfg.provider == "ollama":
        return OllamaEmbedder(cfg.base_url, cfg.model, cfg.timeout_s)
    if cfg.provider == "hashing":
        return HashingEmbedder()
    raise ValueError(f"unknown embeddings provider {cfg.provider!r}, expected ollama or hashing")


def story_text(view: dict[str, Any]) -> str:
    devices = ", ".join(d.get("name", d["device_id"]) for d in view.get("devices", []))
    return (
        f"{view['title']}. {view['summary']} Category: {view.get('category_name') or 'unlabeled'}."
        f" Devices: {devices}."
    )


def playbook_text(cat: Any) -> str:
    return (
        f"{cat.name}. {cat.definition} {cat.plain} Checks: {'; '.join(cat.checks)}."
        f" Actions: {'; '.join(cat.actions)}."
    )


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


class StorySearch:
    def __init__(self, embedder: Embedder, fallback: Embedder | None = None) -> None:
        self.embedder = embedder
        self.fallback = fallback or HashingEmbedder()
        self._cache: dict[tuple[str, str], list[float]] = {}
        self._lock = threading.Lock()

    def _vectors(self, embedder: Embedder, texts: list[str]) -> list[list[float]]:
        keys = [(embedder.method, hashlib.sha256(t.encode()).hexdigest()) for t in texts]
        with self._lock:
            missing = [i for i, k in enumerate(keys) if k not in self._cache]
        if missing:
            fresh = embedder.embed([texts[i] for i in missing], "document")
            with self._lock:
                for i, v in zip(missing, fresh, strict=True):
                    self._cache[keys[i]] = v
        with self._lock:
            return [self._cache[k] for k in keys]

    def rank(self, query: str, texts: list[str]) -> tuple[str, list[float]]:
        """Cosine score of every text against the query, and the method that produced them."""
        if not texts:
            return self.embedder.method, []
        for embedder, note in ((self.embedder, ""), (self.fallback, " (embedding unavailable)")):
            try:
                docs = self._vectors(embedder, texts)
                q = embedder.embed([query], "query")[0]
            except EmbeddingError:
                continue
            return embedder.method + note, [_cosine(q, d) for d in docs]
        raise EmbeddingError("no embedder available")

    def search(
        self, query: str, onto: Ontology, scope: Scope, limit: int = 5
    ) -> tuple[str, list[tuple[float, dict[str, Any]]], list[tuple[float, Any]]]:
        stories = onto.list_stories(scope, limit=100_000)
        cats = [c for c in onto.taxonomy.categories.values() if scope.category_visible(c.id)]
        texts = [story_text(s) for s in stories] + [playbook_text(c) for c in cats]
        method, scores = self.rank(query, texts)
        ranked_stories = sorted(
            zip(scores[: len(stories)], stories, strict=True), key=lambda p: -p[0]
        )
        ranked_cats = sorted(zip(scores[len(stories) :], cats, strict=True), key=lambda p: -p[0])
        return method, ranked_stories[:limit], ranked_cats[:3]
