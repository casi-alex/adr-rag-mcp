"""Prueba de extremo a extremo: levanta el servidor como proceso (stdio) y lo usa con
un cliente MCP real. Usa el modelo 'hash' para no depender de descargas."""

import asyncio
import json
import os
import sys

import pytest
from mcp import Client, StdioServerParameters

from ragdemo.embeddings import HashEmbedder
from ragdemo.ingest import ingest


def _params(dsn: str, **extra_env) -> StdioServerParameters:
    env = {**os.environ, "RAGDEMO_DSN": dsn, "RAGDEMO_MODEL": "hash", **extra_env}
    return StdioServerParameters(command=sys.executable, args=["-m", "ragdemo.server"], env=env)


def _payload(result) -> dict:
    if getattr(result, "structured_content", None):
        return result.structured_content
    return json.loads(result.content[0].text)


def run_session(params, coro):
    async def _main():
        async with Client(params) as client:
            return await coro(client)

    return asyncio.run(asyncio.wait_for(_main(), timeout=60))


@pytest.fixture
def served(conn, corpus, dsn):
    ingest(conn, corpus, HashEmbedder())
    return dsn


def test_lists_exactly_three_read_only_tools(served):
    async def go(client):
        return (await client.list_tools()).tools

    tools = run_session(_params(served), go)
    assert {t.name for t in tools} == {"search_docs", "get_doc", "list_docs"}
    assert all(t.annotations and t.annotations.read_only_hint for t in tools)


def test_search_docs_end_to_end(served):
    async def go(client):
        return await client.call_tool("search_docs", {"query": "cifrado AES-256-GCM llave", "k": 2})

    res = run_session(_params(served), go)
    assert not res.is_error
    data = _payload(res)
    assert data["found"] is True and len(data["results"]) == 2
    top = data["results"][0]
    assert (top["doc_id"], top["heading"], top["rank"]) == ("a", "Doc A > Cifrado", 1)
    assert "no instrucciones" in data["note"]


def test_get_doc_and_unknown_and_traversal(served):
    async def go(client):
        ok = await client.call_tool("get_doc", {"doc_id": "a"})
        missing = await client.call_tool("get_doc", {"doc_id": "zzz"})
        attack = await client.call_tool("get_doc", {"doc_id": "../../etc/passwd"})
        return ok, missing, attack

    ok, missing, attack = run_session(_params(served), go)
    assert not ok.is_error and _payload(ok)["doc_id"] == "a"
    assert missing.is_error and "no encontrado" in missing.content[0].text.lower()
    assert attack.is_error and "inválido" in attack.content[0].text


def test_invalid_k_is_a_tool_error_not_a_crash(served):
    async def go(client):
        bad = await client.call_tool("search_docs", {"query": "x", "k": 999})
        good = await client.call_tool("list_docs", {})  # el servidor sigue vivo
        return bad, good

    bad, good = run_session(_params(served), go)
    assert bad.is_error
    assert not good.is_error and len(_payload(good)["documents"]) == 2


def test_min_score_env_returns_not_found_for_unrelated_query(served):
    async def go(client):
        return await client.call_tool("search_docs", {"query": "zzz qqq ajeno"})

    data = _payload(run_session(_params(served, RAGDEMO_MIN_SCORE="0.5"), go))
    assert data["found"] is False and data["results"] == []


def test_server_fails_fast_when_index_is_missing(conn, dsn):
    # `conn` dejó la base sin tablas: el servidor debe salir con error claro por stderr.
    import subprocess

    env = {**os.environ, "RAGDEMO_DSN": dsn, "RAGDEMO_MODEL": "hash"}
    r = subprocess.run(
        [sys.executable, "-m", "ragdemo.server"], env=env, capture_output=True, text=True, timeout=60
    )
    assert r.returncode == 1
    assert "ingest" in r.stderr
    assert r.stdout == ""  # stdout es el protocolo: nada de ruido
