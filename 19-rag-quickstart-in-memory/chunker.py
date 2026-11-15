# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Text chunking strategies for RAG pipelines — no external frameworks required.
Source: 02_chunking_strategies.py

The three patterns here cover 95% of real-world RAG chunking needs.
Pick the strategy based on your document structure and retrieval precision needs.
"""


def fixed_size(text: str, size: int = 200) -> list[str]:
    """
    Split text into fixed-size character windows.

    Simplest approach. Fast and predictable storage. The downside: it cuts
    mid-sentence, so retrieved chunks can start or end with broken context.
    Use only when documents have no meaningful structure (e.g., raw logs).
    """
    return [text[i: i + size] for i in range(0, len(text), size)]


def overlapping(text: str, size: int = 500, overlap: int = 100) -> list[str]:
    """
    Sliding-window chunking with character overlap between adjacent chunks.

    The overlap repairs boundary damage: a sentence cut at the end of chunk N
    appears complete at the start of chunk N+1. This is critical for RAG
    when answers straddle a chunk boundary.

    Storage cost: ~size/(size-overlap) x raw text (e.g., 20% overhead for
    size=500, overlap=100). Worth it for most retrieval use cases.
    """
    step = size - overlap
    chunks = []
    for i in range(0, len(text), step):
        chunk = text[i: i + size]
        if chunk.strip():
            chunks.append(chunk)
    return chunks


def by_separator(text: str, separator: str = "\n\n") -> list[str]:
    """
    Split text on a structural separator (default: blank line = paragraph boundary).

    Respects author intent — each paragraph is typically one coherent idea,
    so these chunks are semantically coherent and score better in retrieval
    than fixed-size chunks.

    Downside: variable chunk sizes. A 5-page section becomes one giant chunk.
    Production pattern: separator first, then apply overlapping() as a fallback
    on chunks that exceed a max-size threshold.
    """
    return [part.strip() for part in text.split(separator) if part.strip()]


def by_separator_with_max(text: str, separator: str = "\n\n",
                           max_size: int = 1000, overlap: int = 100) -> list[str]:
    """
    Structure-aware chunking with a max-size safety net.

    Splits on separator first (semantic coherence). Any chunk that exceeds
    max_size is re-split using the overlapping strategy to prevent one huge
    chunk from dominating retrieval.

    This is the production-recommended default for well-structured documents
    like reports, knowledge base articles, and policy docs.
    """
    primary = by_separator(text, separator)
    result = []
    for chunk in primary:
        if len(chunk) <= max_size:
            result.append(chunk)
        else:
            # Fallback: overlapping split for oversized paragraphs.
            result.extend(overlapping(chunk, size=max_size, overlap=overlap))
    return result


def chunk_stats(name: str, chunks: list[str]) -> dict:
    """Return a summary dict of chunk statistics."""
    sizes = [len(c) for c in chunks]
    return {
        "strategy": name,
        "count": len(chunks),
        "min_chars": min(sizes),
        "avg_chars": sum(sizes) // len(sizes),
        "max_chars": max(sizes),
        "total_chars": sum(sizes),
    }


def print_chunk_report(name: str, chunks: list[str]) -> None:
    """Pretty-print chunk statistics for a given strategy."""
    stats = chunk_stats(name, chunks)
    print(f"\n{name}")
    print(f"  chunks : {stats['count']} | "
          f"min/avg/max chars: {stats['min_chars']}/{stats['avg_chars']}/{stats['max_chars']} | "
          f"total stored: {stats['total_chars']}")
    if chunks:
        print(f"  first  : {chunks[0][:80]!r}...")
