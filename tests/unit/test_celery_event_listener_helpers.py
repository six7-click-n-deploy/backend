"""Unit tests for the pure log helpers in celery_event_listener.

Diese Tests decken die ANSI-Bereinigung, das Filtern verbose Logs,
das Formatieren strukturierter Log-Einträge sowie die Übersetzung
bekannter Celery-Infrastruktur-Exceptions ab. Sie laufen ohne
Datenbank-, Netzwerk- oder Worker-Zugriff.
"""

from __future__ import annotations

import pytest

from app.services.celery_event_listener import (
    _CELERY_INFRA_EXCEPTIONS,
    _get_icon,
    _translate_celery_infra_exception,
    clean_log_line,
    filter_logs,
    format_logs,
    is_verbose_line,
)


# ---------------------------------------------------------------------------
# clean_log_line
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_clean_log_line_strips_basic_ansi_color_codes() -> None:
    """ANSI-Farbcodes werden vollständig entfernt."""
    result = clean_log_line("\x1b[32mfoo\x1b[0m")
    assert result == "foo"


@pytest.mark.unit
def test_clean_log_line_strips_multiple_ansi_sequences() -> None:
    result = clean_log_line("\x1b[1;31mERROR\x1b[0m: \x1b[33msomething\x1b[0m")
    assert result == "ERROR: something"


@pytest.mark.unit
def test_clean_log_line_noop_on_plain_text() -> None:
    """Reiner Text bleibt unverändert."""
    assert clean_log_line("hello world") == "hello world"


@pytest.mark.unit
def test_clean_log_line_normalizes_doubled_quotes() -> None:
    """Doppelte Anführungszeichen werden zu einfachen normalisiert."""
    assert clean_log_line('foo ""bar"" baz') == 'foo "bar" baz'


@pytest.mark.unit
def test_clean_log_line_handles_empty_string() -> None:
    assert clean_log_line("") == ""


# ---------------------------------------------------------------------------
# is_verbose_line
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_is_verbose_line_detects_trace_marker() -> None:
    assert is_verbose_line("2024-01-01 [TRACE] some inner state") is True


@pytest.mark.unit
def test_is_verbose_line_detects_debug_marker() -> None:
    assert is_verbose_line("[DEBUG] discovering plugins") is True


@pytest.mark.unit
def test_is_verbose_line_detects_plugingetter() -> None:
    assert is_verbose_line("running plugingetter for vsphere") is True


@pytest.mark.unit
def test_is_verbose_line_detects_github_getter() -> None:
    assert is_verbose_line("using github-getter to resolve plugin") is True


@pytest.mark.unit
def test_is_verbose_line_detects_discovering_plugins() -> None:
    assert is_verbose_line("Discovering plugins...") is True


@pytest.mark.unit
def test_is_verbose_line_detects_binary_installation_options() -> None:
    assert is_verbose_line("BinaryInstallationOptions: {}") is True


@pytest.mark.unit
def test_is_verbose_line_detects_list_installations_options() -> None:
    assert is_verbose_line("ListInstallationsOptions returns") is True


@pytest.mark.unit
def test_is_verbose_line_detects_json_dumps_keyword() -> None:
    assert is_verbose_line("calling json.dumps on payload") is True


@pytest.mark.unit
def test_is_verbose_line_is_case_insensitive() -> None:
    assert is_verbose_line("[trace] lowercase variant") is True


@pytest.mark.unit
def test_is_verbose_line_returns_false_for_user_output() -> None:
    """Echte User-Ausgaben werden nicht als verbose erkannt."""
    assert is_verbose_line("Apply complete! Resources: 3 added.") is False


@pytest.mark.unit
def test_is_verbose_line_returns_false_for_plain_text() -> None:
    assert is_verbose_line("Deployment finished successfully") is False


@pytest.mark.unit
def test_is_verbose_line_returns_false_for_empty_string() -> None:
    assert is_verbose_line("") is False


# ---------------------------------------------------------------------------
# filter_logs
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_filter_logs_drops_verbose_lines_keeps_order() -> None:
    """Verbose Zeilen werden entfernt, übrige bleiben in Reihenfolge."""
    text = "\n".join(
        [
            "line one",
            "[TRACE] noise",
            "line two",
            "[DEBUG] more noise",
            "line three",
        ]
    )
    result = filter_logs(text)
    assert result == "line one\nline two\nline three"


@pytest.mark.unit
def test_filter_logs_strips_ansi_codes_from_kept_lines() -> None:
    text = "\x1b[32mgreen line\x1b[0m\nplain line"
    result = filter_logs(text)
    assert result == "green line\nplain line"


@pytest.mark.unit
def test_filter_logs_drops_empty_lines() -> None:
    text = "alpha\n\n   \nbeta"
    result = filter_logs(text)
    assert result == "alpha\nbeta"


@pytest.mark.unit
def test_filter_logs_truncates_when_over_max_lines() -> None:
    """Bei zu vielen Zeilen wird auf Kopf + Schwanz mit Ellipsis gekürzt."""
    lines = [f"line {i}" for i in range(100)]
    result = filter_logs("\n".join(lines), max_lines=50)
    out_lines = result.split("\n")
    # 20 head + ellipsis + 30 tail
    assert out_lines[0] == "line 0"
    assert "..." in out_lines
    assert out_lines[-1] == "line 99"
    assert len(out_lines) == 51


@pytest.mark.unit
def test_filter_logs_no_truncation_when_under_max_lines() -> None:
    text = "a\nb\nc"
    result = filter_logs(text, max_lines=100)
    assert result == "a\nb\nc"


@pytest.mark.unit
def test_filter_logs_handles_empty_string() -> None:
    assert filter_logs("") == ""


# ---------------------------------------------------------------------------
# format_logs
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_format_logs_empty_list_returns_empty_string() -> None:
    assert format_logs([]) == ""


@pytest.mark.unit
def test_format_logs_single_dict_entry_includes_icon_and_timestamp() -> None:
    entries = [
        {"timestamp": "12:00:00", "message": "hello", "level": "INFO"},
    ]
    result = format_logs(entries)
    assert result == "[inf] [12:00:00] hello"


@pytest.mark.unit
def test_format_logs_multiple_entries_joined_by_newline() -> None:
    entries = [
        {"timestamp": "t1", "message": "one", "level": "INFO"},
        {"timestamp": "t2", "message": "two", "level": "ERROR"},
    ]
    result = format_logs(entries)
    lines = result.split("\n")
    assert len(lines) == 2
    assert "one" in lines[0]
    assert "two" in lines[1]
    assert "[inf]" in lines[0]
    assert "[err]" in lines[1]


@pytest.mark.unit
def test_format_logs_strips_ansi_from_message() -> None:
    entries = [
        {"timestamp": "t", "message": "\x1b[32mok\x1b[0m", "level": "INFO"},
    ]
    result = format_logs(entries)
    assert "ok" in result
    assert "\x1b" not in result


@pytest.mark.unit
def test_format_logs_truncates_long_single_line_message() -> None:
    long_msg = "x" * 600
    entries = [{"timestamp": "t", "message": long_msg, "level": "INFO"}]
    result = format_logs(entries)
    assert "..." in result
    # 500 chars of x + ellipsis present
    assert "x" * 500 in result


@pytest.mark.unit
def test_format_logs_filters_long_multiline_message() -> None:
    """Lange, mehrzeilige Messages mit > 20 Zeilen werden gefiltert."""
    body = "\n".join([f"plain line {i}" for i in range(25)]) + "\n" + "x" * 200
    entries = [{"timestamp": "t", "message": body, "level": "INFO"}]
    result = format_logs(entries)
    # filter_logs was triggered; output still contains the plain lines
    assert "plain line 0" in result


@pytest.mark.unit
def test_format_logs_handles_string_entries() -> None:
    entries = ["\x1b[31mred\x1b[0m", "plain"]
    result = format_logs(entries)
    assert result == "red\nplain"


@pytest.mark.unit
def test_format_logs_falls_back_to_str_for_non_list() -> None:
    """Keine Liste -> str() Konvertierung."""
    assert format_logs("just a string") == "just a string"
    assert format_logs(None) == "None"
    assert format_logs(42) == "42"


@pytest.mark.unit
def test_format_logs_defaults_level_to_info_when_missing() -> None:
    entries = [{"timestamp": "t", "message": "m"}]
    result = format_logs(entries)
    assert result.startswith("[inf]")


# ---------------------------------------------------------------------------
# _get_icon
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_get_icon_known_levels() -> None:
    assert _get_icon("DEBUG") == "[dbg]"
    assert _get_icon("INFO") == "[inf]"
    assert _get_icon("SUCCESS") == " [ok]"
    assert _get_icon("WARNING") == "[warn]"
    assert _get_icon("ERROR") == "[err]"


@pytest.mark.unit
def test_get_icon_unknown_level_returns_default() -> None:
    """Unbekanntes Level liefert den Default-Marker."""
    assert _get_icon("CRITICAL") == "    -"
    assert _get_icon("") == "    -"
    assert _get_icon("info") == "    -"  # case-sensitive


# ---------------------------------------------------------------------------
# _translate_celery_infra_exception
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_translate_celery_infra_exception_not_registered() -> None:
    headline, kind = _translate_celery_infra_exception(
        "NotRegistered('tasks.pause_deployment')"
    )
    assert headline is not None
    assert "Worker" in headline
    assert kind == "celery_infrastructure"


@pytest.mark.unit
def test_translate_celery_infra_exception_worker_lost() -> None:
    headline, kind = _translate_celery_infra_exception("WorkerLostError(...)")
    assert headline is not None
    assert "Worker-Prozess" in headline
    assert kind == "celery_infrastructure"


@pytest.mark.unit
def test_translate_celery_infra_exception_timeout() -> None:
    headline, kind = _translate_celery_infra_exception("TimeoutError('deadline')")
    assert headline is not None
    assert "Zeit-Limit" in headline
    assert kind == "celery_infrastructure"


@pytest.mark.unit
def test_translate_celery_infra_exception_reject() -> None:
    headline, kind = _translate_celery_infra_exception("Reject('queue')")
    assert headline is not None
    assert "abgelehnt" in headline
    assert kind == "celery_infrastructure"


@pytest.mark.unit
def test_translate_celery_infra_exception_content_disallowed() -> None:
    headline, kind = _translate_celery_infra_exception("ContentDisallowed(...)")
    assert headline is not None
    assert "deserialisiert" in headline
    assert kind == "celery_infrastructure"


@pytest.mark.unit
def test_translate_celery_infra_exception_unknown_returns_none() -> None:
    """Unbekannte Exceptions liefern (None, None)."""
    headline, kind = _translate_celery_infra_exception("ValueError('boom')")
    assert headline is None
    assert kind is None


@pytest.mark.unit
def test_translate_celery_infra_exception_empty_input_returns_none() -> None:
    """Leerer Eingabe-String wird als unbekannt behandelt."""
    assert _translate_celery_infra_exception("") == (None, None)


@pytest.mark.unit
def test_translate_celery_infra_exception_substring_match() -> None:
    """Match erfolgt als Substring im exception-Repr."""
    headline, kind = _translate_celery_infra_exception(
        "celery.exceptions.NotRegistered: 'foo'"
    )
    assert headline is not None
    assert kind == "celery_infrastructure"


@pytest.mark.unit
def test_celery_infra_exceptions_table_well_formed() -> None:
    """Die Übersetzungstabelle hat (needle, headline, kind) Tripel."""
    assert len(_CELERY_INFRA_EXCEPTIONS) >= 1
    for entry in _CELERY_INFRA_EXCEPTIONS:
        assert len(entry) == 3
        needle, headline, kind = entry
        assert needle and isinstance(needle, str)
        assert headline and isinstance(headline, str)
        assert kind == "celery_infrastructure"
