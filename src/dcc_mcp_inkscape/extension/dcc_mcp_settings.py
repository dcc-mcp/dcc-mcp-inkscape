"""Fixed native Settings entry: no-document effects discard INX parameters."""

from dcc_mcp_menu import DccMcpMenu


class DccMcpSettings(DccMcpMenu):
    PAGE = "settings"


if __name__ == "__main__":
    DccMcpSettings().run()
