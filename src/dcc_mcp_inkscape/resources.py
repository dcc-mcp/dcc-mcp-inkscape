"""Bundled native extension files shared by runtime and installation."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXTENSION_PATHS = {
    "dcc_mcp_vector.inx": "extension/dcc_mcp_vector.inx",
    "dcc_mcp_vector.py": "extension/dcc_mcp_vector.py",
    "plan.py": "plan.py",
    "windows_process.py": "windows_process.py",
    "dcc_mcp_status.inx": "extension/dcc_mcp_status.inx",
    "dcc_mcp_settings.inx": "extension/dcc_mcp_settings.inx",
    "dcc_mcp_connection.inx": "extension/dcc_mcp_connection.inx",
    "dcc_mcp_menu.py": "extension/dcc_mcp_menu.py",
    "menu_bridge.py": "menu_bridge.py",
    "dcc_mcp_settings.py": "extension/dcc_mcp_settings.py",
    "dcc_mcp_connection.py": "extension/dcc_mcp_connection.py",
}
EXTENSION_FILES = {name: ROOT / path for name, path in EXTENSION_PATHS.items()}
LEGACY_NAMES = frozenset(("dcc_mcp_vector.inx", "dcc_mcp_vector.py", "plan.py", "windows_process.py"))
MENU_CONTEXT = "dcc_mcp_menu_context.json"
FIRST_MENU_NAMES = frozenset(set(EXTENSION_PATHS) - {"dcc_mcp_settings.py", "dcc_mcp_connection.py"})
RESOURCE_REVISION = 2
