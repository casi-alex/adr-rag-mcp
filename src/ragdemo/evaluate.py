"""Evaluación de la recuperación: hit@k, MRR y barrido de umbral de puntaje.

Uso: python -m ragdemo.evaluate [--questions eval/questions.jsonl] [--k 3] [--model X]
                                [--min-hit 0.8] [--json salida.json]

Formato de cada línea de questions.jsonl:
  {"id": "q01", "lang": "es", "query": "...", "expected": ["doc_id", ...]}
`expected` vacío = pregunta fuera de alcance: el índice no debería tener nada útil.
La métrica es a nivel de documento (¿apareció algún doc esperado en el top-k?), no de trozo.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from .db import connect_readonly
from .embeddings import DEFAULT_MODEL, Embedder, get_embedder
from .search import IndexNotReady, check_index, search


@dataclass(frozen=True)
class Question:
    id: str
    lang: str
    query: str
    expected: tuple[str, ...]


@dataclass(frozen=True)
class Result:
    question: Question
    ranked_docs: tuple[str, ...]  # doc_ids únicos en orden de aparición
    top_score: float  # puntaje del mejor trozo (0.0 si no hubo resultados)
    rank: int | None  # posición (1-based) del primer doc esperado; None si no apareció


def load_questions(path: Path) -> list[Question]:
    out: list[Question] = []
    seen: set[str] = set()
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
            q = Question(
                id=d["id"], lang=d.get("lang", "?"), query=d["query"], expected=tuple(d["expected"])
            )
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            raise ValueError(f"{path}:{n}: línea inválida ({e})") from e
        if not q.query.strip():
            raise ValueError(f"{path}:{n}: consulta vacía")
        if q.id in seen:
            raise ValueError(f"{path}:{n}: id repetido '{q.id}'")
        seen.add(q.id)
        out.append(q)
    if not out:
        raise ValueError(f"{path}: no hay preguntas")
    return out


def run(conn, embedder: Embedder, questions: list[Question], k: int, pool: int = 20) -> list[Result]:
    """Busca `pool` trozos y los colapsa a documentos únicos (varios trozos del mismo
    documento no deben ocupar varias posiciones del top-k)."""
    results = []
    for q in questions:
        hits = search(conn, embedder, q.query, k=pool)
        docs: list[str] = []
        for h in hits:
            if h.doc_id not in docs:
                docs.append(h.doc_id)
        rank = next((i for i, d in enumerate(docs, 1) if d in q.expected), None) if q.expected else None
        results.append(Result(q, tuple(docs), hits[0].score if hits else 0.0, rank))
    return results


def metrics(results: list[Result], k: int) -> dict:
    inscope = [r for r in results if r.question.expected]
    n = len(inscope)
    hit_k = sum(1 for r in inscope if r.rank is not None and r.rank <= k)
    hit_1 = sum(1 for r in inscope if r.rank == 1)
    mrr = sum(1 / r.rank for r in inscope if r.rank is not None) / n if n else 0.0
    return {
        "k": k,
        "in_scope": n,
        "hit_at_1": hit_1 / n if n else 0.0,
        "hit_at_k": hit_k / n if n else 0.0,
        "mrr": mrr,
    }


def threshold_sweep(results: list[Result], k: int) -> list[dict]:
    """Para cada umbral candidato: ¿cuántas preguntas válidas se pierden y cuántas fuera de
    alcance se cuelan? Un umbral filtra por el puntaje del mejor trozo."""
    inscope = [r for r in results if r.question.expected]
    outscope = [r for r in results if not r.question.expected]
    cands = sorted({round(r.top_score, 3) for r in results})
    rows = []
    for t in cands:
        kept_ok = sum(1 for r in inscope if r.top_score >= t and r.rank is not None and r.rank <= k)
        leaked = sum(1 for r in outscope if r.top_score >= t)
        rows.append(
            {
                "threshold": t,
                "in_scope_answered": kept_ok,
                "in_scope_total": len(inscope),
                "out_of_scope_leaked": leaked,
                "out_of_scope_total": len(outscope),
            }
        )
    return rows


def score_gap(results: list[Result]) -> dict:
    ins = [r.top_score for r in results if r.question.expected]
    outs = [r.top_score for r in results if not r.question.expected]
    return {
        "in_scope_min": min(ins) if ins else None,
        "out_of_scope_max": max(outs) if outs else None,
        "separable": bool(ins and outs and min(ins) > max(outs)),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Evalúa la recuperación (hit@k, MRR, umbral)")
    ap.add_argument("--questions", default="eval/questions.jsonl")
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--model", default=DEFAULT_MODEL, help="el mismo con que se indexó")
    ap.add_argument("--min-hit", type=float, default=None, help="sale con código 1 si hit@k es menor")
    ap.add_argument("--json", dest="json_out", default=None, help="guarda el reporte en este archivo")
    args = ap.parse_args(argv)

    try:
        questions = load_questions(Path(args.questions))
    except (OSError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    embedder = get_embedder(args.model)
    conn = connect_readonly()
    try:
        check_index(conn, embedder)
        results = run(conn, embedder, questions, args.k)
    except (IndexNotReady, RuntimeError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    m = metrics(results, args.k)
    gap = score_gap(results)
    sweep = threshold_sweep(results, args.k)

    print(f"{'id':<5} {'ok':<3} {'rank':<5} {'score':<6} consulta -> top docs")
    for r in results:
        if r.question.expected:
            ok = "sí" if r.rank is not None and r.rank <= args.k else "NO"
            rank = str(r.rank) if r.rank else "-"
        else:
            ok, rank = "n/a", "-"
        top = ", ".join(d.split("-")[0] + "-" + d.split("-")[1] for d in r.ranked_docs[:3])
        print(f"{r.question.id:<5} {ok:<3} {rank:<5} {r.top_score:<6.3f} {r.question.query[:60]} -> {top}")

    print(f"\nModelo: {embedder.name}  |  preguntas con respuesta: {m['in_scope']}")
    print(f"hit@1 = {m['hit_at_1']:.2f}   hit@{args.k} = {m['hit_at_k']:.2f}   MRR = {m['mrr']:.2f}")
    print(f"Mejor puntaje fuera de alcance: {gap['out_of_scope_max']}  |  "
          f"peor puntaje con respuesta: {gap['in_scope_min']}  |  separables: {gap['separable']}")
    print("\numbral  respondidas  fuera-de-alcance-que-se-cuelan")
    for s in sweep:
        print(f"{s['threshold']:<7} {s['in_scope_answered']}/{s['in_scope_total']:<9} "
              f"{s['out_of_scope_leaked']}/{s['out_of_scope_total']}")
    print("\nAviso: muestra pequeña; el umbral es orientativo, no una garantía.")

    if args.json_out:
        report = {
            "model": embedder.name,
            "metrics": m,
            "score_gap": gap,
            "sweep": sweep,
            "results": [
                {
                    "id": r.question.id,
                    "query": r.question.query,
                    "expected": list(r.question.expected),
                    "ranked_docs": list(r.ranked_docs[:5]),
                    "top_score": r.top_score,
                    "rank": r.rank,
                }
                for r in results
            ],
        }
        Path(args.json_out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.min_hit is not None and m["hit_at_k"] < args.min_hit:
        print(f"FALLA: hit@{args.k} {m['hit_at_k']:.2f} < {args.min_hit}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
