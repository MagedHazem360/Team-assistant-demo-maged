"""Conversations and their messages (``ARCHITECTURE.md`` B4/B4a, ``docs/design/db-design.md``).

ORM constructs only (parameterised by the driver — rule 50). Message content and titles are
user text: nothing here logs them.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, Message
from app.models.conversation import DEFAULT_TITLE, TITLE_MAX_LENGTH


def list_conversations_query(limit: int) -> Select[tuple[Conversation]]:
    """Newest activity first (``updated_at`` is set on every new message); ``id`` breaks ties
    so the order is stable."""
    return (
        select(Conversation)
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        .limit(limit)
    )


def messages_query(conversation_id: UUID) -> Select[tuple[Message]]:
    """A conversation's messages, oldest first — served by ix_messages_conversation_id."""
    return (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc(), Message.id.asc())
    )


async def create_conversation(session: AsyncSession, title: str | None) -> Conversation:
    conversation = Conversation(title=title or DEFAULT_TITLE)
    session.add(conversation)
    await session.commit()
    # created_at / updated_at are server defaults. Read them back explicitly: SQLAlchemy may
    # already fetch them via OUTPUT INSERTED, but that is unverified on mssql+aioodbc, and the
    # cost is one primary-key SELECT per new conversation.
    await session.refresh(conversation)
    return conversation


async def list_conversations(session: AsyncSession, limit: int) -> list[Conversation]:
    result = await session.execute(list_conversations_query(limit))
    return list(result.scalars().all())


HISTORY_LIMIT = 10  # messages sent to the model before the current question (B3, B8 #4)


def recent_messages_query(
    conversation_id: UUID, limit: int = HISTORY_LIMIT
) -> Select[tuple[Message]]:
    """The newest ``limit`` messages, newest first (the caller reverses them)."""
    return (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(limit)
    )


def title_from_question(question: str) -> str:
    """The question on one line, cut to ``TITLE_MAX_LENGTH`` UTF-16 units (``nvarchar(200)``)."""
    one_line = " ".join(question.split())
    units = one_line.encode("utf-16-le")[: TITLE_MAX_LENGTH * 2]
    return units.decode("utf-16-le", errors="ignore")  # never ends on half a surrogate pair


def _utc_now() -> datetime:
    # datetime2 holds naive UTC, like the SYSUTCDATETIME() server defaults.
    return datetime.now(UTC).replace(tzinfo=None)


async def get_conversation_for_ask(
    session: AsyncSession, conversation_id: UUID
) -> tuple[Conversation, list[Message]] | None:
    """The conversation and its last ``HISTORY_LIMIT`` messages, oldest first."""
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None:
        return None
    result = await session.execute(recent_messages_query(conversation_id))
    return conversation, list(reversed(result.scalars().all()))


async def record_question(
    session: AsyncSession, conversation: Conversation, question: str
) -> Message:
    """Store the user's message and bump the conversation; committed **before** the model is
    called, so the question is kept even when answering fails. The first question replaces the
    default title."""
    message = Message(id=uuid4(), conversation_id=conversation.id, role="user", content=question)
    session.add(message)
    if conversation.title == DEFAULT_TITLE:
        conversation.title = title_from_question(question)
    conversation.updated_at = _utc_now()
    await session.commit()
    return message


async def record_answer(
    session: AsyncSession,
    conversation: Conversation,
    answer: str,
    citations: list[dict[str, str]],
    token_count: int | None,
) -> Message:
    """Store the assistant's message. ``citations`` must already be ``[{title, path}]``
    (validated by the caller) — the column CHECK only guarantees JSON."""
    message = Message(
        id=uuid4(),
        conversation_id=conversation.id,
        role="assistant",
        content=answer,
        citations=json.dumps(citations, ensure_ascii=False),
        token_count=token_count,
    )
    session.add(message)
    conversation.updated_at = _utc_now()
    await session.commit()
    return message


async def record_answer_for(
    session: AsyncSession,
    conversation_id: UUID,
    answer: str,
    citations: list[dict[str, str]],
    token_count: int | None,
) -> Message | None:
    """``record_answer`` on a fresh session (the streaming route stores the answer after the
    request's own session may already be closed). ``None`` if the conversation is gone."""
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None:
        return None
    return await record_answer(session, conversation, answer, citations, token_count)


async def get_conversation(
    session: AsyncSession, conversation_id: UUID
) -> tuple[Conversation, list[Message]] | None:
    """The conversation and its messages, or ``None`` when the id is unknown."""
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None:
        return None
    result = await session.execute(messages_query(conversation_id))
    return conversation, list(result.scalars().all())
