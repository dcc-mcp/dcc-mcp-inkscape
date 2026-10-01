"""Plan-first installation of an adapter-owned private Inkscape profile.

The Python adapter is already installed in the selected interpreter. This
module enables its bundled effect in an explicit workspace, without acquiring
software, changing a default profile, starting a gateway, or stopping a host.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler
from urllib.request import ProxyHandler
from urllib.request import build_opener

from dcc_mcp_inkscape.__version__ import __version__
from dcc_mcp_inkscape.resources import EXTENSION_FILES
from dcc_mcp_inkscape.resources import LEGACY_NAMES
from dcc_mcp_inkscape.resources import MENU_CONTEXT

HERE = Path(__file__).resolve().parent
OWNER = "dcc-mcp-inkscape"
FILES = EXTENSION_FILES
RECEIPT_VERSION = 2
MIN_HOST = (1, 4)
SOP_DIGEST = "daa5840e07c956d7c9269e5709d6993a3988b905f986c06e7c4c02f5023e9422"


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def configure_install_parser(subparsers):
    """Add the five standard lifecycle verbs to the adapter CLI."""
    for verb in ("install", "status", "verify", "uninstall", "upgrade"):
        parser = subparsers.add_parser(verb, help="Plan-first private-profile " + verb)
        parser.add_argument("--json", action="store_true", help="Emit one Install SOP JSON document")
        parser.add_argument("--yes", action="store_true", help="Execute this mutating invocation")
        parser.add_argument("--dry-run", action="store_true", help="Resolve and inspect without writing")
        parser.add_argument("--workspace", default=os.environ.get("DCC_MCP_INKSCAPE_WORKSPACE"))
        parser.add_argument("--dcc-path", default=os.environ.get("DCC_MCP_INKSCAPE_EXE"))
        parser.add_argument("--python", default=None)
        parser.add_argument("--profile-dir", default=None)
        parser.add_argument("--registry-dir", default=os.environ.get("DCC_MCP_INKSCAPE_REGISTRY_DIR"))
        parser.add_argument("--gateway-port", type=int, default=os.environ.get("DCC_MCP_INKSCAPE_GATEWAY_PORT"))
        parser.add_argument("--instance-id", default=None, help="Narrow readonly readiness to one UUID")


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inside(root, value):
    path = Path(value)
    if any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in str(path)):
        raise ValueError("Paths must not contain control characters")
    path = (path if path.is_absolute() else root / path).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("Managed path is outside the explicit workspace") from exc
    if path == root:
        raise ValueError("The workspace root cannot be used as a managed install path")
    return path


def _read_json(path):
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise ValueError("Installation metadata is not a bounded regular JSON file")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Installation metadata must be a JSON object")
    return value


def _resolve(args):
    if not args.workspace:
        raise ValueError("An explicit existing --workspace is required")
    workspace = Path(args.workspace).resolve()
    if not workspace.is_dir():
        raise ValueError("The explicit workspace must already be a directory")
    install_root = _inside(workspace, ".dcc-mcp-inkscape/install")
    receipt_path = _inside(workspace, install_root / "receipt.json")
    receipt = _read_json(receipt_path)
    if receipt is not None:
        if (
            receipt.get("owner") != OWNER
            or type(receipt.get("schema_version")) is not int
            or receipt.get("schema_version") not in (1, RECEIPT_VERSION)
        ):
            raise ValueError("Receipt ownership or schema version is invalid")
        if receipt.get("workspace") != str(workspace):
            raise ValueError("Receipt belongs to a different workspace")
    saved = receipt.get("config", {}) if receipt else {}
    if not isinstance(saved, dict):
        raise ValueError("Receipt configuration is invalid")
    profile = _inside(workspace, args.profile_dir or saved.get("profile") or ".inkscape-mcp/profile")
    if profile.name != "profile":
        raise ValueError("The native runtime profile path must end in 'profile'")
    registry = _inside(workspace, args.registry_dir or saved.get("registry_dir") or ".dcc-registry")
    executable = args.dcc_path or saved.get("executable")
    if not executable:
        raise ValueError("An explicit --dcc-path is required on first installation")
    executable = Path(executable).resolve()
    if executable.suffix.lower() == ".com":
        executable = executable.with_suffix(".exe")
    if executable.stem.lower() != "inkscape" or not executable.is_file():
        raise ValueError("The selected existing Inkscape executable is unavailable")
    python = Path(args.python or saved.get("python") or sys.executable).resolve()
    if not python.is_file():
        raise ValueError("The selected Python interpreter is unavailable")
    gateway_port = args.gateway_port or saved.get("gateway_port")
    if gateway_port is not None and not 1024 <= int(gateway_port) <= 65535:
        raise ValueError("The explicit gateway port must be between 1024 and 65535")
    config = {
        "workspace": str(workspace),
        "profile": str(profile),
        "executable": str(executable),
        "python": str(python),
        "registry_dir": str(registry),
        "gateway_port": gateway_port,
        "ready_file": str(_inside(workspace, install_root / "ready.json")),
    }
    if receipt and saved != config:
        raise ValueError("An owned installation must retain its receipt configuration; uninstall before changing scope")
    config_path = _inside(workspace, install_root / "config.json")
    if config_path.exists() and (not receipt or _read_json(config_path) != config):
        raise ValueError("Existing configuration is not owned or has been modified")
    extensions = _inside(workspace, profile / "extensions")
    for name in FILES:
        _inside(workspace, extensions / name)
    return workspace, install_root, receipt_path, receipt, config


def _capture(command, env=None):
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        timeout=30,
        check=False,
        shell=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise ValueError("Selected executable probe failed: " + result.stderr.decode("utf-8", "replace")[-1000:])
    if len(result.stdout) > 65536:
        raise ValueError("Selected executable probe output exceeds the validation limit")
    return result.stdout.decode("utf-8", "replace").strip()


def _windows_version_strings(executable):
    """Read version resources without launching Inkscape or creating a profile."""
    if os.name != "nt":
        raise ValueError("Read-only lifecycle host version probing currently supports Windows only")
    import ctypes
    from ctypes import wintypes

    version = ctypes.WinDLL("version", use_last_error=True)
    version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    version.GetFileVersionInfoW.restype = wintypes.BOOL
    version.VerQueryValueW.argtypes = [
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.UINT),
    ]
    version.VerQueryValueW.restype = wintypes.BOOL
    unused = wintypes.DWORD()
    size = version.GetFileVersionInfoSizeW(str(executable), ctypes.byref(unused))
    if not size or size > 1024 * 1024:
        raise ValueError("Selected host has no bounded Windows version resource")
    data = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(str(executable), 0, size, data):
        raise ctypes.WinError(ctypes.get_last_error())
    pointer = ctypes.c_void_p()
    length = wintypes.UINT()
    if not version.VerQueryValueW(data, "\\VarFileInfo\\Translation", ctypes.byref(pointer), ctypes.byref(length)):
        raise ValueError("Selected host has no version-resource language table")
    if not pointer.value or length.value < 4 or length.value > size or length.value % 4:
        raise ValueError("Selected host has an invalid version-resource language table")
    words = ctypes.cast(pointer, ctypes.POINTER(wintypes.WORD))
    for index in range(0, length.value // 2, 2):
        table = "\\StringFileInfo\\{:04x}{:04x}\\".format(words[index], words[index + 1])
        values = {}
        for key in ("ProductName", "ProductVersion", "FileVersion"):
            if version.VerQueryValueW(data, table + key, ctypes.byref(pointer), ctypes.byref(length)):
                if not pointer.value or not 1 <= length.value <= 4096:
                    raise ValueError("Selected host has an invalid version-resource string")
                values[key] = ctypes.wstring_at(pointer, length.value).rstrip("\0")
        if values.get("ProductName") == "Inkscape":
            return values
    raise ValueError("Selected executable version resource does not identify Inkscape")


def _host_probe(executable):
    values = _windows_version_strings(executable)
    value = values.get("ProductVersion", "")
    match = re.fullmatch(r"(\d+)\.(\d+)(?:\.(\d+))?", value)
    if not match or tuple(int(part) for part in match.groups()[:2]) < MIN_HOST:
        raise ValueError("Inkscape executable version must be at least 1.4")
    return {
        "host_version": value,
        "host_version_output": "Inkscape " + value + " (Windows version resource; host not launched)",
        "host_version_source": "windows_version_resource",
        "host_executable_sha256": _digest(Path(executable)),
    }


def _probe(config):
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    host = _host_probe(config["executable"])
    script = (
        "import hashlib,json,sys; from pathlib import Path; import dcc_mcp_inkscape, dcc_mcp_core; "
        "from dcc_mcp_inkscape.__version__ import __version__; "
        "from dcc_mcp_core.deployment import load_install_sop_schema,validate_install_sop_report; "
        "schema=load_install_sop_schema(); "
        "assert schema['$id'].endswith('adapter-install-sop-v2.schema.json'); "
        "validate_install_sop_report({'schema_version':1,'status':'planned','dcc_type':'inkscape',"
        "'adapter_version':__version__,'core_version':dcc_mcp_core.__version__,'steps':[],'next_steps':[],"
        "'receipt_path':None,'verify':{'directly_usable':False,'failure_stage':None,'failure_reason':None}}); "
        "root=Path(dcc_mcp_inkscape.__file__).resolve().parent; "
        "from dcc_mcp_inkscape.resources import EXTENSION_PATHS; "
        "bundle={name:hashlib.sha256((root/path).read_bytes()).hexdigest() for name,path in EXTENSION_PATHS.items()}; "
        "print(json.dumps({'python':sys.executable,'adapter_version':__version__,'bundle_sha256':bundle,"
        "'adapter_file':dcc_mcp_inkscape.__file__,'core_version':dcc_mcp_core.__version__,"
        "'core_file':dcc_mcp_core.__file__,'schema_id':schema['$id']}))"
    )
    imports = json.loads(_capture([config["python"], "-c", script], environment))
    if imports.get("adapter_version") != __version__:
        raise ValueError("Selected interpreter imports a different adapter version")
    if imports.get("bundle_sha256") != {name: _digest(path) for name, path in FILES.items()}:
        raise ValueError("Selected interpreter imports different adapter effect bytes")
    host["imports"] = imports
    return host


def _payloads(config, probe=None):
    source = (probe or {}).get("imports", {}).get("adapter_file", str(HERE / "__init__.py"))
    context = {
        "schema_version": 1,
        "workspace": config["workspace"],
        "config": config,
        "module_root": str(Path(source).resolve().parent.parent),
    }
    payloads = {name: source.read_bytes() for name, source in FILES.items()}
    payloads[MENU_CONTEXT] = (json.dumps(context, sort_keys=True, indent=2) + "\n").encode("utf-8")
    return payloads


def _artifacts(workspace, receipt, config, probe=None, removing=False):
    files = receipt.get("files", []) if receipt else []
    legacy = bool(receipt and receipt.get("schema_version") == 1)
    names = LEGACY_NAMES if legacy else set(FILES) | {MENU_CONTEXT}
    if receipt and (not isinstance(files, list) or len(files) != len(names)):
        raise ValueError("Receipt file ownership inventory is invalid")
    payloads = _payloads(config, probe)
    expected = {
        str(Path(config["profile"]) / "extensions" / name): hashlib.sha256(data).hexdigest()
        for name, data in payloads.items()
        if not removing or name in names
    }
    recorded = {}
    for record in files:
        if not isinstance(record, dict):
            raise ValueError("Receipt file ownership record is invalid")
        path = _inside(workspace, record.get("path", ""))
        if path.name not in names or str(path) not in expected or str(path) in recorded:
            raise ValueError("Receipt contains an unexpected owned file")
        digest = record.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Receipt has an invalid owned file digest")
        recorded[str(path)] = digest
    results = []
    for path, digest in expected.items():
        target = Path(path)
        if target.is_symlink():
            raise ValueError("Managed extension targets cannot be symbolic links")
        if _inside(workspace, target) != target:
            raise ValueError("Managed extension targets must retain their canonical profile location")
        actual = _digest(target) if target.is_file() else None
        if target.exists() and actual is None:
            raise ValueError("Managed extension target is not a regular file")
        if path not in recorded and actual is not None:
            raise ValueError("Existing extension files have no ownership receipt; select a fresh private profile")
        if path in recorded and actual is not None and actual != recorded[path]:
            raise ValueError("An owned extension file was modified; refusing to overwrite or remove it")
        results.append({"path": path, "sha256": digest, "actual_sha256": actual, "owned": path in recorded})
    return results


def _runtime(config, instance_id=None):
    from dcc_mcp_core.deployment import query_runtime_state
    from dcc_mcp_core.readiness import READINESS_ALL_BITS
    from dcc_mcp_core.readiness import readiness_report_subset

    state = query_runtime_state(config["registry_dir"], dcc_type="inkscape", include_dead=False)
    published = _read_json(Path(config["ready_file"])) or {}
    published_scope = (
        published.get("adapter") == OWNER
        and published.get("adapter_version") == __version__
        and published.get("workspace") == config["workspace"]
        and published.get("profile") == config["profile"]
    )
    candidates = []
    for entry in state.get("entries", []):
        metadata = entry.get("metadata", {})
        scope = metadata.get("dcc_mcp_project") or metadata.get("project") or metadata.get("workspace")
        if scope is None and published_scope and entry.get("instance_id") == published.get("instance_id"):
            if entry.get("runtime_pid") == published.get("owner_pid"):
                scope = published.get("workspace")
        if scope is None or str(Path(scope).resolve()) != config["workspace"]:
            continue
        if instance_id and entry.get("instance_id") != instance_id:
            continue
        if entry.get("runtime_alive") is True:
            candidates.append(entry)
    if not candidates:
        return {"status": "not_running", "ready": False, "entries": []}
    if len(candidates) != 1:
        return {"status": "ambiguous", "ready": False, "entries": candidates}
    entry = candidates[0]
    url = str(entry.get("mcp_url") or "")
    published_matches = (
        published_scope
        and published.get("status") == "ready"
        and bool(entry.get("instance_id"))
        and entry.get("instance_id") == published.get("instance_id")
        and type(entry.get("runtime_pid")) is int
        and entry["runtime_pid"] > 0
        and type(published.get("owner_pid")) is int
        and entry["runtime_pid"] == published["owner_pid"]
        and published.get("backend_mcp_url") == url
    )
    versions = entry.get("versions") or {}
    registry_versions = [
        value for value in (entry.get("adapter_version"), versions.get("adapter")) if value is not None
    ]
    if registry_versions:
        version_source = "registry"
        version_matches = all(value == __version__ for value in registry_versions)
    else:
        # Core 0.20.36 may omit native standalone adapter versions. A private
        # publication is usable only when bound to this exact live entry.
        version_source = "adapter_ready_file" if published_matches else None
        version_matches = published_matches
    parsed = urlsplit(url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
        or parsed.username
        or parsed.password
        or parsed.path != "/mcp"
        or parsed.query
        or parsed.fragment
    ):
        return {"status": "invalid_endpoint", "ready": False, "entries": candidates}
    endpoint = url.rsplit("/mcp", 1)[0] + "/v1/readyz"
    try:
        # Read only the selected local backend; never start or contact a gateway.
        with build_opener(ProxyHandler({}), _NoRedirect()).open(endpoint, timeout=3) as response:
            data = response.read(65537)
        if len(data) > 65536:
            raise ValueError("Readiness response exceeds the validation limit")
        report = json.loads(data.decode("utf-8"))
        bits = readiness_report_subset(report)
        ready = (
            len(bits) == len(READINESS_ALL_BITS)
            and all(bits.values())
            and all(type(report.get(key)) is bool for key in READINESS_ALL_BITS)
            and published_matches
            and version_matches
        )
        return {
            "status": "ready" if ready else "not_ready",
            "ready": ready,
            "instance_id": entry.get("instance_id"),
            "version_source": version_source,
            "bits": bits,
            "entries": candidates,
        }
    except (OSError, TypeError, ValueError) as exc:
        return {"status": "unreachable", "ready": False, "reason": str(exc), "entries": candidates}


def _atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + str(os.getpid()) + ".tmp")
    try:
        temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        os.replace(str(temporary), str(path))
    finally:
        if temporary.exists():
            temporary.unlink()


def _next_steps(config, verb="verify"):
    command = [config["python"], "-m", "dcc_mcp_inkscape", verb, "--workspace", config["workspace"], "--json"]
    result = [
        {
            "id": "verify",
            "description": "Verify owned files, imports, and an already running service.",
            "command": command,
            "why": "Installed and importable do not establish live readiness.",
        }
    ]
    if config.get("gateway_port"):
        result.insert(
            0,
            {
                "id": "serve",
                "description": "Start the explicitly scoped adapter controller.",
                "command": [
                    config["python"],
                    "-m",
                    "dcc_mcp_inkscape",
                    "serve",
                    "--executable",
                    config["executable"],
                    "--workspace",
                    config["workspace"],
                    "--state-dir",
                    str(Path(config["profile"]).parent),
                    "--registry-dir",
                    config["registry_dir"],
                    "--gateway-port",
                    str(config["gateway_port"]),
                    "--ready-file",
                    config["ready_file"],
                ],
                "why": "The installation command never starts a service or gateway.",
            },
        )
    return result


def _report(status="planned", core_version="unavailable", receipt_path=None):
    return {
        "schema_version": 1,
        "status": status,
        "dcc_type": "inkscape",
        "adapter_version": __version__,
        "core_version": core_version,
        "steps": [],
        "next_steps": [],
        "receipt_path": receipt_path,
        "verify": {"directly_usable": False, "failure_stage": None, "failure_reason": None},
    }


def _apply(workspace, install_root, receipt_path, receipt, config, inventory, probe):
    targets = [Path(record["path"]) for record in inventory]
    previous = {path: path.read_bytes() if path.exists() else None for path in targets}
    config_path = _inside(workspace, install_root / "config.json")
    previous_config = config_path.read_bytes() if config_path.exists() else None
    previous_receipt = receipt_path.read_bytes() if receipt_path.exists() else None
    payloads = _payloads(config, probe)
    changed = set()
    try:
        for record in inventory:
            target = _inside(workspace, record["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            if record["actual_sha256"] != record["sha256"]:
                if previous[target] is None:
                    # A concurrent unowned file must never be replaced or rolled back.
                    with target.open("xb") as stream:
                        changed.add(target)
                        stream.write(payloads[target.name])
                    continue
                with tempfile.NamedTemporaryFile(dir=str(target.parent), delete=False) as stream:
                    stream.write(payloads[target.name])
                    staged = Path(stream.name)
                try:
                    os.replace(str(staged), str(target))
                    changed.add(target)
                finally:
                    if staged.exists():
                        staged.unlink()
        _atomic_json(config_path, config)
        owned = [{"path": str(path), "sha256": _digest(path)} for path in targets]
        committed = {
            "schema_version": RECEIPT_VERSION,
            "owner": OWNER,
            "workspace": str(workspace),
            "adapter_version": __version__,
            "core_version": probe["imports"]["core_version"],
            "host_version": probe["host_version"],
            "host_version_source": probe.get("host_version_source"),
            "host_executable_sha256": probe.get("host_executable_sha256"),
            "config": config,
            "files": owned,
            "source": {
                "adapter_file": probe["imports"]["adapter_file"],
                "sop_schema_artifact": 2,
                "sop_schema_sha256": SOP_DIGEST,
            },
        }
        _atomic_json(receipt_path, committed)
        return committed
    except (OSError, ValueError):
        for path, data in [(path, previous[path]) for path in changed] + [
            (config_path, previous_config),
            (receipt_path, previous_receipt),
        ]:
            if data is None:
                if path.exists():
                    path.unlink()
            else:
                path.write_bytes(data)
        raise


def _remove_owned(workspace, install_root, receipt_path, inventory):
    paths = [_inside(workspace, record["path"]) for record in inventory]
    paths.extend([_inside(workspace, install_root / "config.json"), receipt_path])
    previous = {path: path.read_bytes() for path in paths if path.exists()}
    try:
        for path in paths:
            if path.exists():
                path.unlink()
    except OSError:
        for path, data in previous.items():
            if not path.exists():
                path.write_bytes(data)
        raise


def run_install_command(args):
    """Resolve, plan, or execute one lifecycle command and emit one SOP report."""
    report = _report()
    code = 0
    phase = "preflight"
    try:
        from dcc_mcp_core.deployment import validate_install_sop_report

        validate_install_sop_report(report)
        workspace, install_root, receipt_path, receipt, config = _resolve(args)
        report["receipt_path"] = str(receipt_path)
        report["configuration"] = config
        probe = _probe(config)
        report["core_version"] = probe["imports"]["core_version"]
        report["probe"] = probe
        inventory = _artifacts(workspace, receipt, config, probe, removing=args.command == "uninstall")
        current = bool(
            receipt
            and receipt.get("schema_version") == RECEIPT_VERSION
            and all(record["actual_sha256"] == record["sha256"] for record in inventory)
            and receipt.get("adapter_version") == __version__
            and (install_root / "config.json").is_file()
        )
        report["state"] = (
            "current"
            if current
            else (
                "upgrade"
                if receipt
                and (receipt.get("adapter_version") != __version__ or receipt.get("schema_version") != RECEIPT_VERSION)
                else ("repair" if receipt else "fresh")
            )
        )
        report["installed"] = bool(receipt and all(record["actual_sha256"] for record in inventory if record["owned"]))
        report["menu_resources"] = {
            "available": current,
            "receipt_schema_version": receipt.get("schema_version") if receipt else None,
            "entries": ["Status", "Settings", "Connection"],
            "gui_observed": False,
        }
        report["importable"] = True
        report["steps"] = [
            {"id": "preflight", "status": "ok", "message": "Explicit scope and actual host/import probes passed"}
        ]
        runtime = _runtime(config, args.instance_id)
        report["readiness"] = runtime
        report["next_steps"] = _next_steps(config)
        mutating = args.command in ("install", "upgrade", "uninstall")
        execute = mutating and args.yes and not args.dry_run
        if not execute and mutating:
            report["steps"].append(
                {"id": args.command, "status": "planned", "message": "No files, profile, or receipt changed"}
            )
            execute_command = [
                config["python"],
                "-m",
                "dcc_mcp_inkscape",
                args.command,
                "--workspace",
                str(workspace),
                "--dcc-path",
                config["executable"],
                "--python",
                config["python"],
                "--profile-dir",
                config["profile"],
                "--registry-dir",
                config["registry_dir"],
                "--json",
                "--yes",
            ]
            if config.get("gateway_port"):
                execute_command.extend(["--gateway-port", str(config["gateway_port"])])
            report["next_steps"].insert(
                0,
                {
                    "id": "execute",
                    "description": "Execute the scoped plan after repeat preflight.",
                    "command": execute_command,
                    "why": "Mutation requires --yes on this invocation.",
                },
            )
        elif execute:
            if runtime.get("entries") and not (current and args.command in ("install", "upgrade")):
                raise ValueError("A service is using this workspace; stop that exact owned instance before mutation")
            phase = "install"
            # Repeat ownership and containment checks immediately before mutation.
            _, _, _, checked_receipt, checked_config = _resolve(args)
            if checked_receipt != receipt or checked_config != config:
                raise ValueError("Installation state changed after preflight")
            inventory = _artifacts(workspace, receipt, config, probe, removing=args.command == "uninstall")
            if args.command == "uninstall":
                if receipt:
                    _remove_owned(workspace, install_root, receipt_path, inventory)
                report["installed"] = False
                report["menu_resources"].update(available=False, receipt_schema_version=None)
                report["status"] = "ok"
                report["verify"]["directly_usable"] = False
                report["next_steps"] = []
                report["steps"].append(
                    {"id": "uninstall", "status": "ok", "message": "Only receipt-owned files were removed"}
                )
            else:
                committed = (
                    receipt
                    if current
                    else _apply(workspace, install_root, receipt_path, receipt, config, inventory, probe)
                )
                current = True
                report["menu_resources"].update(available=True, receipt_schema_version=RECEIPT_VERSION)
                report["installed"] = True
                report["status"] = "partial" if not runtime["ready"] else "ok"
                report["steps"].append(
                    {
                        "id": args.command,
                        "status": "ok",
                        "message": "Private extension and provenance receipt committed",
                    }
                )
                report["receipt"] = committed
                if not runtime["ready"]:
                    code = 40
                    report["verify"].update(failure_stage="readiness", failure_reason=runtime["status"])
        else:
            report["status"] = (
                "ok" if args.command == "status" else ("ok" if current and runtime["ready"] else "partial")
            )
            if args.command == "verify" and not (current and runtime["ready"]):
                code = 40
                report["verify"].update(
                    failure_stage="artifacts" if not current else "readiness",
                    failure_reason="owned_artifacts_unavailable" if not current else runtime["status"],
                )
        if args.command != "uninstall":
            report["verify"]["directly_usable"] = bool(report["installed"] and current and runtime["ready"])
        report["steps"].append(
            {
                "id": "verify",
                "status": "ok" if report["verify"]["directly_usable"] else "pending",
                "message": "Installed/importable and live readiness are reported independently",
            }
        )
    except (ImportError, RuntimeError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        code = 10 if phase == "preflight" else 30
        report["status"] = "failed"
        report["steps"].append({"id": "failure", "status": "failed", "message": str(exc)})
        report["verify"].update(failure_stage="preflight" if code == 10 else "install", failure_reason=str(exc))
    # Core owns schema integrity and native validation; do not add a second validator.
    try:
        from dcc_mcp_core.deployment import validate_install_sop_report

        validate_install_sop_report(report)
    except (ImportError, RuntimeError, ValueError) as exc:
        code = 10
        report["status"] = "failed"
        report["verify"].update(failure_stage="sop_validation", failure_reason=str(exc))
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return code
