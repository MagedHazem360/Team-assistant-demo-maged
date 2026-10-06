"""``/v1/conversations`` (roadmap api 1.2): create, list, read — offline.

Route tests override ``get_session`` and swap the repository functions for fakes (status codes,
shapes, validation, the error contract, ``x-trace-id``). Repository tests use a recording fake
session and check the T-SQL the queries compile to — there is no local database (rule 40).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import mssql

from app.db import get_session
from app.main import app
from app.models import Conversation, Message
from app.repositories import conversations as repo

TRACE_RE = re.compile(r"^[0-9a-f]{32}$")
INBOUND = "0eb01" + "c" * 27
CONV_ID = UUID("11111111-2222-3333-4444-555555555555")
T0 = datetime(2026, 10, 6, 9, 0, 0)  # datetime2 is naive UTC
T1 = datetime(2026, 10, 6, 10, 30, 0)


def _conversation(title: str = "Trace ids", **kw: Any) -> Conversation:
    """A real ORM instance (no session needed), as the repository would return it."""
    return Conversation(
        id=kw.get("id", CONV_ID), title=title, created_at=T0, updated_at=kw.get("updated_at", T1)
    )


def _message(role: str, content: str, citations: str | None = None) -> Message:
    return Message(
        id=uuid4(),
        conversation_id=CONV_ID,
        role=role,
        content=content,
        citations=citations,
        created_at=T1,
    )


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch):
    """The real app, with a dummy session (the fakes below never touch it)."""
    app.dependency_overrides[get_session] = lambda: object()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_session, None)


def _assert_error(response: Any, status: int, code: str) -> None:
    assert response.status_code == status
    body = response.json()
    assert body == {"error": code, "trace_id": body["trace_id"]}
    assert TRACE_RE.match(body["trace_id"]) and response.headers["x-trace-id"] == body["trace_id"]


# ── POST /v1/conversations ───────────────────────────────────────────────────


def test_create_returns_201_and_the_conversation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str | None] = []

    async def fake_create(session: Any, title: str | None) -> Conversation:
        seen.append(title)
        return _conversation(title or "New conversation")

    monkeypatch.setattr(repo, "create_conversation", fake_create)

    response = client.post(
        "/v1/conversations", json={"title": "Trace ids"}, headers={"x-trace-id": INBOUND}
    )
    assert response.status_code == 201
    assert response.headers["x-trace-id"] == INBOUND
    assert response.json() == {
        "id": str(CONV_ID),
        "title": "Trace ids",
        "created_at": "2026-10-06T09:00:00Z",
        "updated_at": "2026-10-06T10:30:00Z",
    }
    assert seen == ["Trace ids"]


@pytest.mark.parametrize(
    "body", [{}, {"title": None}, {"title": "   "}], ids=["missing", "null", "blank"]
)
def test_create_without_a_title_uses_the_default(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, body: dict[str, Any]
) -> None:
    seen: list[str | None] = []

    async def fake_create(session: Any, title: str | None) -> Conversation:
        seen.append(title)
        return _conversation("New conversation")

    monkeypatch.setattr(repo, "create_conversation", fake_create)
    response = client.post("/v1/conversations", json=body)
    assert response.status_code == 201 and response.json()["title"] == "New conversation"
    assert seen == [None]  # the repository applies the default


def test_create_rejects_a_title_longer_than_200(client: TestClient) -> None:
    _assert_error(
        client.post("/v1/conversations", json={"title": "x" * 201}), 422, "validation_error"
    )


# ── GET /v1/conversations ────────────────────────────────────────────────────


def test_list_returns_items_newest_first_with_count(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    limits: list[int] = []
    second = UUID("99999999-2222-3333-4444-555555555555")

    async def fake_list(session: Any, limit: int) -> list[Conversation]:
        limits.append(limit)
        return [_conversation("Newest"), _conversation("Older", id=second, updated_at=T0)]

    monkeypatch.setattr(repo, "list_conversations", fake_list)

    response = client.get("/v1/conversations")
    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {"id": str(CONV_ID), "title": "Newest", "updated_at": "2026-10-06T10:30:00Z"},
            {"id": str(second), "title": "Older", "updated_at": "2026-10-06T09:00:00Z"},
        ],
        "count": 2,
    }
    assert limits == [50]  # the default
    assert client.get("/v1/conversations?limit=100").status_code == 200
    assert limits[-1] == 100


def test_list_of_nothing_is_an_empty_page(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_list(session: Any, limit: int) -> list[Any]:
        return []

    monkeypatch.setattr(repo, "list_conversations", fake_list)
    assert client.get("/v1/conversations").json() == {"items": [], "count": 0}


@pytest.mark.parametrize("limit", ["0", "101", "abc"])
def test_list_rejects_an_out_of_range_limit(client: TestClient, limit: str) -> None:
    _assert_error(client.get(f"/v1/conversations?limit={limit}"), 422, "validation_error")


# ── GET /v1/conversations/{id} ───────────────────────────────────────────────


def test_get_returns_the_conversation_with_messages_and_citations(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    citations = json.dumps([{"title": "The trace_id contract", "path": "architecture/tracing.md"}])
    user = _message("user", "How does trace_id propagation work?")
    answer = _message("assistant", "It is minted at the edge …", citations)

    async def fake_get(session: Any, conversation_id: UUID) -> Any:
        assert conversation_id == CONV_ID
        return _conversation(), [user, answer]

    monkeypatch.setattr(repo, "get_conversation", fake_get)

    response = client.get(f"/v1/conversations/{CONV_ID}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(CONV_ID) and body["title"] == "Trace ids"
    assert body["created_at"] == "2026-10-06T09:00:00Z"
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assert body["messages"][0]["citations"] is None
    assert body["messages"][1]["citations"] == [
        {"title": "The trace_id contract", "path": "architecture/tracing.md"}
    ]
    assert body["messages"][1]["id"] == str(answer.id)


def test_get_an_unknown_conversation_is_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_get(session: Any, conversation_id: UUID) -> None:
        return None

    monkeypatch.setattr(repo, "get_conversation", fake_get)
    response = client.get(f"/v1/conversations/{uuid4()}", headers={"x-trace-id": INBOUND})
    _assert_error(response, 404, "not_found")
    assert response.json()["trace_id"] == INBOUND


def test_get_with_a_malformed_id_is_422(client: TestClient) -> None:
    _assert_error(client.get("/v1/conversations/not-a-uuid"), 422, "validation_error")


def test_titles_and_content_never_reach_the_logs(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    secret = "SENTINEL-user-text-7f3a"

    async def fake_create(session: Any, title: str | None) -> Conversation:
        return _conversation(title or "")

    async def fake_get(session: Any, conversation_id: UUID) -> Any:
        return _conversation(secret), [_message("user", secret)]

    monkeypatch.setattr(repo, "create_conversation", fake_create)
    monkeypatch.setattr(repo, "get_conversation", fake_get)
    with caplog.at_level(logging.DEBUG):
        client.post("/v1/conversations", json={"title": secret})
        client.get(f"/v1/conversations/{CONV_ID}")
        client.post("/v1/conversations", json={"title": secret + "x" * 200})  # 422 path
    assert secret not in caplog.text


# ── repository (fake session + compiled T-SQL) ───────────────────────────────


def _sql(statement: Any) -> str:
    return str(statement.compile(dialect=mssql.dialect(), compile_kwargs={"literal_binds": True}))


def test_list_query_orders_newest_first_and_limits() -> None:
    sql = _sql(repo.list_conversations_query(25))
    assert "TOP 25" in sql
    assert "ORDER BY conversations.updated_at DESC, conversations.id DESC" in sql


def test_messages_query_filters_by_conversation_oldest_first() -> None:
    sql = _sql(repo.messages_query(CONV_ID))
    assert "WHERE messages.conversation_id = '11111111-2222-3333-4444-555555555555'" in sql
    assert "ORDER BY messages.created_at ASC, messages.id ASC" in sql


class _Scalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _Scalars:
        return self

    def all(self) -> list[Any]:
        return self._rows


class RecordingSession:
    """Just enough of ``AsyncSession`` for the repository functions."""

    def __init__(self, *, get_result: Any = None, rows: list[Any] | None = None) -> None:
        self.added: list[Any] = []
        self.calls: list[str] = []
        self.statements: list[Any] = []
        self.keys: list[Any] = []
        self._get_result = get_result
        self._rows = rows or []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.calls.append("commit")

    async def refresh(self, obj: Any) -> None:
        self.calls.append("refresh")

    async def get(self, model: Any, key: Any) -> Any:
        self.calls.append(f"get:{model.__name__}")
        self.keys.append(key)
        return self._get_result

    async def execute(self, statement: Any) -> _Scalars:
        self.statements.append(statement)
        return _Scalars(self._rows)


def test_create_conversation_defaults_the_title_commits_and_refreshes() -> None:
    session = RecordingSession()
    conversation = asyncio.run(repo.create_conversation(session, None))  # type: ignore[arg-type]
    assert isinstance(conversation, Conversation) and conversation.title == "New conversation"
    assert session.added == [conversation]
    assert session.calls == ["commit", "refresh"]  # server-default timestamps read back
    named = asyncio.run(repo.create_conversation(RecordingSession(), "Trace ids"))  # type: ignore[arg-type]
    assert named.title == "Trace ids"


def test_list_conversations_runs_the_list_query() -> None:
    rows = [Conversation(title="a")]
    session = RecordingSession(rows=rows)
    assert asyncio.run(repo.list_conversations(session, 10)) == rows  # type: ignore[arg-type]
    assert "TOP 10" in _sql(session.statements[0])


def test_get_conversation_returns_none_without_querying_messages() -> None:
    session = RecordingSession(get_result=None)
    assert asyncio.run(repo.get_conversation(session, CONV_ID)) is None  # type: ignore[arg-type]
    assert session.calls == ["get:Conversation"] and session.statements == []
    assert session.keys == [CONV_ID]


def test_get_conversation_returns_it_with_its_messages() -> None:
    conversation = Conversation(title="t")
    messages = [Message(role="user", content="q")]
    session = RecordingSession(get_result=conversation, rows=messages)
    found = asyncio.run(repo.get_conversation(session, CONV_ID))  # type: ignore[arg-type]
    assert found == (conversation, messages)
    assert session.keys == [CONV_ID]
    assert f"messages.conversation_id = '{CONV_ID}'" in _sql(session.statements[0])


# ── review follow-ups: edge cases and the failure paths ──────────────────────


def _echo_create(seen: list[str | None]) -> Any:
    async def fake_create(session: Any, title: str | None) -> Conversation:
        seen.append(title)
        return _conversation(title or "New conversation")

    return fake_create


def test_title_bounds_are_checked_after_trimming_in_utf16_units(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str | None] = []
    monkeypatch.setattr(repo, "create_conversation", _echo_create(seen))

    assert client.post("/v1/conversations", json={"title": "x" * 200}).status_code == 201
    padded = client.post("/v1/conversations", json={"title": "  " + "y" * 199 + "     "})
    assert padded.status_code == 201 and seen[-1] == "y" * 199  # trimmed, then bounded
    # 150 emoji = 150 characters but 300 UTF-16 units: nvarchar(200) cannot hold them
    _assert_error(
        client.post("/v1/conversations", json={"title": "\U0001f600" * 150}),
        422,
        "validation_error",
    )
    assert client.post("/v1/conversations", json={"title": "\U0001f600" * 100}).status_code == 201


def test_list_accepts_the_smallest_limit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    limits: list[int] = []

    async def fake_list(session: Any, limit: int) -> list[Conversation]:
        limits.append(limit)
        return [_conversation()]

    monkeypatch.setattr(repo, "list_conversations", fake_list)
    assert client.get("/v1/conversations?limit=1").json()["count"] == 1
    assert limits == [1]


def test_a_conversation_without_messages_has_an_empty_list(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_get(session: Any, conversation_id: UUID) -> Any:
        return _conversation(), []

    monkeypatch.setattr(repo, "get_conversation", fake_get)
    assert client.get(f"/v1/conversations/{CONV_ID}").json()["messages"] == []


@pytest.mark.parametrize(
    "stored",
    ['["a.md"]', "{}", '"x"', '[{"title": "only a title"}]'],
    ids=["strings", "object", "scalar", "missing-path"],
)
def test_malformed_stored_citations_degrade_to_null_not_500(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, stored: str
) -> None:
    good = json.dumps([{"title": "T", "path": "p.md"}])

    async def fake_get(session: Any, conversation_id: UUID) -> Any:
        return _conversation(), [
            _message("assistant", "a", stored),
            _message("assistant", "b", good),
        ]

    monkeypatch.setattr(repo, "get_conversation", fake_get)
    response = client.get(f"/v1/conversations/{CONV_ID}")
    assert response.status_code == 200
    messages = response.json()["messages"]
    assert messages[0]["citations"] is None
    assert messages[1]["citations"] == [{"title": "T", "path": "p.md"}]


def test_a_repository_failure_is_a_500_that_leaks_no_user_text(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "SENTINEL-db-error-9c1d"

    async def failing_create(session: Any, title: str | None) -> Conversation:
        raise RuntimeError(f"INSERT failed for title={title} {secret}")

    monkeypatch.setattr(repo, "create_conversation", failing_create)
    failing_client = TestClient(app, raise_server_exceptions=False)
    with caplog.at_level(logging.DEBUG):
        response = failing_client.post("/v1/conversations", json={"title": secret})
    _assert_error(response, 500, "internal_error")
    assert secret not in response.text
    assert secret not in caplog.text
    assert secret not in capsys.readouterr().out
