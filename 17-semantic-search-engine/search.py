# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Semantic search logic: cosine similarity over a pre-built numpy index
and catalog search with metadata passthrough.

Sources: 04_cosine_similarity_search.py, 05_semantic_search_catalog.py
"""

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from embedder import get_client, embed_documents, embed_query

CATALOG_CSV = Path(__file__).parent / "data" / "product_catalog.csv"


@dataclass
class SearchResult:
    rank: int
    score: float
    text: str
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Core cosine similarity search (source: 04_cosine_similarity_search.py)
# ---------------------------------------------------------------------------

class CosineSimilarityIndex:
    """
    In-memory brute-force semantic search index backed by a numpy matrix.

    For corpora under ~100K vectors this brute-force matrix multiply is
    often all you need — sub-millisecond, exact, and no ANN index required.
    At larger scales, swap the matmul for Vertex AI Vector Search.
    """

    def __init__(self):
        self._matrix: np.ndarray | None = None
        self._documents: list[str] = []

    def build(self, client, documents: list[str]) -> None:
        """Embed all documents and store the normalized matrix."""
        print(f"Building index over {len(documents)} documents...")
        self._matrix = embed_documents(client, documents)
        self._documents = documents
        print(f"Index ready. Matrix shape: {self._matrix.shape}")

    def search(self, client, query: str, top_k: int = 5) -> list[SearchResult]:
        """
        Embed the query and return the top-k documents by cosine similarity.
        Cosine similarity = dot product of two L2-normalized vectors.
        """
        if self._matrix is None:
            raise RuntimeError("Call build() before search().")

        q_vec = embed_query(client, query)
        # Both matrix and query are already normalized — matmul gives cosine scores.
        scores = self._matrix @ q_vec
        top_idx = np.argsort(scores)[::-1][:top_k]

        return [
            SearchResult(rank=rank, score=float(scores[i]), text=self._documents[i])
            for rank, i in enumerate(top_idx, start=1)
        ]


# ---------------------------------------------------------------------------
# Catalog search with metadata (source: 05_semantic_search_catalog.py)
# ---------------------------------------------------------------------------

class ProductCatalogSearch:
    """
    Semantic search over a product catalog CSV.

    Builds one embedding per unique product (deduplicated from the catalog)
    so repeated products in a sales/order table are only embedded once.
    Metadata (id, category, price) is preserved alongside the score.
    """

    def __init__(self, csv_path: Path = CATALOG_CSV):
        self._csv_path = csv_path
        self._catalog_df: pd.DataFrame | None = None
        self._texts: list[str] = []
        self._matrix: np.ndarray | None = None

    def _build_search_texts(self, df: pd.DataFrame) -> list[str]:
        """
        Concatenate name + description + category into a single search string.
        Richer text → more signal for the embedding model.
        """
        return [
            f"{row['name']} — {row['description']} ({row['category']})"
            for _, row in df.iterrows()
        ]

    def build(self, client) -> None:
        """Load catalog CSV, deduplicate, and embed all products."""
        df = pd.read_csv(self._csv_path)
        self._catalog_df = df.drop_duplicates(subset=["id"]).reset_index(drop=True)
        self._texts = self._build_search_texts(self._catalog_df)

        print(f"Catalog loaded: {len(self._catalog_df)} products from {self._csv_path.name}")
        self._matrix = embed_documents(client, self._texts)
        print(f"Catalog index ready. Shape: {self._matrix.shape}")

    def search(self, client, query: str, top_k: int = 5) -> list[SearchResult]:
        """
        Run a natural-language query over the catalog.
        Returns SearchResult objects with price, category, and product id in metadata.
        """
        if self._matrix is None or self._catalog_df is None:
            raise RuntimeError("Call build() before search().")

        q_vec = embed_query(client, query)
        scores = self._matrix @ q_vec
        top_idx = np.argsort(scores)[::-1][:top_k]

        results = []
        for rank, i in enumerate(top_idx, start=1):
            row = self._catalog_df.iloc[i]
            results.append(SearchResult(
                rank=rank,
                score=float(scores[i]),
                text=self._texts[i],
                metadata={
                    "id": int(row["id"]),
                    "category": row["category"],
                    "price": float(row["price"]),
                },
            ))
        return results


def print_results(results: list[SearchResult], query: str) -> None:
    """Pretty-print ranked search results."""
    print(f"\nQuery : {query!r}")
    print("-" * 70)
    for r in results:
        meta = ""
        if r.metadata:
            cat = r.metadata.get("category", "")
            price = r.metadata.get("price", "")
            meta = f"  [category={cat}, price=${price}]"
        print(f"  {r.rank}. [{r.score:.4f}] {r.text[:75]}")
        if meta:
            print(f"          {meta}")
