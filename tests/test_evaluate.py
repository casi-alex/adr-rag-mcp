"""Pruebas de la evaluación. La validación del archivo de preguntas no necesita base de datos."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ragdemo.embeddings import get_embedder
from ragdemo.evaluate import Result, Question, load_questions, metrics, run, score_gap, threshold_sweep
from ragdemo.ingest import ingest

ROOT = Path(__file__).resolve().parent.parent


def test_questions_file_valid_and_expected_docs_exist():
    qs = load_questions(ROOT / "eval" / "questions.jsonl")
    ids = {p.stem for p in (ROOT / "corpus").glob("*.md")}
    for q in qs:
        assert set(q.expected) <= ids, f"{q.id} espera un doc que no existe"
    assert any(q.expected for q in qs) and any(not q.expected for q in qs)


def test_load_questions_rejects_bad_lines(tmp_path):
    p = tmp_path / "q.jsonl"
    p.write_text('{"id": "a", "query": "x", "expected": []}\n{"id": "a", "query": "y", "expected": []}\n')
    with pytest.raises(ValueError, match="repetido"):
        load_questions(p)
    p.write_text("no es json\n")
    with pytest.raises(ValueError, match="inválida"):
        load_questions(p)
    p.write_text('{"id": "a", "query": "  ", "expected": []}\n')
    with pytest.raises(ValueError, match="vacía"):
        load_questions(p)


def _res(qid, expected, rank, score):
    return Result(Question(qid, "es", "q", tuple(expected)), (), score, rank)


def test_metrics_and_sweep():
    rs = [_res("a", ["d1"], 1, 0.5), _res("b", ["d2"], 3, 0.4), _res("c", ["d3"], None, 0.3),
          _res("o", [], None, 0.35)]
    m = metrics(rs, 3)
    assert m["in_scope"] == 3
    assert m["hit_at_1"] == pytest.approx(1 / 3)
    assert m["hit_at_k"] == pytest.approx(2 / 3)
    assert m["mrr"] == pytest.approx((1 + 1 / 3) / 3)
    assert metrics(rs, 2)["hit_at_k"] == pytest.approx(1 / 3)
    assert score_gap(rs) == {"in_scope_min": 0.3, "out_of_scope_max": 0.35, "separable": False}
    row = next(s for s in threshold_sweep(rs, 3) if s["threshold"] == 0.4)
    assert row["in_scope_answered"] == 2 and row["out_of_scope_leaked"] == 0


def test_run_collapses_chunks_to_docs(conn, corpus):
    emb = get_embedder("hash")
    ingest(conn, corpus, emb, max_chars=200)
    qs = [Question("q", "es", "AES-256-GCM llave fuera de la base de datos", ("a",))]
    (r,) = run(conn, emb, qs, k=3)
    assert r.rank == 1
    assert len(set(r.ranked_docs)) == len(r.ranked_docs)
