"""Public controller lifecycle and import-boundary regressions; no DCC runs."""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from dcc_mcp_inkscape import cli
from dcc_mcp_inkscape import server as server_module


@pytest.fixture
def options(tmp_path):
    return server_module.InkscapeServerOptions(
        executable=str(tmp_path / "bin" / "inkscape.exe"),
        workspace=str(tmp_path / "work"),
        gateway_port=19765,
        registry_dir=str(tmp_path / "registry"),
        state_dir=".private-state",
    )


@pytest.fixture
def scoped_environment(monkeypatch):
    monkeypatch.setattr(server_module.os, "environ", dict(os.environ))
    monkeypatch.setattr(server_module, "_server", None)


def test_package_import_does_not_load_runtime_or_core_server(tmp_path):
    source = Path(__file__).resolve().parents[1] / "src"
    environment = dict(os.environ, PYTHONPATH=str(source))
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import dcc_mcp_inkscape; assert 'dcc_mcp_inkscape.server' not in sys.modules; assert 'dcc_mcp_inkscape.runtime' not in sys.modules; assert 'dcc_mcp_core' not in sys.modules",
        ],
        cwd=str(tmp_path),
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("port", [None, True, False, -1, 0, 1023, 65536, "19765"])
def test_implicit_or_invalid_gateway_is_rejected_before_native_setup(options, port):
    with pytest.raises(ValueError, match="explicit gateway"):
        server_module.InkscapeServerOptions(options.executable, options.workspace, port, options.registry_dir)


def test_missing_explicit_scope_is_rejected_before_native_setup(options):
    for field in ("executable", "workspace", "registry_dir"):
        values = dict(vars(options), **{field: ""})
        with pytest.raises(ValueError, match="Explicit"):
            server_module.InkscapeServerOptions(**values)


def test_source_relative_scope_is_canonical_before_subprocess_configuration(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    options = server_module.InkscapeServerOptions(
        "bin/inkscape.exe", "work", 19765, "registry", font_dirs=("fonts",), extra_skill_paths=("skills",)
    )
    assert Path(options.workspace) == tmp_path / "work"
    assert Path(options.executable) == tmp_path / "bin" / "inkscape.exe"
    assert Path(options.registry_dir) == tmp_path / "registry"
    assert options.font_dirs == (str(tmp_path / "fonts"),)
    assert options.extra_skill_paths == (str(tmp_path / "skills"),)
    assert list(tmp_path.iterdir()) == []


def test_constructor_uses_public_core_standalone_and_workspace_metadata(options, monkeypatch, scoped_environment):
    runtime = Mock(workspace=Path(options.workspace), profile=Path(options.workspace) / ".private-state" / "profile")
    runtime.capabilities.return_value = {"version": "Inkscape test double"}
    runtime_factory = Mock(return_value=runtime)
    monkeypatch.setattr(server_module, "InkscapeRuntime", runtime_factory)
    core_options = Mock(return_value=object())
    monkeypatch.setattr(server_module.DccServerOptions, "from_env", core_options)
    base_init = Mock(return_value=None)
    monkeypatch.setattr(server_module.DccServerBase, "__init__", base_init)
    register = Mock()
    monkeypatch.setattr(server_module.DccServerBase, "register_builtin_actions", register)
    bind = Mock()
    monkeypatch.setattr(server_module.AdapterReadinessBinder, "bind_headless", bind)
    instance = server_module.InkscapeMcpServer(options)
    settings = core_options.call_args[1]
    assert settings["instance_type"] == "standalone"
    assert settings["enable_gateway_failover"] is False
    assert settings["gateway_port"] == 19765
    assert settings["registry_dir"] == options.registry_dir
    assert settings["scene"] == options.workspace
    assert settings["dcc_version"] == "Inkscape test double"
    assert settings["adapter_version"] == "0.1.0"
    assert "dcc_pid" not in settings
    assert "execution_bridge" not in settings
    assert os.environ[server_module.ENV_PROJECT] == options.workspace
    assert os.environ["DCC_MCP_INKSCAPE_STATE_DIR"] == ".private-state"
    assert instance.workspace_path == options.workspace
    bind.assert_called_once_with(instance)
    register.assert_called_once_with(extra_skill_paths=[], include_bundled=False)


@pytest.fixture
def fake_server(monkeypatch, options, scoped_environment):
    class FakeServer:
        def __init__(self, selected=None):
            self.adapter_options = selected or options
            self.workspace_path = self.adapter_options.workspace
            self.private_profile = str(Path(self.workspace_path) / ".private-state" / "profile")
            self.runtime_capabilities = {"version": "Inkscape test double"}
            self.instance_id = "owned-instance"
            self.mcp_url = "http://127.0.0.1:12345/mcp"
            self.is_running = False
            self.started = 0
            self.stopped = 0
            self.metadata = None

        def start(self):
            self.started += 1
            self.is_running = True

        def stop(self):
            self.stopped += 1
            self.is_running = False

        def update_gateway_metadata(self, **metadata):
            self.metadata = metadata
            return True

    monkeypatch.setattr(server_module, "InkscapeMcpServer", FakeServer)
    return FakeServer


def test_public_start_reuses_same_scope_and_stops_only_owned_server(options, fake_server):
    instance = server_module.start_server(options)
    assert server_module.get_server() is instance
    assert server_module.start_server(options) is instance
    assert server_module.start_server() is instance
    assert instance.started == 1
    assert instance.metadata == {"scene": options.workspace, "version": "Inkscape test double"}
    server_module.stop_server()
    assert instance.stopped == 1
    assert server_module.get_server() is None


def test_public_start_refuses_scope_change_in_same_process(options, fake_server):
    instance = server_module.start_server(options)
    changed = server_module.InkscapeServerOptions(options.executable, options.workspace, 19766, options.registry_dir)
    with pytest.raises(ValueError, match="One Inkscape controller scope"):
        server_module.start_server(changed)
    assert instance.started == 1
    assert instance.stopped == 0


def test_failed_start_cleans_only_candidate_and_does_not_publish_server(options, fake_server, monkeypatch):
    candidate = fake_server(options)
    candidate.start = Mock(side_effect=RuntimeError("failed start"))
    factory = Mock(return_value=candidate)
    monkeypatch.setattr(server_module, "InkscapeMcpServer", factory)
    with pytest.raises(RuntimeError, match="failed start"):
        server_module.start_server(options)
    assert candidate.stopped == 1
    assert server_module.get_server() is None


def test_stop_rejects_foreign_server_without_stopping_owned_server(options, fake_server):
    instance = server_module.start_server(options)
    with pytest.raises(TypeError, match="Only an InkscapeMcpServer"):
        server_module.stop_server(object())
    assert instance.stopped == 0


@pytest.mark.parametrize("foreign_ready", [False, True])
def test_foreground_exit_removes_only_its_own_ready_receipt(options, fake_server, monkeypatch, capsys, foreign_ready):
    instance = fake_server(options)
    monkeypatch.setattr(server_module, "start_server", lambda selected: instance)
    stop = Mock()
    monkeypatch.setattr(server_module, "stop_server", stop)
    ready = Path(options.workspace) / "evidence" / "ready.json"

    class StoppedEvent:
        def set(self):
            pass

        def wait(self, timeout):
            if foreign_ready:
                ready.write_text(json.dumps({"owner_pid": 9999, "instance_id": "other-instance"}), encoding="utf-8")
            return True

    monkeypatch.setattr(cli.threading, "Event", StoppedEvent)
    signals = Mock(return_value=None)
    monkeypatch.setattr(cli.signal, "signal", signals)
    arguments = SimpleNamespace(**vars(options), font_dir=[], extra_skill_path=[], ready_file=ready)
    assert cli.run_serve(arguments) == 0
    stop.assert_called_once_with(instance)
    report = json.loads(capsys.readouterr().out)
    assert report["owner_pid"] == os.getpid()
    assert report["instance_id"] == "owned-instance"
    assert report["workspace"] == options.workspace
    assert report["profile"] == instance.private_profile
    assert report["runtime_shape"] == "standalone-controller+native-extension"
    assert ready.exists() is foreign_ready
    assert signals.call_count == 4
