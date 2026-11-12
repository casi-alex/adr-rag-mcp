import psycopg
import pytest

from ragdemo.db import connect_readonly
from ragdemo.embeddings import HashEmbedder
from ragdemo.ingest import ingest
from ragdemo.search import IndexNotReady, check_index, get_doc, list_docs, search


@pytest.fixture
def indexed(conn, corpus):
    emb = HashEmbedder()
    ingest(conn, corpus, emb)
    return conn, emb


def test_search_returns_provenance_and_ranks_best_first(indexed):
    conn, emb = indexed
    hits = search(conn, emb, "cifrado AES-256-GCM llave", k=3)
    assert hits[0].doc_id == "a" and hits[0].heading == "Doc A > Cifrado"
    assert hits[0].ordinal == 0
    assert all(0.0 <= h.score <= 1.0001 for h in hits)
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)


def test_k_limits_results(indexed):
    conn, emb = indexed
    assert len(search(conn, emb, "agentes", k=1)) == 1
    assert len(search(conn, emb, "agentes", k=20)) == 3  # solo hay 3 trozos


@pytest.mark.parametrize("bad_k", [0, -1, 21, 2.5, "3", True])
def test_invalid_k_is_rejected(indexed, bad_k):
    conn, emb = indexed
    with pytest.raises(ValueError, match="k debe ser"):
        search(conn, emb, "agentes", k=bad_k)


@pytest.mark.parametrize("bad_q", ["", "   ", "\n\t"])
def test_empty_query_is_rejected(indexed, bad_q):
    conn, emb = indexed
    with pytest.raises(ValueError, match="vacía"):
        search(conn, emb, bad_q)


def test_oversized_query_is_rejected(indexed):
    conn, emb = indexed
    with pytest.raises(ValueError, match="excede"):
        search(conn, emb, "x" * 2001)


def test_min_score_filters_out_weak_matches(indexed):
    conn, emb = indexed
    assert search(conn, emb, "tema totalmente ajeno zzz qqq", k=5, min_score=0.5) == []
    assert len(search(conn, emb, "cifrado AES-256-GCM llave", k=5, min_score=0.2)) >= 1


def test_query_with_sql_metacharacters_is_just_text(indexed):
    conn, emb = indexed
    search(conn, emb, "'; DROP TABLE chunks; --", k=3)
    assert conn.execute("SELECT count(*) FROM chunks").fetchone()[0] == 3


def test_list_docs(indexed):
    conn, _ = indexed
    assert list_docs(conn) == [{"doc_id": "a", "chunks": 2}, {"doc_id": "b", "chunks": 1}]


def test_get_doc_reconstructs_in_order(indexed):
    conn, _ = indexed
    doc = get_doc(conn, "a")
    assert doc["chunks"] == 2
    assert doc["content"].index("Cifrado") < doc["content"].index("Reintentos")


def test_get_doc_unknown_returns_none(indexed):
    conn, _ = indexed
    assert get_doc(conn, "no-existe") is None


@pytest.mark.parametrize(
    "bad_id",
    ["../../etc/passwd", "..\\..\\windows\\win.ini", "a/../b", "a b", "", "a" * 121, "a;DROP", "a\x00b", None, 7],
)
def test_get_doc_rejects_malicious_ids(indexed, bad_id):
    conn, _ = indexed
    with pytest.raises(ValueError, match="doc_id inválido"):
        get_doc(conn, bad_id)


def test_readonly_connection_rejects_writes(indexed, dsn):
    ro = connect_readonly(dsn)
    try:
        assert ro.execute("SELECT count(*) FROM chunks").fetchone()[0] == 3
        for sql in (
            "DELETE FROM chunks",
            "INSERT INTO index_meta VALUES ('x','y')",
            "DROP TABLE chunks",
        ):
            with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
                ro.execute(sql)
    finally:
        ro.close()
    assert indexed[0].execute("SELECT count(*) FROM chunks").fetchone()[0] == 3


def test_check_index_without_index_raises_not_ready(conn):
    with pytest.raises(IndexNotReady, match="ingest"):
        check_index(conn, HashEmbedder())


def test_check_index_detects_model_mismatch(indexed):
    conn, _ = indexed
    with pytest.raises(RuntimeError, match="RAGDEMO_MODEL"):
        check_index(conn, HashEmbedder(dim=128))


def test_ask_cli_prints_ranked_hits_and_fails_cleanly(indexed, dsn, monkeypatch, capsys):
    from ragdemo import ask

    monkeypatch.setenv("RAGDEMO_DSN", dsn)
    assert ask.main(["cifrado AES-256-GCM llave", "-k", "2", "--model", "hash"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("1. [") and "a #0 | Doc A > Cifrado" in out
    assert ask.main(["x", "-k", "0", "--model", "hash"]) == 1
    assert "k debe ser" in capsys.readouterr().err
