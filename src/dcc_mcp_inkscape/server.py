"""Public Core composition root for a standalone native Inkscape controller."""

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from typing import Tuple

from dcc_mcp_core import AdapterReadinessBinder
from dcc_mcp_core import DccServerBase
from dcc_mcp_core import DccServerOptions
from dcc_mcp_core.constants import ENV_PROJECT

from dcc_mcp_inkscape.__version__ import __version__
from dcc_mcp_inkscape.runtime import InkscapeRuntime

SERVER_NAME = "dcc-mcp-inkscape"
SKILLS_DIR = Path(__file__).resolve().parent / "skills"


@dataclass(frozen=True)
class InkscapeServerOptions:
    """Explicit operator-owned runtime scope; no default gateway is launched."""

    executable: str
    workspace: str
    gateway_port: int
    registry_dir: str
    state_dir: Optional[str] = None
    port: Optional[int] = None
    font_dirs: Tuple[str, ...] = ()
    extra_skill_paths: Tuple[str, ...] = ()

    def __post_init__(self):
        if (
            isinstance(self.gateway_port, bool)
            or not isinstance(self.gateway_port, int)
            or not 1024 <= self.gateway_port <= 65535
        ):
            raise ValueError("An explicit gateway port between 1024 and 65535 is required")
        if self.port is not None and (
            isinstance(self.port, bool) or not isinstance(self.port, int) or not 0 <= self.port <= 65535
        ):
            raise ValueError("The backend port must be between 0 and 65535")
        for field in ("executable", "workspace", "registry_dir"):
            if not getattr(self, field):
                raise ValueError("Explicit " + field + " configuration is required")
            # Older Windows pathlib can retain a missing relative path after resolve().
            object.__setattr__(self, field, str(Path(getattr(self, field)).expanduser().absolute().resolve()))
        object.__setattr__(
            self, "font_dirs", tuple(str(Path(value).expanduser().absolute().resolve()) for value in self.font_dirs)
        )
        object.__setattr__(
            self,
            "extra_skill_paths",
            tuple(str(Path(value).expanduser().absolute().resolve()) for value in self.extra_skill_paths),
        )

    @classmethod
    def from_env(cls):
        """Load only explicit adapter-specific operator configuration."""
        return cls(
            executable=os.environ["DCC_MCP_INKSCAPE_EXE"],
            workspace=os.environ["DCC_MCP_INKSCAPE_WORKSPACE"],
            gateway_port=int(os.environ["DCC_MCP_INKSCAPE_GATEWAY_PORT"]),
            registry_dir=os.environ["DCC_MCP_INKSCAPE_REGISTRY_DIR"],
            state_dir=os.environ.get("DCC_MCP_INKSCAPE_STATE_DIR") or None,
            port=int(os.environ["DCC_MCP_INKSCAPE_PORT"]) if os.environ.get("DCC_MCP_INKSCAPE_PORT") else None,
            font_dirs=tuple(
                value for value in os.environ.get("DCC_MCP_INKSCAPE_FONT_DIRS", "").split(os.pathsep) if value
            ),
        )

    def configure_process(self):
        """Publish this one controller's scope to Core's declarative subprocesses."""
        values = {
            "DCC_MCP_INKSCAPE_EXE": self.executable,
            "DCC_MCP_INKSCAPE_WORKSPACE": self.workspace,
            "DCC_MCP_INKSCAPE_GATEWAY_PORT": str(self.gateway_port),
            "DCC_MCP_INKSCAPE_REGISTRY_DIR": self.registry_dir,
            "DCC_MCP_INKSCAPE_FONT_DIRS": os.pathsep.join(self.font_dirs),
            "DCC_MCP_INKSCAPE_STATE_DIR": self.state_dir or ".inkscape-mcp",
            ENV_PROJECT: self.workspace,
        }
        os.environ.update(values)
        # An installed wheel resolves through normal imports; a source checkout retains its src path.
        source_root = str(Path(__file__).resolve().parent.parent)
        paths = os.environ.get("PYTHONPATH", "").split(os.pathsep)
        if source_root not in paths:
            os.environ["PYTHONPATH"] = os.pathsep.join([source_root] + [value for value in paths if value])


class InkscapeMcpServer(DccServerBase):
    """One standalone controller, with real host-invoked native vector effects."""

    def __init__(self, options=None):
        self.adapter_options = options or InkscapeServerOptions.from_env()
        runtime = InkscapeRuntime(
            self.adapter_options.executable,
            self.adapter_options.workspace,
            state_dir=self.adapter_options.state_dir,
            font_dirs=self.adapter_options.font_dirs,
        )
        self.runtime_capabilities = runtime.capabilities()
        self.workspace_path = str(runtime.workspace)
        self.private_profile = str(runtime.profile)
        self.adapter_options.configure_process()
        core_options = DccServerOptions.from_env(
            "inkscape",
            SKILLS_DIR,
            port=self.adapter_options.port,
            server_name=SERVER_NAME,
            adapter_version=__version__,
            instance_type="standalone",
            dcc_version=self.runtime_capabilities["version"],
            scene=self.workspace_path,
            gateway_port=self.adapter_options.gateway_port,
            registry_dir=self.adapter_options.registry_dir,
            enable_gateway_failover=False,
        )
        super().__init__(core_options)
        AdapterReadinessBinder.bind_headless(self)
        self.register_builtin_actions(
            extra_skill_paths=list(self.adapter_options.extra_skill_paths), include_bundled=False
        )


_server = None
_lifecycle_lock = threading.RLock()


def get_server():
    """Return the process-owned adapter instance, if one was started."""
    return _server


def start_server(options=None):
    """Start or reuse one process-owned server through public Core APIs."""
    global _server
    with _lifecycle_lock:
        if _server is not None:
            if options is not None and options != _server.adapter_options:
                raise ValueError("One Inkscape controller scope is supported per Python process")
            if _server.is_running:
                return _server
        candidate = InkscapeMcpServer(options)
        try:
            candidate.start()
            candidate.update_gateway_metadata(
                scene=candidate.workspace_path, version=candidate.runtime_capabilities["version"]
            )
        except Exception:
            candidate.stop()
            raise
        _server = candidate
        return candidate


def stop_server(server=None):
    """Stop only this adapter's supplied or process-owned server."""
    global _server
    with _lifecycle_lock:
        target = server or _server
        if target is None:
            return
        if not isinstance(target, InkscapeMcpServer):
            raise TypeError("Only an InkscapeMcpServer can be stopped by this API")
        target.stop()
        if target is _server:
            _server = None
