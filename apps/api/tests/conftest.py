"""Suite-wide setup.

Hermetic env: ``app.main``, ``alembic/env.py`` and ``app.ai.ingest`` call ``load_local_env()``,
which reads ``apps/api/.env`` by default. Point it at a file that never exists so no test picks
up a developer's real database URL, keys or endpoints. This runs before any test module is
imported (pytest loads conftest first). Tests that exercise loading pass an explicit path or
monkeypatch ``app.config.LOCAL_ENV_FILE``.
"""

from __future__ import annotations

from pathlib import Path

import app.config

app.config.LOCAL_ENV_FILE = Path(__file__).resolve().parent / ".env.never-loaded-by-tests"
