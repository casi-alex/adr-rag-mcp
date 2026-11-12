"""Troceo de Markdown por encabezados, con la ruta de encabezados como contexto.

Reglas:
- Un trozo nunca cruza un encabezado (H1 a H3).
- Las secciones largas se parten por párrafos; un párrafo o tabla más larga que
  `max_chars` se parte por líneas, y una línea más larga se parte a la fuerza.
- Los "#" dentro de bloques de código (```) no cuentan como encabezado.
- Las secciones sin cuerpo (solo el encabezado) no generan trozo.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$")
FENCE_RE = re.compile(r"^\s*```")
NO_TITLE = "(sin título)"


@dataclass(frozen=True)
class Chunk:
    heading: str  # ruta de encabezados, p. ej. "ADR-016 > 2. Decisión > 2.1 Método"
    text: str

    @property
    def text_hash(self) -> str:
        return hashlib.sha256(f"{self.heading}\n{self.text}".encode("utf-8")).hexdigest()

    @property
    def embedding_input(self) -> str:
        """Lo que se manda al modelo: el encabezado da contexto al cuerpo."""
        return f"{self.heading}\n{self.text}"


def _split_long(piece: str, max_chars: int) -> list[str]:
    """Parte un bloque mayor a max_chars por líneas; líneas gigantes, a la fuerza."""
    out: list[str] = []
    current = ""
    for line in piece.split("\n"):
        while len(line) > max_chars:
            if current:
                out.append(current)
                current = ""
            out.append(line[:max_chars])
            line = line[max_chars:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > max_chars and current:
            out.append(current)
            current = line
        else:
            current = candidate
    if current:
        out.append(current)
    return out


def _pack(body: str, max_chars: int) -> list[str]:
    """Agrupa párrafos hasta max_chars por trozo."""
    paragraphs = [p.strip("\n") for p in re.split(r"\n\s*\n", body) if p.strip()]
    pieces: list[str] = []
    for p in paragraphs:
        if len(p) > max_chars:
            pieces.extend(_split_long(p, max_chars))
        else:
            pieces.append(p)
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        candidate = f"{current}\n\n{piece}" if current else piece
        if len(candidate) > max_chars and current:
            chunks.append(current)
            current = piece
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def chunk_markdown(text: str, max_chars: int = 1000) -> list[Chunk]:
    if max_chars < 100:
        raise ValueError("max_chars debe ser >= 100")

    chunks: list[Chunk] = []
    stack: list[str] = []
    body_lines: list[str] = []
    in_fence = False

    def flush() -> None:
        body = "\n".join(body_lines).strip()
        body_lines.clear()
        if not body:
            return
        path = " > ".join(stack) if stack else NO_TITLE
        for piece in _pack(body, max_chars):
            chunks.append(Chunk(heading=path, text=piece))

    for line in text.replace("\r\n", "\n").split("\n"):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            body_lines.append(line)
            continue
        m = None if in_fence else HEADING_RE.match(line)
        if m:
            flush()
            level = len(m.group(1))
            del stack[level - 1 :]
            stack.append(m.group(2))
        else:
            body_lines.append(line)
    flush()
    return chunks
