"""Conversations and their messages (``ARCHITECTURE.md`` B4/B4a, ``docs/design/db-design.md``).

ORM constructs only (parameterised by the driver — rule 50). Message content and titles are
user text: nothing here logs them.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, Message
from app.models.conversation import DEFAULT_TITLE


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


async def get_conversation(
    session: AsyncSession, conversation_id: UUID
) -> tuple[Conversation, list[Message]] | None:
    """The conversation and its messages, or ``None`` when the id is unknown."""
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None:
        return None
    result = await session.execute(messages_query(conversation_id))
    return conversation, list(result.scalars().all())
