"""RAG 工具：从《2024 美股市场表现》PDF 知识库检索相关内容。

把 Agents/RAG_Agent.py 的检索能力封装成 DataAgent 的一个工具：
  - 复用 Agents/ 目录下已持久化的 Chroma 向量库（collection=stock_market），
    避免每次启动都重新 embedding；
  - 向量库不存在时，从 PDF 重建（首次需要远程 embedding 服务可达）；
  - 懒加载 + 缓存：第一次调用才初始化，import 失败不拖垮整个 agent。

工具会自动注册进 tools 包（见 tools/__init__.py 的 pkgutil 扫描），无需改 agent 脚本。
"""
import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.tools import tool

load_dotenv()

# ---- 路径：相对本文件定位到仓库根，避免依赖运行时 cwd ----
_REPO_ROOT = Path(__file__).resolve().parents[2]
_AGENTS_DIR = _REPO_ROOT / "Agents"
_PDF_PATH = _AGENTS_DIR / "Stock_Market_Performance_2024.pdf"
_PERSIST_DIR = str(_AGENTS_DIR)  # Chroma 持久化目录（内含 chroma.sqlite3）
_COLLECTION = "stock_market"

# ---- 远程 Ollama（embedding 模型需与建库时一致）----
_OLLAMA_BASE_URL = os.getenv("OLLAMA_HOST", "http://10.8.20.83:11435")
_EMBED_MODEL = "nomic-embed-text"

# 懒加载缓存：首次调用才建 embedding / 连 Chroma
_vectorstore = None


def _get_vectorstore():
    """懒加载 Chroma 向量库：优先复用已持久化的库，否则从 PDF 重建。"""
    global _vectorstore
    if _vectorstore is not None:
        return _vectorstore

    from langchain_chroma import Chroma
    from langchain_ollama import OllamaEmbeddings

    embeddings = OllamaEmbeddings(base_url=_OLLAMA_BASE_URL, model=_EMBED_MODEL)

    # 优先复用已存在的向量库（避免每次启动都重新 embedding 全部文档）
    if (_AGENTS_DIR / "chroma.sqlite3").exists():
        try:
            _vectorstore = Chroma(
                collection_name=_COLLECTION,
                embedding_function=embeddings,
                persist_directory=_PERSIST_DIR,
            )
            return _vectorstore
        except Exception:
            # 复用失败（版本不兼容 / 损坏）则退回重建
            _vectorstore = None

    # 向量库不存在 → 从 PDF 重建
    if not _PDF_PATH.exists():
        raise RuntimeError(f"PDF 文件不存在: {_PDF_PATH}")

    from langchain_community.document_loaders import PyPDFLoader
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    pages = PyPDFLoader(str(_PDF_PATH)).load()
    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
    docs = splitter.split_documents(pages)

    _vectorstore = Chroma.from_documents(
        documents=docs,
        embedding=embeddings,
        persist_directory=_PERSIST_DIR,
        collection_name=_COLLECTION,
    )
    return _vectorstore


@tool
def search_stock_market(query: str) -> str:
    """从《2024 美股市场表现》文档库中检索相关内容。

    当用户询问 2024 年美股 / 股票市场表现相关的问题（如指数涨跌、板块表现、
    各季度行情、涨跌幅等）时，调用本工具从 PDF 中检索相关片段作为回答依据。
    与生产数据库无关的问题请勿调用本工具。

    Args:
        query: 检索查询，用自然语言描述想查找的内容，如 "标普500 2024 年涨幅"。

    Returns:
        检索到的文档片段（按相关性排序，带 Document 编号）；无结果时返回说明。
    """
    try:
        vectorstore = _get_vectorstore()
    except RuntimeError as e:
        return f"RAG 知识库不可用: {e}"

    try:
        retriever = vectorstore.as_retriever(
            search_type="similarity",
            search_kwargs={"k": 5},
        )
        docs = retriever.invoke(query)
    except Exception as e:
        return f"检索失败: {e}"

    if not docs:
        return "在《2024 美股市场表现》文档中没有找到相关内容。"

    results = [f"Document {i + 1}:\n{doc.page_content}" for i, doc in enumerate(docs)]
    return "\n\n".join(results)
