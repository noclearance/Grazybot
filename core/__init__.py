"""Core utilities for Grazybot.

Place small, shared startup code here (db pool factory, ai setup wrappers, etc.).
This package intentionally does not alter existing runtime files — it's a safe
place to centralize logic before moving callers (e.g., replacing code in
`bot.py`).
"""

__all__ = ["db"]
