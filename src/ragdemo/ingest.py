"""Ingesta idempotente: indexa los .md de un directorio en Postgres + pgvector.

- Idempotente por hash del contenido del archivo: si no cambió, no se re-embebe.
- Cada documento se reemplaza en su propia transacción: si algo falla, el documento
  queda como estaba y se puede volver a correr.
- Los documentos que ya no están en el directorio se eliminan del índice.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import psycopg

from .chunking import chunk_markdown
from .db import connect, init_schema
from .embeddings import DEFAULT_MODEL, Embedder, get_embedder

BATCH_SIZE = 16


@dataclass
class IngestReport:
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0
    chunks: int = 0  # trozos escritos en esta corrida

    def __str__(self) -> str:
        return (
            f"nuevos={self.added} actualizados={self.updated} sin_cambios={self.unchanged} "
            f"eliminados={self.removed} trozos_escritos={self.chunks}"
        )


def _embed_all(embedder: Embedder, texts: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for i in range(0, len(texts), BATCH_SIZE):
        vectors.extend(embedder.embed_passages(texts[i : i + BATCH_SIZE]))
    if len(vectors) != len(texts):
        raise RuntimeError("El embedder devolvió una cantidad distinta de vectores")
    for v in vectors:
        if len(v) != embedder.dim:
            raise RuntimeError(f"Dimensión inesperada: {len(v)} != {embedder.dim}")
    return vectors


def ingest(
    conn: psycopg.Connection,
    corpus_dir: str | Path,
    embedder: Embedder,
    *,
    reset: bool = False,
    max_chars: int = 1000,
) -> IngestReport:
    files = sorted(Path(corpus_dir).glob("*.md"))
    if not files:
        raise ValueError(f"No hay archivos .md en {corpus_dir}")

    init_schema(conn, embedder.name, embedder.dim, max_chars, reset)

    existing = dict(conn.execute("SELECT doc_id, content_hash FROM documents").fetchall())
    report = IngestReport()
    seen: set[str] = set()

    for path in files:
        doc_id = path.stem
        seen.add(doc_id)
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as e:
            raise ValueError(f"{path.name} no es UTF-8 válido: {e}") from e
        content_hash = hashlib.sha256(raw).hexdigest()

        if existing.get(doc_id) == content_hash:
            report.unchanged += 1
            continue

        chunks = chunk_markdown(text, max_chars=max_chars)
        if not chunks:
            raise ValueError(f"{path.name} no tiene contenido indexable")

        # Se embebe ANTES de abrir la transacción: si el modelo falla, no se toca la BD.
        vectors = _embed_all(embedder, [c.embedding_input for c in chunks])

        with conn.transaction():
            conn.execute("DELETE FROM chunks WHERE doc_id = %s", (doc_id,))
            conn.execute(
                """INSERT INTO documents (doc_id, content_hash) VALUES (%s, %s)
                   ON CONFLICT (doc_id) DO UPDATE
                   SET content_hash = EXCLUDED.content_hash, ingested_at = now()""",
                (doc_id, content_hash),
            )
            with conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO chunks (doc_id, ordinal, heading, text, text_hash, embedding)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    [
                        (doc_id, i, c.heading, c.text, c.text_hash, np.asarray(v, dtype=np.float32))
                        for i, (c, v) in enumerate(zip(chunks, vectors))
                    ],
                )
        report.chunks += len(chunks)
        if doc_id in existing:
            report.updated += 1
        else:
            report.added += 1

    for doc_id in set(existing) - seen:
        conn.execute("DELETE FROM documents WHERE doc_id = %s", (doc_id,))  # cascada a chunks
        report.removed += 1

    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Indexa el corpus en Postgres + pgvector")
    ap.add_argument("--corpus", default="corpus", help="directorio con archivos .md")
    ap.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="modelo de fastembed, o 'hash' (solo pruebas, no semántico)",
    )
    ap.add_argument("--max-chars", type=int, default=1000, help="tamaño máximo de trozo")
    ap.add_argument("--reset", action="store_true", help="borra y recrea el índice")
    args = ap.parse_args(argv)

    embedder = get_embedder(args.model)
    conn = connect()
    try:
        report = ingest(conn, args.corpus, embedder, reset=args.reset, max_chars=args.max_chars)
    except (RuntimeError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    print(f"[{embedder.name}] {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
