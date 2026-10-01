# dcc-mcp-inkscape

Dedicated DCC-MCP adapter for native Inkscape vector authoring and export.

This repository bootstrap establishes the package's source ownership. The
initial implementation is prepared on a feature branch for review. No package
release or release-catalog install is available from this bootstrap commit.

The planned runtime uses a standalone Core controller, a host-invoked Inkscape
inkex extension, and native software exports. It does not embed the Inkscape
Python interpreter into the controller or claim persistent GUI binding.
