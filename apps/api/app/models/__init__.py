"""ORM models. Every model module is imported here so it registers on ``Base.metadata``
(Alembic autogenerate only sees imported models).

HOW TO EXTEND:
  1. Add a module here (e.g. ``app/models/widget.py``) with a 2.0-style model
     subclassing ``app.db.Base`` and import it below.
  2. With a reachable dev database, generate a revision (needs a human + DB):
         uv run alembic revision --autogenerate -m "add widget"
     or hand-write one from ``alembic/script.py.mako``. Review the
     ``upgrade()``/``downgrade()``; offline SQL for review:
         uv run alembic upgrade head --sql
  3. Applying it (``alembic upgrade head``) is a deliberate, confirmed human step —
     never run from an agent (``.claude/rules/25-sqlalchemy.md``).

Models: ``conversation`` — ``Conversation`` / ``Message`` (chat history, ``ARCHITECTURE.md`` B4).
"""

from __future__ import annotations

from app.models.conversation import Conversation, Message

__all__: list[str] = ["Conversation", "Message"]
