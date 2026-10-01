"""Fixed menu actions and kernel-owned child identity checks without a live GUI."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from dcc_mcp_inkscape import menu_bridge
from dcc_mcp_inkscape import menu_process


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
    monkeypatch.setattr(menu_process.os, "name", "posix")
    with pytest.raises(ValueError, match="Windows only"):
        menu_process.WindowsMenuProcess([], {}, Path("unused"))


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
