# dcc-mcp-inkscape

Use public `dcc_mcp_core` APIs. The runtime is a standalone controller that
invokes Inkscape's native inkex effect and native command-line actions. It has
no embedded Python host or persistent GUI document binding.

Keep code and repository text in English. Preserve Python 3.7 compatibility.
All vector creation must remain typed MCP -> Inkscape -> native effect ->
software commit/export. Never create production SVG through external XML
writers, embedded bitmaps, arbitrary scripts, or guessed software integration.

Read `README.md`, `install.md`, and `docs/architecture.md` before changing
runtime or install ownership. Keep resource files in built wheels and test
installed-package imports. Keep the bundled tool schemas explicit.

Use a distinct workspace, profile, registry, gateway, and output filename for
live verification. Never change another DCC session, global font installation,
system security settings, or an existing gateway. Stop only owned processes.
GUI acceptance reuses the official exact-process `ui-control` server; do not
add screenshots, input, or window discovery to this adapter.

Run targeted tests, Ruff checks, wheel resource verification, and explicit
native smoke when applicable. Mocked process data is not native proof.
Do not merge, publish a release, or claim release-catalog availability as part
of source preparation. License notices must distinguish this MIT controller
from the separately installed Inkscape/inkex host.

