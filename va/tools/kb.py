"""Per-tenant knowledge base: markdown/text files + keyword search.

The agent gets a `search_company_kb` tool; Gemini calls it for company-specific
questions and answers from the retrieved chunks. No embeddings or external
services - chunk paragraphs, score by query-token overlap (headings weigh
double), return the top matches. Files are re-read on every call, so edits
apply to the next call without a restart.

This is RAG-lite: good for hundreds of KB of text. If callers start asking
questions that paraphrase heavily ("money back" vs "refund"), the upgrade path
is embedding-based retrieval behind the same tool interface.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger


@dataclass
class KBChunk:
    text: str
    source: str
    heading: str = ""
    score: int = 0


_TOKEN_RE = re.compile(r"[a-z0-9\u0900-\u097f]+")
# Common words that carry no search signal
_STOP = {
    "the", "a", "an", "is", "are", "was", "were", "do", "does", "did", "i",
    "you", "we", "my", "your", "our", "of", "to", "for", "in", "on", "at",
    "and", "or", "can", "how", "what", "when", "where", "who", "kya", "hai",
}


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOP and len(t) > 1]


def load_chunks(kb_dir: Path) -> list[KBChunk]:
    """Parse .md/.txt files into paragraph chunks, carrying the current heading."""
    chunks: list[KBChunk] = []
    for path in sorted(list(kb_dir.glob("*.md")) + list(kb_dir.glob("*.txt"))):
        heading = path.stem
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("KB file unreadable: {} ({})", path, exc)
            continue
        for para in re.split(r"\n\s*\n", content):
            para = para.strip()
            if len(para) < 20:
                continue
            if para.startswith("#"):
                # markdown heading - becomes the context for following paragraphs
                heading = para.lstrip("#").strip()
                chunks.append(KBChunk(text=para, source=path.name, heading=heading))
                continue
            chunks.append(KBChunk(text=para, source=path.name, heading=heading))
    return chunks


def search(chunks: list[KBChunk], query: str, top_n: int = 3) -> list[KBChunk]:
    """Score chunks by query-token overlap; heading matches count double."""
    q_tokens = set(_tokens(query))
    if not q_tokens:
        return []
    for chunk in chunks:
        body_tokens = _tokens(chunk.text)
        heading_tokens = set(_tokens(chunk.heading))
        score = 0
        for token in q_tokens:
            if token in heading_tokens:
                score += 2
            score += body_tokens.count(token) > 0
        chunk.score = score
    hits = [c for c in chunks if c.score > 0]
    hits.sort(key=lambda c: c.score, reverse=True)
    return hits[:top_n]


def search_dir(kb_dir: Path, query: str, top_n: int = 3) -> list[KBChunk]:
    return search(load_chunks(kb_dir), query, top_n)


def format_results(hits: list[KBChunk]) -> dict:
    if not hits:
        return {
            "ok": True,
            "results": [],
            "hint": "No company information matched. Tell the caller you will check with a colleague and offer a callback.",
        }
    return {
        "ok": True,
        "results": [
            {"text": c.text, "topic": c.heading, "source": c.source}
            for c in hits
        ],
    }
