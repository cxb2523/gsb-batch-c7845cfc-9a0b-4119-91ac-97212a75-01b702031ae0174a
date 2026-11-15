# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
BM25 keyword search component.

BM25 (Okapi BM25) is the standard term-frequency/inverse-document-frequency
ranking algorithm used by search engines. It excels at exact token matching:
rare tokens like error codes (0x80070005), SKUs, version numbers, and proper
nouns are weighted heavily because they appear in few documents. This makes
BM25 a critical complement to dense vector search, which tends to blur
semantically similar but lexically distinct tokens.
"""

import numpy as np
from rank_bm25 import BM25Okapi


def build_bm25_index(docs: list[str]) -> BM25Okapi:
    """Tokenize documents and build a BM25Okapi index."""
    tokenized = [d.lower().split() for d in docs]
    index = BM25Okapi(tokenized)
    print(f"[BM25] Index built: {len(docs)} documents")
    return index


def bm25_rank(index: BM25Okapi, query: str, top_k: int | None = None) -> list[int]:
    """
    Rank all document indices by BM25 score for the given query.
    Returns a list of doc indices sorted by descending score (best first).
    If top_k is provided, returns only the top-k indices.
    """
    scores = index.get_scores(query.lower().split())
    ranked = list(np.argsort(scores)[::-1])
    return ranked[:top_k] if top_k else ranked
