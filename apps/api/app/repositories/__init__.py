"""Query functions that take an ``AsyncSession`` — SQL stays out of the routers.

One module per aggregate (``conversations.py``). Routers get the session via
``Depends(get_session)`` and pass it in; nothing here opens a session or an engine.
"""
