"""Fixed native Connection entry: no-document effects discard INX parameters."""

from dcc_mcp_menu import DccMcpMenu


class DccMcpConnection(DccMcpMenu):
    PAGE = "connection"


if __name__ == "__main__":
    DccMcpConnection().run()
