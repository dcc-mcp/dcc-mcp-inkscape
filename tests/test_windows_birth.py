"""Pure regressions for owned Windows process birth retention.

Kernel events, process queries, and native output are synthetic fixtures. These
tests never launch Inkscape, attach a debugger, or alter an operator session.
"""

from __future__ import annotations

import copy
import ctypes
import json
import os
import subprocess
import threading
from types import SimpleNamespace
from unittest.mock import Mock
from unittest.mock import call

import pytest

from dcc_mcp_inkscape import runtime as runtime_module
from dcc_mcp_inkscape import windows_process as windows_module

DBG_CONTINUE = 0x00010002
DBG_EXCEPTION_NOT_HANDLED = 0x80010001
BREAKPOINT = 0x80000003


def _event(code, pid=4321, file=901, exception=BREAKPOINT, first_chance=1):
    return SimpleNamespace(
        code=code,
        pid=pid,
        tid=pid + 100,
        data=SimpleNamespace(
            create=SimpleNamespace(file=file, process=801, thread=802),
            dll=SimpleNamespace(file=file),
            exception=SimpleNamespace(record=SimpleNamespace(code=exception), first_chance=first_chance),
        ),
    )


@pytest.fixture
def collector():
    process = windows_module.WindowsNativeProcess.__new__(windows_module.WindowsNativeProcess)
    process.command = ["owned-host.exe", "--action-list"]
    process.environment = {"OWNED_PROFILE": "isolated"}
    process.process = Mock(pid=4321)
    process.host_process = None
    process.births = {}
    process.handles = {}
    process.active = set()
    process.loader_breakpoints = set()
    process.errors = []
    process.report = None
    process.ready = threading.Event()
    process.stop = threading.Event()
    process.kernel = Mock()
    return process


@pytest.fixture
def chain(tmp_path):
    executable = tmp_path / "application" / "bin" / "inkscape.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"Synthetic executable fixture: never executed")
    native_dir = r"\Device\HarddiskVolume7\application\bin"

    def record(pid, parent_pid, creation_time, name):
        return {
            "requested_pid": pid,
            "pid": pid,
            "parent_pid": parent_pid,
            "creation_time": creation_time,
            "exit_time": 0,
            "exit_status": 259,
            "image_native": native_dir + "\\" + name,
            "image_win32": str(executable.parent / name),
        }

    host = record(4321, 4000, 10, "inkscape.exe")
    helper = record(4322, 4321, 20, "gspawn-win64-helper.exe")
    extension = record(4323, 4322, 30, "pythonw.exe")
    final_helper = dict(helper, exit_time=40, exit_status=0)
    evidence = {
        "nonce": "owned-native-invocation",
        "self_call": "true",
        "extension_pid": 4323,
        "parent_pid": 4322,
        "windows_process_lineage": copy.deepcopy([extension, final_helper, host]),
        "object_count": 1,
    }
    observation = {
        "mode": "windows-debug-birth-retention",
        "birth_processes": copy.deepcopy([host, helper, extension]),
        "helpers": [copy.deepcopy(final_helper)],
        "errors": [],
    }
    return SimpleNamespace(executable=executable, host=host, evidence=evidence, observation=observation)


@pytest.mark.parametrize("file", [0, 901])
def test_birth_retains_query_identity_and_closes_only_its_image_file(collector, monkeypatch, file):
    snapshot = {"pid": 4321, "parent_pid": 4000, "creation_time": 10}
    retained = Mock()
    retained.query.return_value = snapshot
    acquire = Mock(return_value=retained)
    monkeypatch.setattr(windows_module, "RetainedWindowsProcess", acquire)

    assert collector._handle_event(_event(3, file=file)) == DBG_CONTINUE
    acquire.assert_called_once_with(4321)
    retained.query.assert_called_once_with()
    assert collector.handles[4321] is retained
    assert collector.births[4321] == snapshot
    assert collector.host_process == snapshot
    assert collector.active == {4321}
    assert collector.ready.is_set()
    retained.close.assert_not_called()
    assert collector.kernel.CloseHandle.call_args_list == ([call(file)] if file else [])


@pytest.mark.parametrize("reason", ["duplicate", "bound", "query-error"])
def test_invalid_birth_still_releases_image_file_without_closing_system_handles(collector, monkeypatch, reason):
    retained = Mock()
    acquire = Mock(return_value=retained)
    monkeypatch.setattr(windows_module, "RetainedWindowsProcess", acquire)
    if reason == "duplicate":
        collector.handles[4321] = Mock()
    elif reason == "bound":
        collector.handles = {pid: Mock() for pid in range(64)}
    else:
        retained.query.side_effect = OSError("Cannot query birth identity")

    with pytest.raises((RuntimeError, OSError)):
        collector._handle_event(_event(3))
    collector.kernel.CloseHandle.assert_called_once_with(901)
    if reason == "query-error":
        assert collector.handles[4321] is retained
    else:
        acquire.assert_not_called()


def test_exit_requires_a_matching_birth_and_does_not_release_retained_identity(collector):
    retained = Mock()
    collector.handles[4321] = retained
    collector.active = {4321}
    assert collector._handle_event(_event(5)) == DBG_CONTINUE
    assert not collector.active
    retained.close.assert_not_called()
    collector.kernel.CloseHandle.assert_not_called()
    with pytest.raises(RuntimeError, match="matching owned process birth"):
        collector._handle_event(_event(5))


@pytest.mark.parametrize("file", [0, 902])
def test_dll_notification_releases_only_a_present_file_handle(collector, file):
    assert collector._handle_event(_event(6, file=file)) == DBG_CONTINUE
    if file:
        collector.kernel.CloseHandle.assert_called_once_with(file)
    else:
        collector.kernel.CloseHandle.assert_not_called()


def test_initial_loader_breakpoint_is_consumed_once_per_owned_process(collector):
    collector.active = {4321, 4322}
    collector.births = {4321: {"pid": 4321}, 4322: {"pid": 4322}}
    for pid in (4321, 4322):
        assert collector._handle_event(_event(1, pid=pid)) == DBG_CONTINUE
        assert collector._handle_event(_event(1, pid=pid)) == DBG_EXCEPTION_NOT_HANDLED


@pytest.mark.parametrize(
    ("exception", "first_chance", "pid"),
    [(0xC0000005, 1, 4321), (0x80000004, 1, 4321), (BREAKPOINT, 0, 4321), (BREAKPOINT, 1, 9999)],
)
def test_real_exceptions_and_unowned_or_second_chance_traps_are_not_swallowed(collector, exception, first_chance, pid):
    collector.active = {4321}
    collector.births = {4321: {"pid": 4321}}
    assert collector._handle_event(_event(1, pid=pid, exception=exception, first_chance=first_chance)) == (
        DBG_EXCEPTION_NOT_HANDLED
    )
    assert pid not in collector.loader_breakpoints


def _mock_launch(collector, chain, monkeypatch, events, final_error_pid=None):
    log = []
    events = iter(events)
    births = {item["pid"]: item for item in chain.observation["birth_processes"]}
    retained_objects = {}

    def retain(pid):
        retained = Mock()
        count = 0

        def query():
            nonlocal count
            count += 1
            log.append(("query", pid, threading.get_ident()))
            if count > 1 and pid == final_error_pid:
                raise RuntimeError("Synthetic final identity failure")
            if count > 1 and pid == 4322:
                return copy.deepcopy(chain.observation["helpers"][0])
            return copy.deepcopy(births[pid])

        retained.query.side_effect = query
        retained.close.side_effect = lambda: log.append(("close", pid, threading.get_ident()))
        retained_objects[pid] = retained
        return retained

    def wait(event, milliseconds):
        assert milliseconds == 50
        value = next(events)
        event.__dict__.update(value.__dict__)
        log.append(("wait", event.pid, threading.get_ident()))
        return True

    def proceed(pid, tid, status):
        log.append(("continue", pid, threading.get_ident()))
        return True

    def api():
        collector.event_type = SimpleNamespace
        collector.kernel.WaitForDebugEvent.side_effect = wait
        collector.kernel.ContinueDebugEvent.side_effect = proceed

    def launch(*args, **kwargs):
        log.append(("launch", 4321, threading.get_ident()))
        return collector.process

    monkeypatch.setattr(collector, "_api", api)
    monkeypatch.setattr(ctypes, "byref", lambda value: value)
    monkeypatch.setattr(windows_module, "RetainedWindowsProcess", retain)
    spawn = Mock(side_effect=launch)
    monkeypatch.setattr(subprocess, "Popen", spawn)
    return log, retained_objects, spawn


def test_event_pump_queries_before_continuation_and_keeps_collecting_after_root_exit(collector, chain, monkeypatch):
    events = [_event(3, pid) for pid in (4321, 4322, 4323)]
    events += [_event(5, pid) for pid in (4321, 4322, 4323)]
    log, retained, spawn = _mock_launch(collector, chain, monkeypatch, events)
    thread = threading.Thread(target=collector._launch)
    thread.start()
    thread.join(2)
    assert not thread.is_alive()
    assert not collector.errors
    assert len({item[2] for item in log if item[0] in {"launch", "wait", "continue"}}) == 1
    for pid in retained:
        query_index = next(index for index, item in enumerate(log) if item[:2] == ("query", pid))
        continue_index = next(index for index, item in enumerate(log) if item[:2] == ("continue", pid))
        assert query_index < continue_index
        retained[pid].close.assert_called_once_with()
    assert collector.report == chain.observation
    kwargs = spawn.call_args[1]
    assert spawn.call_args[0] == (collector.command,)
    assert kwargs["shell"] is False
    assert kwargs["creationflags"] & 0x1
    assert not kwargs["creationflags"] & 0x2
    assert kwargs["stdout"] == kwargs["stderr"] == subprocess.PIPE
    assert kwargs["env"] == collector.environment


def test_event_handler_failure_continues_delivered_event_then_fails_closed(collector, chain, monkeypatch):
    events = [_event(3), _event(5, 9999)]
    _, retained, _ = _mock_launch(collector, chain, monkeypatch, events)
    collector._launch()
    assert collector.report["errors"]
    assert "matching owned process birth" in collector.report["errors"][0]
    assert collector.kernel.ContinueDebugEvent.call_args[0] == (9999, 10099, DBG_EXCEPTION_NOT_HANDLED)
    retained[4321].close.assert_called_once_with()


def test_final_query_failure_still_closes_every_owned_read_handle_and_reports_failure(collector, chain, monkeypatch):
    events = [_event(3, pid) for pid in (4321, 4322, 4323)]
    events += [_event(5, pid) for pid in (4322, 4323, 4321)]
    _, retained, _ = _mock_launch(collector, chain, monkeypatch, events, final_error_pid=4321)
    collector._launch()
    for process in retained.values():
        process.close.assert_called_once_with()
    assert collector.report["errors"] or any(item.get("error") for item in collector.report["helpers"])


@pytest.mark.skipif(os.name != "nt", reason="The Windows x64 ABI is only defined by Windows ctypes types")
def test_debug_event_layout_preserves_native_x64_union_alignment(collector, monkeypatch):
    monkeypatch.setattr(ctypes, "WinDLL", Mock(return_value=Mock()))
    collector._api()
    assert ctypes.sizeof(collector.event_type) == 176
    assert collector.event_type.data.offset == 16
    exception = collector.event_type().data.exception
    assert type(exception).first_chance.offset == 152
    assert ctypes.sizeof(type(exception.record)) == 152


@pytest.mark.parametrize("stuck", [False, True])
def test_finish_waits_with_a_bound_and_requests_stop_for_a_stuck_owned_pump(collector, stuck):
    collector.thread = Mock()
    collector.thread.is_alive.return_value = stuck
    collector.report = {"errors": []}
    if stuck:
        with pytest.raises(RuntimeError, match="within its bound"):
            collector.finish()
        assert collector.stop.is_set()
        assert [call[0] for call in collector.thread.join.call_args_list] == [(5,), (2,)]
    else:
        assert collector.finish() == collector.report
        collector.thread.join.assert_called_once_with(5)


def test_birth_observation_accepts_helper_exit_after_extension_creation(chain):
    assert runtime_module.verify_evidence(
        chain.evidence, "owned-native-invocation", 4321, chain.executable, chain.host
    ) == ("windows-glib-helper")
    runtime_module.verify_windows_birth_observation(chain.evidence, chain.observation)


@pytest.mark.parametrize("index", [0, 1, 2])
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("requested_pid", True),
        ("parent_pid", 9999),
        ("creation_time", 9999),
        ("image_native", r"\Device\HarddiskVolume7\other\unowned.exe"),
        ("exit_time", 40),
        ("exit_time", False),
        ("exit_status", 0),
        ("error", "unreadable birth"),
    ],
)
def test_birth_observation_rejects_a_mismatched_or_non_live_member(chain, index, field, value):
    chain.observation["birth_processes"][index][field] = value
    with pytest.raises(RuntimeError):
        runtime_module.verify_windows_birth_observation(chain.evidence, chain.observation)


@pytest.mark.parametrize("failure", ["mode", "errors", "missing", "duplicate", "helper-final"])
def test_birth_observation_rejects_missing_ambiguous_or_failed_capture(chain, failure):
    if failure == "mode":
        chain.observation["mode"] = "snapshot-only"
    elif failure == "errors":
        chain.observation["errors"] = ["event collection failed"]
    elif failure == "missing":
        chain.observation["birth_processes"].pop()
    elif failure == "duplicate":
        chain.observation["birth_processes"].append(copy.deepcopy(chain.host))
    else:
        chain.observation["helpers"][0]["exit_time"] = 99
    with pytest.raises(RuntimeError):
        runtime_module.verify_windows_birth_observation(chain.evidence, chain.observation)


def test_runtime_uses_owned_birth_collector_for_a_native_windows_invocation(chain, tmp_path, monkeypatch):
    runtime = runtime_module.InkscapeRuntime(chain.executable, tmp_path)
    process = Mock(pid=4321, returncode=0)
    process.communicate.return_value = (b"Native stdout", b"")
    collector = Mock(process=process, host_process=chain.host)
    collector.finish.return_value = chain.observation
    create = Mock(return_value=collector)
    monkeypatch.setattr(runtime_module, "WindowsNativeProcess", create)
    monkeypatch.setattr(runtime_module, "os", SimpleNamespace(name="nt"))
    ambient_spawn = Mock(side_effect=AssertionError("Native execution must use the owned collector"))
    monkeypatch.setattr(subprocess, "Popen", ambient_spawn)

    result = runtime._run(["--actions=typed-native-effect"], timeout=7, native_provenance=True)
    assert result["owned_host_process"] == chain.host
    assert result["observed_windows_helpers"] == chain.observation
    assert result["host_pid"] == 4321
    process.communicate.assert_called_once_with(timeout=7)
    collector.finish.assert_called_once_with()
    ambient_spawn.assert_not_called()
    assert create.call_args[0][0][0] == str(chain.executable)


@pytest.mark.parametrize("failure", [None, "missing-birth", "captured-mismatch", "collector-error"])
def test_document_publication_requires_the_captured_complete_owned_tree(chain, tmp_path, monkeypatch, failure):
    runtime = runtime_module.InkscapeRuntime(chain.executable, tmp_path)
    plan = {"width": 64, "height": 64, "nodes": [{"type": "path", "id": "shape", "d": "M0,0 L64,64"}]}

    def native_fixture(arguments, environment=None, timeout=120, host_report=None, native_provenance=False):
        request = json.loads(runtime_module.Path(environment["DCC_MCP_INKSCAPE_REQUEST"]).read_text(encoding="utf-8"))
        evidence = copy.deepcopy(chain.evidence)
        evidence["nonce"] = request["nonce"]
        runtime_module.Path(request["evidence_path"]).write_text(json.dumps(evidence), encoding="utf-8")
        runtime_module.Path(request["evidence_path"]).with_name("result.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">'
            '<path id="shape" d="M0,0 L64,64"/></svg>',
            encoding="utf-8",
        )
        observation = copy.deepcopy(chain.observation)
        if failure == "missing-birth":
            observation["birth_processes"].pop()
        elif failure == "captured-mismatch":
            observation["birth_processes"][1]["creation_time"] = 99
        elif failure == "collector-error":
            observation["errors"] = ["native event failed"]
        return {"host_pid": 4321, "owned_host_process": chain.host, "observed_windows_helpers": observation}

    monkeypatch.setattr(runtime, "_run", native_fixture)
    commit = Mock(return_value={"published": True})
    monkeypatch.setattr(runtime, "_commit", commit)
    if failure:
        with pytest.raises(RuntimeError):
            runtime.document_build("birth-proof.svg", plan)
        commit.assert_not_called()
        assert not (tmp_path / "birth-proof.svg").exists()
    else:
        assert runtime.document_build("birth-proof.svg", plan) == {"published": True}
        evidence = commit.call_args[0][3]
        assert evidence["controller_birth_observation"] == "exact-match"
        assert evidence["controller_helper_observation"] == "exact-match"
