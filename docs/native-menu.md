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

The three entries use fixed script entry points. Inkscape 1.4.4's native
no-document execution drops INX parameters, so the selected page must not depend
on a hidden `--page` parameter. The host supplies `SELF_CALL`; the controller
still requires that native identity and the exact nonce and profile. The
bundled inkex 1.4.0 exposes the public extension base through `inkex.base`.

For visual acceptance, open a **new independent Inkscape instance** with that
profile and a distinct application ID. Preserve current documents and their
profiles. Use the official exact-process UI service to observe the submenu,
all three dialogs, their actual configured values, and explicit connection
behavior. Lifecycle hash checks and action enumeration alone do not establish
visual acceptance. Controller readiness and GUI document acceptance remain
separate evidence.

The typed `control_open` MCP tool accepts only `status`, `settings` or
`connection`. It invokes that fixed native action in a new private-profile
Inkscape instance. On Windows it creates the fresh host suspended, assigns an
unnamed default Job Object before resuming execution, then validates the
nonce-bound native dialog PID against that exact kernel-owned tree, bundled
Python image and actual process creation/lifetime data. No job limits,
kill-on-close, breakaway, security policies or debug policies are set. Releasing
proof handles preserves the GUI. This menu ownership proof is separate from
the vector effect's unchanged three-hop process-birth verification.

The result provides OS-verified host/dialog identities for the official
exact-process UI service. It still returns `accepted=false`; window creation
and kernel ownership do not prove the visible labels or displayed status.
The verified MCP control-opening route currently supports Windows only.
Manually opening the native menu on other platforms requires separate GUI
acceptance. Job association or identity failures are refused, without attaching
to an existing application or accepting an arbitrary self-reported PID.

An optional `source_file` opens an existing workspace-contained SVG behind the
dialog. It passes the same vector/resource preflight used by `document_open`;
the tool neither edits the source nor accepts arbitrary application actions.

The case's installed-wheel Windows 1.4.4 smoke opened Status and verified its
native nonce, `SELF_CALL`, exact Job membership, bundled Python image and live
process-birth identities. That is component evidence. MCP route acceptance and
the visible submenu/dialog contents require their own actual observations.

The ownership contract follows Microsoft's
[Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)
and [AssignProcessToJobObject](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-assignprocesstojobobject)
documentation.

The implementation follows Inkscape's official
[INX descriptor contract](https://inkscape.gitlab.io/extensions/documentation/authors/inx-overview.html)
and bundled native extension-manager example. GTK support is supplied by
[inkex's GUI environment](https://inkscape.gitlab.io/extensions/documentation/),
not a new controller GUI dependency. Windows 1.4.4 is the actual case host;
Linux/macOS GUI behavior requires its own native acceptance.

The no-document argument behavior is established by the official
[Inkscape 1.4.4 script implementation](https://gitlab.com/inkscape/inkscape/-/blob/INKSCAPE_1_4_4/src/extension/implementation/script.cpp).
