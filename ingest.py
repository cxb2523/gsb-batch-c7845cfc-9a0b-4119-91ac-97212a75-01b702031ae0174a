import json

from config import VECTOR_STORE_PATH, SCRAPED_DATA_PATH, EMBEDDING_MODEL


class IngestError(Exception):
    """Raised when the scraped data cannot be ingested."""


def load_scraped_records(path=SCRAPED_DATA_PATH):
    """Load and validate the JSON list produced by the scraper."""

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError as exc:
        raise IngestError(
            f"未找到抓取数据文件：{path}。请先在页面上运行站点采集。"
        ) from exc
    except OSError as exc:
        raise IngestError(f"无法读取抓取数据文件 {path}：{exc}") from exc
    except json.JSONDecodeError as exc:
        raise IngestError(f"抓取数据文件不是合法的 JSON：{path}（{exc}）") from exc

    if not isinstance(data, list):
        raise IngestError(f"抓取数据文件 {path} 的格式不正确：应为页面对象列表。")
    if not data:
        raise IngestError(f"抓取数据文件 {path} 中没有页面内容，无法入库。")

    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise IngestError(f"抓取数据文件 {path} 的第 {index + 1} 条记录不是对象。")
        for key in ("url", "title", "content"):
            if key not in item:
                raise IngestError(
                    f"抓取数据文件 {path} 的第 {index + 1} 条记录缺少字段 “{key}”。"
                )
    return data


def ingest_data(
    data_path=SCRAPED_DATA_PATH,
    vector_store_path=VECTOR_STORE_PATH,
    embedding_model=EMBEDDING_MODEL,
):
    """Build the Chroma vector store from the scraped pages.

    Returns a small summary dict.  Failures are raised (``IngestError`` or the
    original exception) instead of being swallowed so the caller can show the
    reason, both from the CLI and from the Streamlit page.
    """

    # The data file is validated first: a missing/invalid file should be
    # reported clearly without depending on the vector-store stack.
    data = load_scraped_records(data_path)

    # Imported lazily: keeping these heavy components out of module import time
    # makes ``ingest.py`` importable (and errors reportable) even when the
    # LangChain/Ollama stack is not fully installed.
    try:
        from langchain_chroma import Chroma
        from langchain_ollama import OllamaEmbeddings
        from langchain_text_splitters import RecursiveCharacterTextSplitter
        from langchain_core.documents import Document
    except ImportError as exc:
        raise IngestError(
            f"缺少入库依赖（{exc}）。请先安装 requirements.txt 中的依赖。"
        ) from exc

    print("--- Starting data ingestion with metadata ---")

    documents = []
    for item in data:
        metadata = {
            "source": item["url"],
            "title": item["title"],
        }
        documents.append(Document(page_content=item["content"], metadata=metadata))

    text_splitter = RecursiveCharacterTextSplitter(chunk_size=750, chunk_overlap=100)
    docs = text_splitter.split_documents(documents)
    print(f"Split content into {len(docs)} metadata-rich chunks.")

    embeddings = OllamaEmbeddings(model=embedding_model)
    print("Creating embeddings...")

    Chroma.from_documents(
        documents=docs,
        embedding=embeddings,
        persist_directory=vector_store_path,
    )
    print(f"Successfully created enhanced vector store at {vector_store_path}")

    return {
        "pages": len(documents),
        "chunks": len(docs),
        "vector_store": vector_store_path,
    }


if __name__ == "__main__":
    try:
        summary = ingest_data()
    except Exception as exc:  # noqa: BLE001 - CLI should report and fail loudly
        print(f"Error: ingest failed: {exc}")
        raise SystemExit(1)
    print(
        f"Ingested {summary['pages']} pages into {summary['chunks']} chunks "
        f"at {summary['vector_store']}."
    )
