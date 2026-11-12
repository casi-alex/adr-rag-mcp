from ragdemo.chunking import NO_TITLE, chunk_markdown


def test_heading_path_and_no_crossing():
    md = "# T\n\n## A\n\ntexto a\n\n### A1\n\ntexto a1\n\n## B\n\ntexto b\n"
    got = [(c.heading, c.text) for c in chunk_markdown(md)]
    assert got == [
        ("T > A", "texto a"),
        ("T > A > A1", "texto a1"),
        ("T > B", "texto b"),
    ]


def test_heading_only_sections_produce_no_chunk():
    assert chunk_markdown("# Solo título\n\n## Vacía\n") == []


def test_text_before_first_heading():
    chunks = chunk_markdown("intro suelta\n\n# T\n\ncuerpo\n")
    assert chunks[0].heading == NO_TITLE and chunks[0].text == "intro suelta"
    assert chunks[1].heading == "T"


def test_hash_inside_code_fence_is_not_a_heading():
    md = "# T\n\n```sql\n# no soy encabezado\nSELECT 1;\n```\n"
    chunks = chunk_markdown(md)
    assert len(chunks) == 1
    assert "# no soy encabezado" in chunks[0].text


def test_long_section_is_split_within_limit():
    para = "palabra " * 60  # ~480 caracteres
    md = "# T\n\n" + "\n\n".join([para.strip()] * 6)
    chunks = chunk_markdown(md, max_chars=1000)
    assert len(chunks) > 1
    assert all(len(c.text) <= 1000 for c in chunks)
    assert {c.heading for c in chunks} == {"T"}


def test_giant_line_is_hard_split():
    md = "# T\n\n" + "x" * 2500
    chunks = chunk_markdown(md, max_chars=1000)
    assert all(len(c.text) <= 1000 for c in chunks)
    assert "".join(c.text for c in chunks) == "x" * 2500


def test_big_table_splits_by_rows_not_mid_row():
    rows = "\n".join(f"| fila {i} | {'dato ' * 10}|" for i in range(60))
    md = "# T\n\n| a | b |\n|---|---|\n" + rows
    chunks = chunk_markdown(md, max_chars=500)
    assert len(chunks) > 1
    for c in chunks:
        assert all(line.startswith("|") and line.endswith("|") for line in c.text.split("\n"))


def test_hash_is_deterministic_and_depends_on_heading():
    a = chunk_markdown("# T\n\n## A\n\nmismo\n")[0]
    b = chunk_markdown("# T\n\n## B\n\nmismo\n")[0]
    assert a.text_hash == chunk_markdown("# T\n\n## A\n\nmismo\n")[0].text_hash
    assert a.text_hash != b.text_hash


def test_crlf_input():
    chunks = chunk_markdown("# T\r\n\r\n## A\r\n\r\ncuerpo\r\n")
    assert [(c.heading, c.text) for c in chunks] == [("T > A", "cuerpo")]
