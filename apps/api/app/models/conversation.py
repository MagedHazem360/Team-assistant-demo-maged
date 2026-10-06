"""Conversations and their messages — the chat history of F1/F2 (``ARCHITECTURE.md`` B4).

Table design: ``docs/design/db-design.md``. Conversations have no owner until auth lands
(ADR-0013). Timestamps are ``datetime2(3)`` in UTC, defaulted by the server
(``SYSUTCDATETIME()``); ``conversations.updated_at`` is set by the app on every new message.
Message content and titles are user text: never log them (rule 70).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, ForeignKey, Index, Unicode, func
from sqlalchemy.dialects.mssql import DATETIME2
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

DEFAULT_TITLE = "New conversation"
TITLE_MAX_LENGTH = 200


def _utc_timestamp() -> Mapped[datetime]:
    return mapped_column(DATETIME2(precision=3), server_default=func.sysutcdatetime())


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(Unicode(TITLE_MAX_LENGTH), default=DEFAULT_TITLE)
    created_at: Mapped[datetime] = _utc_timestamp()
    updated_at: Mapped[datetime] = _utc_timestamp()


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="role"),
        CheckConstraint("citations IS NULL OR ISJSON(citations) = 1", name="citations_json"),
        # Serves "the last 10 messages of a conversation" and the thread load (B4).
        Index(None, "conversation_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    # NO ACTION on delete: deleting conversations is out of scope (B8 #6).
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey("conversations.id"))
    role: Mapped[str] = mapped_column(Unicode(20))
    content: Mapped[str] = mapped_column(Unicode())  # NVARCHAR(max); UnicodeText would be NTEXT
    # JSON array of {"title", "path"} (ADR-0015); NULL on user messages.
    citations: Mapped[str | None] = mapped_column(Unicode())
    token_count: Mapped[int | None]
    created_at: Mapped[datetime] = _utc_timestamp()
