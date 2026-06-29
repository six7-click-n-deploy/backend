"""Unit tests for ``app.services.email_service``.

Two layers are exercised here without any SMTP socket I/O:

* ``is_smtp_enabled()`` — the predicate the resend-access endpoint
  consults to decide between 503 (configuration) and 502 (delivery
  failure). The truth table needs to be locked down because both
  ``SMTP_ENABLED`` and the credentials must be present; flipping
  either independently was previously possible and produced
  surprising states (mail attempted with empty creds, or mail
  silently off while creds were populated).
* ``send_email()`` — the kill-switch must short-circuit BEFORE any
  ``smtplib`` access. We patch ``smtplib.SMTP_SSL`` / ``smtplib.SMTP``
  and assert the constructor is never called when the gate is off.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.config import settings
from app.services import email_service


# ----------------------------------------------------------------
# is_smtp_enabled — truth table
# ----------------------------------------------------------------
@pytest.mark.parametrize(
    "enabled,user,password,expected",
    [
        # The fully-configured happy path. All three predicates true.
        (True, "u@example.com", "secret", True),
        # Kill-switch overrides everything else — operator chose "off".
        (False, "u@example.com", "secret", False),
        # ENABLED=True but credentials missing is "configuration in
        # progress"; we treat it as off so the resend endpoint
        # returns 503 instead of a 502 at submit-time auth failure.
        (True, "", "secret", False),
        (True, "u@example.com", "", False),
        # Both off and unconfigured — still off.
        (False, "", "", False),
    ],
)
def test_is_smtp_enabled_truth_table(monkeypatch, enabled, user, password, expected):
    monkeypatch.setattr(settings, "SMTP_ENABLED", enabled, raising=False)
    monkeypatch.setattr(settings, "SMTP_USER", user, raising=False)
    monkeypatch.setattr(settings, "SMTP_PASSWORD", password, raising=False)
    assert email_service.is_smtp_enabled() is expected


# ----------------------------------------------------------------
# send_email — short-circuit semantics
# ----------------------------------------------------------------
def test_send_email_no_op_when_disabled(monkeypatch):
    """When ``SMTP_ENABLED=False``, send_email must return False
    WITHOUT touching ``smtplib``. We patch both transports — neither
    should be constructed. This guards the kill-switch against a
    future refactor that accidentally moves the gate AFTER the
    connection attempt (where it would still leak DNS lookups / TCP
    connects to ``smtp.gmail.com`` in air-gapped environments).
    """
    monkeypatch.setattr(settings, "SMTP_ENABLED", False, raising=False)
    # Credentials populated to prove the kill-switch wins regardless.
    monkeypatch.setattr(settings, "SMTP_USER", "u@example.com", raising=False)
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "secret", raising=False)

    with patch("smtplib.SMTP_SSL") as ssl_cls, patch("smtplib.SMTP") as plain_cls:
        result = email_service.send_email(
            to="recipient@example.com",
            subject="hi",
            html_body="<p>hi</p>",
            text_body="hi",
        )

    assert result is False
    ssl_cls.assert_not_called()
    plain_cls.assert_not_called()


def test_send_email_no_op_when_credentials_missing(monkeypatch):
    """``SMTP_ENABLED=True`` but missing credentials must also be a
    no-op. The existing credential-empty branch survives the
    ``SMTP_ENABLED`` addition.
    """
    monkeypatch.setattr(settings, "SMTP_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "SMTP_USER", "", raising=False)
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "", raising=False)

    with patch("smtplib.SMTP_SSL") as ssl_cls, patch("smtplib.SMTP") as plain_cls:
        result = email_service.send_email(
            to="recipient@example.com",
            subject="hi",
            html_body="<p>hi</p>",
            text_body="hi",
        )

    assert result is False
    ssl_cls.assert_not_called()
    plain_cls.assert_not_called()
