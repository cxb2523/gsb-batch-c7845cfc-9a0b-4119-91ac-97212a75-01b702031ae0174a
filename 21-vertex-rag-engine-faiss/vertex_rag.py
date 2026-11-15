# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Vertex AI RAG Engine — fully managed corpus-based RAG.

NOTE: This creates a managed corpus RESOURCE in your project that is billable.
The run() function deletes the corpus in the finally block, but verify via the
console if the script fails midway to avoid stray corpora.

Region note: us-central1/us-east1 Spanner-mode RAG Engine is allowlist-only
for new projects. europe-west4 works out of the box. Set RAG_ENGINE_LOCATION
env var to change the region.
"""

import os

import vertexai
from google import genai
from google.genai import types
from vertexai import rag

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
LOCATION = os.environ.get("RAG_ENGINE_LOCATION", "europe-west4")
GENERATION_MODEL = "gemini-2.5-flash"

# Any GCS file works; this public PDF is used as the demo import source.
IMPORT_URI = "gs://cloud-samples-data/generative-ai/pdf/2403.05530.pdf"


def create_corpus(display_name: str = "demo-rag-corpus") -> object:
    """Create a managed RAG corpus with text-embedding-005 as the embedding model."""
    vertexai.init(project=PROJECT_ID, location=LOCATION)
    corpus = rag.create_corpus(
        display_name=display_name,
        backend_config=rag.RagVectorDbConfig(
            rag_embedding_model_config=rag.RagEmbeddingModelConfig(
                vertex_prediction_endpoint=rag.VertexPredictionEndpoint(
                    publisher_model="publishers/google/models/text-embedding-005"
                )
            )
        ),
    )
    print(f"[Vertex RAG] Created corpus: {corpus.name}")
    return corpus


def import_documents(corpus_name: str, gcs_uri: str) -> None:
    """Import a GCS document into the corpus. RAG Engine chunks and embeds server-side."""
    rag.import_files(
        corpus_name,
        [gcs_uri],
        transformation_config=rag.TransformationConfig(
            chunking_config=rag.ChunkingConfig(chunk_size=512, chunk_overlap=100)
        ),
    )
    print(f"[Vertex RAG] Imported: {gcs_uri}")


def retrieve(corpus_name: str, question: str, top_k: int = 3) -> list:
    """Direct retrieval — returns scored context chunks without generation."""
    retrieval = rag.retrieval_query(
        rag_resources=[rag.RagResource(rag_corpus=corpus_name)],
        text=question,
        rag_retrieval_config=rag.RagRetrievalConfig(top_k=top_k),
    )
    contexts = retrieval.contexts.contexts
    print(f"[Vertex RAG] Retrieved {len(contexts)} chunks for: {question!r}")
    for ctx in contexts:
        print(f"  [score={ctx.score:.3f}] {ctx.text[:80]}...")
    return contexts


def generate_grounded_answer(corpus_name: str, question: str) -> str:
    """Use the corpus as a Gemini tool — model retrieves and answers automatically."""
    client = genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION)
    rag_tool = types.Tool(
        retrieval=types.Retrieval(
            vertex_rag_store=types.VertexRagStore(
                rag_resources=[types.VertexRagStoreRagResource(rag_corpus=corpus_name)]
            )
        )
    )
    response = client.models.generate_content(
        model=GENERATION_MODEL,
        contents=question,
        config=types.GenerateContentConfig(tools=[rag_tool]),
    )
    print(f"[Vertex RAG] Grounded answer:\n{response.text}")
    return response.text


def delete_corpus(corpus_name: str) -> None:
    """Delete the corpus. Always call this after a demo to avoid billed stray resources."""
    rag.delete_corpus(corpus_name)
    print(f"[Vertex RAG] Deleted corpus: {corpus_name}")


def run(question: str = "What are the key capabilities described in this document?") -> str:
    """
    End-to-end demo: create corpus → import → retrieve → generate → cleanup.
    Returns the grounded answer text.
    """
    vertexai.init(project=PROJECT_ID, location=LOCATION)
    corpus = create_corpus()
    try:
        import_documents(corpus.name, IMPORT_URI)
        retrieve(corpus.name, question)
        answer = generate_grounded_answer(corpus.name, question)
        return answer
    finally:
        delete_corpus(corpus.name)
