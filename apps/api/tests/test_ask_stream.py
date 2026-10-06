"""``POST /v1/conversations/{id}/ask/stream`` (roadmap api 2.3) — offline.

Same fakes as ``test_ask.py``: the real graph with a fake model/retriever, a recording
repository, and a fake session factory for the answer write. The streaming module's own edge
cases (deadline, disconnect, a failing store) are in ``tests/ai/test_ai_graph_streaming.py``.
"""

from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

from app.ai.graph import build_graph
from app.db import get_session
from app.main import app
from app.repositories import conversations as repo
from app.routers import conversations as router_module
from tests.ai.fakes import FakeRetriever, fake_chat_model
from tests.test_ask import CONTEXT, CONV_ID, FailingRetriever, FakeRepo, _conversation

TRACE_RE = re.compile(r"^[0-9a-f]{32}$")
STREAM = f"/v1/conversations/{CONV_ID}/ask/stream"


def _events(body: str) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for frame in body.strip().split("\n\n"):
        lines = frame.split("\n")
        out.append((lines[0].removeprefix("event: "), json.loads(lines[1].removeprefix("data: "))))
    return out


@pytest.fixture()
def wire(monkeypatch: pytest.MonkeyPatch):
    stored: list[dict[str, Any]] = []

    async def fake_record_answer_for(
        session: Any, conversation_id: UUID, answer: str, citations: Any, tokens: Any
    ) -> Any:
        assert session == "answer-session" and conversation_id == CONV_ID
        stored.append({"answer": answer, "citations": citations, "token_count": tokens})

    @asynccontextmanager
    async def answer_session():
        yield "answer-session"

    def _wire(fake: FakeRepo, *, model: Any = None, retriever: Any = None) -> TestClient:
        for name in ("get_conversation_for_ask", "record_question"):
            monkeypatch.setattr(repo, name, getattr(fake, name))
        monkeypatch.setattr(repo, "record_answer_for", fake_record_answer_for)
        graph = build_graph(
            chat_model=model or fake_chat_model("It is minted at the edge [1]."),
            retriever=retriever or FakeRetriever(CONTEXT),
            deployment_label="test",
        )
        app.dependency_overrides[get_session] = lambda: object()
        app.dependency_overrides[router_module.get_answer_graph] = lambda: graph
        app.dependency_overrides[router_module.get_answer_session_factory] = lambda: answer_session
        return TestClient(app, raise_server_exceptions=False)

    _wire.stored = stored  # type: ignore[attr-defined]
    yield _wire
    for dep in (
        get_session,
        router_module.get_answer_graph,
        router_module.get_answer_session_factory,
    ):
        app.dependency_overrides.pop(dep, None)


def _assert_error(response: Any, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    body = response.json()
    assert body == {"error": code, "trace_id": body["trace_id"]}
    assert TRACE_RE.match(body["trace_id"]) and response.headers["x-trace-id"] == body["trace_id"]


def test_streams_sources_tokens_done_and_stores_the_answer(wire: Any) -> None:
    fake = FakeRepo(_conversation())
    response = wire(fake).post(STREAM, json={"question": "How does trace_id work?"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    assert TRACE_RE.match(response.headers["x-trace-id"])

    events = _events(response.text)
    cite = [{"title": "The trace_id contract", "path": "architecture/tracing.md"}]
    assert events[0] == ("sources", {"sources": cite, "count": 2})
    assert "".join(d["text"] for e, d in events if e == "token") == "It is minted at the edge [1]."
    assert events[-1][0] == "done" and events[-1][1]["sources"] == cite
    assert fake.questions == ["How does trace_id work?"]
    assert wire.stored == [  # type: ignore[attr-defined]
        {"answer": "It is minted at the edge [1].", "citations": cite, "token_count": None}
    ]


def test_validation_and_unknown_ids_are_json_errors_before_streaming(wire: Any) -> None:
    client = wire(FakeRepo(None))
    _assert_error(client.post(STREAM, json={"question": "q"}), 404, "not_found")
    client = wire(FakeRepo(_conversation()))
    _assert_error(client.post(STREAM, json={"question": "  "}), 422, "validation_error")
    _assert_error(client.post(STREAM, json={"question": "x" * 4001}), 413, "payload_too_large")


def test_a_failure_before_the_first_frame_is_a_json_503(wire: Any) -> None:
    fake = FakeRepo(_conversation())
    client = wire(fake, retriever=FailingRetriever(RuntimeError("search down")))
    _assert_error(client.post(STREAM, json={"question": "q"}), 503, "ai_unavailable")
    assert fake.questions == ["q"] and wire.stored == []  # type: ignore[attr-defined]


def test_a_failure_after_the_first_frame_is_an_error_frame(wire: Any) -> None:
    class Broken(GenericFakeChatModel):
        def _generate(self, *args: Any, **kwargs: Any) -> Any:
            raise TimeoutError("model timed out: secret detail")

        async def _astream(self, *args: Any, **kwargs: Any) -> Any:
            raise TimeoutError("model timed out: secret detail")
            yield  # pragma: no cover

    fake = FakeRepo(_conversation())
    response = wire(fake, model=Broken(messages=iter([]))).post(STREAM, json={"question": "q"})

    assert response.status_code == 200  # the stream had started (sources were sent)
    events = _events(response.text)
    assert events[0][0] == "sources"
    assert events[-1] == ("error", {"error": "ai_unavailable", "error_kind": "timeout"})
    assert "secret" not in response.text
    assert fake.questions == ["q"] and wire.stored == []  # type: ignore[attr-defined]
