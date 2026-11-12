"""Consulta rápida desde la terminal (misma lógica que el servidor MCP).

Uso: python -m ragdemo.ask "¿cómo se cifran los teléfonos?" -k 3
"""

from __future__ import annotations

import argparse
import sys

from .db import connect_readonly
from .embeddings import DEFAULT_MODEL, get_embedder
from .search import IndexNotReady, check_index, search


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Busca en el índice de ADRs")
    ap.add_argument("query")
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("--model", default=DEFAULT_MODEL, help="el mismo con que se indexó")
    ap.add_argument("--min-score", type=float, default=None)
    args = ap.parse_args(argv)

    embedder = get_embedder(args.model)
    conn = connect_readonly()
    try:
        check_index(conn, embedder)
        hits = search(conn, embedder, args.query, args.k, args.min_score)
    except (IndexNotReady, RuntimeError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    if not hits:
        print("Sin resultados por encima del puntaje mínimo.")
        return 0
    for i, h in enumerate(hits, 1):
        snippet = " ".join(h.text.split())[:160]
        print(f"{i}. [{h.score:.3f}] {h.doc_id} #{h.ordinal} | {h.heading}\n   {snippet}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
