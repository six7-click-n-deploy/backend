"""Time helpers.

``datetime.utcnow()`` is deprecated from Python 3.12 on. The idiomatic
replacement is ``datetime.now(UTC)``, but that returns a *timezone-aware*
value while every ``DateTime`` column in :mod:`app.models` is declared
*naive* (no ``timezone=True``). Mixing aware and naive datetimes raises
``TypeError`` on comparison and would silently change what gets persisted.

:func:`utcnow` therefore returns a naive UTC timestamp — byte-for-byte the
same value the old ``datetime.utcnow()`` produced — so it is a drop-in
replacement that keeps the stored representation unchanged while dropping
the deprecated call.
"""

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Return the current UTC time as a naive ``datetime`` (no tzinfo)."""
    return datetime.now(UTC).replace(tzinfo=None)
