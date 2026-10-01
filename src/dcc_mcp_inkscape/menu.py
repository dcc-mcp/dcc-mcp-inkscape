"""Owned native-menu operations; settings and connection checks are read-only."""

import argparse
import contextlib
import io
import json
import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from dcc_mcp_inkscape import installation
from dcc_mcp_inkscape.menu_bridge import environment
from dcc_mcp_inkscape.menu_bridge import load_context


def _arguments(config):
    parser = argparse.ArgumentParser()
    installation.configure_install_parser(parser.add_subparsers(dest="command"))
    arguments = [
        "status",
        "--workspace",
        config["workspace"],
        "--dcc-path",
        config["executable"],
        "--python",
        config["python"],
        "--profile-dir",
        config["profile"],
        "--registry-dir",
        config["registry_dir"],
        "--json",
    ]
    if config.get("gateway_port"):
        arguments.extend(["--gateway-port", str(config["gateway_port"])])
    return parser.parse_args(arguments)


def _status(config):
    stream = io.StringIO()
    with contextlib.redirect_stdout(stream):
        code = installation.run_install_command(_arguments(config))
    report = json.loads(stream.getvalue())
    if code or report.get("status") == "failed":
        raise ValueError(report.get("verify", {}).get("failure_reason") or "Installation preflight failed")
    return report


def _wait_for_publication(config, child, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if child.poll() is not None:
            return "exited"
        runtime = installation._runtime(config)
        if runtime.get("ready") and any(entry.get("runtime_pid") == child.pid for entry in runtime.get("entries", [])):
            return "ready"
        time.sleep(0.1)
    return "starting"


def _launch(context, command):
    config = context["config"]
    install_root = Path(config["ready_file"]).parent
    lock = install_root / "menu-start.lock"
    token = json.dumps(
        {
            "owner": "dcc-mcp-inkscape",
            "nonce": uuid.uuid4().hex,
            "launcher_pid": os.getpid(),
            "workspace": config["workspace"],
        }
    ).encode("utf-8")
    # Exclusive creation serializes concurrent menu starts, including publication time.
    with lock.open("xb") as stream:
        stream.write(token)
    outcome = "failed"
    try:
        if installation._runtime(config).get("entries"):
            raise ValueError("A controller appeared during connection preflight; refresh its status")
        with tempfile.NamedTemporaryFile(
            prefix="menu-controller-", suffix=".log", dir=str(install_root), delete=False
        ) as log:
            child = subprocess.Popen(
                command,
                env=environment(context),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                shell=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                start_new_session=True,
            )
        outcome = "starting"
        outcome = _wait_for_publication(config, child)
        return {
            "started": True,
            "owner_pid": child.pid,
            "log_file": log.name,
            "command": command,
            "startup_state": outcome,
            "start_lock": str(lock) if outcome == "starting" else None,
        }
    finally:
        # An unresolved child keeps its owned lock; never launch a duplicate on timeout.
        if outcome != "starting" and lock.is_file() and lock.read_bytes() == token:
            lock.unlink()


def operate(context_file, operation):
    context = load_context(context_file)
    config = context["config"]
    status = _status(config)
    report = {
        "operation": operation,
        "configuration": config,
        "installation": status,
        "message": "Live readiness and installed menu resources are checked independently.",
    }
    if operation == "settings":
        report["message"] = (
            "These are the receipt-owned settings. To change scope, stop the exact controller, plan uninstall, then install in the selected scope. This dialog does not edit configuration."
        )
        report["next_steps"] = status["next_steps"]
    elif operation == "start":
        if status["state"] != "current" or not status["installed"]:
            raise ValueError("Upgrade and verify the private-profile resources before starting a connection")
        runtime = status["readiness"]
        if runtime.get("entries"):
            report["message"] = "A controller already owns this workspace. No additional process was started."
            report["started"] = False
        elif runtime.get("status") != "not_running":
            raise ValueError("Connection state is unresolved; refusing to start a second controller")
        else:
            if not config.get("gateway_port"):
                raise ValueError("Configure an explicit gateway port through the installation lifecycle first")
            command = [
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
            ]
            report.update(_launch(context, command))
            if report["startup_state"] == "ready":
                report["installation"] = _status(config)
            report["message"] = (
                "The configured adapter process was launched; startup state: "
                + report["startup_state"]
                + ". Refresh Status to check actual readiness. The gateway is not started or reconfigured by this action."
            )
    elif operation not in ("status", "connection"):
        raise ValueError("Unknown menu operation")
    return report


def run_menu_command(args):
    try:
        report = operate(args.context_file, args.operation)
        code = 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, ImportError, subprocess.SubprocessError) as exc:
        report = {
            "operation": args.operation,
            "error": str(exc),
            "message": "DCC MCP menu operation failed; no connected state is claimed.",
        }
        code = 10
    print(json.dumps(report), flush=True)
    return code
