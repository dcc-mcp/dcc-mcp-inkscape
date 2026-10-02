"""Small stdlib-only bridge used by Inkscape's bundled Python interpreter."""

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from datetime import timezone
from pathlib import Path

DIAGNOSTIC_LIMIT = 131072
DIAGNOSTIC_EVENTS = 32
GDK_NULL_TOOL_MESSAGE = "gdk_seat_default_remove_tool: assertion 'tool != NULL' failed"
_GDK_HANDLERS = []


def _bytes(path, limit):
    if path.is_symlink() or not path.is_file():
        raise ValueError("Menu metadata must be a regular owned JSON file")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Menu metadata exceeds its size limit")
    return data


def _object(path, limit):
    value = json.loads(_bytes(path, limit).decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Menu metadata must be a JSON object")
    return value


def load_context(context_file):
    """Accept only the exact receipt-owned context in its installed profile."""
    if Path(context_file).is_symlink():
        raise ValueError("Menu context cannot be a symbolic link")
    path = Path(context_file).resolve()
    if path.name != "dcc_mcp_menu_context.json" or not path.is_file() or path.stat().st_size > 65536:
        raise ValueError("Install this adapter in a private profile before using the DCC MCP menu")
    context = _object(path, 65536)
    if type(context.get("schema_version")) is not int or context.get("schema_version") != 1:
        raise ValueError("Menu context is invalid")
    workspace = Path(context["workspace"]).resolve()
    receipt_path = workspace / ".dcc-mcp-inkscape/install/receipt.json"
    if not receipt_path.is_file() or receipt_path.stat().st_size > 1024 * 1024:
        raise ValueError("Menu installation has no owned receipt")
    receipt = _object(receipt_path, 1024 * 1024)
    config = receipt.get("config", {})
    if not isinstance(config, dict):
        raise ValueError("Receipt configuration must be an object")
    if (
        receipt.get("owner") != "dcc-mcp-inkscape"
        or type(receipt.get("schema_version")) is not int
        or receipt.get("schema_version") != 2
        or receipt.get("workspace") != str(workspace)
        or context.get("config") != config
        or path != Path(config["profile"]).resolve() / "extensions" / path.name
    ):
        raise ValueError("Menu context does not match its owned workspace and profile")
    Path(config["profile"]).resolve().relative_to(workspace)
    config_path = workspace / ".dcc-mcp-inkscape/install/config.json"
    if _object(config_path, 65536) != config:
        raise ValueError("Installation configuration was modified")
    records = receipt.get("files", [])
    if not isinstance(records, list) or not all(isinstance(record, dict) for record in records):
        raise ValueError("Receipt file ownership inventory is invalid")
    matches = [record for record in records if record.get("path") == str(path)]
    if len(matches) != 1 or matches[0].get("sha256") != hashlib.sha256(_bytes(path, 65536)).hexdigest():
        raise ValueError("Menu context is not receipt-owned or was modified")
    if not isinstance(receipt.get("source"), dict):
        raise ValueError("Receipt source is invalid")
    source = Path(receipt["source"]["adapter_file"]).resolve()
    if context.get("module_root") != str(source.parent.parent) or not source.is_file():
        raise ValueError("The installed adapter import location is unavailable")
    if not Path(config["python"]).is_file():
        raise ValueError("The configured Python interpreter is unavailable")
    return context


def environment(context):
    """Use the receipt-selected package, without inherited adapter scope overrides."""
    result = dict(os.environ)
    for name in list(result):
        if name.startswith("DCC_MCP_INKSCAPE_"):
            result.pop(name)
    result["PYTHONPATH"] = context["module_root"]
    result["PYTHONDONTWRITEBYTECODE"] = "1"
    return result


def _diagnostic_path(context_file):
    context = load_context(context_file)
    config = context["config"]
    root = Path(config["profile"]).parent
    directory = root / "evidence"
    path = directory / "menu-diagnostics.json"
    path.resolve().relative_to(Path(config["workspace"]).resolve())
    if root.is_symlink() or directory.is_symlink() or path.is_symlink():
        raise ValueError("Menu diagnostics must retain their owned location")
    return path


def _diagnostic_events(path):
    if not path.exists():
        return []
    value = _object(path, DIAGNOSTIC_LIMIT)
    events = value.get("events")
    if (
        value.get("owner") != "dcc-mcp-inkscape-menu-diagnostics"
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
        or not isinstance(events, list)
        or len(events) > DIAGNOSTIC_EVENTS
    ):
        raise ValueError("Menu diagnostic log is invalid")
    for event in events:
        if (
            not isinstance(event, dict)
            or set(event) != {"time_utc", "pid", "page", "source", "severity", "message"}
            or type(event["pid"]) is not int
            or event["pid"] <= 0
            or event["page"] not in (None, "status", "settings", "connection")
            or event["severity"] not in ("error", "warning", "expected_nonfatal")
            or not isinstance(event["source"], str)
            or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", event["source"])
            or not isinstance(event["time_utc"], str)
            or len(event["time_utc"]) > 40
            or not isinstance(event["message"], str)
            or len(event["message"].encode("utf-8")) > 2048
        ):
            raise ValueError("Menu diagnostic event is invalid")
    return events


def record_diagnostic(context_file, source, message, severity="error", page=None):
    """Retain bounded diagnostics only in the validated installed private scope."""
    temporary = None
    lock = None
    locked = False
    try:
        if (
            severity not in ("error", "warning", "expected_nonfatal")
            or page not in (None, "status", "settings", "connection")
            or not isinstance(source, str)
            or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", source)
        ):
            return False
        path = _diagnostic_path(context_file)
        path.parent.mkdir(exist_ok=True)
        lock = path.with_suffix(".lock")
        # Do not discard another process's error during concurrent menu use.
        with lock.open("x", encoding="utf-8") as stream:
            stream.write(str(os.getpid()))
        locked = True
        events = _diagnostic_events(path)
        bounded = str(message).encode("utf-8", "replace")[:2048].decode("utf-8", "ignore")
        events.append(
            {
                "time_utc": datetime.now(timezone.utc).isoformat(),
                "pid": os.getpid(),
                "page": page,
                "source": source,
                "severity": severity,
                "message": bounded,
            }
        )
        payload = json.dumps(
            {"owner": "dcc-mcp-inkscape-menu-diagnostics", "schema_version": 1, "events": events[-DIAGNOSTIC_EVENTS:]},
            ensure_ascii=False,
        ).encode("utf-8")
        if len(payload) > DIAGNOSTIC_LIMIT:
            return False
        with tempfile.NamedTemporaryFile(dir=str(path.parent), prefix="menu-diagnostics-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        os.replace(str(temporary), str(path))
        temporary = None
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass
        if locked:
            try:
                lock.unlink()
            except OSError:
                pass


def diagnostic_status(context_file):
    """Read retained diagnostics without changing readiness or creating files."""
    path = _diagnostic_path(context_file)
    try:
        events = _diagnostic_events(path)
        return {
            "available": True,
            "log_file": str(path),
            "recent": events,
            "counts": {
                severity: sum(event["severity"] == severity for event in events)
                for severity in ("error", "warning", "expected_nonfatal")
            },
        }
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"available": False, "log_file": str(path), "recent": [], "counts": {}, "error": str(exc)}


def install_gdk_diagnostic_handler(glib, context_file, page):
    """Route just the nonfatal Windows GDK NULL-tool diagnostic to its owned log."""
    if sys.platform != "win32" or not all(
        hasattr(glib, name) for name in ("log_set_handler", "log_default_handler", "LogLevelFlags")
    ):
        return None
    critical = glib.LogLevelFlags.LEVEL_CRITICAL

    def handler(domain, level, message, user_data=None):
        known = domain == "Gdk" and int(level) == int(critical) and message == GDK_NULL_TOOL_MESSAGE
        recorded = record_diagnostic(context_file, "glib-gdk", message, "expected_nonfatal" if known else "error", page)
        if not (known and recorded):
            glib.log_default_handler(domain, level, message, user_data)

    # Fatal/recursion flags are deliberately not registered; their original policy stays in force.
    identity = glib.log_set_handler("Gdk", critical, handler, None)
    # This is a one-shot native extension process. Keep the callback through GTK destruction and late cleanup.
    _GDK_HANDLERS.append((identity, handler))
    return identity


def publish_menu_identity(context_file, panel):
    """Report this extension's own PID for a fresh bounded native-menu request."""
    raw = os.environ.get("DCC_MCP_INKSCAPE_MENU_REQUEST")
    if raw is None:
        return None
    if len(raw) > 8192:
        raise ValueError("Native menu request exceeds its size limit")
    request = json.loads(raw)
    context = load_context(context_file)
    config = context["config"]
    if (
        not isinstance(request, dict)
        or panel not in ("status", "settings", "connection")
        or request.get("panel") != panel
        or request.get("workspace") != config["workspace"]
        or request.get("profile") != config["profile"]
        or not isinstance(request.get("nonce"), str)
        or not re.fullmatch(r"[0-9a-f]{32}", request["nonce"])
        or os.environ.get("SELF_CALL", "").lower() != "true"
    ):
        raise ValueError("Native menu request does not match this Inkscape-hosted profile")
    evidence = Path(config["profile"]).parent / "evidence" / ("menu-" + request["nonce"] + ".json")
    evidence.resolve().relative_to(Path(config["workspace"]))
    if evidence.parent.is_symlink() or evidence.is_symlink():
        raise ValueError("Native menu evidence must retain its owned location")
    report = {
        "schema_version": 1,
        "nonce": request["nonce"],
        "panel": panel,
        "workspace": config["workspace"],
        "profile": config["profile"],
        "menu_pid": os.getpid(),
        "menu_parent_pid": os.getppid(),
        "menu_executable": sys.executable,
        "self_call": True,
    }
    with evidence.open("x", encoding="utf-8") as stream:
        json.dump(report, stream)
    return report


def invoke(context_file, operation):
    if operation not in ("status", "settings", "connection", "start"):
        raise ValueError("Unknown menu operation")
    context = load_context(context_file)
    command = [
        context["config"]["python"],
        "-m",
        "dcc_mcp_inkscape",
        "menu",
        "--context-file",
        str(Path(context_file).resolve()),
        "--operation",
        operation,
    ]
    result = subprocess.run(
        command,
        env=environment(context),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=45,
        shell=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.stderr:
        record_diagnostic(
            context_file,
            "controller",
            result.stderr.decode("utf-8", "replace"),
            "error" if result.returncode else "warning",
            operation if operation in ("status", "settings", "connection") else "connection",
        )
    if len(result.stdout) > 1024 * 1024:
        raise ValueError("Menu response exceeds its size limit")
    try:
        report = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(
            "Configured adapter command failed: " + result.stderr.decode("utf-8", "replace")[-1000:]
        ) from exc
    if not isinstance(report, dict):
        raise ValueError("Menu response must be an object")
    if result.returncode:
        record_diagnostic(context_file, "controller", report.get("error") or "Configured adapter command failed")
        raise ValueError(str(report.get("error") or "Configured adapter command failed"))
    return report
