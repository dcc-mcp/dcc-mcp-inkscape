"""Bounded menu diagnostics and narrowly scoped nonfatal Gdk routing."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from dcc_mcp_inkscape import installation
from dcc_mcp_inkscape import menu
from dcc_mcp_inkscape import menu_bridge
from dcc_mcp_inkscape.cli import build_parser
from dcc_mcp_inkscape.resources import MENU_CONTEXT

EXPECTED_GDK_MESSAGE = "gdk_seat_default_remove_tool: assertion 'tool != NULL' failed"


@pytest.fixture
def menu_install(tmp_path, monkeypatch, capsys):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    host = tmp_path / "inkscape.exe"
    host.write_bytes(b"unit fixture; never executed")
    monkeypatch.setattr(
        installation,
        "_probe",
        lambda config: {
            "host_version": "1.4.4",
            "imports": {"adapter_file": str(installation.HERE / "__init__.py"), "core_version": "0.20.36"},
        },
    )
    monkeypatch.setattr(
        installation,
        "_runtime",
        lambda config, instance_id=None: {"status": "not_running", "ready": False, "entries": []},
    )
    args = build_parser().parse_args(
        [
            "install",
            "--workspace",
            str(workspace),
            "--dcc-path",
            str(host),
            "--python",
            sys.executable,
            "--gateway-port",
            "19771",
            "--json",
            "--yes",
        ]
    )
    assert installation.run_install_command(args) == 40
    capsys.readouterr()
    receipt = workspace / ".dcc-mcp-inkscape/install/receipt.json"
    context = workspace / ".inkscape-mcp/profile/extensions" / MENU_CONTEXT
    return workspace, receipt, context


def file_snapshot(workspace):
    return {str(path.relative_to(workspace)): path.read_bytes() for path in workspace.rglob("*") if path.is_file()}


class FakeGLib:
    LogLevelFlags = SimpleNamespace(
        LEVEL_CRITICAL=8,
        LEVEL_WARNING=16,
        LEVEL_ERROR=4,
        FLAG_FATAL=2,
        FLAG_RECURSION=1,
    )

    def __init__(self):
        self.registrations = []
        self.default_calls = []

    def log_set_handler(self, domain, levels, callback, user_data=None):
        self.registrations.append((domain, levels, callback, user_data))
        return 73

    def log_default_handler(self, domain, levels, message, user_data=None):
        self.default_calls.append((domain, levels, message, user_data))

    def log_set_default_handler(self, *args):
        pytest.fail("Do not replace the process-wide default log handler")

    def log_set_fatal_mask(self, *args):
        pytest.fail("Do not weaken GLib's fatal-message policy")

    def log_set_always_fatal(self, *args):
        pytest.fail("Do not weaken GLib's fatal-message policy")

    def emit(self, domain="Gdk", levels=8, message=EXPECTED_GDK_MESSAGE):
        _, _, callback, user_data = self.registrations[-1]
        callback(domain, levels, message, user_data)


@pytest.fixture
def handler(menu_install, monkeypatch):
    context = menu_install[2]
    monkeypatch.setattr(menu_bridge.sys, "platform", "win32")
    glib = FakeGLib()
    assert menu_bridge.install_gdk_diagnostic_handler(glib, context, "connection") == 73
    assert len(glib.registrations) == 1
    assert glib.registrations[0][:2] == ("Gdk", glib.LogLevelFlags.LEVEL_CRITICAL)
    return context, glib


def test_missing_diagnostics_log_is_empty_and_readonly(menu_install):
    workspace, _, context = menu_install
    before = file_snapshot(workspace)
    report = menu_bridge.diagnostic_status(context)
    assert report["available"] is True
    assert report["recent"] == []
    assert report["counts"].get("error", 0) == 0
    assert report["counts"].get("expected_nonfatal", 0) == 0
    assert file_snapshot(workspace) == before


def test_recorded_diagnostics_are_queryable_without_readiness_claim(menu_install):
    workspace, _, context = menu_install
    assert menu_bridge.record_diagnostic(context, "unit-test", "actual error", page="settings") is True
    assert (
        menu_bridge.record_diagnostic(
            context, "glib-gdk", EXPECTED_GDK_MESSAGE, severity="expected_nonfatal", page="connection"
        )
        is True
    )
    before = file_snapshot(workspace)
    report = menu_bridge.diagnostic_status(context)
    assert report["available"] is True
    assert report["counts"]["error"] == 1
    assert report["counts"]["expected_nonfatal"] == 1
    events = {event["message"]: event for event in report["recent"]}
    assert events["actual error"]["source"] == "unit-test"
    assert events["actual error"]["severity"] == "error"
    assert events["actual error"]["page"] == "settings"
    assert events[EXPECTED_GDK_MESSAGE]["severity"] == "expected_nonfatal"
    log = Path(report["log_file"])
    log.resolve().relative_to(workspace)
    assert log.is_file()
    for operation in ("status", "settings", "connection"):
        status = menu.operate(context, operation)
        assert status["diagnostics"]["available"] is True
        assert status["installation"]["readiness"]["status"] == "not_running"
        assert status["installation"]["readiness"]["ready"] is False
        assert status["installation"]["verify"]["directly_usable"] is False
    assert file_snapshot(workspace) == before


@pytest.mark.parametrize("part", ["context", "config", "receipt_scope", "context_digest"])
def test_tampered_scope_cannot_write_diagnostics(menu_install, part):
    workspace, receipt_path, context = menu_install
    if part == "context":
        context.write_text("{}", encoding="utf-8")
    elif part == "config":
        (receipt_path.parent / "config.json").write_text("{}", encoding="utf-8")
    else:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if part == "receipt_scope":
            receipt["workspace"] = str(workspace.parent)
        else:
            next(record for record in receipt["files"] if record["path"] == str(context))["sha256"] = "0" * 64
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    before = file_snapshot(workspace)
    assert menu_bridge.record_diagnostic(context, "unit-test", "must not write") is False
    assert file_snapshot(workspace) == before


@pytest.mark.parametrize(
    "contents", [b"not JSON", b"[]", b"null", b" " * (1024 * 1024 + 1)], ids=["non-json", "array", "null", "oversized"]
)
def test_invalid_or_oversized_log_is_not_overwritten(menu_install, contents):
    _, _, context = menu_install
    assert menu_bridge.record_diagnostic(context, "unit-test", "initial error") is True
    log = Path(menu_bridge.diagnostic_status(context)["log_file"])
    log.write_bytes(contents)
    assert menu_bridge.diagnostic_status(context)["available"] is False
    assert menu_bridge.record_diagnostic(context, "unit-test", "must not replace invalid evidence") is False
    assert log.read_bytes() == contents


def test_symlink_log_cannot_be_read_or_written(menu_install, tmp_path):
    _, _, context = menu_install
    assert menu_bridge.record_diagnostic(context, "unit-test", "initial error") is True
    log = Path(menu_bridge.diagnostic_status(context)["log_file"])
    outside = tmp_path / "operator-owned-log.json"
    outside.write_bytes(b"operator evidence; must remain unchanged")
    log.unlink()
    try:
        log.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("This platform cannot create a test symbolic link without additional privileges")
    with pytest.raises(ValueError):
        menu_bridge.diagnostic_status(context)
    assert menu_bridge.record_diagnostic(context, "unit-test", "must not write through link") is False
    assert outside.read_bytes() == b"operator evidence; must remain unchanged"


def test_failed_atomic_publication_preserves_previous_log(menu_install, monkeypatch):
    _, _, context = menu_install
    assert menu_bridge.record_diagnostic(context, "unit-test", "initial error") is True
    log = Path(menu_bridge.diagnostic_status(context)["log_file"])
    before = log.read_bytes()
    original_replace = menu_bridge.os.replace

    def fail_publication(source, destination):
        if Path(destination).resolve() == log.resolve():
            raise PermissionError("simulated owned-log publication failure")
        return original_replace(source, destination)

    monkeypatch.setattr(menu_bridge.os, "replace", fail_publication)
    assert menu_bridge.record_diagnostic(context, "unit-test", "failed write") is False
    assert log.read_bytes() == before


def test_exact_nonfatal_gdk_message_is_logged_without_default_output(handler):
    context, glib = handler
    glib.emit()
    assert glib.default_calls == []
    report = menu_bridge.diagnostic_status(context)
    assert report["counts"]["expected_nonfatal"] == 1
    assert report["counts"].get("error", 0) == 0
    assert report["recent"][-1]["message"] == EXPECTED_GDK_MESSAGE
    assert report["recent"][-1]["page"] == "connection"


@pytest.mark.parametrize(
    "domain,levels,message",
    [
        ("Gdk", 8, "another Gdk failure"),
        ("Gdk", 8, EXPECTED_GDK_MESSAGE + " extra diagnostic"),
        ("Gdk", 8, "prefix " + EXPECTED_GDK_MESSAGE),
        ("Gtk", 8, EXPECTED_GDK_MESSAGE),
        (None, 8, EXPECTED_GDK_MESSAGE),
        ("Gdk", 16, EXPECTED_GDK_MESSAGE),
        ("Gdk", 4, EXPECTED_GDK_MESSAGE),
        ("Gdk", 8 | 2, EXPECTED_GDK_MESSAGE),
        ("Gdk", 8 | 1, EXPECTED_GDK_MESSAGE),
        ("Gdk", 8 | 2 | 1, EXPECTED_GDK_MESSAGE),
    ],
)
def test_other_messages_levels_and_fatal_flags_keep_default_output(handler, domain, levels, message):
    context, glib = handler
    glib.emit(domain, levels, message)
    assert len(glib.default_calls) == 1
    assert glib.default_calls[0][:3] == (domain, levels, message)
    report = menu_bridge.diagnostic_status(context)
    assert report["counts"]["error"] == 1
    assert report["counts"].get("expected_nonfatal", 0) == 0


def test_known_message_still_uses_default_output_when_logging_fails(handler, monkeypatch):
    _, glib = handler
    monkeypatch.setattr(menu_bridge, "record_diagnostic", lambda *args, **kwargs: False)
    glib.emit()
    assert len(glib.default_calls) == 1
    assert glib.default_calls[0][:3] == ("Gdk", 8, EXPECTED_GDK_MESSAGE)


def test_known_message_keeps_default_output_with_modified_installation(handler, menu_install):
    context, glib = handler
    workspace, receipt, _ = menu_install
    (receipt.parent / "config.json").write_text("{}", encoding="utf-8")
    before = file_snapshot(workspace)
    glib.emit()
    assert len(glib.default_calls) == 1
    assert glib.default_calls[0][:3] == ("Gdk", 8, EXPECTED_GDK_MESSAGE)
    assert file_snapshot(workspace) == before


def test_handler_setup_does_not_create_evidence_or_claim_a_connection(menu_install, monkeypatch):
    workspace, _, context = menu_install
    monkeypatch.setattr(menu_bridge.sys, "platform", "win32")
    before = file_snapshot(workspace)
    glib = FakeGLib()
    assert menu_bridge.install_gdk_diagnostic_handler(glib, context, "status") == 73
    assert file_snapshot(workspace) == before
    assert glib.default_calls == []


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_non_windows_does_not_register_a_diagnostic_filter(menu_install, monkeypatch, platform):
    monkeypatch.setattr(menu_bridge.sys, "platform", platform)
    glib = FakeGLib()
    assert menu_bridge.install_gdk_diagnostic_handler(glib, menu_install[2], "status") is None
    assert glib.registrations == []


@pytest.mark.parametrize("missing", ["log_set_handler", "log_default_handler", "LogLevelFlags"])
def test_missing_glib_api_leaves_existing_output_behavior(menu_install, monkeypatch, missing):
    monkeypatch.setattr(menu_bridge.sys, "platform", "win32")
    glib = FakeGLib()
    available = {
        "log_set_handler": glib.log_set_handler,
        "log_default_handler": glib.log_default_handler,
        "LogLevelFlags": glib.LogLevelFlags,
    }
    available.pop(missing)
    assert menu_bridge.install_gdk_diagnostic_handler(SimpleNamespace(**available), menu_install[2], "status") is None
    assert glib.registrations == []


def test_gdk_filter_does_not_replace_python_stderr(handler, capsys):
    _, glib = handler
    before = sys.stderr
    sys.stderr.write("Traceback: actual Python failure\n")
    glib.emit()
    assert sys.stderr is before
    assert capsys.readouterr().err == "Traceback: actual Python failure\n"


def test_diagnostic_ring_and_utf8_messages_remain_bounded(menu_install):
    _, _, context = menu_install
    for index in range(34):
        assert menu_bridge.record_diagnostic(context, "unit-test", "event " + str(index)) is True
    report = menu_bridge.diagnostic_status(context)
    assert len(report["recent"]) == 32
    assert report["recent"][0]["message"] == "event 2"
    assert report["recent"][-1]["message"] == "event 33"
    message = "汉" * 1000
    assert menu_bridge.record_diagnostic(context, "unit-test", message) is True
    report = menu_bridge.diagnostic_status(context)
    bounded = report["recent"][-1]["message"]
    assert len(bounded.encode("utf-8")) <= 2048
    assert message.startswith(bounded)
    assert "\ufffd" not in bounded
    assert len(report["recent"]) == 32
    assert Path(report["log_file"]).stat().st_size <= 131072


@pytest.mark.parametrize(
    "change",
    ["owner", "boolean_schema", "boolean_pid", "unknown_severity", "invalid_source", "invalid_page", "extra_key"],
)
def test_structurally_modified_event_log_is_not_adopted(menu_install, change):
    _, _, context = menu_install
    assert menu_bridge.record_diagnostic(context, "unit-test", "original diagnostic") is True
    log = Path(menu_bridge.diagnostic_status(context)["log_file"])
    value = json.loads(log.read_text(encoding="utf-8"))
    if change == "owner":
        value["owner"] = "other-owner"
    elif change == "boolean_schema":
        value["schema_version"] = True
    elif change == "boolean_pid":
        value["events"][0]["pid"] = True
    elif change == "unknown_severity":
        value["events"][0]["severity"] = "connected"
    elif change == "invalid_source":
        value["events"][0]["source"] = "../other-scope"
    elif change == "invalid_page":
        value["events"][0]["page"] = "start"
    else:
        value["events"][0]["ready"] = True
    contents = json.dumps(value).encode("utf-8")
    log.write_bytes(contents)
    assert menu_bridge.diagnostic_status(context)["available"] is False
    assert menu_bridge.record_diagnostic(context, "unit-test", "must not overwrite invalid evidence") is False
    assert log.read_bytes() == contents


@pytest.mark.parametrize(
    "source,severity,page",
    [("../outside", "error", None), ("unit-test", "ready", None), ("unit-test", "error", "start")],
)
def test_invalid_diagnostic_fields_do_not_create_a_log(menu_install, source, severity, page):
    workspace, _, context = menu_install
    before = file_snapshot(workspace)
    assert menu_bridge.record_diagnostic(context, source, "invalid event", severity, page) is False
    assert file_snapshot(workspace) == before


def test_existing_diagnostic_lock_is_preserved(menu_install):
    _, _, context = menu_install
    assert menu_bridge.record_diagnostic(context, "unit-test", "original diagnostic") is True
    log = Path(menu_bridge.diagnostic_status(context)["log_file"])
    lock = log.with_suffix(".lock")
    lock.write_bytes(b"other owned writer")
    before = log.read_bytes()
    assert menu_bridge.record_diagnostic(context, "unit-test", "must not replace concurrent evidence") is False
    assert log.read_bytes() == before
    assert lock.read_bytes() == b"other owned writer"


def test_bridge_retains_successful_child_stderr_without_changing_its_report(menu_install, monkeypatch):
    context = menu_install[2]
    expected = {"installation": {"readiness": {"status": "not_running", "ready": False}}}
    monkeypatch.setattr(
        menu_bridge.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout=json.dumps(expected).encode("utf-8"), stderr=b"actual child warning"
        ),
    )
    assert menu_bridge.invoke(context, "connection") == expected
    diagnostics = menu_bridge.diagnostic_status(context)
    assert diagnostics["counts"]["warning"] == 1
    assert diagnostics["recent"][-1]["message"] == "actual child warning"
    assert diagnostics["recent"][-1]["page"] == "connection"


def test_bridge_failed_child_keeps_traceback_and_never_returns_success(menu_install, monkeypatch):
    context = menu_install[2]
    monkeypatch.setattr(
        menu_bridge.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=10, stdout=b'{"error":"actual operation failure"}', stderr=b"Traceback: original child failure"
        ),
    )
    with pytest.raises(ValueError, match="actual operation failure"):
        menu_bridge.invoke(context, "connection")
    diagnostics = menu_bridge.diagnostic_status(context)
    assert diagnostics["counts"]["error"] >= 1
    messages = {event["message"] for event in diagnostics["recent"]}
    assert "Traceback: original child failure" in messages
    assert "actual operation failure" in messages


def test_bridge_malformed_response_keeps_error_evidence(menu_install, monkeypatch):
    context = menu_install[2]
    monkeypatch.setattr(
        menu_bridge.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout=b"not JSON", stderr=b"original response diagnostic"
        ),
    )
    with pytest.raises(ValueError, match="original response diagnostic"):
        menu_bridge.invoke(context, "status")
    diagnostics = menu_bridge.diagnostic_status(context)
    assert any(event["message"] == "original response diagnostic" for event in diagnostics["recent"])


def test_background_start_uses_owned_log_and_no_console_without_claiming_ready(menu_install, monkeypatch):
    context = menu_install[2]
    observed = {}

    def launch(command, **options):
        observed["command"] = command
        observed["options"] = options
        observed["log_file"] = options["stdout"].name
        options["stderr"].write(b"original controller startup diagnostic")
        return SimpleNamespace(pid=123456)

    monkeypatch.setattr(menu.subprocess, "Popen", launch)
    monkeypatch.setattr(menu, "_wait_for_publication", lambda config, child: "starting")
    report = menu.operate(context, "start")
    options = observed["options"]
    assert observed["command"][1:4] == ["-m", "dcc_mcp_inkscape", "serve"]
    assert "--actions" not in observed["command"]
    assert "--with-gui" not in observed["command"]
    assert options["shell"] is False
    assert options["stdin"] == menu.subprocess.DEVNULL
    assert options["stdout"] is options["stderr"]
    assert options["creationflags"] == getattr(menu.subprocess, "CREATE_NO_WINDOW", 0)
    assert Path(observed["log_file"]).read_bytes() == b"original controller startup diagnostic"
    assert report["startup_state"] == "starting"
    assert report["installation"]["readiness"]["ready"] is False
    assert report["installation"]["verify"]["directly_usable"] is False


def test_successful_child_keeps_report_and_temporary_diagnostic_when_log_write_fails(menu_install, monkeypatch):
    workspace, _, context = menu_install
    expected = {
        "operation": "connection",
        "installation": {"readiness": {"status": "not_running", "ready": False}, "verify": {"directly_usable": False}},
    }
    stderr = "original child warning"
    monkeypatch.setattr(menu_bridge, "record_diagnostic", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        menu_bridge.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout=json.dumps(expected).encode("utf-8"), stderr=stderr.encode("utf-8")
        ),
    )
    before = file_snapshot(workspace)
    report = menu_bridge.invoke(context, "connection")
    assert report["operation"] == expected["operation"]
    assert report["installation"] == expected["installation"]
    fallback = report["diagnostic_retention_failure"]
    assert fallback["retention_failed"] is True
    assert fallback["source"] == "controller"
    assert fallback["severity"] == "warning"
    assert fallback["message"] == stderr
    assert len(fallback["message"].encode("utf-8")) <= 2048
    assert file_snapshot(workspace) == before


def test_failed_child_keeps_original_error_and_bounded_stderr_when_log_write_fails(menu_install, monkeypatch):
    context = menu_install[2]
    original_error = "actual operation failure"
    stderr = "original Unicode stderr: " + "汉𐍈" * 1000
    monkeypatch.setattr(menu_bridge, "record_diagnostic", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        menu_bridge.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=10,
            stdout=json.dumps({"error": original_error}).encode("utf-8"),
            stderr=stderr.encode("utf-8"),
        ),
    )
    with pytest.raises(ValueError) as failure:
        menu_bridge.invoke(context, "connection")
    error, separator, retained_stderr = str(failure.value).partition("\nDiagnostic log write failed: ")
    assert error == original_error
    assert separator
    assert retained_stderr.startswith("original Unicode stderr: ")
    assert stderr.startswith(retained_stderr)
    assert retained_stderr != stderr
    assert len(retained_stderr.encode("utf-8")) <= 2048
    assert "\ufffd" not in retained_stderr


def test_successful_child_temporary_diagnostic_bounds_unicode_when_log_write_fails(menu_install, monkeypatch):
    context = menu_install[2]
    expected = {"installation": {"readiness": {"status": "starting", "ready": False}}}
    stderr = "original Unicode warning: " + "汉𐍈" * 1000
    monkeypatch.setattr(menu_bridge, "record_diagnostic", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        menu_bridge.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout=json.dumps(expected).encode("utf-8"), stderr=stderr.encode("utf-8")
        ),
    )
    report = menu_bridge.invoke(context, "connection")
    assert report["installation"] == expected["installation"]
    fallback = report["diagnostic_retention_failure"]
    assert fallback["retention_failed"] is True
    assert fallback["source"] == "controller"
    assert fallback["severity"] == "warning"
    retained = fallback["message"]
    assert retained.startswith("original Unicode warning: ")
    assert stderr.startswith(retained)
    assert retained != stderr
    assert len(retained.encode("utf-8")) <= 2048
    assert "\ufffd" not in retained
