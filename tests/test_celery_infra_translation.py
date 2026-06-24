"""Pure-function tests for the celery infrastructure-exception
translator in ``celery_event_listener.py``.

The listener itself is the long-running consumer of celery events;
testing it end-to-end requires a real worker. The translation
helper that decides whether a ``task-failed`` payload was a
worker-side ``Failure(...)`` or a celery-infrastructure exception
(``NotRegistered``, ``WorkerLostError``, …) is a pure function and
worth a small targeted test so the user-facing error text doesn't
silently regress.
"""
import pytest

from app.services.celery_event_listener import _translate_celery_infra_exception


@pytest.mark.unit
@pytest.mark.parametrize(
    "exception_repr, expected_kind",
    [
        ("NotRegistered('tasks.pause_deployment')", "celery_infrastructure"),
        ("WorkerLostError('worker died')", "celery_infrastructure"),
        ("TimeoutError(60)", "celery_infrastructure"),
        ("Reject('queue full')", "celery_infrastructure"),
        ("ContentDisallowed('pickle')", "celery_infrastructure"),
    ],
)
def test_known_celery_infrastructure_exceptions_get_friendly_text(
    exception_repr, expected_kind,
):
    headline, kind = _translate_celery_infra_exception(exception_repr)
    assert kind == expected_kind
    assert headline is not None
    # Headline must be human readable — no raw class name leaking through.
    assert "Worker" in headline or "worker" in headline or "Task" in headline or "OpenStack" in headline or "Plattform" in headline or "Routing" in headline or "konnte" in headline or "Zeit" in headline


@pytest.mark.unit
def test_notregistered_explicitly_mentions_worker_mismatch():
    """The most common operator-facing problem: backend and worker
    are out of sync. The headline must point the operator at that
    diagnosis, not just say "task failed"."""
    headline, _ = _translate_celery_infra_exception(
        "NotRegistered('tasks.pause_deployment')"
    )
    assert headline is not None
    assert "synchron" in headline.lower() or "mismatch" in headline.lower() or "nicht erkannt" in headline.lower()


@pytest.mark.unit
def test_unknown_exception_returns_none_so_caller_falls_back():
    """The translator is best-effort. Unknown exception classes
    should let the caller render the raw text — a forced friendly
    message would hide a useful diagnostic."""
    headline, kind = _translate_celery_infra_exception(
        "ValueError('something the worker raised')"
    )
    assert headline is None
    assert kind is None


@pytest.mark.unit
def test_empty_input_returns_none():
    headline, kind = _translate_celery_infra_exception("")
    assert headline is None
    assert kind is None
