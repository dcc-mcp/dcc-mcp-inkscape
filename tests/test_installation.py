"""Ownership, planning, rollback, and SOP regressions without a live DCC."""

import argparse
import io
import json
import sys
from pathlib import Path

import pytest

from dcc_mcp_inkscape import installation


@pytest.fixture
def scope(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    executable = tmp_path / "inkscape.exe"
    executable.write_bytes(b"not executed: unit-test fixture")
    monkeypatch.setattr(
        installation,
        "_probe",
        lambda config: {
            "host_version": "1.4.4",
            "host_version_output": "Inkscape 1.4.4",
            "imports": {
                "adapter_version": installation.__version__,
                "adapter_file": str(installation.HERE / "__init__.py"),
                "core_version": "0.20.37",
                "python": sys.executable,
            },
        },
    )
    monkeypatch.setattr(
        installation,
        "_runtime",
        lambda config, instance_id=None: {
            "status": "not_running",
            "ready": False,
            "entries": [],
        },
    )
    return workspace, executable


def arguments(scope, verb="install", *extra):
    parser = argparse.ArgumentParser()
    installation.configure_install_parser(parser.add_subparsers(dest="command"))
    workspace, executable = scope
    return parser.parse_args(
        [
            verb,
            "--workspace",
            str(workspace),
            "--dcc-path",
            str(executable),
            "--python",
            sys.executable,
            "--gateway-port",
            "19769",
            "--json",
            *extra,
        ]
    )


def invoke(scope, capsys, verb="install", *extra):
    code = installation.run_install_command(arguments(scope, verb, *extra))
    output = capsys.readouterr()
    assert len(output.out.splitlines()) == 1
    report = json.loads(output.out)
    assert report["schema_version"] == 1
    return code, report


def tree(workspace):
    return {str(path.relative_to(workspace)): path.read_bytes() for path in workspace.rglob("*") if path.is_file()}


@pytest.mark.parametrize("verb", ["install", "upgrade", "uninstall"])
def test_mutation_verbs_plan_by_default_without_creating_directories(scope, capsys, verb):
    code, report = invoke(scope, capsys, verb)
    assert code == 0
    assert report["status"] == "planned"
    assert not list(scope[0].iterdir())
    assert "--yes" in report["next_steps"][0]["command"]


@pytest.mark.parametrize("verb", ["install", "upgrade", "uninstall"])
def test_dry_run_outranks_yes(scope, capsys, verb):
    code, report = invoke(scope, capsys, verb, "--yes", "--dry-run")
    assert code == 0 and report["status"] == "planned"
    assert tree(scope[0]) == {}


def test_install_reports_configured_artifacts_separately_from_closed_service(scope, capsys):
    code, report = invoke(scope, capsys, "install", "--yes")
    assert code == 40 and report["status"] == "partial"
    assert report["installed"] and report["importable"]
    assert report["readiness"]["status"] == "not_running"
    assert not report["verify"]["directly_usable"]
    assert report["verify"]["failure_reason"] == "not_running"
    receipt = json.loads(Path(report["receipt_path"]).read_text(encoding="utf-8"))
    assert len(receipt["files"]) == len(installation.FILES) + 1
    assert receipt["schema_version"] == 2
    assert receipt["source"]["sop_schema_artifact"] == 2
    for record in receipt["files"]:
        assert installation._digest(Path(record["path"])) == record["sha256"]


def test_repeat_install_is_idempotent_and_status_verify_remain_readonly(scope, capsys):
    invoke(scope, capsys, "install", "--yes")
    before = tree(scope[0])
    invoke(scope, capsys, "install", "--yes")
    status_code, status = invoke(scope, capsys, "status")
    verify_code, verify = invoke(scope, capsys, "verify")
    assert status_code == 0 and status["state"] == "current"
    assert verify_code == 40 and verify["readiness"]["status"] == "not_running"
    assert tree(scope[0]) == before


def test_uninstall_only_removes_receipt_owned_files_and_keeps_user_profile(scope, capsys):
    invoke(scope, capsys, "install", "--yes")
    preference = scope[0] / ".inkscape-mcp/profile/preferences.xml"
    preference.write_text("operator preferences", encoding="utf-8")
    code, report = invoke(scope, capsys, "uninstall", "--yes")
    assert code == 0 and not report["installed"]
    assert preference.read_text(encoding="utf-8") == "operator preferences"
    assert not Path(report["receipt_path"]).exists()
    assert not list((preference.parent / "extensions").iterdir())


@pytest.mark.parametrize("verb", ["install", "upgrade", "uninstall"])
def test_modified_owned_file_is_preserved_before_any_mutation(scope, capsys, verb):
    invoke(scope, capsys, "install", "--yes")
    target = scope[0] / ".inkscape-mcp/profile/extensions/plan.py"
    target.write_bytes(b"operator edited this file")
    before = tree(scope[0])
    code, report = invoke(scope, capsys, verb, "--yes")
    assert code == 10 and report["status"] == "failed"
    assert "modified" in report["verify"]["failure_reason"]
    assert tree(scope[0]) == before


def test_modified_configuration_blocks_uninstall_before_removing_extensions(scope, capsys):
    invoke(scope, capsys, "install", "--yes")
    config = scope[0] / ".dcc-mcp-inkscape/install/config.json"
    config.write_text('{"operator": "modified"}', encoding="utf-8")
    before = tree(scope[0])
    code, report = invoke(scope, capsys, "uninstall", "--yes")
    assert code == 10 and report["status"] == "failed"
    assert tree(scope[0]) == before


def test_existing_unowned_extension_is_never_claimed_or_overwritten(scope, capsys):
    target = scope[0] / ".inkscape-mcp/profile/extensions/plan.py"
    target.parent.mkdir(parents=True)
    target.write_bytes(installation.FILES["plan.py"].read_bytes())
    before = tree(scope[0])
    code, report = invoke(scope, capsys, "install", "--yes")
    assert code == 10
    assert "no ownership receipt" in report["verify"]["failure_reason"]
    assert tree(scope[0]) == before


def test_private_profile_cannot_escape_workspace(scope, capsys, tmp_path):
    outside = tmp_path / "outside/profile"
    code, report = invoke(scope, capsys, "install", "--yes", "--profile-dir", str(outside))
    assert code == 10 and report["status"] == "failed"
    assert not outside.exists()
    assert not list(scope[0].iterdir())


def test_failed_receipt_commit_rolls_back_files_and_configuration(scope, capsys, monkeypatch):
    original = installation._atomic_json

    def fail_receipt(path, value):
        if path.name == "receipt.json":
            raise OSError("simulated receipt commit failure")
        original(path, value)

    monkeypatch.setattr(installation, "_atomic_json", fail_receipt)
    code, report = invoke(scope, capsys, "install", "--yes")
    assert code == 30 and report["status"] == "failed"
    assert tree(scope[0]) == {}


def test_active_workspace_service_blocks_uninstall_and_repair(scope, capsys, monkeypatch):
    invoke(scope, capsys, "install", "--yes")
    monkeypatch.setattr(
        installation,
        "_runtime",
        lambda config, instance_id=None: {
            "status": "not_ready",
            "ready": False,
            "entries": [{"instance_id": "fixture"}],
        },
    )
    before = tree(scope[0])
    code, report = invoke(scope, capsys, "uninstall", "--yes")
    assert code == 10 and "using this workspace" in report["verify"]["failure_reason"]
    assert tree(scope[0]) == before


def test_running_readiness_requires_owned_files_and_is_not_guessed(scope, capsys, monkeypatch):
    invoke(scope, capsys, "install", "--yes")
    monkeypatch.setattr(
        installation,
        "_runtime",
        lambda config, instance_id=None: {
            "status": "ready",
            "ready": True,
            "entries": [{"instance_id": "fixture"}],
        },
    )
    before = tree(scope[0])
    code, report = invoke(scope, capsys, "verify")
    assert code == 0 and report["verify"]["directly_usable"]
    assert tree(scope[0]) == before


def test_receipt_cannot_add_an_arbitrary_deletion_target(scope, capsys):
    _, report = invoke(scope, capsys, "install", "--yes")
    receipt_path = Path(report["receipt_path"])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    unrelated = scope[0] / "unrelated.svg"
    unrelated.write_bytes(b"operator artwork")
    receipt["files"][0]["path"] = str(unrelated)
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    before = tree(scope[0])
    code, report = invoke(scope, capsys, "uninstall", "--yes")
    assert code == 10 and "unexpected owned file" in report["verify"]["failure_reason"]
    assert tree(scope[0]) == before


def test_failed_uninstall_restores_previous_owned_files(scope, capsys, monkeypatch):
    invoke(scope, capsys, "install", "--yes")
    before = tree(scope[0])
    original = Path.unlink

    def fail_one(path, *args, **kwargs):
        if path.name == "plan.py":
            raise OSError("simulated owned-file lock")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_one)
    code, report = invoke(scope, capsys, "uninstall", "--yes")
    assert code == 30 and report["status"] == "failed"
    assert tree(scope[0]) == before


@pytest.mark.parametrize("version", ["1.3.2", "unknown", "1.4.4 injected"])
def test_static_host_probe_rejects_unsupported_or_unparseable_versions(tmp_path, monkeypatch, version):
    executable = tmp_path / "inkscape.exe"
    executable.write_bytes(b"test fixture")
    monkeypatch.setattr(installation, "_windows_version_strings", lambda path: {"ProductVersion": version})
    with pytest.raises(ValueError, match="at least 1.4"):
        installation._host_probe(str(executable))


def test_host_probe_reads_metadata_and_bytes_without_spawning_inkscape(tmp_path, monkeypatch):
    executable = tmp_path / "inkscape.exe"
    executable.write_bytes(b"test fixture")
    before = tree(tmp_path)
    monkeypatch.setattr(installation, "_windows_version_strings", lambda path: {"ProductVersion": "1.4.4"})
    monkeypatch.setattr(installation, "_capture", lambda *args: pytest.fail("Host must not be launched"))
    report = installation._host_probe(str(executable))
    assert report["host_version"] == "1.4.4"
    assert report["host_version_source"] == "windows_version_resource"
    assert report["host_executable_sha256"] == installation._digest(executable)
    assert tree(tmp_path) == before


def test_import_probe_only_launches_selected_python_with_bytecode_disabled(tmp_path, monkeypatch):
    config = {"executable": str(tmp_path / "inkscape.exe"), "python": sys.executable}
    monkeypatch.setattr(installation, "_host_probe", lambda path: {"host_version": "1.4.4"})

    def capture(command, environment):
        assert command[:2] == [sys.executable, "-c"]
        assert "--version" not in command
        assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
        return json.dumps(
            {
                "adapter_version": installation.__version__,
                "bundle_sha256": {name: installation._digest(path) for name, path in installation.FILES.items()},
            }
        )

    monkeypatch.setattr(installation, "_capture", capture)
    assert installation._probe(config)["host_version"] == "1.4.4"


@pytest.mark.parametrize("mismatch", [None, "uuid", "pid", "profile", "version", "nonboolean_bit"])
def test_readiness_requires_matching_live_identity_and_boolean_bits(tmp_path, monkeypatch, mismatch):
    from dcc_mcp_core import deployment
    from dcc_mcp_core.readiness import READINESS_ALL_BITS

    config = {
        "workspace": str(tmp_path),
        "profile": str(tmp_path / "private/profile"),
        "registry_dir": str(tmp_path / "registry"),
        "ready_file": str(tmp_path / "ready.json"),
    }
    published = {
        "status": "ready",
        "adapter": installation.OWNER,
        "adapter_version": installation.__version__,
        "workspace": config["workspace"],
        "profile": config["profile"],
        "instance_id": "owned-fixture",
        "owner_pid": 123,
        "backend_mcp_url": "http://127.0.0.1:19769/mcp",
    }
    entry = {
        "instance_id": "owned-fixture",
        "runtime_pid": 123,
        "runtime_alive": True,
        "adapter_version": installation.__version__,
        "mcp_url": "http://127.0.0.1:19769/mcp",
    }
    if mismatch == "uuid":
        published["instance_id"] = "stale-fixture"
    elif mismatch == "pid":
        published["owner_pid"] = 124
    elif mismatch == "profile":
        published["profile"] = str(tmp_path / "other/profile")
    elif mismatch == "version":
        entry["adapter_version"] = "other-version"
    Path(config["ready_file"]).write_text(json.dumps(published), encoding="utf-8")
    monkeypatch.setattr(deployment, "query_runtime_state", lambda *args, **kwargs: {"entries": [entry]})
    bits = {key: True for key in READINESS_ALL_BITS}
    if mismatch == "nonboolean_bit":
        bits[next(iter(READINESS_ALL_BITS))] = 1

    class LocalReadiness:
        def open(self, endpoint, timeout):
            assert endpoint == "http://127.0.0.1:19769/v1/readyz"
            return io.BytesIO(json.dumps(bits).encode("utf-8"))

    monkeypatch.setattr(installation, "build_opener", lambda *handlers: LocalReadiness())
    before = tree(tmp_path)
    report = installation._runtime(config)
    assert report["ready"] is (mismatch is None)
    assert tree(tmp_path) == before


@pytest.fixture
def runtime_scope(tmp_path, monkeypatch):
    """Actual Core 0.20.36 projection shape, without starting a service."""
    from dcc_mcp_core import deployment
    from dcc_mcp_core.readiness import READINESS_ALL_BITS

    config = {
        "workspace": str(tmp_path),
        "profile": str(tmp_path / "private/profile"),
        "registry_dir": str(tmp_path / "registry"),
        "ready_file": str(tmp_path / "ready.json"),
    }
    published = {
        "status": "ready",
        "adapter": installation.OWNER,
        "adapter_version": installation.__version__,
        "workspace": config["workspace"],
        "profile": config["profile"],
        "instance_id": "4d7c3fdd-7046-4a10-9227-d0025b043180",
        "owner_pid": 360592,
        "backend_mcp_url": "http://127.0.0.1:64604/mcp",
    }
    entry = {
        "instance_id": published["instance_id"],
        "runtime_pid": published["owner_pid"],
        "runtime_alive": True,
        "adapter_version": None,
        "versions": {"core": None, "server": "0.20.36", "adapter": None},
        "dispatch": {"reported": False, "status": "not_reported", "ready": None},
        "mcp_url": published["backend_mcp_url"],
        "metadata": {"project": config["workspace"]},
    }
    monkeypatch.setattr(deployment, "query_runtime_state", lambda *args, **kwargs: {"entries": [entry]})

    class LocalReadiness:
        def open(self, endpoint, timeout):
            assert endpoint == "http://127.0.0.1:64604/v1/readyz"
            return io.BytesIO(json.dumps({key: True for key in READINESS_ALL_BITS}).encode("utf-8"))

    monkeypatch.setattr(installation, "build_opener", lambda *handlers: LocalReadiness())
    return config, published, entry


def runtime_report(runtime_scope):
    config, published, entry = runtime_scope
    Path(config["ready_file"]).write_text(json.dumps(published), encoding="utf-8")
    before = tree(Path(config["workspace"]))
    report = installation._runtime(config)
    assert tree(Path(config["workspace"])) == before
    return report


def test_core_036_missing_registry_versions_use_exact_bound_adapter_publication(runtime_scope):
    report = runtime_report(runtime_scope)
    assert report["ready"] is True and report["status"] == "ready"
    assert report["version_source"] == "adapter_ready_file"
    assert all(report["bits"].values())


@pytest.mark.parametrize(
    "top,nested,expected_ready",
    [
        (installation.__version__, None, True),
        (None, installation.__version__, True),
        ("other-version", installation.__version__, False),
        (installation.__version__, "other-version", False),
        ("", None, False),
        (False, None, False),
    ],
)
def test_nonnull_registry_versions_cannot_be_overridden_by_ready_file(runtime_scope, top, nested, expected_ready):
    config, published, entry = runtime_scope
    entry["adapter_version"] = top
    entry["versions"]["adapter"] = nested
    report = runtime_report(runtime_scope)
    assert report["ready"] is expected_ready
    assert report["version_source"] == "registry"


@pytest.mark.parametrize("registry_version", [None, installation.__version__])
@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("instance_id", "stale-instance"),
        ("owner_pid", 360593),
        ("workspace", "other-workspace"),
        ("profile", "other-profile"),
        ("backend_mcp_url", "http://127.0.0.1:64605/mcp"),
        ("adapter", "other-adapter"),
        ("adapter_version", "other-version"),
        ("status", "starting"),
    ],
)
def test_any_ready_file_binding_mismatch_denies_readiness(runtime_scope, registry_version, field, bad_value):
    config, published, entry = runtime_scope
    entry["adapter_version"] = registry_version
    published[field] = bad_value
    report = runtime_report(runtime_scope)
    assert report["ready"] is False and report["status"] == "not_ready"
    assert all(report["bits"].values())
    assert report["version_source"] == ("registry" if registry_version is not None else None)
