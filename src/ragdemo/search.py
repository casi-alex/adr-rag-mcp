"""Consultas de solo lectura: búsqueda por similitud con procedencia, lectura de documentos.

Cada resultado trae de dónde salió (documento, número de trozo y encabezado) y su
puntaje de similitud coseno (1 = idéntico), para poder verificar la respuesta en la fuente.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
import psycopg
from psycopg import errors

from .embeddings import Embedder

MAX_QUERY_CHARS = 2000
MAX_K = 20
DOC_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,120}$")


class IndexNotReady(RuntimeError):
    """No hay índice: falta correr `python -m ragdemo.ingest`."""


@dataclass(frozen=True)
class Hit:
    doc_id: str
    ordinal: int
    heading: str
    text: str
    score: float


def check_index(conn: psycopg.Connection, embedder: Embedder) -> None:
    """Falla si el índice se creó con otro modelo: comparar vectores de modelos
    distintos devuelve resultados sin sentido sin lanzar ningún error."""
    try:
        row = conn.execute("SELECT value FROM index_meta WHERE key = 'fingerprint'").fetchone()
    except errors.UndefinedTable as e:
        raise IndexNotReady("No hay índice. Corre: python -m ragdemo.ingest") from e
    if row is None:
        raise IndexNotReady("No hay índice. Corre: python -m ragdemo.ingest")
    model, dim, _max_chars = row[0].rsplit("|", 2)
    if model != embedder.name or int(dim) != embedder.dim:
        raise RuntimeError(
            f"El índice usa '{model}' (dim {dim}) pero el servidor usa "
            f"'{embedder.name}' (dim {embedder.dim}). Define RAGDEMO_MODEL con el mismo modelo."
        )


def search(
    conn: psycopg.Connection,
    embedder: Embedder,
    query: str,
    k: int = 5,
    min_score: float | None = None,
) -> list[Hit]:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("La consulta está vacía")
    if len(query) > MAX_QUERY_CHARS:
        raise ValueError(f"La consulta excede {MAX_QUERY_CHARS} caracteres")
    if not isinstance(k, int) or isinstance(k, bool) or not 1 <= k <= MAX_K:
        raise ValueError(f"k debe ser un entero entre 1 y {MAX_K}")

    qv = np.asarray(embedder.embed_query(query.strip()), dtype=np.float32)
    rows = conn.execute(
        """SELECT doc_id, ordinal, heading, text, 1 - (embedding <=> %s) AS score
           FROM chunks ORDER BY embedding <=> %s LIMIT %s""",
        (qv, qv, k),
    ).fetchall()
    hits = [Hit(r[0], r[1], r[2], r[3], float(r[4])) for r in rows]
    if min_score is not None:
        hits = [h for h in hits if h.score >= min_score]
    return hits


def list_docs(conn: psycopg.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT d.doc_id, count(c.id) FROM documents d
           LEFT JOIN chunks c USING (doc_id) GROUP BY d.doc_id ORDER BY d.doc_id"""
    ).fetchall()
    return [{"doc_id": r[0], "chunks": r[1]} for r in rows]


def get_doc(conn: psycopg.Connection, doc_id: str) -> dict | None:
    """Reconstruye el documento desde el índice. No toca el sistema de archivos, así que
    no hay recorrido de rutas posible: solo se aceptan IDs que existen en `documents`."""
    if not isinstance(doc_id, str) or not DOC_ID_RE.match(doc_id):
        raise ValueError("doc_id inválido: usa letras, números, punto, guion y guion bajo")
    rows = conn.execute(
        """SELECT c.heading, c.text FROM chunks c
           JOIN documents d USING (doc_id) WHERE d.doc_id = %s ORDER BY c.ordinal""",
        (doc_id,),
    ).fetchall()
    if not rows:
        return None
    content = "\n\n".join(f"## {heading}\n{text}" for heading, text in rows)
    return {"doc_id": doc_id, "chunks": len(rows), "content": content}
