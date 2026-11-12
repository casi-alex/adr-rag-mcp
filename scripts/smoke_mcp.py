"""Prueba de humo: usa el servidor MCP como lo haría un cliente real (proceso stdio),
con el modelo y la base que haya en el entorno (RAGDEMO_DSN, RAGDEMO_MODEL).

Uso: python scripts/smoke_mcp.py ["pregunta"]
Sale con código 1 si algo no se cumple.
"""

import asyncio
import json
import os
import sys

from mcp import Client, StdioServerParameters

QUERY = sys.argv[1] if len(sys.argv) > 1 else "¿Con qué algoritmo se cifran los teléfonos?"


def payload(res):
    if getattr(res, "structured_content", None):
        return res.structured_content
    return json.loads(res.content[0].text)


async def main() -> int:
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "ragdemo.server"], env={**os.environ}
    )
    async with Client(params) as client:
        tools = {t.name for t in (await client.list_tools()).tools}
        print("herramientas:", sorted(tools))
        assert tools == {"search_docs", "get_doc", "list_docs"}, "herramientas inesperadas"

        docs = payload(await client.call_tool("list_docs", {}))
        print("list_docs:", json.dumps(docs, ensure_ascii=False)[:300])

        res = await client.call_tool("search_docs", {"query": QUERY, "k": 3})
        assert not res.is_error, "search_docs devolvió error"
        data = payload(res)
        for r in data["results"]:
            print(f"  #{r['rank']} [{r['score']:.3f}] {r['doc_id']} | {r['heading']}")
        assert data["found"] and data["results"], "sin resultados"

        top = data["results"][0]["doc_id"]
        doc = payload(await client.call_tool("get_doc", {"doc_id": top}))
        print("get_doc:", top, "->", doc.get("chunks"), "trozos")

        bad = await client.call_tool("get_doc", {"doc_id": "../../etc/passwd"})
        assert bad.is_error, "get_doc aceptó un doc_id con ruta"
        print("get_doc con ruta rechazado: OK")
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(asyncio.wait_for(main(), timeout=180)))
