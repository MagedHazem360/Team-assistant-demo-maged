"""RAG ingestion job (ADR-0009): load → chunk → embed → upload to Azure AI Search.

Owns the **index definition** (``build_index``) so the retriever and the index never drift:
fields ``id`` (key), ``content`` (searchable), ``title`` (searchable — the first ``# `` heading,
else the file name; ADR-0015), ``source`` (filterable — the path relative to the ingested
folder), ``chunk_index``, and ``content_vector`` (HNSW vector profile,
``AZURE_AI_EMBEDDING_DIMENSIONS`` wide). ``ensure_index`` is idempotent
(``create_or_update_index``).

Run it from ``apps/api`` (needs the AI + search settings; the api identity or a dev key with
*Search Index Data Contributor* + *Search Service Contributor*)::

    uv run python -m app.ai.ingest ../../docs --prune         # dry run: count stale chunks
    uv run python -m app.ai.ingest ../../docs --prune --yes   # ... and delete them
    uv run python -m app.ai.ingest notes.md --chunk-size 800 --overlap 80

``--prune`` finds every chunk in the index that this run did not produce (renamed or deleted
documents, older path formats) and, with ``--yes``, deletes them. It needs the corpus folder
(not a file), never runs after a failed or empty upload, and refuses to delete more chunks than
the run keeps unless ``--force`` is given — it assumes the index holds this corpus only.

Batches are embedded with ``aembed_documents`` and uploaded with ``upload_documents``; the
report prints counts only — never content. Very large corpora deserve a real pipeline (an
indexer + skillset); this job is the starting point.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from langchain_core.embeddings import Embeddings

from app.ai.config import AISettings, get_ai_settings
from app.ai.tools.retrieve import (
    CONTENT_FIELD,
    ID_FIELD,
    SOURCE_FIELD,
    TITLE_FIELD,
    VECTOR_FIELD,
    build_search_client,
    build_search_credential,
)
from app.config import load_local_env
from app.logging_config import configure_logging, get_logger

CHUNK_INDEX_FIELD = "chunk_index"
DEFAULT_CHUNK_SIZE = 1000
DEFAULT_OVERLAP = 100
VECTOR_PROFILE = "vector-profile"
HNSW_CONFIG = "hnsw"
TEXT_SUFFIXES = (".md", ".txt")


@dataclass(frozen=True)
class IngestDocument:
    source: str
    text: str
    title: str = ""


@dataclass
class IngestReport:
    documents: int = 0
    chunks: int = 0
    uploaded: int = 0
    failed: int = 0
    stale: int = 0  # chunks in the index this run did not produce (found by --prune)
    pruned: int = 0
    prune_applied: bool = False  # --prune --yes (deletes), as opposed to a dry run
    # ids of every chunk this run produced (what --prune keeps); never printed or logged
    chunk_ids: set[str] = field(default_factory=set, repr=False)


def chunk_text(
    text: str, size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_OVERLAP
) -> list[str]:
    """Fixed-size character windows with overlap; whitespace-only chunks are dropped."""
    if size <= 0:
        raise ValueError("size must be > 0")
    if overlap < 0 or overlap >= size:
        raise ValueError("overlap must be >= 0 and < size")
    text = text.strip()
    if not text:
        return []
    chunks: list[str] = []
    step = size - overlap
    for start in range(0, len(text), step):
        piece = text[start : start + size].strip()
        if piece:
            chunks.append(piece)
        if start + size >= len(text):
            break
    return chunks


def chunk_id(source: str, index: int) -> str:
    """Stable, key-safe id (re-ingesting the same source overwrites the same documents)."""
    return hashlib.sha1(f"{source}#{index}".encode()).hexdigest()


def build_index(name: str, dimensions: int) -> Any:
    """The ``SearchIndex`` the retriever expects (keyword + vector fields, HNSW profile)."""
    from azure.search.documents.indexes.models import (
        HnswAlgorithmConfiguration,
        SearchableField,
        SearchField,
        SearchFieldDataType,
        SearchIndex,
        SimpleField,
        VectorSearch,
        VectorSearchProfile,
    )

    fields = [
        SimpleField(name=ID_FIELD, type=SearchFieldDataType.String, key=True, filterable=True),
        SearchableField(name=CONTENT_FIELD),
        SearchableField(name=TITLE_FIELD),
        SimpleField(name=SOURCE_FIELD, type=SearchFieldDataType.String, filterable=True),
        SimpleField(
            name=CHUNK_INDEX_FIELD, type=SearchFieldDataType.Int32, filterable=True, sortable=True
        ),
        SearchField(
            name=VECTOR_FIELD,
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=dimensions,
            vector_search_profile_name=VECTOR_PROFILE,
        ),
    ]
    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name=HNSW_CONFIG)],
        profiles=[
            VectorSearchProfile(name=VECTOR_PROFILE, algorithm_configuration_name=HNSW_CONFIG)
        ],
    )
    return SearchIndex(name=name, fields=fields, vector_search=vector_search)


def build_index_client(settings: AISettings | None = None) -> Any:
    from azure.search.documents.indexes import SearchIndexClient

    s = settings or get_ai_settings()
    s.require_search()
    return SearchIndexClient(endpoint=s.search_endpoint, credential=build_search_credential(s))


def ensure_index(index_client: Any, index: Any) -> Any:
    """Create or update the index (idempotent)."""
    return index_client.create_or_update_index(index)


TITLE_MAX_LENGTH = 200  # matches conversations.title; a citation title is a label, not content


def document_title(text: str, fallback: str) -> str:
    """The first Markdown ``# `` heading, else ``fallback``; capped at ``TITLE_MAX_LENGTH``.

    Skips YAML front matter and fenced code blocks; a line indented 4+ spaces is code, not a
    heading; closing hashes (``# Title #``) are dropped.
    """
    lines = text.lstrip("﻿").splitlines()
    if lines and lines[0].strip() == "---":  # YAML front matter
        end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
        lines = lines[end + 1 :] if end is not None else lines
    fence: str | None = None
    for line in lines:
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        marker = stripped[:3]
        if marker in ("```", "~~~"):
            if fence is None:
                fence = marker
            elif marker == fence:
                fence = None
            continue
        if fence is None and indent <= 3 and stripped.startswith("# "):
            title = stripped[2:].strip().rstrip("#").strip()
            if title:
                return title[:TITLE_MAX_LENGTH]
    return fallback[:TITLE_MAX_LENGTH]


def load_path(path: Path) -> list[IngestDocument]:
    """Every ``.md``/``.txt`` file under ``path`` (or the file itself) as a document.

    ``source`` is relative to the ingested folder with POSIX separators (``architecture/x.md``),
    so the same document gets the same path — and the same chunk ids — wherever it is run from.
    """
    root = path.parent if path.is_file() else path
    files = (
        [path]
        if path.is_file()
        else sorted(p for p in path.rglob("*") if p.suffix.lower() in TEXT_SUFFIXES)
    )
    docs: list[IngestDocument] = []
    for file in files:
        if file.suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = file.read_text(encoding="utf-8")
        title = document_title(text, file.name) if file.suffix.lower() == ".md" else file.name
        source = file.relative_to(root).as_posix()
        docs.append(IngestDocument(source=source, text=text, title=title))
    return docs


def _batches(items: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


async def ingest_documents(
    documents: Iterable[IngestDocument],
    *,
    embeddings: Embeddings,
    search_client: Any,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
    batch_size: int = 64,
) -> IngestReport:
    """Chunk, embed and upload. Returns counts only."""
    log = get_logger("app.ai.ingest")
    report = IngestReport()
    records: list[dict[str, Any]] = []
    for doc in documents:
        report.documents += 1
        for i, piece in enumerate(chunk_text(doc.text, chunk_size, overlap)):
            records.append(
                {
                    ID_FIELD: chunk_id(doc.source, i),
                    CONTENT_FIELD: piece,
                    SOURCE_FIELD: doc.source,
                    TITLE_FIELD: doc.title or Path(doc.source).name,
                    CHUNK_INDEX_FIELD: i,
                }
            )
    report.chunks = len(records)
    report.chunk_ids = {r[ID_FIELD] for r in records}
    for batch in _batches(records, batch_size):
        vectors = await embeddings.aembed_documents([r[CONTENT_FIELD] for r in batch])
        payload = [{**r, VECTOR_FIELD: v} for r, v in zip(batch, vectors, strict=True)]
        results = await asyncio.to_thread(search_client.upload_documents, documents=payload)
        ok = sum(1 for r in results if getattr(r, "succeeded", False))
        report.uploaded += ok
        report.failed += len(payload) - ok
        log.info("ingest batch uploaded", uploaded=ok, failed=len(payload) - ok)
    log.info(
        "ingest complete",
        documents=report.documents,
        chunks=report.chunks,
        uploaded=report.uploaded,
        failed=report.failed,
    )
    return report


class PruneRefused(Exception):
    """``--prune`` would delete more than it keeps — likely a partial path or the wrong index."""


async def prune_stale(
    search_client: Any,
    report: IngestReport,
    *,
    apply: bool = False,
    force: bool = False,
    batch_size: int = 500,
) -> int:
    """Find every chunk in the index that ``report``'s run did not produce (``report.stale``)
    and, only when ``apply`` is set, delete them. Returns the number deleted.

    Guards, in order — each exists because pruning a shared search service is destructive:
    - a run that found no documents, uploaded nothing, or had any failed upload prunes nothing;
    - without ``apply`` it is a dry run: the stale count is reported, nothing is deleted;
    - deleting more chunks than the run kept raises ``PruneRefused`` unless ``force`` is set.
    """
    log = get_logger("app.ai.ingest")
    if report.documents == 0 or report.uploaded == 0 or report.failed:
        log.warning(
            "ingest prune skipped",
            documents=report.documents,
            uploaded=report.uploaded,
            failed=report.failed,
        )
        return 0

    def _stale_ids() -> list[str]:
        rows = search_client.search(search_text="*", select=[ID_FIELD])
        ids = (str(r.get(ID_FIELD) or "") for r in rows)
        return [key for key in ids if key and key not in report.chunk_ids]

    stale = await asyncio.to_thread(_stale_ids)
    report.stale = len(stale)
    if not apply or not stale:
        log.info("ingest prune dry run", stale=len(stale), kept=len(report.chunk_ids))
        return 0
    if len(stale) > len(report.chunk_ids) and not force:
        raise PruneRefused(
            f"refusing to delete {len(stale)} chunks while keeping {len(report.chunk_ids)}"
            " — check the path and AZURE_SEARCH_INDEX, then re-run with --force"
        )
    pruned = 0
    for batch in _batches(stale, batch_size):
        keys = [{ID_FIELD: key} for key in batch]
        results = await asyncio.to_thread(search_client.delete_documents, documents=keys)
        pruned += sum(1 for r in results if getattr(r, "succeeded", False))
    if pruned < len(stale):
        log.warning("ingest prune incomplete", stale=len(stale), pruned=pruned)
    else:
        log.info("ingest prune complete", stale=len(stale), pruned=pruned)
    return pruned


async def run(argv: Sequence[str] | None = None) -> IngestReport:
    parser = argparse.ArgumentParser(prog="python -m app.ai.ingest", description=__doc__)
    parser.add_argument("path", type=Path, help="a .md/.txt file or a directory to ingest")
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--overlap", type=int, default=DEFAULT_OVERLAP)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument(
        "--prune",
        action="store_true",
        help="find chunks this run did not produce; a dry run unless --yes is also given",
    )
    parser.add_argument("--yes", action="store_true", help="with --prune: actually delete them")
    parser.add_argument(
        "--force",
        action="store_true",
        help="with --prune --yes: allow deleting more chunks than the run keeps",
    )
    args = parser.parse_args(argv)
    if args.prune and not args.path.is_dir():
        parser.error("--prune needs the whole corpus folder, not a single file")
    if (args.yes or args.force) and not args.prune:
        parser.error("--yes/--force only apply with --prune")

    from app.ai.client import get_embeddings

    settings = get_ai_settings()
    settings.require_search()
    settings.require_embeddings()
    # Say where writes go before making any: the index name and service host are not secrets.
    get_logger("app.ai.ingest").info(
        "ingest target",
        search_host=urlsplit(settings.search_endpoint or "").hostname,
        index=settings.search_index,
        prune="apply" if args.yes else ("dry-run" if args.prune else "off"),
    )
    index = build_index(settings.search_index or "", settings.embedding_dimensions)
    ensure_index(build_index_client(settings), index)
    search_client = build_search_client(settings)
    report = await ingest_documents(
        load_path(args.path),
        embeddings=get_embeddings(),
        search_client=search_client,
        chunk_size=args.chunk_size,
        overlap=args.overlap,
        batch_size=args.batch_size,
    )
    if args.prune:
        report.prune_applied = args.yes
        report.pruned = await prune_stale(search_client, report, apply=args.yes, force=args.force)
    return report


# Third-party loggers that print every HTTP request (and its headers) at INFO.
_NOISY_LOGGERS = ("azure", "httpx", "openai")


def main() -> None:
    # Local dev: read apps/api/.env like the service does (the shell's values still win).
    load_local_env()
    configure_logging()
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    try:
        report = asyncio.run(run(sys.argv[1:]))
    except PruneRefused as exc:
        print(f"prune refused: {exc}", file=sys.stderr)
        sys.exit(2)
    print(
        f"documents={report.documents} chunks={report.chunks} uploaded={report.uploaded} "
        f"failed={report.failed} stale={report.stale} pruned={report.pruned}"
    )
    if report.stale and not report.prune_applied:
        print(f"dry run: {report.stale} stale chunks found — re-run with --prune --yes to delete")
    if report.failed or (report.prune_applied and report.pruned < report.stale):
        sys.exit(1)


if __name__ == "__main__":
    main()
