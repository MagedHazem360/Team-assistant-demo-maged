"""``/v1/conversations`` — create, list and read conversations (roadmap api 1.2, ``ARCHITECTURE.md``
B4a). Asking questions is ``/v1/conversations/{id}/ask`` (roadmap api 2.2/2.3).

Conversations are shared — no owner until auth lands (ADR-0013). Titles and message content are
user text: they are returned to the caller and never logged or put in telemetry (rule 70).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.errors import ErrorBody, NotFound
from app.logging_config import get_logger
from app.models.conversation import TITLE_MAX_LENGTH
from app.repositories import conversations as repo

router = APIRouter(prefix="/conversations", tags=["conversations"])
_log = get_logger("app.routers.conversations")
DbSession = Annotated[AsyncSession, Depends(get_session)]

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 100


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
