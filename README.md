# mcp-rag-demo

A small, read-only **MCP server** over a **RAG index** (Postgres + pgvector, local embeddings),
with **provenance on every result** and a **retrieval evaluation** you can re-run.

The corpus is 8 sanitized Architecture Decision Records (ADRs, written in Spanish) from a
WhatsApp appointment-booking assistant built on n8n. It is a deliberately small demo: the goal is
to show the design decisions (fail-closed index, read-only access, untrusted-corpus handling,
measurable retrieval quality), not scale.

> **Status:** working demo. 63 tests pass; retrieval was evaluated on 18 questions (see
> [Evaluation](#evaluation) and its limits). Not production software.

## Architecture

```mermaid
flowchart LR
    C["corpus/*.md<br/>8 ADRs"] -->|"chunk by headings"| I["ingest.py"]
    I -->|"local embeddings<br/>fastembed ONNX"| P[("Postgres 16<br/>+ pgvector HNSW")]
    P -->|"read-only session"| S["search.py"]
    S --> M["server.py<br/>MCP, stdio"]
    S --> A["ask.py CLI"]
    S --> E["evaluate.py<br/>hit-at-k, MRR, threshold sweep"]
    M -->|"search_docs / get_doc / list_docs"| K["MCP client<br/>Claude Desktop, Claude Code, ..."]
```

| Piece | What it does |
|---|---|
| `chunking.py` | Splits Markdown by H1-H3 headings, keeps the heading path as context, respects code fences, packs long sections by paragraph (default 1000 chars). |
| `embeddings.py` | Local embeddings via [fastembed](https://github.com/qdrant/fastembed). Default `jinaai/jina-embeddings-v2-base-es` (768 dims). No API calls. |
| `ingest.py` | Idempotent: skips files whose SHA-256 is unchanged, removes documents no longer on disk. |
| `search.py` | Cosine similarity (`1 - distance`), returns `doc_id`, chunk number, heading and score. |
| `server.py` | MCP server (official Python SDK) with three read-only tools. |
| `evaluate.py` | Runs `eval/questions.jsonl`, prints hit@1, hit@k, MRR and a score-threshold sweep. |

## Design decisions

| Decision | Why |
|---|---|
| **Fail closed on model changes.** A fingerprint `model\|dim\|max_chars` is stored in `index_meta`; a mismatch aborts (re-index with `--reset`). | Comparing vectors from different models returns plausible-looking garbage without raising any error. |
| **Read-only by construction.** The server opens Postgres with `default_transaction_read_only=on`; all three tools are annotated `readOnlyHint`. | A tool that only reads should not be able to write even if the code is wrong. A test removes the option and fails. |
| **`get_doc` reads from the DB, not the filesystem.** Plus a `doc_id` allowlist regex. | No path traversal surface. Tested with traversal strings and SQL metacharacters. |
| **Corpus is treated as untrusted data.** Every response carries a note telling the model not to follow instructions found in fragments. | Prompt-injection from indexed documents is the main RAG-specific risk. This is a mitigation, not a guarantee. |
| **Only `ToolError` messages reach the model.** Other exceptions become a generic error. | Avoids leaking internals (DSN, stack traces) to the client. |
| **Tests refuse to run on a non-`*_test` database.** | The tests drop tables; they must never touch the demo index. |

## Quick start

Requirements: Python 3.10+, Docker. First run downloads the embedding model (~0.64 GB).

```powershell
docker compose up -d                       # Postgres + pgvector on 127.0.0.1:5433
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[mcp]"
python -m ragdemo.ingest --corpus corpus   # first run: add --reset if you changed model
python -m ragdemo.ask "¿Con qué algoritmo se cifran los teléfonos?" -k 3
python scripts\smoke_mcp.py                # optional: exercises the MCP server as a real client
```

Expected: the top hit is `ADR-016-cifrado-de-campo-postgres` with its heading and a score.

(On Linux/macOS use `python3 -m venv .venv && source .venv/bin/activate`.)

## Use it from an MCP client

Add this to your client's MCP configuration (path to the venv's Python must be absolute):

```json
{
  "mcpServers": {
    "ragdemo": {
      "command": "C:\\path\\to\\mcp-rag-demo\\.venv\\Scripts\\python.exe",
      "args": ["-m", "ragdemo.server"],
      "env": {
        "RAGDEMO_DSN": "postgresql://ragdemo:ragdemo@127.0.0.1:5433/ragdemo",
        "RAGDEMO_MIN_SCORE": "0.30"
      }
    }
  }
}
```

| Variable | Default | Meaning |
|---|---|---|
| `RAGDEMO_DSN` | docker-compose DSN | Postgres connection. |
| `RAGDEMO_MODEL` | `jinaai/jina-embeddings-v2-base-es` | Must match the model used to ingest. |
| `RAGDEMO_MIN_SCORE` | unset (no filter) | Drop hits below this similarity. `0.30` is suggested from the evaluation below; it is a rough estimate, not a guarantee. |

Tools: `search_docs(query, k=5)`, `get_doc(doc_id)`, `list_docs()`.
The server is stdio-based: stdout is the protocol, all logs go to stderr. It exits with an error
if there is no index or the model does not match.

## Evaluation

```powershell
python -m ragdemo.evaluate --k 3 --json eval\report-jina.json
```

18 questions in `eval/questions.jsonl`: 14 answerable (12 Spanish, 2 English) with the expected ADR,
and 4 out of scope. Metrics are at document level (did an expected ADR appear in the top-k?).
Result with `jinaai/jina-embeddings-v2-base-es`, run on 2026-11-12:

| Metric | Value |
|---|---|
| hit@1 / hit@3 / MRR | 1.00 / 1.00 / 1.00 (14/14) |
| Lowest top-1 score among answerable questions | 0.435 |
| Highest top-1 score among out-of-scope questions | 0.233 |

**How to read this honestly**

- Perfect scores on 14 questions show the pipeline works; they do not rank it against alternatives.
- The questions were written by the corpus author after reading the ADRs, so they share vocabulary
  with the documents. Real users phrase things worse. A short query ("dos agentes") scored 0.382 in
  a manual check, well below the long-form scores.
- The 4 out-of-scope questions are unrelated to the domain (recipes, geography, stock prices).
  There are no *hard negatives* (on-topic questions the corpus does not answer), so the `0.30`
  threshold is untested where it matters most.
- Only one embedding model was evaluated.

## Tests

```powershell
docker compose exec db createdb -U ragdemo ragdemo_test
$env:RAGDEMO_TEST_DSN = "postgresql://ragdemo:ragdemo@127.0.0.1:5433/ragdemo_test"
pip install pytest
pytest -q          # 63 tests
```

Without `RAGDEMO_TEST_DSN`, tests that need a database are skipped (on Linux/macOS with Python < 3.13,
`pip install -e ".[dev]"` brings an embedded Postgres via `pgserver`). Tests use a bag-of-words
`hash` embedder, so they exercise the pipeline, not semantic quality.

## Known limits

- Tiny corpus (8 documents, ~70 chunks); no re-ranking, no hybrid (keyword) search.
- Chunking is by Markdown headings with a character cap; no token-aware splitting.
- The stdio server is tested end-to-end with the official MCP Python client: in the test suite (hash
  embedder) and with the real model via `scripts/smoke_mcp.py` (Windows, Python 3.14). It has not
  been verified with a desktop MCP client such as Claude Desktop.
- The corpus was sanitized by rules plus manual review (names, phone numbers, client names).
  Indirect identifiers can survive any automated pass.
- Spanish-first: the default model is Spanish-oriented; English queries work (see q13, q14) but
  were only lightly evaluated.

## License

[MIT](LICENSE).
