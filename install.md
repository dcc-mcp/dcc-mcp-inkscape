# Inkscape adapter installation

<!-- install-sop-version: 1 -->

This is a source-stage adapter. Version `0.1.0` in the project describes the
checkout; it does not establish a published PyPI release. Build or install the
reviewed source in an operator-owned virtual environment, and retain its full
Git commit and wheel hash. A Core catalog `type: git` plan only checks out a
pinned commit; it does not install its Python package or establish readiness.

The installer configures the already installed Python adapter's native effect
in a private Inkscape profile. It never downloads Inkscape, installs Python
dependencies, changes a default user profile, starts a gateway, or stops a DCC.
Use the separate authorized host installation workflow if Inkscape is missing.
Inkscape 1.4 or newer and a native Core wheel exposing the canonical Install
SOP v2 schema artifact's public validator must be present in the selected
interpreter. The runbook follows current Install SOP version 1.

The lifecycle's read-only software version probe currently supports Windows
executables with an Inkscape version resource. It reads that resource and the
binary SHA-256 without launching the software. Inkscape's `--version` can
create preferences, so it is not used by plan/status/verify. Linux/macOS
lifecycle host probing fails with an explicit unsupported preflight result;
native runtime support is a separate capability. Actual native execution is
proved by the running adapter's typed tools, not by static version metadata.

## Standard commands

The console command and `python -m dcc_mcp_inkscape` expose the same verbs:

```text
dcc-mcp-inkscape install|status|verify|uninstall|upgrade
```

Every verb accepts `--json`, `--yes`, `--dry-run`, `--dcc-path`, `--python`,
`--workspace`, `--profile-dir`, `--registry-dir`, `--gateway-port`, and optional
`--instance-id`. Status and verify are read-only. The three mutating verbs
plan by default; `--yes` executes this invocation, and `--dry-run` always wins.
Output is exactly one JSON result document; diagnostics belong on stderr.

Start with an existing explicit workspace and the exact installed programs.
These sample paths belong to the operator, not to the adapter:

```powershell
$python = 'C:\work\vector-project\.venv\Scripts\python.exe'
$hostExe = 'C:\work\software\inkscape\bin\inkscape.exe'
$workspace = 'C:\work\vector-project'
& $python -m dcc_mcp_inkscape install --workspace $workspace --dcc-path $hostExe --python $python --profile-dir "$workspace\.inkscape-mcp\profile" --registry-dir "$workspace\.dcc-registry" --gateway-port 19765 --json
& $python -m dcc_mcp_inkscape install --workspace $workspace --dcc-path $hostExe --python $python --profile-dir "$workspace\.inkscape-mcp\profile" --registry-dir "$workspace\.dcc-registry" --gateway-port 19765 --json --yes
& $python -m dcc_mcp_inkscape status --workspace $workspace --json
& $python -m dcc_mcp_inkscape verify --workspace $workspace --json
```

The profile must resolve inside the workspace and end in `profile`, matching
the runtime's `<state-dir>/profile` contract. The default is
`<workspace>/.inkscape-mcp/profile`. The registry also resolves inside the
workspace. A gateway port is explicit configuration; no installation command
contacts or launches that gateway. The package and host interpreter selections
are retained in the receipt; a later invocation cannot silently switch scope.

## Ownership and integrity

The stable receipt is `<workspace>/.dcc-mcp-inkscape/install/receipt.json`;
the adjacent `config.json` records interpreter, software, profile, registry,
gateway, and private readiness-publication paths. The receipt records actual
host/Core/adapter versions, import origin, the SOP v2 artifact digest, and
SHA-256 for four enabled extension files. The selected interpreter's effect
bytes must match the installer package before execution.

An existing unreceipted extension is never adopted or overwritten. A modified
owned effect or configuration blocks upgrade/removal before mutation. Use a
new private profile when adopting another deployment. Install and upgrade
repeat containment and ownership preflight, publish the receipt last, and
restore the previous owned bytes if commit fails. Retrying an unchanged
installation is idempotent.

Uninstall removes only the four receipt-owned extension files and the matching
installation metadata. It preserves preferences, documents, exported artwork,
the Python package, the Inkscape program, and the rest of the profile. A running
service in the selected scope must be stopped by its operator before changing
or removing its effect. No shared DCC process is stopped automatically.

## Installed versus ready

The report exposes `installed`, `importable`, and `readiness` separately.
Configuring the profile without a live service produces `status: partial`,
`readiness.status: not_running`, `verify.directly_usable: false`, and exit 40.
This is honest completed setup with a pending runtime step, not a claim that
the adapter is callable. Installation never invents a restart requirement.

The returned `serve` next step launches the adapter in the foreground with the
exact workspace, profile parent, registry, and gateway port. It publishes a
private ready file only after start succeeds. Verification correlates that
publication's workspace/profile and instance UUID/PID with Core's live registry
query, then reads the selected backend's `/v1/readyz` using Core's readiness
projection. The probe never starts a host, contacts a shared gateway, calls an
effect, or writes a profile. A stale ready file alone proves nothing.

The controller has `instance_type: standalone`; it launches real native
Inkscape operations for each typed request. It does not claim a persistent GUI
document binding. Use discovery, then the actual typed vector tools, to prove
native editing/export after readiness succeeds.

## SOP and exit codes

All reports use the immutable Install SOP **v2 artifact**, SHA-256
`daa5840e07c956d7c9269e5709d6993a3988b905f986c06e7c4c02f5023e9422`.
The report's `schema_version` remains **1**. Reports are checked with Core's
public `load_install_sop_schema` and `validate_install_sop_report`; the adapter
does not reach into Core native/private symbols or provide a second validator.

| Exit | Meaning |
|---:|---|
| 0 | Read-only plan/status or verified usable/removal result |
| 10 | Resolution, ownership, interpreter, host, or SOP preflight failed |
| 20 | Reserved acquisition failure; this installer acquires no software |
| 30 | Profile installation, receipt commit, removal, or rollback failed |
| 40 | Artifacts/imports may be configured, but verification is not usable |
| 50 | Reserved real loaded-artifact/restart evidence; never inferred from a closed service |

For upgrade, install the reviewed replacement adapter package into the chosen
virtual environment first, then plan and execute `upgrade --workspace ...
--json --yes`. For removal, plan and execute `uninstall --workspace ... --json
--yes`. Review failed preflight diagnostics before retrying; edited files are
preserved for the operator to resolve.
