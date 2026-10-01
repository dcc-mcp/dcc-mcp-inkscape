# Source catalog and future release contract

Package identity is `dcc-mcp-inkscape`; canonical DCC id is `inkscape`.
The adapter entry point is `dcc_mcp_inkscape:InkscapeMcpServer`, and the console
entry point is `dcc_mcp_inkscape.cli:main`. Core floor is `0.20.36`, with an
upper bound below `1.0.0`. The initial source version is `0.1.0`.

Source discovery may point at a reviewed repository revision. Clone planning
does not install the package, import it in the selected interpreter, start a
service, or establish usable capabilities. `install.md` owns those steps.
Until a release exists, omit automatic wheel install metadata and release
URLs/hashes. Do not add this source version to a released-version snapshot.

A proposed source catalog row is:

```yaml
name: dcc-mcp-inkscape
description: Native Inkscape vector authoring and software export through typed MCP tools
dcc: [inkscape]
url: https://github.com/dcc-mcp/dcc-mcp-inkscape
issues_url: https://github.com/dcc-mcp/dcc-mcp-inkscape/issues
tags: [adapter, inkscape, vector, svg]
min_core_version: '0.20.36'
```

After an approved release, its catalog row can add the exact released version,
verified wheel URL/SHA-256, console entry point, and immutable instructions.
The canonical runbook URL is
`https://raw.githubusercontent.com/dcc-mcp/dcc-mcp-inkscape/main/install.md`.
Its marker is `<!-- install-sop-version: 1 -->`; SOP reports are validated by
Core's published shared schema/validator. A future release requires actual
supported-platform smoke, the aggregate native Python 3.7 compatibility gate,
and a matching compatibility-matrix/catalog PR. No release workflow is invoked
by the initial source preparation.
