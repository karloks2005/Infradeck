"""HTTP routes, grouped by area, mounted on the app in main.py.

Handlers reach the app's shared helpers and singletons through the ``main`` module (``main._current_user``,
``main.redis_client`` and the like) rather than importing them by value, so the tests that monkeypatch those names
on ``main`` still affect the routers. ``get_db`` is imported directly from ``db`` because it is the same object the
tests override.
"""
