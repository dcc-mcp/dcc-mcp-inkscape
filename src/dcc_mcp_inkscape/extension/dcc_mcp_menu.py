"""Native Inkscape/GTK control window; it never reads or changes SVG content."""

from pathlib import Path

import inkex
from inkex.base import InkscapeExtension
from menu_bridge import invoke
from menu_bridge import publish_menu_identity


class DccMcpMenu(InkscapeExtension):
    PAGE = "status"

    def add_arguments(self, parser):
        parser.add_argument("--page", choices=("status", "settings", "connection"), default=self.PAGE)

    def load(self, stream):
        return None

    def save(self, stream):
        pass

    def effect(self):
        # GTK is the interface toolkit shipped with Inkscape; no external UI package is installed.
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        context_file = Path(__file__).resolve().with_name("dcc_mcp_menu_context.json")
        dialog = Gtk.Dialog(title="DCC MCP — " + self.options.page.title())
        dialog.set_default_size(720, 480)
        dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        box = dialog.get_content_area()
        box.set_spacing(10)
        box.set_border_width(12)
        text = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        scroll = Gtk.ScrolledWindow()
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)
        scroll.add(text)
        box.pack_start(scroll, True, True, 0)
        full_paths = Gtk.CheckButton(label="Show full paths")
        box.pack_start(full_paths, False, False, 0)

        def display_value(key, value, config):
            if full_paths.get_active() or key not in (
                "workspace",
                "profile",
                "executable",
                "python",
                "registry_dir",
                "ready_file",
            ):
                return str(value)
            path = Path(value)
            if key in ("profile", "registry_dir", "ready_file"):
                try:
                    return str(path.relative_to(config["workspace"]))
                except ValueError:
                    pass
            return path.name

        def update(operation):
            try:
                report = invoke(context_file, operation)
                config = report.get("configuration", {})
                status = report.get("installation", {})
                ready = status.get("readiness", {})
                lines = [
                    report.get("message", ""),
                    report.get("error", ""),
                    "",
                    "Installation: " + str(status.get("state", "unavailable")),
                    "Connection: " + str(ready.get("status", "unavailable")),
                    "Directly usable: " + str(status.get("verify", {}).get("directly_usable", False)),
                ]
                lines.extend(key + ": " + display_value(key, value, config) for key, value in config.items())
                if report.get("started"):
                    lines.extend(
                        ["Started controller PID: " + str(report["owner_pid"]), "Controller log: " + report["log_file"]]
                    )
                text.get_buffer().set_text("\n".join(lines))
            except Exception as exc:
                text.get_buffer().set_text("DCC MCP is not ready: " + str(exc))

        refresh = Gtk.Button(label="Refresh status" if self.options.page != "connection" else "Check connection")
        refresh.connect("clicked", lambda button: update(self.options.page))
        full_paths.connect("toggled", lambda button: update(self.options.page))
        box.pack_start(refresh, False, False, 0)
        if self.options.page == "connection":
            start = Gtk.Button(label="Start configured adapter")

            def start_confirmed(button):
                confirm = Gtk.MessageDialog(
                    transient_for=dialog,
                    modal=True,
                    message_type=Gtk.MessageType.QUESTION,
                    buttons=Gtk.ButtonsType.OK_CANCEL,
                    text="Start the receipt-configured DCC MCP adapter?",
                )
                confirm.format_secondary_text(
                    "This starts a standalone controller in the installed private profile. It does not start a gateway, bind this unsaved document, or change settings."
                )
                result = confirm.run()
                confirm.destroy()
                if result == Gtk.ResponseType.OK:
                    update("start")

            start.connect("clicked", start_confirmed)
            box.pack_start(start, False, False, 0)
        dialog.show_all()
        try:
            publish_menu_identity(context_file, self.options.page)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            inkex.errormsg("DCC MCP menu evidence failed: " + str(exc))
        update(self.options.page)
        dialog.run()
        dialog.destroy()


if __name__ == "__main__":
    DccMcpMenu().run()
