# Third-party notices

The controller, vector-plan validator, native effect integration, and tests
were migrated from the MIT-licensed DCC-MCP Core native Inkscape implementation.
The MIT copyright and permission notice are retained in `LICENSE`.

Inkscape and its bundled inkex extension runtime are separately installed host
software. Upstream's licensing notice distinguishes source that is mostly
GPL-2.0-or-later from the complete executable distribution, which is
GPL-3.0-or-later because of linked dependencies. The actual installed package's
license notices govern its components. This package does not
redistribute its executable, shared libraries, bundled Python, or inkex source.
The MIT adapter communicates with that host through native extension and CLI
protocols; this notice does not relicense the separately supplied software.
See [Inkscape COPYING](https://gitlab.com/inkscape/inkscape/-/raw/master/COPYING)
and [Inkscape licensing](https://inkscape.org/about/license/).

Fonts and branding assets are operator inputs and are not bundled here. Their
licenses remain the operator's responsibility when redistributing artwork.
The package adds private font directories without installing system fonts.
