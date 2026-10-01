"""Standard adapter lifecycle and foreground server entry point."""

import argparse
import json
import os
import signal
import threading
from pathlib import Path

from dcc_mcp_inkscape.__version__ import __version__


def build_parser():
    """Declare source-only adapter serve and install lifecycle commands."""
    parser = argparse.ArgumentParser(prog="dcc-mcp-inkscape")
    parser.add_argument("--version", action="version", version="dcc-mcp-inkscape " + __version__)
    commands = parser.add_subparsers(dest="command")
    serve = commands.add_parser("serve", help="Start the native typed MCP controller in the foreground")
    serve.add_argument("--executable", default=os.environ.get("DCC_MCP_INKSCAPE_EXE"))
    serve.add_argument("--workspace", default=os.environ.get("DCC_MCP_INKSCAPE_WORKSPACE"))
    serve.add_argument("--gateway-port", type=int, default=os.environ.get("DCC_MCP_INKSCAPE_GATEWAY_PORT"))
    serve.add_argument("--registry-dir", default=os.environ.get("DCC_MCP_INKSCAPE_REGISTRY_DIR"))
    serve.add_argument("--state-dir", default=os.environ.get("DCC_MCP_INKSCAPE_STATE_DIR"))
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument(
        "--font-dir",
        action="append",
        default=[value for value in os.environ.get("DCC_MCP_INKSCAPE_FONT_DIRS", "").split(os.pathsep) if value],
    )
    serve.add_argument("--extra-skill-path", action="append", default=[])
    serve.add_argument("--ready-file", type=Path)
    from dcc_mcp_inkscape.installation import configure_install_parser

    configure_install_parser(commands)
    menu = commands.add_parser("menu", help="Receipt-scoped native menu bridge")
    menu.add_argument("--context-file", required=True, type=Path)
    menu.add_argument("--operation", required=True, choices=("status", "settings", "connection", "start"))
    return parser


def run_serve(args):
    """Keep one owned service alive until an explicit process stop signal."""
    from dcc_mcp_inkscape.runtime import contained_path
    from dcc_mcp_inkscape.server import InkscapeServerOptions
    from dcc_mcp_inkscape.server import start_server
    from dcc_mcp_inkscape.server import stop_server

    options = InkscapeServerOptions(
        executable=args.executable,
        workspace=args.workspace,
        gateway_port=args.gateway_port,
        registry_dir=args.registry_dir,
        state_dir=args.state_dir,
        port=args.port,
        font_dirs=tuple(args.font_dir),
        extra_skill_paths=tuple(args.extra_skill_path),
    )
    server = start_server(options)
    stopped = threading.Event()
    previous = {}
    ready_file = None
    report = None

    def stop(signum, frame):
        stopped.set()

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, stop)
        report = {
            "status": "ready",
            "adapter": "dcc-mcp-inkscape",
            "adapter_version": __version__,
            "runtime_shape": "standalone-controller+native-extension",
            "owner_pid": os.getpid(),
            "instance_id": server.instance_id,
            "backend_mcp_url": server.mcp_url,
            "gateway_port": options.gateway_port,
            "gateway_mcp_url": "http://127.0.0.1:" + str(options.gateway_port) + "/mcp",
            "registry_dir": options.registry_dir,
            "workspace": server.workspace_path,
            "profile": server.private_profile,
            "dcc_version": server.runtime_capabilities["version"],
        }
        ready_file = contained_path(Path(options.workspace), args.ready_file or ".dcc-mcp-inkscape/install/ready.json")
        if ready_file is not None:
            ready_file.parent.mkdir(parents=True, exist_ok=True)
            temporary = ready_file.with_name(ready_file.name + "." + str(os.getpid()) + ".tmp")
            temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
            os.replace(str(temporary), str(ready_file))
        print(json.dumps(report), flush=True)
        while not stopped.wait(0.25):
            pass
        return 0
    finally:
        try:
            stop_server(server)
            if ready_file is not None and ready_file.is_file() and report is not None:
                try:
                    stored = json.loads(ready_file.read_text(encoding="utf-8"))
                except (ValueError, OSError):
                    stored = None
                if (
                    isinstance(stored, dict)
                    and stored.get("owner_pid") == report["owner_pid"]
                    and stored.get("instance_id") == report["instance_id"]
                ):
                    ready_file.unlink()
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)


def main(argv=None):
    """Dispatch standard adapter commands and preserve machine-readable failures."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    try:
        if args.command == "serve":
            return run_serve(args)
        if args.command == "menu":
            from dcc_mcp_inkscape.menu import run_menu_command

            return run_menu_command(args)
        from dcc_mcp_inkscape.installation import run_install_command

        return run_install_command(args)
    except (KeyError, TypeError, ValueError, RuntimeError, OSError) as exc:
        print(json.dumps({"status": "error", "error": type(exc).__name__, "message": str(exc)}), flush=True)
        return 10
