"""Read-only kernel pipe correlation for an owned Linux Inkscape invocation.

GLib may double-fork its effect. Correlate both native communication pipes
while the effect waits, rather than trusting its reparented process ancestry.
No process attachment, privileges, or global system settings are changed.
"""

import hashlib
import json
import os
import re
import stat
import threading
from pathlib import Path


def process_identity(pid):
    """Read PID lifetime and image, checking the lifetime around the image read."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 1:
        raise RuntimeError("Linux native identity requires a positive non-init PID")
    root = Path("/proc") / str(pid)

    def lifetime():
        fields = (root / "stat").read_text().rsplit(")", 1)[1].split()
        if fields[0] in {"Z", "X", "x"}:
            raise RuntimeError("Linux native process has exited")
        return int(fields[19])

    start = lifetime()
    executable = os.readlink(str(root / "exe"))
    cwd = os.readlink(str(root / "cwd"))
    command = (root / "cmdline").read_bytes().rstrip(b"\0").split(b"\0")
    command = [item.decode("utf-8", errors="strict") for item in command]
    if executable.endswith(" (deleted)") or lifetime() != start:
        raise RuntimeError("Linux native process identity changed")
    return {"pid": pid, "start_ticks": start, "executable": executable, "command": command, "cwd": cwd}


def pipe_descriptor(pid, fd, expected_mode):
    """Read a pipe descriptor twice, rejecting descriptor replacement."""
    root = Path("/proc") / str(pid)
    target = os.readlink(str(root / "fd" / str(fd)))
    if not re.fullmatch(r"pipe:\[[1-9][0-9]*\]", target):
        raise RuntimeError("Native effect communication descriptor is not a pipe")
    info = (root / "fdinfo" / str(fd)).read_text()
    flags = next((line.split()[1] for line in info.splitlines() if line.startswith("flags:")), None)
    if flags is None or int(flags, 8) & os.O_ACCMODE != expected_mode:
        raise RuntimeError("Native effect pipe has an unexpected access mode")
    if os.readlink(str(root / "fd" / str(fd))) != target:
        raise RuntimeError("Native effect pipe descriptor changed")
    return target


def correlate_pipes(host_identity, effect_pid):
    """Bind the effect to two readable pipes held by the exact live owned host."""
    host_pid = host_identity["pid"]
    if process_identity(host_pid) != host_identity:
        raise RuntimeError("Owned Linux host identity changed before pipe correlation")
    effect = process_identity(effect_pid)
    if effect_pid == host_pid or effect["start_ticks"] < host_identity["start_ticks"]:
        raise RuntimeError("Invalid native effect lifetime")
    targets = {str(fd): pipe_descriptor(effect_pid, fd, os.O_WRONLY) for fd in (1, 2)}
    if len(set(targets.values())) != 2:
        raise RuntimeError("Native effect must have two distinct communication pipes")
    matches = {}
    for entry in (Path("/proc") / str(host_pid) / "fd").iterdir():
        try:
            target = os.readlink(str(entry))
        except FileNotFoundError:
            continue
        if target in targets.values():
            fd = int(entry.name)
            if pipe_descriptor(host_pid, fd, os.O_RDONLY) != target:
                raise RuntimeError("Owned host communication pipe changed")
            matches[str(fd)] = target
    if set(matches.values()) != set(targets.values()):
        raise RuntimeError("Native effect pipes are not both held by the owned Inkscape host")
    for fd, target in matches.items():
        if pipe_descriptor(host_pid, int(fd), os.O_RDONLY) != target:
            raise RuntimeError("Owned host communication pipe was replaced")
    for fd, target in targets.items():
        if pipe_descriptor(effect_pid, int(fd), os.O_WRONLY) != target:
            raise RuntimeError("Native effect communication pipe was replaced")
    if process_identity(host_pid) != host_identity or process_identity(effect_pid) != effect:
        raise RuntimeError("Native process lifetime changed during pipe correlation")
    return {"host": host_identity, "effect": effect, "host_fds": matches, "effect_fds": targets}


def verify_effect_program(identity, script, interpreter):
    """Reject redirected/proxy programs even if they inherited matching pipes."""
    command = identity.get("command", [])
    if len(command) < 2:
        raise RuntimeError("Linux native effect is not running the configured private extension")
    observed_script = Path(command[1])
    if not observed_script.is_absolute():
        if not identity.get("cwd"):
            raise RuntimeError("Linux native effect has no observed working directory")
        observed_script = Path(identity["cwd"]) / observed_script
    if observed_script.resolve() != Path(script).resolve():
        raise RuntimeError("Linux native effect is not running the configured private extension")
    if Path(identity["executable"]).resolve() != Path(interpreter).resolve():
        raise RuntimeError("Linux native effect interpreter is not the configured PATH Python")


class LinuxPipeObserver:
    """Observe one native-effect rendezvous before releasing that same effect."""

    def __init__(self, process, executable, directory, nonce, script, interpreter):
        self.process = process
        self.directory = Path(directory)
        self.nonce = nonce
        self.script = Path(script).resolve()
        self.interpreter = Path(interpreter).resolve()
        self.script_digest = hashlib.sha256(self.script.read_bytes()).hexdigest()
        self.host = process_identity(process.pid)
        if Path(self.host["executable"]).resolve() != Path(executable).resolve():
            raise RuntimeError("Owned Linux host executable does not match Inkscape")
        self.result = None
        self.error = None
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._observe, daemon=True)
        self.thread.start()

    def _observe(self):
        request = self.directory / "linux-binding-request.json"
        try:
            while not self.stop.wait(0.01):
                if not request.is_file():
                    if self.process.poll() is not None:
                        raise RuntimeError("Owned host exited before native pipe rendezvous")
                    continue
                descriptor = os.open(str(request), os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(descriptor, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
                        raise RuntimeError("Native pipe rendezvous must be a bounded regular file")
                    value = json.loads(stream.read(4097).decode("utf-8"))
                if not isinstance(value, dict) or set(value) != {"nonce", "effect_pid"}:
                    raise RuntimeError("Invalid native pipe rendezvous object")
                if value.get("nonce") != self.nonce:
                    raise RuntimeError("Native pipe rendezvous nonce mismatch")
                result = correlate_pipes(self.host, value.get("effect_pid"))
                verify_effect_program(result["effect"], self.script, self.interpreter)
                if hashlib.sha256(self.script.read_bytes()).hexdigest() != self.script_digest:
                    raise RuntimeError("Private extension changed during native pipe correlation")
                result["private_extension_sha256"] = self.script_digest
                result["private_extension"] = str(self.script)
                result["interpreter"] = str(self.interpreter)
                ack = self.directory / "linux-binding-ack.json"
                temporary = ack.with_suffix(".tmp")
                with self.lock:
                    if self.stop.is_set():
                        raise RuntimeError("Native pipe observation was cancelled before acknowledgement")
                    with temporary.open("x", encoding="utf-8") as stream:
                        json.dump({"nonce": self.nonce, "effect_pid": value["effect_pid"]}, stream)
                    try:
                        os.link(str(temporary), str(ack))
                    finally:
                        temporary.unlink()
                    self.result = result
                return
        except (OSError, ValueError, KeyError, RuntimeError, StopIteration, TypeError) as exc:
            self.error = str(exc)

    def finish(self):
        with self.lock:
            self.stop.set()
        self.thread.join(timeout=2)
        if self.thread.is_alive():
            return {"error": "Linux pipe observer did not stop"}
        if self.error or self.result is None:
            return {"error": self.error or "No independently observed Linux pipe binding"}
        return self.result


def verify_pipe_binding(evidence, host_pid, executable, binding):
    """Require the independently captured kernel binding, never a nonce alone."""
    if not isinstance(binding, dict) or binding.get("error"):
        raise RuntimeError("Native effect has no independently verified Linux pipe binding")
    host = binding.get("host", {})
    effect = binding.get("effect", {})
    if host.get("pid") != host_pid or effect.get("pid") != evidence.get("extension_pid"):
        raise RuntimeError("Native Linux pipe binding process identities do not match")
    if Path(host.get("executable", "")).resolve() != Path(executable).resolve():
        raise RuntimeError("Native Linux pipe binding is not owned by the configured Inkscape")
    pipes = binding.get("effect_fds", {})
    if set(pipes) != {"1", "2"} or len(set(pipes.values())) != 2:
        raise RuntimeError("Native Linux pipe binding lacks both distinct channels")
    if set(binding.get("host_fds", {}).values()) != set(pipes.values()):
        raise RuntimeError("Native Linux communication channels do not match")
