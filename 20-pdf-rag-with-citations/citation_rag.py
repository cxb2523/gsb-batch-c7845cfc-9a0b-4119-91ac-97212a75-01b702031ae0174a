# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
RAG pipeline with inline source citations.
Source: 07_rag_with_citations.py

Citations turn RAG answers from "trust me" into auditable output.
Every retrieved chunk carries a stable source id. The prompt forces
the model to cite [source-N] after every claim. Citations are then
parsed out and mapped back to the original chunks for verification.

Enterprise non-negotiable: hallucinated citations (model invents a source
id it was never given) are detected and flagged explicitly.
"""

import os
import re
from dataclasses import dataclass, field

import numpy as np
from google import genai
from google.genai import types

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
EMBEDDING_MODEL = "gemini-embedding-001"
GENERATION_MODEL = "gemini-2.5-flash"


@dataclass
class CitedAnswer:
    question: str
    answer: str
    sources_retrieved: dict   # {source_id: chunk_text}
    sources_cited: list[str]  # source ids actually used by the model
    hallucinated_ids: list[str] = field(default_factory=list)
    uncited_ids: list[str] = field(default_factory=list)


def get_client() -> genai.Client:
    """Return a configured Vertex AI genai client."""
    return genai.Client(vertexai=True, project=PROJECT_ID, location="global")


def build_index(client: genai.Client, chunks: list[str]) -> np.ndarray:
    """
    Embed all chunks and return a pre-normalized float32 matrix.
    RETRIEVAL_DOCUMENT task type optimizes the space for passage retrieval.
    """
    response = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=chunks,
        config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"),
    )
    matrix = np.array([e.values for e in response.embeddings], dtype=np.float32)
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix


def retrieve_with_ids(
    client: genai.Client,
    question: str,
    index: np.ndarray,
    chunks: list[str],
    top_k: int = 4,
) -> dict[str, str]:
    """
    Retrieve top-K chunks and assign stable source ids.

    Source ids use the ORIGINAL chunk index (e.g., source-3, source-11),
    not rank positions. This keeps citations stable even when retrieval
    order changes between runs — essential for audit trails.

    Returns a dict mapping source_id -> chunk_text.
    """
    response = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=[question],
        config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"),
    )
    q_vec = np.array(response.embeddings[0].values, dtype=np.float32)
    q_vec /= np.linalg.norm(q_vec)

    scores = index @ q_vec
    top_idx = np.argsort(scores)[::-1][:top_k]

    # Use the original chunk index as the citation key for stability.
    return {f"source-{i}": chunks[i] for i in top_idx}


def generate_with_citations(
    client: genai.Client,
    question: str,
    sources: dict[str, str],
) -> str:
    """
    Generate an answer with mandatory inline [source-N] citations.

    The citation instruction is the core of this pattern:
      - Forces the model to link every claim to a specific source
      - Prevents fabricating content not in the retrieved context
      - Makes hallucinated citations detectable (ids not in sources dict)
    """
    context_block = "\n".join(
        f"[{sid}] {text}" for sid, text in sources.items()
    )
    prompt = (
        "Answer the question using ONLY the sources provided below. "
        "After every factual claim, cite the supporting source inline as [source-N]. "
        "Do not cite sources you did not use. "
        "If the sources do not contain enough information to answer, say so.\n\n"
        f"Sources:\n{context_block}\n\n"
        f"Question: {question}"
    )
    response = client.models.generate_content(model=GENERATION_MODEL, contents=prompt)
    return response.text


def parse_citations(answer: str, sources: dict[str, str]) -> tuple[list[str], list[str], list[str]]:
    """
    Parse [source-N] citations from the model answer.

    Returns (cited_ids, hallucinated_ids, uncited_ids):
      - cited_ids: source ids the model used
      - hallucinated_ids: ids the model cited but were not in the retrieved set
      - uncited_ids: retrieved ids the model did not cite
    """
    cited_ids = sorted(set(re.findall(r"\[(source-\d+)\]", answer)))
    hallucinated = [sid for sid in cited_ids if sid not in sources]
    uncited = [sid for sid in sources if sid not in cited_ids]
    return cited_ids, hallucinated, uncited


def run_citation_rag(
    client: genai.Client,
    chunks: list[str],
    question: str,
    top_k: int = 4,
) -> CitedAnswer:
    """
    Full RAG pipeline with citations for a single question.

    Stages:
      1. Build or reuse embedding index
      2. Retrieve top-K chunks with stable source ids
      3. Generate answer with forced inline citations
      4. Parse and validate citations (detect hallucinated ids)

    Returns a CitedAnswer dataclass with all metadata for audit.
    """
    index = build_index(client, chunks)
    sources = retrieve_with_ids(client, question, index, chunks, top_k=top_k)
    answer = generate_with_citations(client, question, sources)
    cited, hallucinated, uncited = parse_citations(answer, sources)

    return CitedAnswer(
        question=question,
        answer=answer,
        sources_retrieved=sources,
        sources_cited=cited,
        hallucinated_ids=hallucinated,
        uncited_ids=uncited,
    )


def print_cited_answer(result: CitedAnswer) -> None:
    """Pretty-print a CitedAnswer with full citation audit trail."""
    print(f"Question : {result.question}")
    print("-" * 70)
    print(f"Answer:\n{result.answer}")
    print("-" * 70)
    print(f"Sources retrieved ({len(result.sources_retrieved)}):")
    for sid, text in result.sources_retrieved.items():
        cited_marker = "[CITED]" if sid in result.sources_cited else "[not cited]"
        print(f"  {cited_marker} [{sid}] {text[:80]}...")

    if result.hallucinated_ids:
        print()
        print("WARNING — Hallucinated citations (model invented these ids):")
        for sid in result.hallucinated_ids:
            print(f"  [{sid}] NOT in retrieved set")

    if result.uncited_ids:
        print()
        print(f"Retrieved but not cited: {', '.join(result.uncited_ids)}")
