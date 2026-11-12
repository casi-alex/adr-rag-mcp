import pytest

from ragdemo.embeddings import HashEmbedder
from ragdemo.ingest import ingest


class CountingEmbedder(HashEmbedder):
    """Cuenta cuántos textos se embeben, para probar la idempotencia."""

    def __init__(self, dim: int = 256, name: str | None = None) -> None:
        super().__init__(dim)
        if name:
            self.name = name
        self.calls = 0

    def embed_passages(self, texts):
        self.calls += len(texts)
        return super().embed_passages(texts)


def count(conn, table):
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_first_run_indexes_everything(conn, corpus):
    r = ingest(conn, corpus, CountingEmbedder())
    assert (r.added, r.updated, r.unchanged, r.removed) == (2, 0, 0, 0)
    assert count(conn, "documents") == 2
    assert count(conn, "chunks") == r.chunks == 3


def test_provenance_columns_are_filled(conn, corpus):
    ingest(conn, corpus, CountingEmbedder())
    rows = conn.execute(
        "SELECT doc_id, ordinal, heading, text_hash FROM chunks ORDER BY doc_id, ordinal"
    ).fetchall()
    assert rows[0][:3] == ("a", 0, "Doc A > Cifrado")
    assert rows[1][:3] == ("a", 1, "Doc A > Reintentos")
    assert all(len(r[3].strip()) == 64 for r in rows)


def test_rerun_is_idempotent_and_embeds_nothing(conn, corpus):
    emb = CountingEmbedder()
    ingest(conn, corpus, emb)
    calls = emb.calls
    r = ingest(conn, corpus, emb)
    assert (r.added, r.updated, r.unchanged) == (0, 0, 2)
    assert emb.calls == calls  # cero embeddings nuevos
    assert count(conn, "chunks") == 3


def test_changed_file_only_reindexes_that_doc(conn, corpus):
    emb = CountingEmbedder()
    ingest(conn, corpus, emb)
    (corpus / "b.md").write_text("# Doc B\n\n## Aislamiento\n\nTexto nuevo.\n\n## Extra\n\nOtro.\n")
    before = emb.calls
    r = ingest(conn, corpus, emb)
    assert (r.updated, r.unchanged) == (1, 1)
    assert emb.calls == before + 2
    assert count(conn, "chunks") == 4  # 2 de a + 2 de b, sin duplicados ni huérfanos


def test_removed_file_is_removed_from_index(conn, corpus):
    ingest(conn, corpus, CountingEmbedder())
    (corpus / "b.md").unlink()
    r = ingest(conn, corpus, CountingEmbedder())
    assert r.removed == 1
    assert count(conn, "documents") == 1
    assert count(conn, "chunks") == 2  # cascada


def test_model_change_without_reset_fails_closed(conn, corpus):
    ingest(conn, corpus, CountingEmbedder(name="modelo-1"))
    with pytest.raises(RuntimeError, match="--reset"):
        ingest(conn, corpus, CountingEmbedder(name="modelo-2"))
    assert count(conn, "chunks") == 3  # no se mezcló nada


def test_dimension_change_requires_reset_and_works_with_it(conn, corpus):
    ingest(conn, corpus, CountingEmbedder(dim=256))
    with pytest.raises(RuntimeError):
        ingest(conn, corpus, CountingEmbedder(dim=128))
    r = ingest(conn, corpus, CountingEmbedder(dim=128), reset=True)
    assert r.added == 2 and count(conn, "chunks") == 3
    dim = conn.execute(
        "SELECT atttypmod FROM pg_attribute WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'"
    ).fetchone()[0]
    assert dim == 128


def test_chunk_size_change_requires_reset(conn, corpus):
    ingest(conn, corpus, CountingEmbedder(), max_chars=1000)
    with pytest.raises(RuntimeError, match="--reset"):
        ingest(conn, corpus, CountingEmbedder(), max_chars=500)


def test_empty_corpus_is_an_error(conn, tmp_path):
    with pytest.raises(ValueError, match="No hay archivos"):
        ingest(conn, tmp_path, CountingEmbedder())


def test_file_without_indexable_content_is_an_error(conn, corpus):
    (corpus / "vacio.md").write_text("# Solo título\n")
    with pytest.raises(ValueError, match="vacio.md"):
        ingest(conn, corpus, CountingEmbedder())


def test_invalid_utf8_is_an_error(conn, corpus):
    (corpus / "malo.md").write_bytes(b"# T\n\n\xff\xfe roto\n")
    with pytest.raises(ValueError, match="UTF-8"):
        ingest(conn, corpus, CountingEmbedder())


def test_embedder_failure_leaves_existing_doc_untouched(conn, corpus):
    ingest(conn, corpus, CountingEmbedder())
    (corpus / "a.md").write_text("# Doc A\n\n## Nuevo\n\ncambio\n")

    class Boom(CountingEmbedder):
        def embed_passages(self, texts):
            raise RuntimeError("modelo no disponible")

    with pytest.raises(RuntimeError, match="no disponible"):
        ingest(conn, corpus, Boom())
    # a sigue con su contenido anterior y completo
    headings = [r[0] for r in conn.execute("SELECT heading FROM chunks WHERE doc_id='a' ORDER BY ordinal")]
    assert headings == ["Doc A > Cifrado", "Doc A > Reintentos"]
    # y al reintentar con el modelo sano se recupera
    r = ingest(conn, corpus, CountingEmbedder())
    assert r.updated == 1


def test_real_corpus_indexes_cleanly(conn):
    """El corpus sanitizado del repo debe indexarse sin errores."""
    from pathlib import Path

    corpus = Path(__file__).resolve().parents[1] / "corpus"
    r = ingest(conn, corpus, CountingEmbedder())
    assert r.added == 8 and r.chunks >= 20
    # ningún trozo vacío
    assert count(conn, "chunks") == r.chunks
    assert conn.execute("SELECT count(*) FROM chunks WHERE length(trim(text)) = 0").fetchone()[0] == 0
