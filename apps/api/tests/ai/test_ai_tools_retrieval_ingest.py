"""Tools (allow-listed read-only SQL), Azure AI Search retrieval, and the ingestion job —
all against fakes; ``pyodbc.connect`` and the network are never touched."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import app.ai.tools as registry
import app.ai.tools.queries as queries
from app.ai import ingest
from app.ai.tools.queries import InvalidQueryParams, QueryNotAllowed, register_query
from app.ai.tools.query_external_db import (
    TextToSqlDisabled,
    describe_queries,
    query_external_db,
    run_allowed_query,
    run_readonly_sql,
)
from app.ai.tools.retrieve import (
    CONTENT_FIELD,
    ID_FIELD,
    SOURCE_FIELD,
    TITLE_FIELD,
    VECTOR_FIELD,
    AzureSearchRetriever,
    NullRetriever,
)
from tests.ai.fakes import FakeEmbeddings, FakeSearchClient, FakeSession, fake_session_factory


@pytest.fixture(autouse=True)
def _reset_registries():
    queries._reset_for_tests()
    yield
    queries._reset_for_tests()


# ── allow-listed queries ─────────────────────────────────────────────────────


def test_register_query_refuses_non_select_and_unknown_types() -> None:
    with pytest.raises(ValueError, match="SELECT/WITH"):
        register_query("bad", sql="DELETE FROM t")
    with pytest.raises(ValueError, match="unsupported param type"):
        register_query("bad2", sql="SELECT 1", params={"x": "date"})


def test_run_allowed_query_binds_validated_params() -> None:
    register_query(
        "orders",
        sql="SELECT TOP (:limit) id FROM dbo.orders WHERE customer_id = :customer_id",
        params={"customer_id": "int", "limit": "int"},
        max_rows=2,
    )
    session = FakeSession(rows=[{"id": 1}, {"id": 2}, {"id": 3}])
    rows = asyncio.run(
        run_allowed_query(
            "orders", {"customer_id": 7, "limit": 5}, session_factory=fake_session_factory(session)
        )
    )
    assert rows == [{"id": 1}, {"id": 2}]  # capped at max_rows
    statement, params = session.executed[0]
    assert "customer_id = :customer_id" in statement
    assert params == {"customer_id": 7, "limit": 5}


def test_param_validation_is_strict() -> None:
    register_query("q", sql="SELECT :a AS a", params={"a": "int"})
    session = FakeSession()
    factory = fake_session_factory(session)
    with pytest.raises(InvalidQueryParams):
        asyncio.run(run_allowed_query("q", {"a": "1"}, session_factory=factory))  # wrong type
    with pytest.raises(InvalidQueryParams):
        asyncio.run(run_allowed_query("q", {"a": 1, "b": 2}, session_factory=factory))  # unknown
    with pytest.raises(InvalidQueryParams):
        asyncio.run(run_allowed_query("q", {"a": True}, session_factory=factory))  # bool ≠ int
    with pytest.raises(QueryNotAllowed):
        asyncio.run(run_allowed_query("missing", {}, session_factory=factory))
    assert session.executed == []


def test_text_to_sql_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AI_ALLOW_TEXT_TO_SQL", raising=False)
    session = FakeSession()
    with pytest.raises(TextToSqlDisabled):
        asyncio.run(run_readonly_sql("SELECT 1", session_factory=fake_session_factory(session)))
    monkeypatch.setenv("AI_ALLOW_TEXT_TO_SQL", "true")
    with pytest.raises(PermissionError):
        asyncio.run(run_readonly_sql("DROP TABLE t", session_factory=fake_session_factory(session)))
    asyncio.run(run_readonly_sql("SELECT 1", session_factory=fake_session_factory(session)))
    assert session.executed[0][0].strip() == "SELECT 1"


def test_langchain_tool_returns_json_and_safe_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    register_query("ping", sql="SELECT 1 AS one", description="health")
    session = FakeSession(rows=[{"one": 1}])
    import app.ai.tools.query_external_db as tool_mod

    monkeypatch.setattr(tool_mod, "get_external_sessionmaker", fake_session_factory(session))
    assert query_external_db.name == "query_external_db"
    assert any(t.name == "query_external_db" for t in registry.get_tools())

    ok = asyncio.run(query_external_db.ainvoke({"query_name": "ping", "params": "{}"}))
    assert json.loads(ok) == [{"one": 1}]
    bad = json.loads(asyncio.run(query_external_db.ainvoke({"query_name": "nope", "params": "{}"})))
    assert "not in the allow-list" in bad["error"]
    assert "ping" in bad["available"]
    malformed = json.loads(
        asyncio.run(query_external_db.ainvoke({"query_name": "ping", "params": "{"}))
    )
    assert "JSON" in malformed["error"]
    assert "ping(" in describe_queries()


# ── retrieval ────────────────────────────────────────────────────────────────


def test_null_retriever_returns_nothing() -> None:
    assert asyncio.run(NullRetriever().retrieve("q")) == []


def test_azure_search_retriever_runs_hybrid_query() -> None:
    client = FakeSearchClient(
        rows=[
            {
                ID_FIELD: "a",
                CONTENT_FIELD: "text a",
                SOURCE_FIELD: "s1",
                TITLE_FIELD: "Title A",
                "@search.score": 1.5,
            }
        ]
    )
    embeddings = FakeEmbeddings(dimensions=3)
    retriever = AzureSearchRetriever(client, embeddings)
    chunks = asyncio.run(retriever.retrieve("hello", top_k=3))
    assert chunks == [
        {"id": "a", "content": "text a", "source": "s1", "title": "Title A", "score": 1.5}
    ]
    assert embeddings.queries == ["hello"]
    call = client.search_calls[0]
    assert call["search_text"] == "hello" and call["top"] == 3
    assert call["select"] == [ID_FIELD, CONTENT_FIELD, SOURCE_FIELD, TITLE_FIELD]
    vq = call["vector_queries"][0]
    assert (
        vq.fields == VECTOR_FIELD and vq.k_nearest_neighbors == 3 and vq.vector == [5.0, 0.0, 0.0]
    )


# ── ingestion ────────────────────────────────────────────────────────────────


def test_chunk_text_windows_with_overlap() -> None:
    assert ingest.chunk_text("") == []
    chunks = ingest.chunk_text("abcdefghij", size=4, overlap=1)
    assert chunks == ["abcd", "defg", "ghij"]
    with pytest.raises(ValueError):
        ingest.chunk_text("x", size=4, overlap=4)


def test_build_index_matches_the_retriever_contract() -> None:
    index = ingest.build_index("docs", dimensions=8)
    names = {f.name: f for f in index.fields}
    assert names[ID_FIELD].key is True
    assert names[CONTENT_FIELD].searchable is True
    assert names[VECTOR_FIELD].vector_search_dimensions == 8
    assert names[VECTOR_FIELD].vector_search_profile_name == ingest.VECTOR_PROFILE
    assert index.vector_search.profiles[0].algorithm_configuration_name == ingest.HNSW_CONFIG


def test_ingest_documents_embeds_and_uploads_in_batches() -> None:
    client = FakeSearchClient()
    embeddings = FakeEmbeddings(dimensions=2)
    docs = [ingest.IngestDocument(source="a.md", text="one two three four five six")]
    report = asyncio.run(
        ingest.ingest_documents(
            docs,
            embeddings=embeddings,
            search_client=client,
            chunk_size=10,
            overlap=2,
            batch_size=2,
        )
    )
    assert report.documents == 1 and report.chunks == report.uploaded > 1 and report.failed == 0
    assert len(client.uploaded) == -(-report.chunks // 2)  # ceil(chunks / batch_size)
    first = client.uploaded[0][0]
    assert set(first) == {
        ID_FIELD,
        CONTENT_FIELD,
        SOURCE_FIELD,
        TITLE_FIELD,
        ingest.CHUNK_INDEX_FIELD,
        VECTOR_FIELD,
    }
    assert first[TITLE_FIELD] == "a.md"  # no title given → the file name
    assert first[ID_FIELD] == ingest.chunk_id("a.md", 0)
    assert len(first[VECTOR_FIELD]) == 2


def test_load_path_reads_text_files_only(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("alpha", encoding="utf-8")
    (tmp_path / "b.txt").write_text("beta", encoding="utf-8")
    (tmp_path / "c.bin").write_bytes(b"\x00")
    docs = ingest.load_path(tmp_path)
    assert [d.text for d in docs] == ["alpha", "beta"]
    assert ingest.load_path(tmp_path / "a.md")[0].source.endswith("a.md")


def test_ingest_cli_loads_the_local_env_file_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`python -m app.ai.ingest` from a plain terminal reads apps/api/.env like the service."""
    import app.config

    env_file = tmp_path / ".env"
    env_file.write_text("AZURE_SEARCH_INDEX=from-env-file\n", encoding="utf-8")
    monkeypatch.setattr(app.config, "LOCAL_ENV_FILE", env_file)
    monkeypatch.setenv("AZURE_SEARCH_INDEX", "placeholder")  # registers the restore …
    monkeypatch.delenv("AZURE_SEARCH_INDEX")  # … then starts from "not set in the shell"

    seen: dict[str, str | None] = {}

    async def fake_run(argv: object) -> ingest.IngestReport:
        seen["index"] = ingest.get_ai_settings().search_index
        return ingest.IngestReport()

    monkeypatch.setattr(ingest, "run", fake_run)
    monkeypatch.setattr("sys.argv", ["ingest", str(tmp_path)])

    ingest.main()

    assert seen["index"] == "from-env-file"
    assert "documents=0" in capsys.readouterr().out


# ── titles, corpus-relative paths and --prune (roadmap api 2.1, ADR-0015) ────


def test_build_index_has_a_searchable_title_field() -> None:
    names = {f.name: f for f in ingest.build_index("docs", dimensions=8).fields}
    assert names[TITLE_FIELD].searchable is True


def test_document_title_is_the_first_h1_outside_code_fences() -> None:
    text = "\ufeffIntro line\n```bash\n# not a title\n```\n## Sub\n# The Title \n# Second\n"
    assert ingest.document_title(text, "fallback.md") == "The Title"
    assert ingest.document_title("no heading here\n#hashtag", "x.md") == "x.md"
    assert ingest.document_title("# \n", "empty.md") == "empty.md"


def test_load_path_stores_corpus_relative_posix_sources_and_titles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = tmp_path / "docs"
    (corpus / "architecture").mkdir(parents=True)
    (corpus / "architecture" / "tracing.md").write_text("# The trace_id contract\n", "utf-8")
    (corpus / "notes.txt").write_text("# not a markdown title", "utf-8")

    # the CLI is run as `ingest ../../docs` from apps/api — the prefix must not leak in
    (tmp_path / "apps" / "api").mkdir(parents=True)
    monkeypatch.chdir(tmp_path / "apps" / "api")
    docs = {d.source: d for d in ingest.load_path(Path("../../docs"))}

    assert set(docs) == {"architecture/tracing.md", "notes.txt"}
    assert docs["architecture/tracing.md"].title == "The trace_id contract"
    assert docs["notes.txt"].title == "notes.txt"  # .txt files use their file name
    single = ingest.load_path(corpus / "architecture" / "tracing.md")
    assert [d.source for d in single] == ["tracing.md"]


def test_ingest_records_the_ids_it_produced() -> None:
    docs = [ingest.IngestDocument(source="a.md", text="x" * 30, title="A")]
    report = asyncio.run(
        ingest.ingest_documents(
            docs,
            embeddings=FakeEmbeddings(dimensions=2),
            search_client=FakeSearchClient(),
            chunk_size=10,
            overlap=0,
        )
    )
    assert report.chunk_ids == {ingest.chunk_id("a.md", i) for i in range(3)}
    assert "chunk_ids" not in repr(report)  # never printed


def _report(**kwargs: int) -> ingest.IngestReport:
    report = ingest.IngestReport(**kwargs)
    report.chunk_ids = {"keep-1", "keep-2"}
    return report


def test_prune_deletes_only_chunks_this_run_did_not_produce() -> None:
    client = FakeSearchClient(
        rows=[{ID_FIELD: "keep-1"}, {ID_FIELD: "old-1"}, {ID_FIELD: "keep-2"}, {ID_FIELD: "old-2"}]
    )
    report = _report(documents=1, uploaded=2)
    pruned = asyncio.run(ingest.prune_stale(client, report, apply=True, batch_size=1))
    assert pruned == 2 and report.stale == 2
    assert client.search_calls[0]["search_text"] == "*"
    assert client.search_calls[0]["select"] == [ID_FIELD]
    assert client.deleted == [[{ID_FIELD: "old-1"}], [{ID_FIELD: "old-2"}]]


@pytest.mark.parametrize(
    "counts",
    [
        {"documents": 0, "uploaded": 0},  # wrong path: nothing found
        {"documents": 3, "uploaded": 0},  # nothing uploaded
        {"documents": 3, "uploaded": 5, "failed": 1},  # a failed upload
    ],
)
def test_prune_never_runs_after_an_empty_or_failed_ingest(counts: dict[str, int]) -> None:
    client = FakeSearchClient(rows=[{ID_FIELD: "old-1"}])
    assert asyncio.run(ingest.prune_stale(client, _report(**counts), apply=True)) == 0
    assert client.search_calls == [] and client.deleted == []


def test_unique_citations_dedupe_by_path_and_fall_back_to_the_file_name() -> None:
    from app.ai.graph import unique_citations

    chunks = [
        {"id": "1", "content": "", "source": "a/x.md", "title": "X doc", "score": 1.0},
        {"id": "2", "content": "", "source": "b/y.md", "title": "", "score": 0.9},
        {"id": "3", "content": "", "source": "a/x.md", "title": "X doc", "score": 0.8},
        {"id": "4", "content": "", "source": "", "title": "orphan", "score": 0.7},
    ]
    assert unique_citations(chunks) == [
        {"title": "X doc", "path": "a/x.md"},
        {"title": "y.md", "path": "b/y.md"},
    ]


def test_ingest_cli_quiets_sdk_request_logging_and_reports_pruned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import logging

    saved = {n: logging.getLogger(n).level for n in ingest._NOISY_LOGGERS}

    async def fake_run(argv: object) -> ingest.IngestReport:
        return ingest.IngestReport(documents=1, chunks=2, uploaded=2, pruned=7)

    monkeypatch.setattr(ingest, "run", fake_run)
    monkeypatch.setattr("sys.argv", ["ingest", str(tmp_path), "--prune"])
    try:
        ingest.main()
        for name in ingest._NOISY_LOGGERS:
            assert logging.getLogger(name).level == logging.WARNING
    finally:
        for name, level in saved.items():
            logging.getLogger(name).setLevel(level)
    assert "pruned=7" in capsys.readouterr().out


def test_prune_without_apply_is_a_dry_run() -> None:
    client = FakeSearchClient(rows=[{ID_FIELD: "keep-1"}, {ID_FIELD: "old-1"}, {"no-id": 1}])
    report = _report(documents=1, uploaded=2)
    assert asyncio.run(ingest.prune_stale(client, report)) == 0
    assert report.stale == 1 and client.deleted == []


def test_prune_refuses_to_delete_more_than_it_keeps_unless_forced() -> None:
    rows = [{ID_FIELD: f"old-{i}"} for i in range(3)] + [{ID_FIELD: "keep-1"}]
    client = FakeSearchClient(rows=rows)
    report = _report(documents=1, uploaded=2)  # keeps 2, would delete 3
    with pytest.raises(ingest.PruneRefused, match="--force"):
        asyncio.run(ingest.prune_stale(client, report, apply=True))
    assert client.deleted == []
    assert asyncio.run(ingest.prune_stale(client, report, apply=True, force=True)) == 3


def test_prune_reports_an_incomplete_delete() -> None:
    class HalfFailing(FakeSearchClient):
        def delete_documents(self, documents: list[dict[str, object]]) -> list[object]:
            class _R:
                def __init__(self, ok: bool) -> None:
                    self.succeeded = ok

            return [_R(i == 0) for i, _ in enumerate(documents)]

    client = HalfFailing(rows=[{ID_FIELD: "old-1"}, {ID_FIELD: "old-2"}])
    report = _report(documents=1, uploaded=2)
    assert asyncio.run(ingest.prune_stale(client, report, apply=True)) == 1


def _wire_run(monkeypatch: pytest.MonkeyPatch, client: FakeSearchClient) -> None:
    """Replace every Azure-facing seam `run()` uses with fakes."""
    from types import SimpleNamespace

    import app.ai.client

    settings = SimpleNamespace(
        search_index="test-index",
        search_endpoint="https://search.invalid",
        embedding_dimensions=2,
        require_search=lambda: None,
        require_embeddings=lambda: None,
    )
    monkeypatch.setattr(ingest, "get_ai_settings", lambda: settings)
    monkeypatch.setattr(ingest, "build_index_client", lambda s: object())
    monkeypatch.setattr(ingest, "ensure_index", lambda client, index: None)
    monkeypatch.setattr(ingest, "build_search_client", lambda s: client)
    monkeypatch.setattr(app.ai.client, "get_embeddings", lambda: FakeEmbeddings(dimensions=2))


def _corpus(tmp_path: Path) -> Path:
    corpus = tmp_path / "docs"
    corpus.mkdir()
    (corpus / "a.md").write_text("# A\nalpha", "utf-8")
    return corpus


def test_run_never_deletes_without_prune_and_yes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeSearchClient(rows=[{ID_FIELD: "old-1"}])
    _wire_run(monkeypatch, client)
    corpus = _corpus(tmp_path)

    plain = asyncio.run(ingest.run([str(corpus)]))
    assert plain.uploaded == 1 and client.search_calls == [] and client.deleted == []

    dry = asyncio.run(ingest.run([str(corpus), "--prune"]))
    assert dry.stale == 1 and dry.pruned == 0 and not dry.prune_applied
    assert client.deleted == []

    applied = asyncio.run(ingest.run([str(corpus), "--prune", "--yes"]))
    assert applied.pruned == 1 and applied.prune_applied
    assert client.deleted == [[{ID_FIELD: "old-1"}]]


@pytest.mark.parametrize(
    "extra", [["--prune"], ["--yes"], ["--force"]], ids=["prune-on-a-file", "yes-alone", "force"]
)
def test_run_rejects_unsafe_flag_combinations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra: list[str]
) -> None:
    _wire_run(monkeypatch, FakeSearchClient())
    corpus = _corpus(tmp_path)
    target = corpus / "a.md" if extra == ["--prune"] else corpus
    with pytest.raises(SystemExit) as exc:
        asyncio.run(ingest.run([str(target), *extra]))
    assert exc.value.code == 2


def test_cli_exit_codes_for_refused_and_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def refused(argv: object) -> ingest.IngestReport:
        raise ingest.PruneRefused("refusing to delete 9 chunks while keeping 1 — use --force")

    monkeypatch.setattr(ingest, "run", refused)
    monkeypatch.setattr("sys.argv", ["ingest", str(tmp_path), "--prune", "--yes"])
    with pytest.raises(SystemExit) as exc:
        ingest.main()
    assert exc.value.code == 2
    assert "prune refused" in capsys.readouterr().err

    async def dry(argv: object) -> ingest.IngestReport:
        return ingest.IngestReport(documents=1, chunks=1, uploaded=1, stale=4)

    monkeypatch.setattr(ingest, "run", dry)
    ingest.main()  # exit 0
    out = capsys.readouterr().out
    assert "stale=4 pruned=0" in out and "--prune --yes" in out


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("---\ntitle: x\n# yaml comment\n---\n# Real\n", "Real"),
        ("    # indented code\n# Heading #\n", "Heading"),
        ("```\n~~~\n# inside\n```\n# After\n", "After"),
        ("# " + "x" * 300, "x" * ingest.TITLE_MAX_LENGTH),
    ],
    ids=["front-matter", "indented-and-closing-hashes", "mixed-fences", "length-cap"],
)
def test_document_title_edge_cases(text: str, expected: str) -> None:
    assert ingest.document_title(text, "fallback.md") == expected
