"""Embeddings locales (sin API de pago).

- FastEmbedEmbedder: modelos ONNX que corren en CPU. Descarga el modelo la primera vez.
- HashEmbedder: SOLO PARA PRUEBAS. Bolsa de palabras con hashing: no es semántico,
  solo detecta palabras en común. Sirve para probar el flujo sin descargar nada.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from typing import Protocol, Sequence

DEFAULT_MODEL = "jinaai/jina-embeddings-v2-base-es"


class Embedder(Protocol):
    name: str
    dim: int

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


class HashEmbedder:
    """Determinista y sin descargas. No usar en el demo real."""

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim
        self.name = f"hash-{dim}-test-only"

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for tok in re.findall(r"\w+", _strip_accents(text.lower())):
            h = hashlib.sha256(tok.encode("utf-8")).digest()
            idx = int.from_bytes(h[:4], "big") % self.dim
            vec[idx] += 1.0 if h[4] % 2 == 0 else -1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0:  # texto vacío: evita vector cero (coseno indefinido)
            vec[0] = 1.0
            return vec
        return [v / norm for v in vec]

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class FastEmbedEmbedder:
    def __init__(self, model_name: str = DEFAULT_MODEL, cache_dir: str | None = None) -> None:
        from fastembed import TextEmbedding  # import perezoso: es pesado

        self.name = model_name
        self.dim = TextEmbedding.get_embedding_size(model_name)
        self._cache_dir = cache_dir
        self._model = None

    def _load(self):
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(model_name=self.name, cache_dir=self._cache_dir)
        return self._model

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        # passage_embed aplica el prefijo que el modelo necesite (p. ej. e5)
        return [v.tolist() for v in self._load().passage_embed(list(texts))]

    def embed_query(self, text: str) -> list[float]:
        return next(iter(self._load().query_embed(text))).tolist()


def get_embedder(spec: str) -> Embedder:
    """'hash' para pruebas; cualquier otro valor es un modelo de fastembed."""
    if spec == "hash":
        return HashEmbedder()
    return FastEmbedEmbedder(spec)
