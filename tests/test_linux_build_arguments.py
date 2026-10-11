"""Linux display requirements do not change headless exports or other hosts."""

import pytest

from dcc_mcp_inkscape import runtime as runtime_module


@pytest.fixture
def runtime(tmp_path):
    executable = tmp_path / "inkscape"
    executable.write_bytes(b"not executed")
    return runtime_module.InkscapeRuntime(executable, tmp_path / "workspace")


def test_linux_build_requires_an_operator_display(runtime, monkeypatch):
    monkeypatch.setattr(runtime_module.sys, "platform", "linux")
    runtime.environment.pop("DISPLAY", None)
    runtime.environment.pop("WAYLAND_DISPLAY", None)
    with pytest.raises(RuntimeError, match="operator-owned Xvfb"):
        runtime._build_arguments("fixed-native-action")


@pytest.mark.parametrize("display_variable", ["DISPLAY", "WAYLAND_DISPLAY"])
def test_linux_build_uses_owned_gui_batch(runtime, monkeypatch, display_variable):
    monkeypatch.setattr(runtime_module.sys, "platform", "linux")
    runtime.environment[display_variable] = "operator-display"
    assert runtime._build_arguments("fixed-native-action") == [
        "--with-gui",
        "--batch-process",
        "--actions=fixed-native-action",
    ]


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_other_hosts_keep_existing_arguments(runtime, monkeypatch, platform):
    monkeypatch.setattr(runtime_module.sys, "platform", platform)
    assert runtime._build_arguments("fixed-native-action") == ["--actions=fixed-native-action"]
