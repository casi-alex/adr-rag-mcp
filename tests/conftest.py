"""Fixtures: una base con pgvector.

Prioridad: RAGDEMO_TEST_DSN (p. ej. el contenedor de docker-compose) y, si no existe,
un Postgres embebido con pgserver. Sin ninguno de los dos, las pruebas de BD se omiten.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest
from psycopg.conninfo import conninfo_to_dict

from ragdemo.db import connect


@pytest.fixture(scope="session")
def dsn():
    env = os.environ.get("RAGDEMO_TEST_DSN")
    if env:
        # Las pruebas hacen DROP TABLE: nunca contra la base del demo.
        dbname = conninfo_to_dict(env).get("dbname", "")
        if not dbname.endswith("_test"):
            pytest.exit(
                f"RAGDEMO_TEST_DSN apunta a la base '{dbname}'. Las pruebas borran tablas: "
                "usa una base cuyo nombre termine en _test (p. ej. ragdemo_test).",
                returncode=2,
            )
        yield env
        return
    try:
        import pgserver
    except ImportError:
        pytest.skip("Define RAGDEMO_TEST_DSN o instala pgserver (pip install -e '.[dev]')")
    tmp = tempfile.mkdtemp(prefix="ragdemo-pg-")
    srv = pgserver.get_server(tmp)
    try:
        yield srv.get_uri()
    finally:
        srv.cleanup()
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def conn(dsn):
    c = connect(dsn)
    c.execute("DROP TABLE IF EXISTS chunks, documents, index_meta CASCADE")
    yield c
    c.close()


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    d = tmp_path / "corpus"
    d.mkdir()
    (d / "a.md").write_text(
        "# Doc A\n\n## Cifrado\n\nAES-256-GCM con llave fuera de la base de datos.\n\n"
        "## Reintentos\n\nTimeout y reintentos con espera exponencial en Calendar.\n",
        encoding="utf-8",
    )
    (d / "b.md").write_text(
        "# Doc B\n\n## Aislamiento\n\nDos agentes separados: admin y paciente.\n",
        encoding="utf-8",
    )
    return d
