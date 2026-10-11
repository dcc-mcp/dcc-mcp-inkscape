"""Linux pipe correlation is strict about process lifetime and both channels."""

import copy
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from dcc_mcp_inkscape import linux_process as lp


@pytest.fixture
def binding():
    return {
        "host": {"pid": 100, "start_ticks": 20, "executable": "/host/inkscape"},
        "effect": {"pid": 200, "start_ticks": 21, "executable": "/host/python3"},
        "host_fds": {"10": "pipe:[50]", "11": "pipe:[51]"},
        "effect_fds": {"1": "pipe:[50]", "2": "pipe:[51]"},
    }


def test_controller_binding_matches_exact_effect(binding):
    lp.verify_pipe_binding({"extension_pid": 200}, 100, Path("/host/inkscape"), binding)


@pytest.mark.parametrize(
    "failure", ["error", "host-pid", "effect-pid", "exe", "one-pipe", "same-pipe", "wrong-host-pipe"]
)
def test_incomplete_or_mismatched_binding_is_rejected(binding, failure):
    changed = copy.deepcopy(binding)
    if failure == "error":
        changed["error"] = "process exited"
    elif failure == "host-pid":
        changed["host"]["pid"] = 101
    elif failure == "effect-pid":
        changed["effect"]["pid"] = 201
    elif failure == "exe":
        changed["host"]["executable"] = "/host/not-inkscape"
    elif failure == "one-pipe":
        del changed["effect_fds"]["2"]
    elif failure == "same-pipe":
        changed["effect_fds"]["2"] = changed["effect_fds"]["1"]
    else:
        changed["host_fds"]["10"] = "pipe:[999]"
    with pytest.raises(RuntimeError):
        lp.verify_pipe_binding({"extension_pid": 200}, 100, Path("/host/inkscape"), changed)


@pytest.mark.parametrize("failure", ["pid-reuse", "exit", "older-effect", "same-process"])
def test_correlation_rejects_invalid_process_lifetimes(binding, monkeypatch, failure):
    host = binding["host"]
    effect = binding["effect"]
    if failure == "pid-reuse":
        identity = Mock(return_value=dict(host, start_ticks=999))
    elif failure == "exit":
        identity = Mock(side_effect=ProcessLookupError("process exited"))
    elif failure == "older-effect":
        identity = Mock(side_effect=[host, dict(effect, start_ticks=19)])
    else:
        identity = Mock(side_effect=[host, host])
    monkeypatch.setattr(lp, "process_identity", identity)
    with pytest.raises((OSError, RuntimeError)):
        lp.correlate_pipes(host, host["pid"] if failure == "same-process" else effect["pid"])


@pytest.mark.parametrize("failure", ["fd-replacement", "wrong-mode", "not-pipe"])
def test_descriptor_checks_reject_replacement_or_nonpipe(tmp_path, monkeypatch, failure):
    # The mocked descriptors use Linux fcntl bits even on Windows test runners.
    monkeypatch.setattr(lp.os, "O_ACCMODE", 3, raising=False)
    targets = ["pipe:[50]", "pipe:[51]"] if failure == "fd-replacement" else ["pipe:[50]"]
    if failure == "not-pipe":
        targets = ["/ordinary/file"]
    monkeypatch.setattr(lp.os, "readlink", Mock(side_effect=targets))
    monkeypatch.setattr(
        lp.Path, "read_text", Mock(return_value="flags:\t02\n" if failure == "wrong-mode" else "flags:\t01\n")
    )
    with pytest.raises(RuntimeError):
        lp.pipe_descriptor(100, 1, os.O_WRONLY)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux procfs kernel integration")
def test_real_kernel_pipes_bind_only_to_the_owned_host():
    # Test-created ordinary Python processes, not evidence of Inkscape execution.
    host_source = (
        "import subprocess,sys,time; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(15)'],"
        "stdout=subprocess.PIPE,stderr=subprocess.PIPE); "
        "print(p.pid,flush=True); time.sleep(15)"
    )
    host = subprocess.Popen([sys.executable, "-c", host_source], stdout=subprocess.PIPE, text=True)
    effect_pid = int(host.stdout.readline())
    try:
        identity = lp.process_identity(host.pid)
        observed = lp.correlate_pipes(identity, effect_pid)
        assert observed["effect"]["pid"] == effect_pid
        assert set(observed["host_fds"].values()) == set(observed["effect_fds"].values())
        with pytest.raises(RuntimeError, match="not both held"):
            lp.correlate_pipes(lp.process_identity(os.getpid()), effect_pid)
    finally:
        os.kill(effect_pid, 15)
        host.terminate()
        host.wait(timeout=5)
        host.stdout.close()


@pytest.mark.parametrize("failure", ["proxy-script", "wrong-interpreter", "empty-command"])
def test_effect_program_binding_rejects_proxy_or_interpreter(failure):
    identity = {
        "command": ["python3", str(Path("/private/dcc_mcp_vector.py").resolve()), "input.svg"],
        "executable": "/usr/bin/python3",
    }
    if failure == "proxy-script":
        identity["command"][1] = "/private/proxy.py"
    elif failure == "wrong-interpreter":
        identity["executable"] = "/unrelated/python3"
    else:
        identity["command"] = []
    with pytest.raises(RuntimeError):
        lp.verify_effect_program(identity, "/private/dcc_mcp_vector.py", "/usr/bin/python3")


def test_effect_program_binding_accepts_exact_private_script():
    identity = {
        "command": ["python3", str(Path("/private/dcc_mcp_vector.py").resolve()), "input.svg"],
        "executable": "/usr/bin/python3",
    }
    lp.verify_effect_program(identity, "/private/dcc_mcp_vector.py", "/usr/bin/python3")


def test_observer_exit_without_rendezvous_is_failure(tmp_path, monkeypatch):
    script = tmp_path / "effect.py"
    script.write_text("# test only")
    monkeypatch.setattr(lp, "process_identity", Mock(return_value={"pid": 100, "executable": "/host/inkscape"}))
    process = Mock(pid=100)
    process.poll.return_value = 0
    observer = lp.LinuxPipeObserver(process, "/host/inkscape", tmp_path, "test-nonce", script, "/host/python3")
    observer.thread.join(timeout=1)
    result = observer.finish()
    assert "exited" in result["error"]
    assert not (tmp_path / "linux-binding-ack.json").exists()


def test_observer_cancel_without_rendezvous_is_failure(tmp_path, monkeypatch):
    script = tmp_path / "effect.py"
    script.write_text("# test only")
    monkeypatch.setattr(lp, "process_identity", Mock(return_value={"pid": 100, "executable": "/host/inkscape"}))
    process = Mock(pid=100)
    process.poll.return_value = None
    observer = lp.LinuxPipeObserver(process, "/host/inkscape", tmp_path, "test-nonce", script, "/host/python3")
    result = observer.finish()
    assert "error" in result
    assert not observer.thread.is_alive()
    assert not (tmp_path / "linux-binding-ack.json").exists()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux no-follow file flags")
@pytest.mark.parametrize("payload", ["[]", "null", "{}", '"text"', "{" + " " * 5000 + "}"])
def test_rendezvous_rejects_nonobject_or_oversized_input(tmp_path, monkeypatch, payload):
    script = tmp_path / "effect.py"
    script.write_text("# test only")
    (tmp_path / "linux-binding-request.json").write_text(payload)
    monkeypatch.setattr(lp, "process_identity", Mock(return_value={"pid": 100, "executable": "/host/inkscape"}))
    process = Mock(pid=100)
    process.poll.return_value = None
    observer = lp.LinuxPipeObserver(process, "/host/inkscape", tmp_path, "test-nonce", script, "/host/python3")
    observer.thread.join(timeout=1)
    assert "error" in observer.finish()
    assert observer.result is None
    assert not (tmp_path / "linux-binding-ack.json").exists()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux no-follow file flags")
def test_rendezvous_refuses_symlink_request(tmp_path, monkeypatch):
    script = tmp_path / "effect.py"
    script.write_text("# test only")
    other = tmp_path / "other.json"
    other.write_text('{"nonce":"test-nonce","effect_pid":200}')
    (tmp_path / "linux-binding-request.json").symlink_to(other)
    monkeypatch.setattr(lp, "process_identity", Mock(return_value={"pid": 100, "executable": "/host/inkscape"}))
    process = Mock(pid=100)
    process.poll.return_value = None
    observer = lp.LinuxPipeObserver(process, "/host/inkscape", tmp_path, "test-nonce", script, "/host/python3")
    observer.thread.join(timeout=1)
    assert "error" in observer.finish()
    assert not (tmp_path / "linux-binding-ack.json").exists()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux no-follow file flags")
def test_existing_ack_is_not_overwritten_or_accepted(tmp_path, monkeypatch, binding):
    script = tmp_path / "effect.py"
    script.write_text("# test only")
    (tmp_path / "linux-binding-request.json").write_text('{"nonce":"test-nonce","effect_pid":200}')
    ack = tmp_path / "linux-binding-ack.json"
    ack.write_text("preexisting")
    monkeypatch.setattr(lp, "process_identity", Mock(return_value={"pid": 100, "executable": "/host/inkscape"}))
    monkeypatch.setattr(lp, "correlate_pipes", Mock(return_value=binding))
    monkeypatch.setattr(lp, "verify_effect_program", Mock())
    process = Mock(pid=100)
    process.poll.return_value = None
    observer = lp.LinuxPipeObserver(process, "/host/inkscape", tmp_path, "test-nonce", script, "/host/python3")
    observer.thread.join(timeout=1)
    assert "error" in observer.finish()
    assert observer.result is None
    assert ack.read_text() == "preexisting"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux no-follow file flags")
def test_cancellation_during_correlation_does_not_publish_ack(tmp_path, monkeypatch, binding):
    import threading

    script = tmp_path / "effect.py"
    script.write_text("# test only")
    (tmp_path / "linux-binding-request.json").write_text('{"nonce":"test-nonce","effect_pid":200}')
    started = threading.Event()
    release = threading.Event()

    def correlation(*args):
        started.set()
        assert release.wait(2)
        return binding

    monkeypatch.setattr(lp, "process_identity", Mock(return_value={"pid": 100, "executable": "/host/inkscape"}))
    monkeypatch.setattr(lp, "correlate_pipes", correlation)
    monkeypatch.setattr(lp, "verify_effect_program", Mock())
    process = Mock(pid=100)
    process.poll.return_value = None
    observer = lp.LinuxPipeObserver(process, "/host/inkscape", tmp_path, "test-nonce", script, "/host/python3")
    assert started.wait(1)
    with observer.lock:
        observer.stop.set()
    release.set()
    assert "error" in observer.finish()
    assert observer.result is None
    assert not (tmp_path / "linux-binding-ack.json").exists()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux no-follow file flags")
def test_successful_rendezvous_publishes_ack_and_retains_binding(tmp_path, monkeypatch, binding):
    script = tmp_path / "effect.py"
    script.write_text("# test only")
    (tmp_path / "linux-binding-request.json").write_text('{"nonce":"test-nonce","effect_pid":200}')
    monkeypatch.setattr(lp, "process_identity", Mock(return_value={"pid": 100, "executable": "/host/inkscape"}))
    monkeypatch.setattr(lp, "correlate_pipes", Mock(return_value=binding))
    monkeypatch.setattr(lp, "verify_effect_program", Mock())
    process = Mock(pid=100)
    process.poll.return_value = None
    observer = lp.LinuxPipeObserver(process, "/host/inkscape", tmp_path, "test-nonce", script, "/host/python3")
    observer.thread.join(timeout=1)
    assert "error" not in observer.finish()
    assert observer.result["effect"]["pid"] == 200
    assert (tmp_path / "linux-binding-ack.json").is_file()


@pytest.mark.parametrize("cwd", ["/private", "/unrelated", None])
def test_relative_script_requires_exact_kernel_observed_cwd(cwd):
    identity = {"command": ["python3", "dcc_mcp_vector.py", "input.svg"], "executable": "/usr/bin/python3", "cwd": cwd}
    if cwd == "/private":
        lp.verify_effect_program(identity, "/private/dcc_mcp_vector.py", "/usr/bin/python3")
    else:
        with pytest.raises(RuntimeError):
            lp.verify_effect_program(identity, "/private/dcc_mcp_vector.py", "/usr/bin/python3")
