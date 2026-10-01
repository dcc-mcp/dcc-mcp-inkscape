"""Fixed menu actions and kernel-owned child identity checks without a live GUI."""

import argparse
import importlib.util
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from dcc_mcp_inkscape import menu_bridge
from dcc_mcp_inkscape import menu_process
from dcc_mcp_inkscape.resources import EXTENSION_FILES
from dcc_mcp_inkscape.runtime import InkscapeRuntime


@pytest.fixture
def proof(tmp_path, monkeypatch):
    executable = tmp_path / "inkscape.exe"
    actual = {
        "pid": 9876,
        "parent_pid": 8765,
        "image_win32": str(tmp_path / "pythonw.exe"),
        "creation_time": 200,
        "exit_time": 0,
    }
    host = {"pid": 7654, "creation_time": 100, "exit_time": 0}
    owned = menu_process.WindowsMenuProcess.__new__(menu_process.WindowsMenuProcess)
    owned.executable = executable
    owned.pid = 7654
    owned.handle = object()
    owned.job = object()
    owned.host_identity = dict(host)
    owned.ctypes = SimpleNamespace(byref=lambda value: value)
    owned.wintypes = SimpleNamespace(BOOL=lambda: SimpleNamespace(value=False))
    member = {"value": True}

    def membership(handle, job, result):
        assert job is owned.job
        result.value = member["value"]
        return True

    owned.kernel = SimpleNamespace(IsProcessInJob=membership)

    class Child:
        handle = object()
        closed = False

        def __init__(self, pid):
            assert pid == actual["pid"]

        def query(self):
            return actual

        def close(self):
            Child.closed = True

    monkeypatch.setattr(menu_process, "RetainedWindowsProcess", Child)
    monkeypatch.setattr(menu_process, "query_process", lambda *args, **kwargs: host)
    identity = {"menu_pid": 9876, "menu_parent_pid": 8765, "menu_executable": actual["image_win32"]}
    return owned, identity, actual, host, member, Child


def test_menu_identity_requires_exact_kernel_job_and_image_birth(proof):
    owned, identity, actual, host, member, child = proof
    result = owned.verify_menu(identity)
    assert result["mode"] == "windows-owned-job"
    assert result["job_membership_verified"]
    assert child.closed


@pytest.mark.parametrize(
    "change",
    [
        "outside_job",
        "wrong_image",
        "wrong_parent",
        "older_birth",
        "child_exited",
        "host_exited",
        "host_reused",
        "claimed_image",
    ],
)
def test_arbitrary_or_stale_menu_identity_is_rejected(proof, change):
    owned, identity, actual, host, member, child = proof
    if change == "outside_job":
        member["value"] = False
    elif change == "wrong_image":
        actual["image_win32"] = str(owned.executable.parent / "other.exe")
    elif change == "wrong_parent":
        actual["parent_pid"] = 1
    elif change == "older_birth":
        actual["creation_time"] = 50
    elif change == "child_exited":
        actual["exit_time"] = 300
    elif change == "host_exited":
        host["exit_time"] = 300
    elif change == "host_reused":
        host["creation_time"] = 150
    else:
        identity["menu_executable"] = str(owned.executable.parent / "other.exe")
    with pytest.raises(RuntimeError):
        owned.verify_menu(identity)
    assert child.closed


def test_non_windows_verified_control_is_explicitly_unsupported(monkeypatch):
    executable = Path("unused")
    monkeypatch.setattr(menu_process.os, "name", "posix")
    with pytest.raises(ValueError, match="Windows only"):
        menu_process.WindowsMenuProcess([], {}, executable)


def test_native_handshake_is_nonce_profile_bound_and_no_overwrite(tmp_path, monkeypatch):
    workspace = tmp_path.resolve()
    profile = workspace / "private/profile"
    (profile.parent / "evidence").mkdir(parents=True)
    config = {"workspace": str(workspace), "profile": str(profile)}
    monkeypatch.setattr(menu_bridge, "load_context", lambda path: {"config": config})
    request = {"nonce": "1" * 32, "panel": "status", **config}
    monkeypatch.setenv("DCC_MCP_INKSCAPE_MENU_REQUEST", json.dumps(request))
    monkeypatch.setenv("SELF_CALL", "true")
    result = menu_bridge.publish_menu_identity(Path("unused"), "status")
    assert type(result["menu_pid"]) is int and result["menu_pid"] > 0
    assert result["nonce"] == request["nonce"]
    with pytest.raises(FileExistsError):
        menu_bridge.publish_menu_identity(Path("unused"), "status")
    with pytest.raises(ValueError):
        menu_bridge.publish_menu_identity(Path("unused"), "settings")


def test_manual_menu_does_not_write_identity_without_control_request(monkeypatch):
    monkeypatch.delenv("DCC_MCP_INKSCAPE_MENU_REQUEST", raising=False)
    assert menu_bridge.publish_menu_identity(Path("unused"), "status") is None


@pytest.mark.parametrize("panel", ["status;export-do", "unknown", "status\n", None])
def test_control_open_rejects_arbitrary_actions_before_any_launch(tmp_path, panel):
    runtime = InkscapeRuntime.__new__(InkscapeRuntime)
    runtime.workspace = tmp_path
    with pytest.raises(ValueError, match="Control panel"):
        runtime.control_open(panel)


def test_control_source_cannot_escape_workspace_or_embed_external_data(tmp_path):
    runtime = InkscapeRuntime.__new__(InkscapeRuntime)
    runtime.workspace = tmp_path
    with pytest.raises(ValueError, match="outside"):
        runtime.control_open(source_file=tmp_path.parent / "outside.svg")
    fixture = tmp_path / "unsafe-test-fixture.svg"
    fixture.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><image href="https://example.invalid/a.png"/></svg>', encoding="utf-8"
    )
    with pytest.raises(ValueError):
        runtime.control_open(source_file=fixture)


def test_control_open_returns_only_verified_native_identity_and_closes_proof_handles(tmp_path, monkeypatch):
    runtime = InkscapeRuntime.__new__(InkscapeRuntime)
    runtime.workspace = tmp_path.resolve()
    runtime.state = runtime.workspace / "private"
    runtime.profile = runtime.state / "profile"
    runtime.executable = runtime.workspace / "inkscape.exe"
    runtime.environment = {"INKSCAPE_PROFILE_DIR": str(runtime.profile)}
    monkeypatch.setattr(menu_bridge, "load_context", lambda context: {})
    observed = {}

    class Owned:
        pid = 9001

        def __init__(self, command, environment, workspace):
            observed["command"] = command
            request = json.loads(environment["DCC_MCP_INKSCAPE_MENU_REQUEST"])
            evidence = runtime.state / "evidence" / ("menu-" + request["nonce"] + ".json")
            evidence.write_text(
                json.dumps(
                    {
                        **request,
                        "menu_pid": 9002,
                        "menu_parent_pid": 9003,
                        "menu_executable": "unit-fixture",
                        "self_call": True,
                    }
                ),
                encoding="utf-8",
            )

        def poll(self):
            return None

        def verify_menu(self, identity):
            assert identity["menu_pid"] == 9002
            observed["verified"] = True
            return {"mode": "unit-fixture"}

        def close(self):
            observed["closed"] = True

    monkeypatch.setattr(menu_process, "WindowsMenuProcess", Owned)
    result = runtime.control_open("settings")
    assert observed["verified"] and observed["closed"]
    assert observed["command"][-1] == "--actions=org.dcc-mcp.menu.settings"
    assert result["host_pid"] == 9001 and result["menu_pid"] == 9002
    assert result["accepted"] is False


def test_fixed_no_document_entries_choose_panel_without_any_cli_parameters(monkeypatch):
    # The real no-document protocol drops INX parameters. Model only its public
    # base interface, without a top-level inkex.InkscapeExtension export.
    inkex = types.ModuleType("inkex")
    inkex.__path__ = []
    base = types.ModuleType("inkex.base")

    class NativeBase:
        def __init__(self):
            self.arg_parser = argparse.ArgumentParser()
            self.add_arguments(self.arg_parser)

    base.InkscapeExtension = NativeBase
    monkeypatch.setitem(sys.modules, "inkex", inkex)
    monkeypatch.setitem(sys.modules, "inkex.base", base)
    monkeypatch.setitem(sys.modules, "menu_bridge", menu_bridge)

    def load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        return module

    native = load("dcc_mcp_menu", EXTENSION_FILES["dcc_mcp_menu.py"])
    assert native.DccMcpMenu().arg_parser.parse_args([]).page == "status"
    settings = load("dcc_mcp_settings", EXTENSION_FILES["dcc_mcp_settings.py"])
    assert settings.DccMcpSettings().arg_parser.parse_args([]).page == "settings"
    connection = load("dcc_mcp_connection", EXTENSION_FILES["dcc_mcp_connection.py"])
    assert connection.DccMcpConnection().arg_parser.parse_args([]).page == "connection"
