"""``/v1/conversations`` — create, list and read conversations (roadmap api 1.2, ``ARCHITECTURE.md``
B4a) and ask questions in them (``POST /{id}/ask``, roadmap api 2.2; streaming is 2.3).

Conversations are shared — no owner until auth lands (ADR-0013). Titles and message content are
user text: they are returned to the caller and never logged or put in telemetry (rule 70).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.config import get_ai_settings
from app.ai.graph import ask, build_graph
from app.ai.telemetry import error_kind
from app.db import get_session
from app.errors import AIUnavailable, ApiError, ErrorBody, NotFound
from app.logging_config import get_logger
from app.models.conversation import TITLE_MAX_LENGTH
from app.repositories import conversations as repo

router = APIRouter(prefix="/conversations", tags=["conversations"])
_log = get_logger("app.routers.conversations")
DbSession = Annotated[AsyncSession, Depends(get_session)]

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 100
QUESTION_MAX_LENGTH = 4000  # B3 limits


def _as_utc(value: datetime) -> datetime:
    """``datetime2`` columns hold UTC without an offset; say so on the wire."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class CreateConversation(BaseModel):
    title: str | None = Field(
        default=None,
        description=f"Optional, at most {TITLE_MAX_LENGTH} characters after trimming. Missing or"
        ' blank → "New conversation"; the first question then replaces it (roadmap api 2.2).',
    )

    @field_validator("title", mode="after")
    @classmethod
    def _trim_and_bound(cls, value: str | None) -> str | None:
        """Trim first, then bound — in UTF-16 code units, as ``nvarchar(200)`` counts them (an
        emoji is 2), so an over-long title is a 422 here, never a truncation error on insert."""
        value = (value or "").strip()
        if not value:
            return None
        if len(value.encode("utf-16-le")) // 2 > TITLE_MAX_LENGTH:
            raise ValueError(f"title is longer than {TITLE_MAX_LENGTH} characters")
        return value


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime

    _utc = field_validator("created_at", "updated_at")(_as_utc)


class ConversationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    updated_at: datetime

    _utc = field_validator("updated_at")(_as_utc)


class ConversationList(BaseModel):
    items: list[ConversationSummary]
    count: int = Field(description="Number of items returned (not a total).")


class Citation(BaseModel):
    title: str
    path: str


_CITATIONS = TypeAdapter(list[Citation])


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    role: Literal["user", "assistant"]
    content: str
    citations: list[Citation] | None = Field(
        default=None, description="Assistant messages: the documents cited (ADR-0015)."
    )
    created_at: datetime

    _utc = field_validator("created_at")(_as_utc)

    @field_validator("citations", mode="before")
    @classmethod
    def _parse_citations(cls, value: Any) -> list[Citation] | None:
        """Stored as a JSON string (``messages.citations``). The column CHECK guarantees JSON, not
        the ``[{title, path}]`` shape: a malformed row degrades to ``null`` for that message
        instead of making the whole conversation unreadable."""
        if value is None:
            return None
        try:
            parsed = json.loads(value) if isinstance(value, str) else value
            return _CITATIONS.validate_python(parsed)
        except ValueError, ValidationError:
            _log.warning("stored citations dropped: not [{title, path}]")
            return None


class ConversationDetail(ConversationOut):
    messages: list[MessageOut] = Field(description="Oldest first.")


@router.post(
    "",
    status_code=201,
    response_model=ConversationOut,
    summary="Start a conversation",
)
async def create_conversation(body: CreateConversation, session: DbSession) -> ConversationOut:
    conversation = await repo.create_conversation(session, body.title)
    return ConversationOut.model_validate(conversation)


@router.get("", response_model=ConversationList, summary="List conversations, newest first")
async def list_conversations(
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=MAX_LIST_LIMIT)] = DEFAULT_LIST_LIMIT,
) -> ConversationList:
    rows = await repo.list_conversations(session, limit)
    items = [ConversationSummary.model_validate(row) for row in rows]
    return ConversationList(items=items, count=len(items))


@router.get(
    "/{conversation_id}",
    response_model=ConversationDetail,
    responses={404: {"model": ErrorBody, "description": "Unknown conversation (`not_found`)"}},
    summary="Read a conversation with its messages",
)
async def get_conversation(conversation_id: UUID, session: DbSession) -> ConversationDetail:
    found = await repo.get_conversation(session, conversation_id)
    if found is None:
        raise NotFound()
    conversation, messages = found
    return ConversationDetail(
        **ConversationOut.model_validate(conversation).model_dump(),
        messages=[MessageOut.model_validate(m) for m in messages],
    )


# ── POST /v1/conversations/{id}/ask (roadmap api 2.2) ────────────────────────


class AskRequest(BaseModel):
    question: str = Field(
        description=f"1–{QUESTION_MAX_LENGTH} characters after trimming; longer → 413."
    )

    @field_validator("question", mode="after")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("question is empty")
        return value


class AskResponse(BaseModel):
    message_id: UUID = Field(description="The stored assistant message.")
    answer: str
    citations: list[Citation] = Field(description="The documents the answer drew on (ADR-0015).")


class PayloadTooLarge(ApiError):
    status_code = 413
    code = "payload_too_large"


@lru_cache(maxsize=1)
def get_answer_graph() -> Any:
    """The compiled ``retrieve → answer`` graph (built once; nothing connects until the first
    call). A dependency so tests swap in a fake model and retriever."""
    return build_graph()


AnswerGraph = Annotated[Any, Depends(get_answer_graph)]


def _ask_timeout_seconds() -> float:
    """The whole answer — retrieval plus the model call — is bounded (B3: 60 s)."""
    return get_ai_settings().request_timeout_seconds


def _as_history(messages: list[Any]) -> list[AnyMessage]:
    """Stored turns as chat messages, oldest first. They reach the model as human/assistant
    turns — never as a system message (rule 70: context is data)."""
    return [
        HumanMessage(content=m.content) if m.role == "user" else AIMessage(content=m.content)
        for m in messages
    ]


@router.post(
    "/{conversation_id}/ask",
    response_model=AskResponse,
    responses={
        404: {"model": ErrorBody, "description": "Unknown conversation (`not_found`)"},
        413: {"model": ErrorBody, "description": "Question too long (`payload_too_large`)"},
        503: {"model": ErrorBody, "description": "Model or search failed (`ai_unavailable`)"},
    },
    summary="Ask a question in a conversation",
)
async def ask_in_conversation(
    conversation_id: UUID, body: AskRequest, session: DbSession, graph: AnswerGraph
) -> AskResponse:
    if len(body.question) > QUESTION_MAX_LENGTH:
        raise PayloadTooLarge()
    found = await repo.get_conversation_for_ask(session, conversation_id)
    if found is None:
        raise NotFound()
    conversation, previous = found

    await repo.record_question(session, conversation, body.question)
    try:
        answer = await asyncio.wait_for(
            ask(graph, body.question, history=_as_history(previous)),
            timeout=_ask_timeout_seconds(),
        )
    except Exception as exc:  # model, search, timeout or configuration — all a 503
        # The question stays stored; no assistant message is written. Kind only, never text.
        _log.warning("ask failed", error_kind=error_kind(exc))
        raise AIUnavailable() from exc

    citations = _CITATIONS.validate_python(answer.citations)
    message = await repo.record_answer(
        session,
        conversation,
        answer.text,
        [c.model_dump() for c in citations],
        answer.output_tokens,
    )
    _log.info("ask answered", citations=len(citations), output_tokens=answer.output_tokens)
    return AskResponse(message_id=message.id, answer=answer.text, citations=citations)
