"""Tests for the @openstack:file:<scope> marker extension and the
HCL type-shape validation that goes with it.

These exercise ``_parse_marker`` and ``_parse_one_variable`` as pure
functions — no Git clone, no FastAPI app, no DB.
"""
import pytest

from app.routers.apps import (
    MarkerError,
    _FILE_SCOPES,
    _OS_TYPES,
    _parse_marker,
    _parse_one_variable,
    _validate_file_var_shape,
)


def _build_block(var_name: str, var_type: str, description: str) -> str:
    """Render a minimal ``variable "x" {...}`` HCL block for parsing."""
    return (
        f'variable "{var_name}" {{\n'
        f"  type        = {var_type}\n"
        f'  description = "{description}"\n'
        f"  default     = {{}}\n"
        f"}}\n"
    )


@pytest.mark.unit
def test_file_type_is_registered():
    """``file`` must be in the supported set so the marker parses."""
    assert "file" in _OS_TYPES


@pytest.mark.unit
@pytest.mark.parametrize("scope", sorted(_FILE_SCOPES))
def test_marker_accepts_each_scope(scope):
    var_type = (
        "map(object({name=string, content_b64=string, size=number, content_type=string}))"
        if scope == "all"
        else "map(map(object({name=string, content_b64=string, size=number, content_type=string})))"
    )
    os_type, mode, multi, parsed_scope = _parse_marker(
        "task_pdf", var_type, f"Aufgabenstellung @openstack:file:{scope}"
    )
    assert os_type == "file"
    # mode/multi are owned by the classic OpenStack-resource branch;
    # for file vars they stay None and the wizard reads ``scope``
    # instead.
    assert mode is None
    assert multi is None
    assert parsed_scope == scope


@pytest.mark.unit
def test_marker_defaults_scope_to_all_when_slot_omitted():
    """Bare ``@openstack:file`` is shorthand for ``scope=all``."""
    os_type, _, _, scope = _parse_marker(
        "task_pdf",
        "map(object({name=string, content_b64=string, size=number, content_type=string}))",
        "Aufgabenstellung @openstack:file",
    )
    assert os_type == "file"
    assert scope is None  # caller resolves to "all" when reading


@pytest.mark.unit
def test_marker_rejects_unknown_scope_with_suggestion():
    """A typo like ``teams`` (plural) hints at the right token."""
    with pytest.raises(MarkerError) as exc:
        _parse_marker(
            "team_dataset",
            "map(map(object({})))",
            "Per-team data @openstack:file:teams",
        )
    assert "teams" in exc.value.message
    assert "team" in exc.value.message  # suggestion


@pytest.mark.unit
def test_marker_rejects_multi_slot_on_file_type():
    """Multi-flag is reserved for the future and must not appear today."""
    with pytest.raises(MarkerError) as exc:
        _parse_marker(
            "task_pdf",
            "map(object({}))",
            "Files @openstack:file:all:multi",
        )
    assert "multi" in exc.value.message.lower()


@pytest.mark.unit
@pytest.mark.parametrize(
    "scope, hcl_type, ok",
    [
        ("all", "map(object({name=string}))", True),
        ("all", "map(map(object({})))", False),
        ("all", "string", False),
        ("team", "map(map(object({})))", True),
        ("team", "map(object({}))", False),
        ("user", "map(map(object({})))", True),
        ("user", "list(string)", False),
    ],
)
def test_validate_file_var_shape(scope, hcl_type, ok):
    if ok:
        _validate_file_var_shape("v", hcl_type, scope)  # must not raise
    else:
        with pytest.raises(MarkerError):
            _validate_file_var_shape("v", hcl_type, scope)


@pytest.mark.unit
def test_parse_one_variable_emits_osScope_for_file_vars(tmp_path):
    """``_parse_one_variable`` is what the GET /apps/{id}/variables
    endpoint actually returns; verify the file payload carries
    ``osScope`` and skips the unrelated ``osMode``/``osMulti`` fields.
    """
    block = _build_block(
        "task_pdf",
        "map(object({name=string, content_b64=string, size=number, content_type=string}))",
        "Aufgabe @openstack:file:all",
    )
    out = _parse_one_variable(
        var_name="task_pdf",
        var_block=block,
        var_block_offset=0,
        file_content=block,
        file_label="terraform/variables.tf",
        source="terraform",
    )
    assert out.get("osType") == "file"
    assert out.get("osScope") == "all"
    assert "osMode" not in out
    assert "osMulti" not in out
    assert "markerError" not in out


@pytest.mark.unit
def test_parse_one_variable_attaches_marker_error_for_type_mismatch():
    """A file var with a wrong HCL type surfaces ``markerError`` so
    the wizard renders it as Free-Text + inline hint without the
    whole apps-variables endpoint failing."""
    block = _build_block(
        "broken_files",
        "string",  # wrong shape for any file scope
        "Whoops @openstack:file:all",
    )
    out = _parse_one_variable(
        var_name="broken_files",
        var_block=block,
        var_block_offset=0,
        file_content=block,
        file_label="terraform/variables.tf",
        source="terraform",
    )
    assert "markerError" in out
    assert "broken_files" == out["markerError"]["variable"]
    # The classic ``osType`` field must NOT be set when the marker
    # errored — the variable degrades to Free-Text on the frontend.
    assert "osType" not in out


@pytest.mark.unit
def test_classic_resource_marker_still_parses():
    """Regression — the file-type branch must not break the existing
    marker for OpenStack resources."""
    os_type, mode, multi, scope = _parse_marker(
        "net",
        "string",
        "Pick a network @openstack:network",
    )
    assert os_type == "network"
    assert scope is None  # not a file marker
