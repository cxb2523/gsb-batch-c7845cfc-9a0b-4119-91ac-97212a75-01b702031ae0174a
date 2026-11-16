import os

import streamlit as st
from bs4 import BeautifulSoup

from config import (
    APP_TITLE,
    APP_ICON,
    ASSISTANT_NAME,
    ASSISTANT_INTRO,
    INPUT_PLACEHOLDER,
    VECTOR_STORE_PATH,
    EMBEDDING_MODEL,
    LLM_MODEL,
    START_URL,
    SCRAPED_DATA_PATH,
)
from crawler import (
    CrawlRunner,
    CrawlSession,
    MAX_PAGES,
    STATUS_COMPLETED,
    STATUS_STOPPED,
)
from ingest import ingest_data

# --- Page Configuration ---
st.set_page_config(
    page_title=APP_TITLE,
    page_icon=APP_ICON,
    layout="wide",
)

# --- CSS for styling ---
st.markdown(
    """
    <style>
        .st-emotion-cache-1c7y2kd, .st-emotion-cache-4oy321 {
            border-radius: 10px;
        }
        header, footer {
            visibility: hidden;
        }
    </style>
""",
    unsafe_allow_html=True,
)


def clean_html(raw_html):
    soup = BeautifulSoup(raw_html, "html.parser")
    return soup.get_text(separator="\n", strip=True)


def format_chat_history(messages):
    history = []
    for msg in messages[-4:]:
        role = "Human" if msg["role"] == "user" else "Assistant"
        history.append(f"{role}: {msg['content']}")
    return "\n".join(history)


@st.cache_resource
def load_resources():
    print("Loading resources...")
    # Imported lazily so the crawl panel keeps working before the RAG stack
    # (Chroma/Ollama) is available.
    from langchain_chroma import Chroma
    from langchain_ollama import OllamaEmbeddings, ChatOllama

    embeddings = OllamaEmbeddings(model=EMBEDDING_MODEL)
    vector_store = Chroma(
        persist_directory=VECTOR_STORE_PATH, embedding_function=embeddings
    )

    retriever = vector_store.as_retriever(search_kwargs={"k": 10})
    llm = ChatOllama(model=LLM_MODEL)

    print("Resources loaded successfully.")
    return retriever, llm


# --- Prompt Template ---
PROMPT_TEMPLATE = f"""
You are {ASSISTANT_NAME}, a helpful AI assistant. Your persona is direct, knowledgeable, and professional.
You are an expert on the information contained in the context documents provided.

Use the following conversation history to understand the context of the new question.
Your primary task is to answer the user's new question directly using ONLY the information from the provided context documents.

- Do NOT mention the context in your response. Answer straightforwardly as an expert.
- If the context does not contain the information needed to answer the question, you MUST reply with the single sentence: "I do not have enough information to answer that question."

Conversation History:
{{chat_history}}

Context Documents:
{{context}}

New Question:
{{question}}
"""


# --- Crawl panel -----------------------------------------------------------
def _get_runner():
    return st.session_state.get("crawl_runner")


def _crawl_is_active(runner) -> bool:
    return runner is not None and runner.is_alive()


def _start_new_crawl():
    runner = _get_runner()
    if _crawl_is_active(runner):
        return
    session = CrawlSession(START_URL, max_pages=MAX_PAGES)
    st.session_state.crawl_runner = CrawlRunner(session, save_path=SCRAPED_DATA_PATH)
    st.session_state.crawl_runner.start()
    st.session_state.pop("ingest_message", None)
    st.session_state.pop("ingest_error", None)


def _render_intro():
    st.caption(
        f"将从 {START_URL} 开始，只抓同域链接，最多 {MAX_PAGES} 页，"
        "单页请求超时 10 秒，跳过 PDF/图片/压缩包等资源文件。"
    )
    if st.button("开始采集", key="crawl_start", type="primary"):
        _start_new_crawl()
        # A full rerun so the page switches to the live progress fragment.
        st.rerun()


def _render_progress(state, live):
    max_pages = state["max_pages"] or MAX_PAGES
    st.progress(
        min(state["visited"] / max_pages, 1.0),
        text=f"已访问 {state['visited']} / {max_pages} 页",
    )

    col1, col2, col3 = st.columns(3)
    col1.metric("已访问页数", state["visited"])
    col2.metric("成功", state["success"])
    col3.metric("失败", state["failure"])

    if not live:
        return
    if state["status"] == STATUS_STOPPED:
        st.info("正在停止…（等待当前页请求结束，最多 10 秒）")
    else:
        st.info(f"正在采集：{state['current_url'] or START_URL}")


def _render_results(runner, state):
    if runner.error:
        st.error(f"采集过程中出现异常：{runner.error}")
    if runner.save_error:
        st.error(f"抓取结果写入 {SCRAPED_DATA_PATH} 失败：{runner.save_error}")
    if state["start_url_error"]:
        st.error(f"START_URL 不可达：{START_URL}（{state['start_url_error']}）")

    if runner.blank_round:
        if os.path.exists(SCRAPED_DATA_PATH):
            st.warning(
                f"本轮没有抓到任何成功页，未写入 {SCRAPED_DATA_PATH}；"
                "已有数据文件保持原内容不变。"
            )
        else:
            st.warning(
                f"本轮没有抓到任何成功页，未生成 {SCRAPED_DATA_PATH}。"
            )
        if state["start_url_error"]:
            st.caption("请确认 START_URL 可访问后重新开始采集。")
    elif runner.saved:
        st.caption(f"已将 {state['success']} 个成功页写入 {SCRAPED_DATA_PATH}。")

    if state["reached_page_limit"]:
        st.info(f"已达到 {state['max_pages']} 页上限，不再发现新页面。")

    st.subheader("本轮结果")
    st.write(
        f"成功 **{state['success']}** 页 / 失败 **{state['failure']}** 页 / "
        f"总访问 **{state['visited']}** 页"
    )

    if state["successes"]:
        st.markdown("**成功页**")
        st.dataframe(
            [
                {"URL": url, "标题": title, "正文长度": length}
                for url, title, length in state["successes"]
            ],
            width="stretch",
            hide_index=True,
        )

    if state["failures"]:
        st.markdown("**失败页**")
        for url, error in state["failures"]:
            st.markdown(f"- `{url}` — {error}")
    elif state["visited"]:
        st.caption("没有失败页。")


def _render_ingest():
    if st.button(
        "将抓取结果入库到向量库",
        key="ingest_button",
        width="stretch",
    ):
        with st.spinner("正在读取抓取结果并构建向量库…"):
            try:
                summary = ingest_data(
                    data_path=SCRAPED_DATA_PATH,
                    vector_store_path=VECTOR_STORE_PATH,
                    embedding_model=EMBEDDING_MODEL,
                )
            except Exception as exc:  # noqa: BLE001 - show the reason, don't swallow
                st.session_state.pop("ingest_message", None)
                st.session_state.ingest_error = f"入库失败：{type(exc).__name__}: {exc}"
            else:
                st.session_state.pop("ingest_error", None)
                load_resources.clear()
                st.session_state.ingest_message = (
                    f"入库成功：{summary['pages']} 个页面 → "
                    f"{summary['chunks']} 个文本块，已写入 {summary['vector_store']}。"
                )
                # Full rerun so the chat area picks up the freshly built store.
                st.rerun()

    ingest_error = st.session_state.get("ingest_error")
    if ingest_error:
        st.error(ingest_error)
    ingest_message = st.session_state.get("ingest_message")
    if ingest_message:
        st.success(ingest_message)


@st.fragment(run_every=1)
def crawl_panel_running():
    """Live view used while the crawl worker is running.

    The fragment polls the background worker once per second, so the
    visited-page count and the current URL update live and the "stop" button
    stays responsive.
    """

    runner = _get_runner()
    if runner is None or not runner.is_alive():
        # The worker ended (natural completion or a stop that just landed):
        # do a full rerun so the idle panel replaces this polling one.
        st.rerun()

    state = runner.session.snapshot()
    _render_progress(state, live=True)

    if st.button(
        "停止采集",
        key="crawl_stop",
        type="primary",
        disabled=state["status"] == STATUS_STOPPED,
    ):
        runner.stop()
        st.rerun()


def _has_scraped_data():
    """True when there is something on disk that ingest could read."""

    try:
        return os.path.getsize(SCRAPED_DATA_PATH) > 0
    except OSError:
        return False


@st.fragment
def crawl_panel_idle():
    """Start / resume / result view used when no crawl is running."""

    runner = _get_runner()
    if runner is None:
        _render_intro()
        if _has_scraped_data():
            _render_ingest()
        return

    state = runner.session.snapshot()
    _render_progress(state, live=False)
    _render_results(runner, state)

    col_resume, col_restart = st.columns(2)
    if state["status"] == STATUS_STOPPED:
        can_resume = state["pending"] > 0 and not state["reached_page_limit"]
        if col_resume.button(
            "继续采集",
            key="crawl_resume",
            type="primary",
            disabled=not can_resume,
        ):
            runner.start()
            st.rerun()
        if not can_resume:
            st.caption("没有待访问页面（或已达到页数上限），无需继续。")
    elif state["status"] == STATUS_COMPLETED:
        if col_restart.button("开始新一轮采集", key="crawl_restart"):
            _start_new_crawl()
            st.rerun()

    if state["success"] or _has_scraped_data():
        _render_ingest()


# --- UI ---
st.title("How may I assist you?")

with st.container(border=True):
    st.subheader("站点采集")
    if _crawl_is_active(_get_runner()):
        crawl_panel_running()
    else:
        crawl_panel_idle()

try:
    retriever, llm = load_resources()
    resources_ready = True
except Exception as e:
    retriever = llm = None
    resources_ready = False
    st.error(
        "知识库加载失败：请先完成站点采集与入库，并确认 Ollama 服务可用。"
        f"\n\n原因：{e}"
    )

if resources_ready:
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import StrOutputParser

    prompt = ChatPromptTemplate.from_template(PROMPT_TEMPLATE)
    generation_chain = prompt | llm | StrOutputParser()
else:
    generation_chain = None

if "messages" not in st.session_state:
    st.session_state.messages = [{"role": "assistant", "content": ASSISTANT_INTRO}]
if "processing" not in st.session_state:
    st.session_state.processing = False

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if st.session_state.processing and generation_chain is not None:
    with st.chat_message("assistant"):
        with st.spinner("Typing..."):
            try:
                user_question = st.session_state.messages[-1]["content"]
                chat_history = format_chat_history(st.session_state.messages[:-1])

                retrieved_docs = retriever.invoke(user_question)

                raw_response = generation_chain.invoke(
                    {
                        "context": retrieved_docs,
                        "question": user_question,
                        "chat_history": chat_history,
                    }
                )

                clean_response = clean_html(raw_response)

                st.markdown(clean_response)
                st.session_state.messages.append(
                    {"role": "assistant", "content": clean_response}
                )
            except Exception as e:
                error_message = (
                    "Sorry, I encountered an error. Please try your question again."
                )
                st.error(error_message)
                st.session_state.messages.append(
                    {"role": "assistant", "content": error_message}
                )
                print(f"Error during RAG chain invocation: {e}")

            st.session_state.processing = False
            st.rerun()
elif st.session_state.processing:
    st.session_state.processing = False

if resources_ready:
    user_question = st.chat_input(
        INPUT_PLACEHOLDER, disabled=st.session_state.processing
    )
else:
    st.chat_input("请先完成站点采集与入库后再提问。", disabled=True)
    user_question = None

if user_question:
    st.session_state.messages.append({"role": "user", "content": user_question})
    st.session_state.processing = True
    st.rerun()
