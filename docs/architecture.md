# Native runtime and ownership

The package exposes `InkscapeMcpServer`, `InkscapeServerOptions`, `start_server`,
`get_server`, and `stop_server`. The class composes public Core APIs:
`DccServerBase`, `DccServerOptions.from_env`, and
`AdapterReadinessBinder.bind_headless`. It does not inspect private Core state.

## Execution

```mermaid
flowchart LR
  MCP[Typed MCP call] --> Script[Core declarative subprocess]
  Script --> JSON[Bounded JSON plan]
  JSON --> Host[Owned native Inkscape process]
  Host --> Effect[Host-invoked inkex effect]
  Effect --> Commit[Inkscape document commit]
  Commit --> Export[Native software export]
  Export --> Check[Vector and invocation postconditions]
  Check --> Publish[Atomic new-file publication]
```

The controller writes only JSON requests and configuration/evidence files.
The effect edits Inkscape's supplied document with native inkex objects and
returns it through the normal stdout extension protocol. Only Inkscape writes
production SVG. Export and text-to-path conversion use actual native actions.

Native text conversion has a measured paint limitation: `object-to-path` can
resolve live-text `currentColor` to black even when inherited from a parent.
It does not guarantee preservation of semantic CSS paint tokens. The verified
brand workflow reads the software-generated outlined glyph `d` values,
submits them in a new typed native plan with explicit `currentColor` paint,
then performs native Plain SVG export. No external SVG patch is used.

The controller has one process-scoped workspace/configuration. Core subprocess
scripts import the installed `dcc_mcp_inkscape.runtime` package. A different
workspace/controller scope needs a different Python service process. Backend
ports are assigned by Core/the OS unless explicitly configured. Gateway port
and registry must be selected explicitly; gateway failover is disabled.

## Provenance

Builds correlate a fresh nonce, `SELF_CALL=true`, object count, actual host PID,
and native effect process identity. Linux requires a direct Inkscape parent.
Windows x64 accepts only the bundled Python -> bundled GLib helper -> owned
Inkscape chain with exact native image directories, matching PIDs, and ordered
creation/exit times. The ancestor image and creation time must match the
controller's independently queried owned host. Windows builds create only
their fresh native process tree with `DEBUG_PROCESS`. The thread that creates
it also pumps `WaitForDebugEvent`/`ContinueDebugEvent`; process-birth events
pause the new process while a query-only, noninheritable handle and its native
identity are retained. This covers helpers that exit before Python starts.
All three birth identities and the helper's final lifetime must cross-match
the unchanged complete effect-reported chain. No existing process is attached,
no privileges are adjusted, and process memory/context is never read or changed.
Initial first-chance loader breakpoints are continued; other exceptions remain
unhandled for normal application handling. Owned handles are released after
the created tree exits. Collection is bounded, and Windows' default debugger
thread exit policy contains only that fresh tree on collection failure.
An unreadable/unknown chain is rejected. These are local provenance checks,
not cryptographic host attestation.

The native-event contract follows Microsoft's
[debugging events](https://learn.microsoft.com/en-us/windows/win32/debug/debugging-events),
[WaitForDebugEvent](https://learn.microsoft.com/en-us/windows/win32/api/debugapi/nf-debugapi-waitfordebugevent),
and [debugger thread exit](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-debugsetprocesskillonexit)
documentation. Snapshot polling and job completion messages do not replace
the required retained birth objects.

Before publication, the software's saved SVG must contain the requested native
IDs, namespaces, layer semantics, parent hierarchy, gradient definitions, and
canvas. Reopened files reject executable objects, external resources, CSS
imports/escapes, DTD/stylesheet instructions, and alternate XML encodings.
Existing outputs cannot be replaced.

## Lifecycle

The service owner is the standalone controller, not an optional GUI process.
Every native operation launches its own uniquely tagged Inkscape process and
private profile. Operations are monolithic; Core job cancellation does not
automatically cancel them. A 120-second timeout stops only the owned process.

`document_open` returns the actual newly opened Inkscape GUI PID. It leaves
`accepted=false`. GUI acceptance uses Core's separate official
`python -m dcc_mcp_core.ui_control_server` with that exact PID and actual HWND.
This package adds no screenshots, window discovery, input, or UI authority.

## Scope

The initial version contains five typed tools. It does not bind a persistent
GUI document, advertise a scene publisher, embed Python into Inkscape, install
system fonts, bundle software binaries, or claim macOS adapter support.
Application-private Fontconfig adds operator-selected font directories and
prefers a task cache before included system caches. A requested text family is
not proof of font resolution; verify it with the actual renderer/fontconfig.
