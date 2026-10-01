"""Small stdlib-only bridge used by Inkscape's bundled Python interpreter."""

import hashlib
import json
import os
import subprocess
from pathlib import Path


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
        raise ValueError(str(report.get("error") or "Configured adapter command failed"))
    return report
