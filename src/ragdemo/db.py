"""Conexión y esquema de Postgres + pgvector."""

from __future__ import annotations

import os

import psycopg
from pgvector.psycopg import register_vector

DEFAULT_DSN = "postgresql://ragdemo:ragdemo@127.0.0.1:5433/ragdemo"


def get_dsn() -> str:
    return os.environ.get("RAGDEMO_DSN", DEFAULT_DSN)


def connect(dsn: str | None = None) -> psycopg.Connection:
    """Conexión en autocommit; las unidades atómicas usan conn.transaction()."""
    conn = psycopg.connect(dsn or get_dsn(), autocommit=True)
    # La extensión debe existir antes de registrar el tipo vector.
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    register_vector(conn)
    return conn


def connect_readonly(dsn: str | None = None) -> psycopg.Connection:
    """Conexión de solo lectura (la usa el servidor MCP).

    Defensa en profundidad: aunque el código tuviera un defecto, Postgres rechaza
    cualquier escritura de esta sesión. No ejecuta CREATE EXTENSION (sería escritura):
    la extensión ya debe existir porque `ingest` la creó.
    """
    conn = psycopg.connect(
        dsn or get_dsn(), autocommit=True, options="-c default_transaction_read_only=on"
    )
    register_vector(conn)
    return conn


def fingerprint(model: str, dim: int, max_chars: int) -> str:
    return f"{model}|{dim}|{max_chars}"


def init_schema(conn: psycopg.Connection, model: str, dim: int, max_chars: int, reset: bool) -> None:
    """Crea el esquema. Si el índice se hizo con otro modelo, dimensión o tamaño de
    trozo, falla con un mensaje claro en vez de mezclar vectores incompatibles."""
    if not isinstance(dim, int) or dim <= 0:
        raise ValueError(f"dimensión inválida: {dim!r}")
    fp = fingerprint(model, dim, max_chars)

    with conn.transaction():
        if reset:
            conn.execute("DROP TABLE IF EXISTS chunks, documents, index_meta CASCADE")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS index_meta (key text PRIMARY KEY, value text NOT NULL)"
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS documents (
                   doc_id       text PRIMARY KEY,
                   content_hash char(64) NOT NULL,
                   ingested_at  timestamptz NOT NULL DEFAULT now()
               )"""
        )
        # dim ya está validado como int: es seguro interpolarlo (DDL no admite parámetros).
        conn.execute(
            f"""CREATE TABLE IF NOT EXISTS chunks (
                    id         bigserial PRIMARY KEY,
                    doc_id     text NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
                    ordinal    integer NOT NULL,
                    heading    text NOT NULL,
                    text       text NOT NULL,
                    text_hash  char(64) NOT NULL,
                    embedding  vector({dim}) NOT NULL,
                    UNIQUE (doc_id, ordinal)
                )"""
        )
        conn.execute(
            """CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw
               ON chunks USING hnsw (embedding vector_cosine_ops)"""
        )

        row = conn.execute("SELECT value FROM index_meta WHERE key = 'fingerprint'").fetchone()
        if row is None:
            conn.execute("INSERT INTO index_meta (key, value) VALUES ('fingerprint', %s)", (fp,))
        elif row[0] != fp:
            raise RuntimeError(
                f"El índice existente se creó con '{row[0]}' y ahora pides '{fp}'. "
                "Vuelve a indexar con --reset."
            )
