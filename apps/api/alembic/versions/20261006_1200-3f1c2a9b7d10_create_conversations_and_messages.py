"""create conversations and messages

Revision ID: 3f1c2a9b7d10
Revises:
Create Date: 2026-10-06 12:00:00

Rules (.claude/rules/25-sqlalchemy.md): ``downgrade()`` is MANDATORY and must
undo ``upgrade()`` exactly; destructive operations (drop table/column, data
loss) need explicit human confirmation. SQL Server has no ``CONCURRENTLY``; populated-table
patterns (``WITH (ONLINE = ON)``, ``WITH NOCHECK``) are in rule 25.

Hand-written (the dev database was not reachable for autogenerate) from
``app/models/conversation.py`` and ``docs/design/db-design.md``. Both tables are new and
empty, so the plain ``create_index`` / inline foreign key need none of the populated-table
patterns. ``downgrade()`` drops both tables — it deletes all chat history; run it only with
explicit confirmation.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mssql

# revision identifiers, used by Alembic.
revision: str = "3f1c2a9b7d10"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the schema change."""
    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Unicode(length=200), nullable=False),
        sa.Column(
            "created_at",
            mssql.DATETIME2(precision=3),
            server_default=sa.func.sysutcdatetime(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            mssql.DATETIME2(precision=3),
            server_default=sa.func.sysutcdatetime(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.Unicode(length=20), nullable=False),
        sa.Column("content", sa.Unicode(), nullable=False),
        sa.Column("citations", sa.Unicode(), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            mssql.DATETIME2(precision=3),
            server_default=sa.func.sysutcdatetime(),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('user', 'assistant')", name=op.f("ck_messages_role")),
        sa.CheckConstraint(
            "citations IS NULL OR ISJSON(citations) = 1",
            name=op.f("ck_messages_citations_json"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_messages_conversation_id_conversations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
    )
    op.create_index(
        op.f("ix_messages_conversation_id"),
        "messages",
        ["conversation_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Revert the schema change (must mirror upgrade).

    DESTRUCTIVE: drops both tables and with them all chat history. Run only with explicit
    human confirmation (root ``CLAUDE.md`` rule 6).
    """
    op.drop_index(op.f("ix_messages_conversation_id"), table_name="messages")
    op.drop_table("messages")
    op.drop_table("conversations")
