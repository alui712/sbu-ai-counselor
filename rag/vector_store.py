from dotenv import load_dotenv
load_dotenv()

import json
import os
import re
import time
from pathlib import Path

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.tools import tool
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

DATA_PATH = Path(__file__).resolve().parent.parent / "sbu_courses.json"
PROGRAMS_PATH = Path(__file__).resolve().parent.parent / "sbu_programs.json"
CHROMA_DIR = Path(__file__).resolve().parent.parent / "chroma_db"
COLLECTION_NAME = "sbu_courses"
PROGRAMS_COLLECTION_NAME = "sbu_programs_collection"
EMBED_BATCH_SIZE = 20
EMBED_BATCH_SLEEP_SECONDS = 10

_vector_store: Chroma | None = None
_programs_vector_store: Chroma | None = None


def _normalize_code(code: str) -> str:
    cleaned = code.replace("\xa0", " ").strip().upper()
    return re.sub(r"\s+", " ", cleaned)


def _format_course_text(info: dict) -> str:
    """Combine description, credits, and SBCs into a single embeddable string."""
    sbcs = info.get("sbcs") or []
    sbc_str = ", ".join(sbcs) if sbcs else "None"
    description = (info.get("description") or "").strip()
    credits = (info.get("credits") or "").strip() or "Not specified"
    return (
        f"Description: {description}\n"
        f"Credits: {credits}\n"
        f"SBCs: {sbc_str}"
    )


def _make_embeddings() -> GoogleGenerativeAIEmbeddings:
    return GoogleGenerativeAIEmbeddings(
        model="models/gemini-embedding-2",
        google_api_key=os.environ.get("GOOGLE_API_KEY"),
    )


def initialize_vector_store() -> Chroma:
    """Load sbu_courses.json, embed course texts in batches, and persist to ./chroma_db."""
    global _vector_store

    with open(DATA_PATH, encoding="utf-8") as f:
        courses = json.load(f)

    texts: list[str] = []
    metadatas: list[dict] = []
    ids: list[str] = []

    for raw_code, info in courses.items():
        course_code = _normalize_code(raw_code)
        texts.append(f"Course: {course_code}\n{_format_course_text(info)}")
        metadatas.append(
            {
                "course_code": course_code,
                "full_title": info.get("full_title", course_code),
                "department": info.get("department", ""),
            }
        )
        ids.append(course_code)

    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    embeddings = _make_embeddings()

    # Create an empty persistent store, then add texts in small batches.
    store = Chroma(
        collection_name=COLLECTION_NAME,
        embedding_function=embeddings,
        persist_directory=str(CHROMA_DIR),
    )

    total = len(texts)
    for start in range(0, total, EMBED_BATCH_SIZE):
        end = min(start + EMBED_BATCH_SIZE, total)
        store.add_texts(
            texts=texts[start:end],
            metadatas=metadatas[start:end],
            ids=ids[start:end],
        )
        if end < total:
            time.sleep(EMBED_BATCH_SLEEP_SECONDS)

    _vector_store = store
    return store


def _slug_id(value: str) -> str:
    """Make a Chroma-safe document id fragment."""
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")[:80]


def build_programs_vector_store():
    """Load sbu_programs.json, chunk requirements, and index them in Chroma.

    Creates/updates the ``sbu_programs_collection`` collection and returns a
    retriever bound to that collection.
    """
    global _programs_vector_store

    with open(PROGRAMS_PATH, encoding="utf-8") as f:
        programs = json.load(f)

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=150,
        separators=["\n\n", "\n", ". ", " ", ""],
    )

    documents: list[Document] = []
    ids: list[str] = []

    for program in programs:
        program_name = (program.get("program_name") or "").strip()
        program_type = (program.get("type") or "").strip()
        requirements = (program.get("requirements_text") or "").strip()
        if not program_name or not requirements:
            continue

        chunks = splitter.split_text(requirements)
        for i, chunk in enumerate(chunks):
            documents.append(
                Document(
                    page_content=(
                        f"Program: {program_name}\n"
                        f"Type: {program_type}\n"
                        f"Requirements:\n{chunk}"
                    ),
                    metadata={
                        "program_name": program_name,
                        "type": program_type,
                        "chunk_index": i,
                    },
                )
            )
            ids.append(f"{_slug_id(program_name)}_{_slug_id(program_type)}_{i}")

    if not documents:
        raise RuntimeError(
            f"No program requirement chunks found in {PROGRAMS_PATH}"
        )

    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    embeddings = _make_embeddings()

    store = Chroma(
        collection_name=PROGRAMS_COLLECTION_NAME,
        embedding_function=embeddings,
        persist_directory=str(CHROMA_DIR),
    )

    total = len(documents)
    for start in range(0, total, EMBED_BATCH_SIZE):
        end = min(start + EMBED_BATCH_SIZE, total)
        batch_docs = documents[start:end]
        store.add_documents(
            documents=batch_docs,
            ids=ids[start:end],
        )
        if end < total:
            time.sleep(EMBED_BATCH_SLEEP_SECONDS)

    _programs_vector_store = store
    return store.as_retriever()


def _get_vector_store() -> Chroma:
    """Return the persistent Chroma store, initializing it if needed."""
    global _vector_store
    if _vector_store is not None:
        return _vector_store

    if CHROMA_DIR.exists() and any(CHROMA_DIR.iterdir()):
        _vector_store = Chroma(
            collection_name=COLLECTION_NAME,
            embedding_function=_make_embeddings(),
            persist_directory=str(CHROMA_DIR),
        )
        return _vector_store

    return initialize_vector_store()


@tool
def query_catalog(query: str) -> str:
    """Search the SBU course catalog for policies and descriptions.

    Performs a similarity search over embedded course bulletin text and returns
    the top 3 matching course descriptions / catalog policies.
    """
    store = _get_vector_store()
    results = store.similarity_search(query, k=3)

    if not results:
        return "No matching courses found in the catalog."

    blocks: list[str] = []
    for i, doc in enumerate(results, start=1):
        code = doc.metadata.get("course_code", "Unknown")
        title = doc.metadata.get("full_title", code)
        blocks.append(f"[{i}] {title} ({code})\n{doc.page_content}")

    return "\n\n".join(blocks)
