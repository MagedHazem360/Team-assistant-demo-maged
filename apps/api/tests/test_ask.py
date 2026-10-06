"""``POST /v1/conversations/{id}/ask`` (roadmap api 2.2) — offline.

The graph is the real ``retrieve → answer`` graph with a fake model and retriever
(``tests/ai/fakes.py``); the repository is swapped for recording fakes; repository functions are
tested against a recording session and the compiled T-SQL. No model, index or database.
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
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import Field
from sqlalchemy.dialects import mssql

from app.ai.graph import build_graph
from app.db import get_session
from app.main import app
from app.models import Conversation, Message
from app.repositories import conversations as repo
from app.routers import conversations as router_module
from tests.ai.fakes import FakeRetriever

TRACE_RE = re.compile(r"^[0-9a-f]{32}$")
CONV_ID = UUID("11111111-2222-3333-4444-555555555555")
T0 = datetime(2026, 10, 6, 9, 0, 0)
CONTEXT = [
    {
        "id": "c1",
        "content": "The trace id is minted at the edge and forwarded unchanged.",
        "source": "architecture/tracing.md",
        "title": "The trace_id contract",
        "score": 1.0,
    },
    {
        "id": "c2",
        "content": "Every response echoes x-trace-id.",
        "source": "architecture/tracing.md",
        "title": "The trace_id contract",
        "score": 0.9,
    },
]


class RecordingChat(GenericFakeChatModel):
    """The LangChain fake model, recording the messages each call received."""

    seen: list[list[Any]] = Field(default_factory=list)

    def _generate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kw: Any) -> Any:
        self.seen.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kw)


def _model(text: str = "It is minted at the edge [1].", output_tokens: int = 7) -> RecordingChat:
    usage = {"input_tokens": 20, "output_tokens": output_tokens, "total_tokens": 20 + output_tokens}
    return RecordingChat(messages=iter([AIMessage(content=text, usage_metadata=usage)]))


class FailingRetriever:
    def __init__(self, exc: BaseException | None = None, delay: float = 0.0) -> None:
        self.exc, self.delay = exc, delay

    async def retrieve(self, query: str, top_k: int = 5) -> list[Any]:
        await asyncio.sleep(self.delay)
        if self.exc:
            raise self.exc
        return []


class FakeRepo:
    """Records what the route asks of the repository."""

    def __init__(self, conversation: Conversation | None, history: list[Message] | None = None):
        self.conversation = conversation
        self.history = history or []
        self.questions: list[str] = []
        self.answers: list[dict[str, Any]] = []

    async def get_conversation_for_ask(self, session: Any, conversation_id: UUID) -> Any:
        assert conversation_id == CONV_ID
        return None if self.conversation is None else (self.conversation, self.history)

    async def record_question(self, session: Any, conversation: Any, question: str) -> Message:
        self.questions.append(question)
        return Message(id=uuid4(), role="user", content=question)

    async def record_answer(
        self, session: Any, conversation: Any, answer: str, citations: Any, token_count: Any
    ) -> Message:
        self.answers.append({"answer": answer, "citations": citations, "token_count": token_count})
        return Message(
            id=UUID("99999999-0000-0000-0000-000000000001"), role="assistant", content=answer
        )


def _conversation(title: str = "New conversation") -> Conversation:
    return Conversation(id=CONV_ID, title=title, created_at=T0, updated_at=T0)


def _turns(n: int) -> list[Message]:
    return [
        Message(id=uuid4(), role="user" if i % 2 == 0 else "assistant", content=f"turn {i}")
        for i in range(n)
    ]


@pytest.fixture()
def wire(monkeypatch: pytest.MonkeyPatch):
    """Install a fake repository and a fake-backed graph; returns a TestClient factory."""

    def _wire(fake: FakeRepo, *, model: Any = None, retriever: Any = None) -> TestClient:
        for name in ("get_conversation_for_ask", "record_question", "record_answer"):
            monkeypatch.setattr(repo, name, getattr(fake, name))
        graph = build_graph(
            chat_model=model or _model(),
            retriever=retriever or FakeRetriever(CONTEXT),
            deployment_label="test",
        )
        app.dependency_overrides[get_session] = lambda: object()
        app.dependency_overrides[router_module.get_answer_graph] = lambda: graph
        return TestClient(app, raise_server_exceptions=False)

    yield _wire
    app.dependency_overrides.pop(get_session, None)
    app.dependency_overrides.pop(router_module.get_answer_graph, None)


def _assert_error(response: Any, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    body = response.json()
    assert body == {"error": code, "trace_id": body["trace_id"]}
    assert TRACE_RE.match(body["trace_id"]) and response.headers["x-trace-id"] == body["trace_id"]


ASK = f"/v1/conversations/{CONV_ID}/ask"


# ── the happy path ───────────────────────────────────────────────────────────


def test_ask_answers_with_citations_and_stores_both_messages(wire: Any) -> None:
    fake = FakeRepo(_conversation())
    model = _model("It is minted at the edge [1].", output_tokens=7)
    client = wire(fake, model=model)

    response = client.post(ASK, json={"question": "  How does trace_id work?  "})

    assert response.status_code == 200, response.text
    assert response.json() == {
        "message_id": "99999999-0000-0000-0000-000000000001",
        "answer": "It is minted at the edge [1].",
        "citations": [{"title": "The trace_id contract", "path": "architecture/tracing.md"}],
    }
    assert fake.questions == ["How does trace_id work?"]  # trimmed, stored first
    assert fake.answers == [
        {
            "answer": "It is minted at the edge [1].",
            "citations": [{"title": "The trace_id contract", "path": "architecture/tracing.md"}],
            "token_count": 7,
        }
    ]


def test_the_model_gets_history_as_turns_then_the_question(wire: Any) -> None:
    history = _turns(4)
    model = _model()
    client = wire(FakeRepo(_conversation("Trace ids"), history), model=model)

    assert client.post(ASK, json={"question": "And on the api side?"}).status_code == 200

    (sent,) = model.seen
    assert isinstance(sent[0], SystemMessage)  # the prompt with the retrieved context only
    assert "minted at the edge" in sent[0].content
    turns = sent[1:]
    assert [type(m) for m in turns] == [
        HumanMessage,
        AIMessage,
        HumanMessage,
        AIMessage,
        HumanMessage,
    ]
    assert [m.content for m in turns] == [
        "turn 0",
        "turn 1",
        "turn 2",
        "turn 3",
        "And on the api side?",
    ]
    assert not any(isinstance(m, SystemMessage) for m in turns)  # history is never a system message


def test_no_reported_usage_stores_a_null_token_count(wire: Any) -> None:
    fake = FakeRepo(_conversation())
    model = RecordingChat(messages=iter([AIMessage(content="ok")]))
    assert wire(fake, model=model).post(ASK, json={"question": "q"}).status_code == 200
    assert fake.answers[0]["token_count"] is None


def test_no_context_means_no_citations(wire: Any) -> None:
    fake = FakeRepo(_conversation())
    client = wire(fake, retriever=FakeRetriever([]), model=_model("I do not know."))
    body = client.post(ASK, json={"question": "q"}).json()
    assert body["citations"] == [] and fake.answers[0]["citations"] == []


# ── validation and the error contract ────────────────────────────────────────


def test_unknown_conversation_is_404_and_stores_nothing(wire: Any) -> None:
    fake = FakeRepo(None)
    _assert_error(wire(fake).post(ASK, json={"question": "q"}), 404, "not_found")
    assert fake.questions == [] and fake.answers == []


@pytest.mark.parametrize(
    "body", [{"question": ""}, {"question": "   "}, {}], ids=["empty", "blank", "missing"]
)
def test_empty_question_is_422(wire: Any, body: dict[str, Any]) -> None:
    fake = FakeRepo(_conversation())
    _assert_error(wire(fake).post(ASK, json=body), 422, "validation_error")
    assert fake.questions == []


def test_question_length_limit_is_4000_characters(wire: Any) -> None:
    fake = FakeRepo(_conversation())
    client = wire(fake)
    _assert_error(client.post(ASK, json={"question": "x" * 4001}), 413, "payload_too_large")
    assert fake.questions == []
    assert client.post(ASK, json={"question": "x" * 4000 + "   "}).status_code == 200


@pytest.mark.parametrize(
    "retriever",
    [FailingRetriever(RuntimeError("search down")), FailingRetriever(delay=5)],
    ids=["search-failure", "timeout"],
)
def test_ai_failure_is_503_keeps_the_question_and_stores_no_answer(
    wire: Any, monkeypatch: pytest.MonkeyPatch, retriever: Any
) -> None:
    monkeypatch.setattr(router_module, "_ask_timeout_seconds", lambda: 0.2)
    fake = FakeRepo(_conversation())
    _assert_error(
        wire(fake, retriever=retriever).post(ASK, json={"question": "q"}), 503, "ai_unavailable"
    )
    assert fake.questions == ["q"] and fake.answers == []


def test_model_failure_is_503(wire: Any) -> None:
    class Broken(GenericFakeChatModel):
        def _generate(self, *args: Any, **kwargs: Any) -> Any:
            raise TimeoutError("model timed out")

    fake = FakeRepo(_conversation())
    model = Broken(messages=iter([]))
    _assert_error(wire(fake, model=model).post(ASK, json={"question": "q"}), 503, "ai_unavailable")
    assert fake.questions == ["q"] and fake.answers == []


def test_questions_answers_and_ids_never_reach_the_logs(
    wire: Any, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    secret_q, secret_a = "SENTINEL-question-51ab", "SENTINEL-answer-77cd"
    client = wire(FakeRepo(_conversation()), model=_model(secret_a))
    with caplog.at_level(logging.DEBUG):
        assert client.post(ASK, json={"question": secret_q}).status_code == 200
        broken = wire(FakeRepo(_conversation()), retriever=FailingRetriever(RuntimeError(secret_q)))
        assert broken.post(ASK, json={"question": secret_q}).status_code == 503
    # The service's own output: the app.* loggers and the structured stdout lines. (The test
    # client logs the URL it calls under httpx — that is the caller, not the api.)
    service = [r.getMessage() for r in caplog.records if r.name.startswith("app")]
    logged = " | ".join(service) + capsys.readouterr().out
    for needle in (secret_q, secret_a, "minted at the edge", str(CONV_ID)):
        assert needle not in logged, needle


# ── repository ───────────────────────────────────────────────────────────────


def _sql(statement: Any) -> str:
    return str(statement.compile(dialect=mssql.dialect(), compile_kwargs={"literal_binds": True}))


def test_recent_messages_query_takes_the_newest_ten() -> None:
    sql = _sql(repo.recent_messages_query(CONV_ID))
    assert "TOP 10" in sql
    assert f"WHERE messages.conversation_id = '{CONV_ID}'" in sql
    assert "ORDER BY messages.created_at DESC, messages.id DESC" in sql


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("  How does\n trace_id\twork?  ", "How does trace_id work?"),
        ("x" * 250, "x" * 200),
        ("\U0001f600" * 150, "\U0001f600" * 100),  # 2 UTF-16 units each — never half a pair
        ("a" + "\U0001f600" * 150, "a" + "\U0001f600" * 99),
    ],
    ids=["one-line", "cut", "emoji", "odd-boundary"],
)
def test_title_from_question(question: str, expected: str) -> None:
    assert repo.title_from_question(question) == expected


class _Scalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _Scalars:
        return self

    def all(self) -> list[Any]:
        return self._rows


class RecordingSession:
    def __init__(self, *, get_result: Any = None, rows: list[Any] | None = None) -> None:
        self.added: list[Any] = []
        self.commits = 0
        self.keys: list[Any] = []
        self._get_result, self._rows = get_result, rows or []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commits += 1

    async def get(self, model: Any, key: Any) -> Any:
        self.keys.append(key)
        return self._get_result

    async def execute(self, statement: Any) -> _Scalars:
        return _Scalars(self._rows)


def test_get_conversation_for_ask_returns_history_oldest_first() -> None:
    newest_first = list(reversed(_turns(3)))
    session = RecordingSession(get_result=_conversation(), rows=newest_first)
    conversation, history = asyncio.run(repo.get_conversation_for_ask(session, CONV_ID))  # type: ignore[arg-type,misc]
    assert session.keys == [CONV_ID]
    assert [m.content for m in history] == ["turn 0", "turn 1", "turn 2"]
    assert asyncio.run(repo.get_conversation_for_ask(RecordingSession(), CONV_ID)) is None  # type: ignore[arg-type]


def test_record_question_replaces_only_the_default_title_and_commits() -> None:
    conversation = _conversation()
    session = RecordingSession()
    message = asyncio.run(repo.record_question(session, conversation, "How does\ntrace_id work?"))  # type: ignore[arg-type]
    assert message.role == "user" and message.token_count is None and message.citations is None
    assert message.conversation_id == CONV_ID and session.added == [message]
    assert conversation.title == "How does trace_id work?"
    assert conversation.updated_at > T0 and session.commits == 1

    named = _conversation("My own title")
    asyncio.run(repo.record_question(RecordingSession(), named, "another"))  # type: ignore[arg-type]
    assert named.title == "My own title"


def test_record_answer_stores_citations_json_and_tokens() -> None:
    conversation = _conversation("t")
    session = RecordingSession()
    cites = [{"title": "Café notes", "path": "notes.md"}]
    message = asyncio.run(repo.record_answer(session, conversation, "answer", cites, 12))  # type: ignore[arg-type]
    assert message.role == "assistant" and message.token_count == 12
    assert json.loads(message.citations or "") == cites and "Café" in (message.citations or "")
    assert conversation.updated_at > T0 and session.commits == 1
