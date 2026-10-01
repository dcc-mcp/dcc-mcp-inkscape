# Native DCC MCP menu

The initial vector adapter deliberately hid `org.dcc-mcp.typed-vector-plan`.
That internal effect consumes MCP request evidence, so unhiding it would not
provide a useful connection interface. This change retains that action and
adds three visible entries under **Extensions > DCC MCP**:

| Entry | Action |
|---|---|
| Status | Refresh actual installation state and strict live readiness |
| Settings | View receipt-owned workspace, profile, host, Python, registry and gateway; refresh those values |
| Connection | Check that exact connection; explicitly start the configured standalone adapter after confirmation |

Settings are read-only because installed configuration belongs to the reviewed
lifecycle. To change scope, stop only the exact controller, plan its uninstall,
and plan an installation with the intended workspace/profile/gateway values.
The dialog does not provide editable controls that silently bypass ownership.

Connection start records the actual child PID, command and unique log. It waits
up to 15 seconds for matching live publication, then shows `ready`, `starting`
or `exited`. Refresh Status to obtain the full current readiness report.
`starting` retains an exclusive start lock to prevent duplicate launches. An
operator resolving failed startup must inspect the recorded log and exact
process before removing that task-owned lock. Menu actions never stop another
process, start a gateway, change its policy, or attach the current unsaved SVG.

Install or upgrade through [install.md](../install.md). The resources and
generated context are installed only in the selected task-private profile.
The context selects the already-installed controller Python and adapter import
root; Inkscape's own Python supplies inkex and GTK. Missing, modified or
out-of-scope metadata produces a native diagnostic, with no default-profile or
default-gateway fallback.

For visual acceptance, open a **new independent Inkscape instance** with that
profile and a distinct application ID. Preserve current documents and their
profiles. Use the official exact-process UI service to observe the submenu,
all three dialogs, their actual configured values, and explicit connection
behavior. Lifecycle hash checks and action enumeration alone do not establish
visual acceptance. Controller readiness and GUI document acceptance remain
separate evidence.

The implementation follows Inkscape's official
[INX descriptor contract](https://inkscape.gitlab.io/extensions/documentation/authors/inx-overview.html)
and bundled native extension-manager example. GTK support is supplied by
[inkex's GUI environment](https://inkscape.gitlab.io/extensions/documentation/),
not a new controller GUI dependency. Windows 1.4.4 is the actual case host;
Linux/macOS GUI behavior requires its own native acceptance.
