"""Native menu descriptors, exact profile scope and receipt migration regressions."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree

import pytest

from dcc_mcp_inkscape import installation
from dcc_mcp_inkscape import menu
from dcc_mcp_inkscape import menu_bridge
from dcc_mcp_inkscape.cli import build_parser
from dcc_mcp_inkscape.resources import LEGACY_NAMES
from dcc_mcp_inkscape.resources import MENU_CONTEXT


@pytest.fixture
def installed(tmp_path, monkeypatch, capsys):
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
    parser = build_parser()

    def run(verb, *extra):
        args = parser.parse_args(
            [
                verb,
                "--workspace",
                str(workspace),
                "--dcc-path",
                str(host),
                "--python",
                sys.executable,
                "--gateway-port",
                "19771",
                "--json",
                *extra,
            ]
        )
        code = installation.run_install_command(args)
        return code, json.loads(capsys.readouterr().out)

    assert run("install", "--yes")[0] == 40
    receipt_path = workspace / ".dcc-mcp-inkscape/install/receipt.json"
    context = workspace / ".inkscape-mcp/profile/extensions" / MENU_CONTEXT
    return workspace, run, receipt_path, context


def snapshot(workspace):
    return {str(path.relative_to(workspace)): path.read_bytes() for path in workspace.rglob("*") if path.is_file()}


def legacy(receipt_path):
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    for record in receipt["files"]:
        if Path(record["path"]).name not in LEGACY_NAMES:
            Path(record["path"]).unlink()
    receipt["schema_version"] = 1
    receipt["files"] = [record for record in receipt["files"] if Path(record["path"]).name in LEGACY_NAMES]
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")


def test_visible_native_descriptors_preserve_hidden_typed_action():
    ns = {"inx": "http://www.inkscape.org/namespace/inkscape/extension"}
    hidden = ElementTree.parse(installation.FILES["dcc_mcp_vector.inx"]).getroot()
    assert hidden.find("inx:effect/inx:effects-menu", ns).get("hidden") == "true"
    assert hidden.find("inx:id", ns).text == "org.dcc-mcp.typed-vector-plan"
    ids = set()
    for page in ("status", "settings", "connection"):
        root = ElementTree.parse(installation.FILES["dcc_mcp_" + page + ".inx"]).getroot()
        effect = root.find("inx:effect", ns)
        assert effect.get("needs-document") == "false"
        assert effect.get("implements-custom-gui") == "true"
        assert effect.find("inx:effects-menu", ns).get("hidden") != "true"
        assert effect.find("inx:effects-menu/inx:submenu", ns).get("name") == "DCC MCP"
        script = "dcc_mcp_menu.py" if page == "status" else "dcc_mcp_" + page + ".py"
        assert root.find("inx:script/inx:command", ns).text == script
        assert root.find("inx:param", ns) is None
        ids.add(root.find("inx:id", ns).text)
    assert len(ids) == 3


def test_status_settings_and_connection_are_readonly_and_use_exact_scope(installed):
    workspace, run, receipt, context = installed
    before = snapshot(workspace)
    for operation in ("status", "settings", "connection"):
        report = menu.operate(context, operation)
        assert report["configuration"]["workspace"] == str(workspace)
        assert report["installation"]["readiness"]["status"] == "not_running"
        assert not report["installation"]["verify"]["directly_usable"]
    assert snapshot(workspace) == before


@pytest.mark.parametrize("part", ["context", "config", "receipt_scope", "context_digest"])
def test_context_tampering_fails_closed(installed, part):
    workspace, run, receipt_path, context = installed
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
    with pytest.raises(ValueError):
        menu_bridge.load_context(context)


def test_legacy_status_is_readonly_and_upgrade_commits_new_resources(installed):
    workspace, run, receipt, context = installed
    legacy(receipt)
    before = snapshot(workspace)
    assert run("status")[1]["menu_resources"]["available"] is False
    assert run("verify")[0] == 40
    assert snapshot(workspace) == before
    code, report = run("upgrade", "--yes")
    assert code == 40 and report["menu_resources"]["available"]
    assert menu_bridge.load_context(context)["config"]["workspace"] == str(workspace)


def test_legacy_uninstall_preserves_unowned_new_menu_file(installed):
    workspace, run, receipt, context = installed
    legacy(receipt)
    target = context.with_name("dcc_mcp_status.inx")
    target.write_bytes(b"operator-owned file")
    assert run("uninstall", "--yes")[0] == 0
    assert target.read_bytes() == b"operator-owned file"


def test_legacy_upgrade_refuses_same_byte_unowned_collision(installed):
    workspace, run, receipt, context = installed
    legacy(receipt)
    context.with_name("dcc_mcp_status.inx").write_bytes(installation.FILES["dcc_mcp_status.inx"].read_bytes())
    before = snapshot(workspace)
    assert run("upgrade", "--yes")[0] == 10
    assert snapshot(workspace) == before


@pytest.mark.parametrize("failure", ["config.json", "receipt.json"])
def test_failed_migration_restores_legacy_files_and_receipt(installed, monkeypatch, failure):
    workspace, run, receipt, context = installed
    legacy(receipt)
    before = snapshot(workspace)
    original = installation._atomic_json

    def fail(path, value):
        if path.name == failure:
            raise OSError("simulated migration commit failure")
        original(path, value)

    monkeypatch.setattr(installation, "_atomic_json", fail)
    assert run("upgrade", "--yes")[0] == 30
    assert snapshot(workspace) == before


def test_connection_start_is_explicit_scoped_and_not_claimed_ready(installed, monkeypatch):
    workspace, run, receipt, context = installed
    observed = []

    def launch(command, **kwargs):
        observed.append((command, kwargs))
        return SimpleNamespace(pid=123456)

    monkeypatch.setattr(menu.subprocess, "Popen", launch)
    monkeypatch.setattr(menu, "_wait_for_publication", lambda config, child: "starting")
    report = menu.operate(context, "start")
    command, options = observed[0]
    assert command[:4] == [str(Path(sys.executable).resolve()), "-m", "dcc_mcp_inkscape", "serve"]
    assert command[command.index("--workspace") + 1] == str(workspace)
    assert command[command.index("--gateway-port") + 1] == "19771"
    assert options["shell"] is False
    assert report["started"] and not report["installation"]["verify"]["directly_usable"]
    assert Path(report["start_lock"]).is_file()
    with pytest.raises(FileExistsError):
        menu.operate(context, "start")


def test_start_refuses_duplicate_owned_controller(installed, monkeypatch):
    workspace, run, receipt, context = installed
    monkeypatch.setattr(
        installation,
        "_runtime",
        lambda config, instance_id=None: {"status": "ready", "ready": True, "entries": [{"runtime_pid": 123456}]},
    )
    monkeypatch.setattr(menu.subprocess, "Popen", lambda *a, **kw: pytest.fail("must not spawn a second controller"))
    assert menu.operate(context, "start")["started"] is False


def test_selected_python_bridge_does_not_inherit_scope_overrides(installed, monkeypatch):
    context = menu_bridge.load_context(installed[3])
    monkeypatch.setenv("DCC_MCP_INKSCAPE_WORKSPACE", "other-workspace")
    monkeypatch.setenv("PYTHONPATH", "other-package")
    environment = menu_bridge.environment(context)
    assert "DCC_MCP_INKSCAPE_WORKSPACE" not in environment
    assert environment["PYTHONPATH"] == context["module_root"]


@pytest.mark.parametrize("target", ["config", "context", "receipt"])
def test_menu_metadata_reads_are_bounded(installed, target):
    workspace, run, receipt, context = installed
    paths = {"config": receipt.parent / "config.json", "context": context, "receipt": receipt}
    paths[target].write_bytes(b" " * (1024 * 1024 + 1))
    with pytest.raises(ValueError):
        menu_bridge.load_context(context)


@pytest.mark.parametrize("field,value", [("files", [None]), ("source", []), ("config", [])])
def test_malformed_receipt_types_fail_as_structured_errors(installed, field, value):
    workspace, run, receipt, context = installed
    record = json.loads(receipt.read_text(encoding="utf-8"))
    record[field] = value
    receipt.write_text(json.dumps(record), encoding="utf-8")
    args = build_parser().parse_args(["menu", "--context-file", str(context), "--operation", "status"])
    assert menu.run_menu_command(args) == 10


def test_bridge_rejects_nonzero_child_even_with_json_output(installed, monkeypatch):
    monkeypatch.setattr(
        menu_bridge.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=10, stdout=b'{"error":"actual child failure"}', stderr=b""),
    )
    with pytest.raises(ValueError, match="actual child failure"):
        menu_bridge.invoke(installed[3], "status")


def test_boolean_receipt_schema_is_rejected(installed):
    workspace, run, receipt, context = installed
    value = json.loads(receipt.read_text(encoding="utf-8"))
    value["schema_version"] = True
    receipt.write_text(json.dumps(value), encoding="utf-8")
    assert run("status")[0] == 10


def test_first_source_menu_manifest_migrates_only_through_explicit_upgrade(installed):
    workspace, run, receipt, context = installed
    value = json.loads(receipt.read_text(encoding="utf-8"))
    new_names = {"dcc_mcp_settings.py", "dcc_mcp_connection.py"}
    for record in value["files"]:
        if Path(record["path"]).name in new_names:
            Path(record["path"]).unlink()
    value["files"] = [record for record in value["files"] if Path(record["path"]).name not in new_names]
    value.pop("resource_manifest_revision")
    receipt.write_text(json.dumps(value), encoding="utf-8")
    before = snapshot(workspace)
    assert run("status")[1]["state"] == "upgrade"
    assert run("verify")[0] == 40
    assert snapshot(workspace) == before
    assert run("upgrade", "--yes")[0] == 40
    assert json.loads(receipt.read_text(encoding="utf-8"))["resource_manifest_revision"] == 2
    assert all(context.with_name(name).is_file() for name in new_names)


@pytest.mark.parametrize("revision", [True, 0, 3, "2"])
def test_unknown_menu_resource_revision_is_refused(installed, revision):
    workspace, run, receipt, context = installed
    value = json.loads(receipt.read_text(encoding="utf-8"))
    value["resource_manifest_revision"] = revision
    receipt.write_text(json.dumps(value), encoding="utf-8")
    before = snapshot(workspace)
    assert run("upgrade", "--yes")[0] == 10
    assert snapshot(workspace) == before
