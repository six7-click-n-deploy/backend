"""Unit-test scoped conftest.

The parent ``tests/conftest.py`` declares two ``autouse=True`` fixtures
(``_setup_schema`` session-scoped, ``_truncate_tables`` per-test) that
hit a live Postgres. That's the right default for the integration
suite but wrong for fast, DB-less capability tests.

This conftest overrides both with no-op fixtures of the SAME name so
the parent autouse contract is satisfied without ever touching a
database. The capability tests mock all DB sessions explicitly via
:class:`unittest.mock.MagicMock`, so no real engine is needed.

Why two no-ops instead of one: pytest resolves autouse fixtures by
name within scope. If we only override ``_truncate_tables``, the
parent's session-scoped ``_setup_schema`` still runs and tries to
``create_all`` against Postgres — which the unit-test runner doesn't
need (and may not even have available in pure-Python CI lanes).
Overriding both keeps unit tests genuinely DB-less.
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="session", autouse=True)
def _setup_schema():
    """No-op override of the parent session-scoped schema fixture."""
    yield


@pytest.fixture(autouse=True)
def _truncate_tables():
    """No-op override of the parent per-test truncate fixture."""
    yield
