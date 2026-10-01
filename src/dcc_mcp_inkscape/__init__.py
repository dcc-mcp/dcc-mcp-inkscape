"""Public lifecycle API for the native Inkscape DCC-MCP adapter."""

from dcc_mcp_inkscape.__version__ import __version__

__all__ = ["__version__", "InkscapeMcpServer", "InkscapeServerOptions", "get_server", "start_server", "stop_server"]


def __getattr__(name):
    """Keep installer imports light while preserving the standard public API."""
    if name in __all__ and name != "__version__":
        from dcc_mcp_inkscape import server

        return getattr(server, name)
    raise AttributeError("module 'dcc_mcp_inkscape' has no attribute " + repr(name))
