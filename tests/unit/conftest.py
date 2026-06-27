"""Unit-test scoped conftest.

The parent ``tests/conftest.py`` declares an ``autouse=True`` fixture
that creates and drops every table on a live Postgres for each test.
That's the right default for the integration suite but wrong for
fast, DB-less capability tests.

This conftest overrides ``setup_db`` with a no-op fixture so unit
tests in this directory don't touch a database. The capability tests
mock all DB sessions explicitly via :class:`unittest.mock.MagicMock`,
so no real engine is needed.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def setup_db():
    """Override the parent autouse Postgres fixture for unit tests."""
    yield
