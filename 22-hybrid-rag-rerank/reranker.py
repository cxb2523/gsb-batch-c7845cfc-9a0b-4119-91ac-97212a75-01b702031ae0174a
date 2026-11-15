# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Reranking components: Reciprocal Rank Fusion (RRF) + Gemini cross-encoder reranker.

Two-stage retrieval pattern:
  Stage 1 — cheap retrievers (BM25 + dense vector) produce a candidate set.
  Stage 2 — expensive but accurate reranker scores only the small candidate set.

RRF (Reciprocal Rank Fusion) combines multiple ranked lists without score
calibration. Score for document d = sum over lists of 1 / (k + rank(d)).
The k=60 damping constant is the standard default (Cormack et al. 2009).

Gemini as a cross-encoder reranker: the model jointly encodes the query and
each candidate document, giving cross-attention over both — far more accurate
than bi-encoder (embedding) similarity, but ~10-100x more expensive per call.
Applying it only to the fused top-k (4-10 candidates) keeps cost negligible.
"""

import os
import re

from google import genai

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
GENERATION_MODEL = "gemini-2.5-flash"
RRF_K = 60   # standard RRF damping constant


def reciprocal_rank_fusion(rank_lists: list[list[int]], k: int = RRF_K) -> list[int]:
    """
    Fuse multiple ranked lists into a single ranking using RRF.
    rank_lists: list of lists of document indices, each sorted best-first.
    Returns a single list of document indices sorted by descending RRF score.
    """
    scores: dict[int, float] = {}
    for ranks in rank_lists:
        for rank, doc_id in enumerate(ranks):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores, key=lambda d: scores[d], reverse=True)


def gemini_rerank(client: object, query: str, docs: list[str], candidate_ids: list[int]) -> list[tuple[int, float]]:
    """
    Use Gemini as a cross-encoder reranker over a small candidate set.
    Returns a list of (doc_id, score) tuples sorted by descending relevance score.
    score is parsed from Gemini's 0-10 rating response.
    """
    numbered = "\n".join(
        f"{n}. {docs[i][:200]}" for n, i in enumerate(candidate_ids, 1)
    )
    prompt = (
        f"Query: {query}\n\n"
        f"Rate each document's relevance to the query from 0 to 10. "
        f"Reply with exactly one line per document in the format: '<number>: <score>'. "
        f"Do not include explanations.\n\n{numbered}"
    )
    resp = client.models.generate_content(model=GENERATION_MODEL, contents=prompt)
    raw = resp.text.strip()
    print(f"[Reranker] Gemini scores:\n{raw}")

    scored = []
    for line in raw.splitlines():
        m = re.match(r"(\d+)\s*:\s*([\d.]+)", line.strip())
        if m:
            num = int(m.group(1)) - 1   # convert 1-based to 0-based index
            score = float(m.group(2))
            if 0 <= num < len(candidate_ids):
                scored.append((candidate_ids[num], score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored
