---
name: inkscape-vector
description: Create editable native vector documents, query geometry, and export with an explicitly configured Inkscape executable.
license: MIT
compatibility: Python 3.7+, dcc-mcp-core 0.20.36+, Inkscape 1.4 with inkex
metadata:
  dcc-mcp:
    dcc: inkscape
    layer: domain
    version: "0.1.0"
    tools: tools.yaml
---

Use capabilities first to inspect the actual Inkscape version and actions.
Create a document with document_build using a structured vector plan. Plans
contain native layers, groups, paths, shapes, text, and linear gradients; raw
SVG, XML, executable code, and arbitrary actions are not accepted.

All vector creation happens in an inkex effect invoked by Inkscape itself.
The effect returns the edited document to Inkscape; the software commits and
exports it. Results include locally verified invocation evidence. These checks
establish local pipeline provenance, not cryptographic host attestation.

Output paths must be inside the operator-configured workspace and must not
already exist. Choose a new filename after a failed or completed operation.
Inspect the returned evidence before deciding whether a timed-out call should
be repeated. The operation is monolithic and process-owned; killing a Core job
does not imply that the native application process was cancelled.

Use document_export for transparent PNG and Plain SVG, including actual
Inkscape text conversion to paths. ICO and ICNS are not native export formats
in this adapter; package the exported PNGs separately and disclose that step.
Native text conversion can resolve currentColor paint to black. To preserve
that token, read the actual outlined glyph path data and submit those paths
with explicit currentColor paint in a new typed native plan before export.
Use document_inspect to reopen and query native geometry. document_open starts
a separate GUI process for exact-process visual acceptance and does not itself
constitute visual approval. Use the official exact-process ui-control service if needed.
