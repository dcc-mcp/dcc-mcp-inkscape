# dcc-mcp-inkscape

Dedicated DCC-MCP adapter for native Inkscape vector authoring and export.

The adapter creates native editable layers, groups, paths, shapes, text, and
linear and radial gradients from bounded typed plans. Inkscape invokes the bundled inkex
effect, commits its returned document, and writes the SVG. Native actions
perform export and text-to-path conversion.

Version `0.1.0` is the initial source version, with no published package release.
Install a reviewed source revision or a locally built wheel. Source catalog
discovery does not itself install or start a usable adapter. See
[install.md](install.md) for the standard plan-first lifecycle and
[catalog contract](docs/catalog-contract.md) for future release requirements.

## Requirements

- Controller Python 3.7+, with `dcc-mcp-core>=0.20.36,<1.0.0`.
- Inkscape 1.4 with its bundled Python/inkex runtime; Windows x64 portable 1.4.4
  has been used for the native pipeline proof. Do not install inkex into the
  controller as a substitute for the real software.
- An explicit executable, writable workspace, private profile, registry, and
  selected local gateway. Linux direct-parent provenance is implemented;
  macOS execution is not validated. No binaries or fonts are bundled.

The runtime is a **standalone controller with a host-invoked native extension**.
The service owner is the controller. Each native operation owns a separate
Inkscape process; an optional GUI document has its own actual PID. This package
does not claim an embedded Python host or persistent GUI document binding.

## Start and discover

After the plan-first installation commits its private profile, a new Inkscape
instance using that profile exposes **Extensions > DCC MCP > Status, Settings,
Connection**. These native GTK dialogs show receipt-owned settings and the
actual lifecycle readiness result. Settings is read-only. Connection can check
readiness or explicitly start the configured standalone adapter; it never
starts or reconfigures a gateway. Launching a process alone is not a connected
result. See [native menu](docs/native-menu.md) for scope and acceptance details.

The typed vector-plan effect remains hidden because it requires a bounded MCP
request. Its visibility does not determine whether the adapter is installed.
Existing open documents are not restarted or bound to the controller by menu
actions.

Build/install the package in an operator-selected controller environment, then
run the lifecycle in [install.md](install.md). Start one foreground service:

```powershell
python -m dcc_mcp_inkscape serve `
  --executable 'C:\task\inkscape\bin\inkscape.exe' `
  --workspace 'C:\task' `
  --state-dir '.inkscape-mcp-private' `
  --gateway-port 19765 `
  --registry-dir 'C:\task\.dcc-registry' `
  --ready-file 'C:\task\evidence\inkscape-ready.json'
```

The selected gateway must already be operator-owned. There is no implicit
gateway port or automatic failover. The backend port is chosen by Core/the OS.
Optional `--font-dir` adds application-private font directories; `--port`
chooses the backend port, and `--extra-skill-path` adds explicit skill paths.
Matching `DCC_MCP_INKSCAPE_EXE`, `WORKSPACE`, `STATE_DIR`, `GATEWAY_PORT`,
`REGISTRY_DIR`, and `FONT_DIRS` environment values remain process-local.

Use the shared router/CLI to inventory the selected gateway, search `inkscape`,
follow the returned load/describe step, and call its exact tool slug. The
bundled skill is `inkscape-vector`. Reuse one gateway agent session ID and
`--require-gateway` when collecting attributable call evidence.

| Tool | Native operation |
|---|---|
| `capabilities` | Actual software version, actions, private profile, and limits |
| `document_build` | Create a new editable native SVG from a typed plan |
| `document_export` | PNG/SVG/PDF export, Plain SVG, and native text conversion |
| `document_inspect` | Reopen the saved SVG and query native geometry |
| `document_open` | Open a separate native GUI process and return its actual PID |

`document_open` returns `accepted=false`. Visual acceptance requires the
official Core exact-process `ui-control` service bound to the actual PID/HWND.
This adapter adds no screenshot, input, desktop discovery, or UI wrapper.

## Native vector plan

```json
{
  "canvas": {"width": 256, "height": 256, "view_box": "0 0 256 256"},
  "nodes": [
    {"type": "layer", "id": "artwork", "label": "Editable artwork"},
    {"type": "path", "id": "triangle", "parent": "artwork",
     "d": "M 32,224 L 128,32 L 224,224 Z", "fill": "currentColor"}
  ]
}
```

Plans allow ordered native layers/groups, paths, rectangles, circles, ellipses,
text, and linear/radial gradients. Raw XML/SVG, scripts, arbitrary actions, external
resources, CSS injection, nonfinite values, and unknown fields are rejected.
The limits are 2 MB, 5000 objects, and a 32768 px canvas. Output paths stay in
the configured workspace and must be new filenames. Small icons should use
separate simplified vector plans. ICO/ICNS packaging is external to this
adapter's native SVG/PNG/PDF exports.

Radial gradients use `type: radial_gradient` (`radialGradient` is an alias),
explicit numeric `cx`, `cy`, and `r`, plus optional `fx` and `fy` that default
to the center. Coordinates have absolute value at most `1e6`; radius is
`1e-9..1e6`. `gradient_units` is `userSpaceOnUse` by default or
`objectBoundingBox`; normalized coordinates may extend outside `0..1` for
off-center illumination. `gradient_transform` accepts the same bounded SVG
transform functions as other nodes, including nonuniform scaling. Each
gradient lives in native root `<defs>`, has no parent, and contains 2..64
ordered literal-color stops with offset/opacity in `0..1`. Paint references
can target either gradient type. This creates continuous native paint on a
single smooth path; it adds no bitmap tracing or filter authority.

Native `object-to-path` can resolve a live text `currentColor` paint to black,
including when the token is inherited from a parent. Text conversion therefore
does not promise to preserve CSS paint tokens. The verified brand workflow
reads the actual outlined glyph path data, rebuilds those paths with
`fill: currentColor` through a typed native plan, and exports that new document
through Inkscape. It does not patch software-written SVG externally.

Every build records nonce, software/effect/helper identities, exact argv,
diagnostics, and output hash. Publication checks the exact native objects,
hierarchy, gradient definitions, and canvas from the software-written SVG.
Unknown or unreadable process chains fail closed. Windows builds retain their
fresh process tree at native birth events and require those independent
identities to match the complete effect chain, including short-lived helpers.
This launches only owned processes and changes no system security settings. See
[architecture](docs/architecture.md) for process, profile, timeout, and evidence
boundaries. A requested font family is not proof of actual font resolution.

## Public API and validation

```python
from dcc_mcp_inkscape import InkscapeServerOptions, start_server, stop_server

options = InkscapeServerOptions(
    executable=r"C:\task\inkscape\bin\inkscape.exe",
    workspace=r"C:\task",
    state_dir=".inkscape-mcp-private",
    gateway_port=19765,
    registry_dir=r"C:\task\.dcc-registry",
)
server = start_server(options)
# server.instance_id and server.mcp_url are public Core identities.
stop_server(server)
```

Only one controller scope is supported per Python service process. Process
signals stop that owned service, and no other DCC or gateway is terminated.

```powershell
python -m pip install -e '.[dev]'
python -m pytest -q -m 'not dcc'
python -m ruff check src tests tools
python -m ruff format --check src tests tools
python -m build
python tools/verify_wheel.py dist
# Explicit native smoke; use an isolated workspace/test temporary directory.
$env:DCC_MCP_INKSCAPE_LIVE_TEST = '1'
$env:DCC_MCP_INKSCAPE_EXE = 'C:\task\inkscape\bin\inkscape.exe'
python -m pytest tests/test_native_inkscape.py -q -m dcc
```

The native pipeline originated in the MIT-licensed Core implementation at
`5245d2586ff00438ef65ec7b2c264e545641c00f`, with 233 safety regressions and actual
Windows 1.4.4 SVG creation/save/reopen and transparent PNG proof. That initial
Core PR is not represented as merged. This dedicated package requires its own
wheel/import/server/MCP checks before release claims. The CI workflow runs
source tests and wheel validation; it does not publish a release.

See [LICENSE](LICENSE) and [third-party notices](THIRD_PARTY_NOTICES.md).
