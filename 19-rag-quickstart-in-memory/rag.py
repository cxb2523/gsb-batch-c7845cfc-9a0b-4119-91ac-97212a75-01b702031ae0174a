# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
In-memory RAG pipeline: chunk → embed → retrieve → generate.
Source: 01_simple_rag_in_memory.py

The canonical minimal RAG loop with zero vector database dependencies.
Every stage is visible and inspectable. Use this as the baseline before
adding vector databases, rerankers, or query reformulation.

Four stages:
  1. CHUNK   — split corpus into retrieval units
  2. EMBED   — one vector per chunk (RETRIEVAL_DOCUMENT task type)
  3. RETRIEVE — embed the question, rank by cosine, take top-K chunks
  4. GENERATE — stuff retrieved context into a grounded Gemini prompt
"""

import os

import numpy as np
from google import genai
from google.genai import types

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
EMBEDDING_MODEL = "gemini-embedding-001"
GENERATION_MODEL = "gemini-2.5-flash"


def get_client() -> genai.Client:
    """Return a configured Vertex AI genai client."""
    return genai.Client(vertexai=True, project=PROJECT_ID, location="global")


def build_index(client: genai.Client, chunks: list[str]) -> np.ndarray:
    """
    Embed a list of text chunks and return an L2-normalized float32 matrix.

    Uses RETRIEVAL_DOCUMENT task type — tuned for content-shaped passages.
    The returned matrix is pre-normalized so that matmul == cosine similarity.
    """
    response = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=chunks,
        config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"),
    )
    matrix = np.array([e.values for e in response.embeddings], dtype=np.float32)
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix


def retrieve(client: genai.Client, question: str,
             index: np.ndarray, chunks: list[str],
             top_k: int = 3) -> tuple[list[str], list[float]]:
    """
    Embed the question and return the top-K most relevant chunks with scores.

    Uses RETRIEVAL_QUERY task type — asymmetric task types improve recall
    because queries are intent-shaped while documents are content-shaped.
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

    return (
        [chunks[i] for i in top_idx],
        [float(scores[i]) for i in top_idx],
    )


def generate_answer(client: genai.Client, question: str, context_chunks: list[str]) -> str:
    """
    Generate a grounded answer from retrieved context chunks.

    The grounding instruction ("Answer using ONLY the context below") is
    what stops the model from free-styling with its training knowledge.
    Without it, the model ignores the context and hallucinates freely.
    """
    context = "\n\n".join(context_chunks)
    prompt = (
        "Answer using ONLY the context below. "
        "If the answer is not in the context, say 'Not found in the provided context.'\n\n"
        f"Context:\n{context}\n\n"
        f"Question: {question}"
    )
    response = client.models.generate_content(model=GENERATION_MODEL, contents=prompt)
    return response.text


def run_rag(client: genai.Client, chunks: list[str],
            question: str, top_k: int = 3) -> dict:
    """
    Full RAG pipeline for a single question.

    Returns a dict with keys: question, retrieved_chunks, scores, answer.
    """
    # STAGE 2 — EMBED corpus
    index = build_index(client, chunks)

    # STAGE 3 — RETRIEVE
    retrieved, scores = retrieve(client, question, index, chunks, top_k=top_k)

    # STAGE 4 — GENERATE
    answer = generate_answer(client, question, retrieved)

    return {
        "question": question,
        "retrieved_chunks": retrieved,
        "scores": scores,
        "answer": answer,
    }


def print_rag_result(result: dict) -> None:
    """Pretty-print a RAG result dict."""
    print(f"Question : {result['question']}")
    print("-" * 70)
    print(f"Retrieved chunks (top {len(result['retrieved_chunks'])}):")
    for chunk, score in zip(result["retrieved_chunks"], result["scores"]):
        print(f"  [{score:.4f}] {chunk[:80]}...")
    print("-" * 70)
    print(f"Answer:\n{result['answer']}")
