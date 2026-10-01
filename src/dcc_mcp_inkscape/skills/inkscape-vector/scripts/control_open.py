"""Open one fixed native control dialog, without raw input or arbitrary actions."""

from dcc_mcp_core.skill import run_main
from dcc_mcp_core.skill import skill_entry
from dcc_mcp_core.skill import skill_error
from dcc_mcp_core.skill import skill_success

from dcc_mcp_inkscape.runtime import configured_runtime


@skill_entry
def main(panel="status", source_file=None):
    try:
        return skill_success(
            "Native control dialog opened for visual acceptance",
            **configured_runtime().control_open(panel, source_file=source_file),
        )
    except (KeyError, ValueError, RuntimeError, OSError) as exc:
        return skill_error(str(exc), "inkscape_control_open_failed")


if __name__ == "__main__":
    run_main(main)
