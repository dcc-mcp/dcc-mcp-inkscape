"""Verify wheel resources and public entry points without claiming host execution."""

import configparser
import sys
import zipfile
from pathlib import Path


def verify_wheel(path):
    required = {
        "dcc_mcp_inkscape/extension/dcc_mcp_vector.inx",
        "dcc_mcp_inkscape/extension/dcc_mcp_vector.py",
        "dcc_mcp_inkscape/plan.py",
        "dcc_mcp_inkscape/windows_process.py",
        "dcc_mcp_inkscape/runtime.py",
        "dcc_mcp_inkscape/server.py",
        "dcc_mcp_inkscape/installation.py",
        "dcc_mcp_inkscape/THIRD_PARTY_NOTICES.md",
        "dcc_mcp_inkscape/skills/inkscape-vector/SKILL.md",
        "dcc_mcp_inkscape/skills/inkscape-vector/tools.yaml",
    }
    required.update(
        "dcc_mcp_inkscape/skills/inkscape-vector/scripts/" + name + ".py"
        for name in ("capabilities", "document_build", "document_export", "document_inspect", "document_open")
    )
    with zipfile.ZipFile(path) as wheel:
        names = set(wheel.namelist())
        missing = required - names
        if missing:
            raise ValueError("Wheel is missing resources: " + ", ".join(sorted(missing)))
        entries = [name for name in names if name.endswith(".dist-info/entry_points.txt")]
        if len(entries) != 1:
            raise ValueError("Wheel must contain one entry-point descriptor")
        parser = configparser.ConfigParser()
        parser.read_string(wheel.read(entries[0]).decode("utf-8"))
        if parser["console_scripts"]["dcc-mcp-inkscape"] != "dcc_mcp_inkscape.cli:main":
            raise ValueError("Wheel console entry point does not match the adapter contract")
        if parser["dcc_mcp.adapters"]["inkscape"] != "dcc_mcp_inkscape:InkscapeMcpServer":
            raise ValueError("Wheel adapter entry point does not match the public API")
    return len(required)


if __name__ == "__main__":
    candidate = Path(sys.argv[1])
    wheels = sorted(candidate.glob("*.whl")) if candidate.is_dir() else [candidate]
    if not wheels:
        raise SystemExit("No wheel found")
    for wheel in wheels:
        print(str(wheel) + ": verified " + str(verify_wheel(wheel)) + " package resources and both entry points")
