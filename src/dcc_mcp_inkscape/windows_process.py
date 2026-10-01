"""Read-only Windows process diagnostics for native extension provenance."""


def query_process(pid, retained_handle=None):
    """Read a retained process object's basic identity, times, and image name."""
    import ctypes
    from ctypes import wintypes

    class BasicInformation(ctypes.Structure):
        _fields_ = [
            ("exit_status", wintypes.LONG),
            ("peb", ctypes.c_void_p),
            ("affinity", ctypes.c_size_t),
            ("priority", wintypes.LONG),
            ("pid", ctypes.c_size_t),
            ("parent_pid", ctypes.c_size_t),
        ]

    class UnicodeString(ctypes.Structure):
        _fields_ = [("length", wintypes.USHORT), ("maximum_length", wintypes.USHORT), ("buffer", ctypes.c_void_p)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    native = ctypes.WinDLL("ntdll")
    native.NtQueryInformationProcess.argtypes = [
        wintypes.HANDLE,
        wintypes.ULONG,
        ctypes.c_void_p,
        wintypes.ULONG,
        ctypes.POINTER(wintypes.ULONG),
    ]
    native.NtQueryInformationProcess.restype = wintypes.LONG
    handle = retained_handle or kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    report = {"requested_pid": pid}
    try:
        basic = BasicInformation()
        length = wintypes.ULONG()
        status = native.NtQueryInformationProcess(
            handle, 0, ctypes.byref(basic), ctypes.sizeof(basic), ctypes.byref(length)
        )
        if status < 0:
            raise OSError("ProcessBasicInformation failed: " + hex(status & 0xFFFFFFFF))
        report.update(pid=basic.pid, parent_pid=basic.parent_pid, exit_status=basic.exit_status)
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *[ctypes.byref(item) for item in times]):
            raise ctypes.WinError(ctypes.get_last_error())
        report["creation_time"] = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        report["exit_time"] = (times[1].dwHighDateTime << 32) | times[1].dwLowDateTime
        image = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(image))
        if kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size)):
            report["image_win32"] = image.value
        else:
            report["image_win32_error"] = ctypes.get_last_error()
        storage = ctypes.create_string_buffer(65536)
        status = native.NtQueryInformationProcess(handle, 27, storage, len(storage), ctypes.byref(length))
        if status >= 0:
            name = UnicodeString.from_buffer(storage)
            report["image_native"] = ctypes.wstring_at(name.buffer, name.length // 2)
        else:
            report["image_native_error"] = hex(status & 0xFFFFFFFF)
        return report
    finally:
        if retained_handle is None:
            kernel.CloseHandle(handle)


class RetainedWindowsProcess:
    """Keep one read-only process object alive across its normal termination."""

    def __init__(self, pid):
        import ctypes
        from ctypes import wintypes

        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise ValueError("A positive process identity is required")
        self.pid = pid
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.kernel.OpenProcess(0x1000, False, pid)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())

    def query(self):
        """Query the same retained kernel object, including after helper exit."""
        if not self.handle:
            raise RuntimeError("The retained process handle is closed")
        return query_process(self.pid, retained_handle=self.handle)

    def close(self):
        """Release only the acquired read handle; never terminate a process."""
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class WindowsHelperObserver:
    """Retain read-only handles to the owned host's short-lived spawn helpers."""

    def __init__(self, host_pid):
        import ctypes
        import threading
        from ctypes import wintypes

        class ProcessEntry(ctypes.Structure):
            _fields_ = [
                ("size", wintypes.DWORD),
                ("usage", wintypes.DWORD),
                ("pid", wintypes.DWORD),
                ("heap", ctypes.c_size_t),
                ("module", wintypes.DWORD),
                ("threads", wintypes.DWORD),
                ("parent_pid", wintypes.DWORD),
                ("priority", wintypes.LONG),
                ("flags", wintypes.DWORD),
                ("filename", wintypes.WCHAR * 260),
            ]

        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        self.kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        for function in (self.kernel.Process32FirstW, self.kernel.Process32NextW):
            function.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.process_entry = ProcessEntry
        self.host_pid = host_pid
        self.handles = {}
        self.errors = []
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._observe, daemon=True)
        self.thread.start()

    def _observe(self):
        import ctypes

        names = {"gspawn-win64-helper.exe", "gspawn-win64-helper-console.exe"}
        try:
            while not self.stop.is_set():
                snapshot = self.kernel.CreateToolhelp32Snapshot(2, 0)
                if snapshot == ctypes.c_void_p(-1).value:
                    raise ctypes.WinError(ctypes.get_last_error())
                try:
                    entry = self.process_entry()
                    entry.size = ctypes.sizeof(entry)
                    present = self.kernel.Process32FirstW(snapshot, ctypes.byref(entry))
                    while present:
                        if (
                            entry.parent_pid == self.host_pid
                            and entry.filename.lower() in names
                            and entry.pid not in self.handles
                        ):
                            handle = self.kernel.OpenProcess(0x1000, False, entry.pid)
                            if handle:
                                self.handles[entry.pid] = handle
                        present = self.kernel.Process32NextW(snapshot, ctypes.byref(entry))
                finally:
                    self.kernel.CloseHandle(snapshot)
                self.stop.wait(0.001)
        except OSError as exc:
            self.errors.append(str(exc))

    def finish(self):
        """Query retained helper objects, then release every owned read handle."""
        self.stop.set()
        self.thread.join()
        reports = []
        for pid, handle in self.handles.items():
            try:
                reports.append(query_process(pid, retained_handle=handle))
            except OSError as exc:
                reports.append({"requested_pid": pid, "error": str(exc)})
            finally:
                self.kernel.CloseHandle(handle)
        return {"helpers": reports, "errors": self.errors}


class WindowsNativeProcess:
    """Retain owned descendants at birth, before short-lived helpers can exit.

    One thread creates a fresh DEBUG_PROCESS tree and pumps its native events.
    It never attaches to another process, changes privileges, or reads/writes
    process memory. Only query-limited handles are retained beyond an event.
    """

    def __init__(self, command, environment):
        import threading

        self.command = command
        self.environment = environment
        self.process = None
        self.host_process = None
        self.births = {}
        self.handles = {}
        self.errors = []
        self.report = None
        self.ready = threading.Event()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._launch, daemon=True)
        self.thread.start()
        if not self.ready.wait(10):
            self.stop.set()
            self.thread.join(2)
            raise RuntimeError("The owned Windows native process did not report its birth")
        if self.process is None or self.host_process is None:
            self.stop.set()
            self.thread.join(2)
            raise RuntimeError("Cannot capture the owned Windows native process: " + "; ".join(self.errors))

    def _api(self):
        import ctypes
        from ctypes import wintypes

        class CreateProcess(ctypes.Structure):
            _fields_ = [
                ("file", wintypes.HANDLE),
                ("process", wintypes.HANDLE),
                ("thread", wintypes.HANDLE),
                ("base", ctypes.c_void_p),
                ("debug_offset", wintypes.DWORD),
                ("debug_size", wintypes.DWORD),
                ("thread_local", ctypes.c_void_p),
                ("start", ctypes.c_void_p),
                ("image", ctypes.c_void_p),
                ("unicode", wintypes.WORD),
            ]

        class LoadDll(ctypes.Structure):
            _fields_ = [("file", wintypes.HANDLE)]

        class ExceptionRecord(ctypes.Structure):
            _fields_ = [
                ("code", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("record", ctypes.c_void_p),
                ("address", ctypes.c_void_p),
                ("parameter_count", wintypes.DWORD),
                ("parameters", ctypes.c_size_t * 15),
            ]

        class ExceptionInfo(ctypes.Structure):
            _fields_ = [("record", ExceptionRecord), ("first_chance", wintypes.DWORD)]

        class EventData(ctypes.Union):
            # EXCEPTION_DEBUG_INFO is the largest x64 DEBUG_EVENT union member.
            _fields_ = [
                ("create", CreateProcess),
                ("dll", LoadDll),
                ("exception", ExceptionInfo),
                ("storage", ctypes.c_byte * 160),
            ]

        class DebugEvent(ctypes.Structure):
            _fields_ = [("code", wintypes.DWORD), ("pid", wintypes.DWORD), ("tid", wintypes.DWORD), ("data", EventData)]

        if ctypes.sizeof(ctypes.c_void_p) != 8 or ctypes.sizeof(DebugEvent) != 176:
            raise RuntimeError("Native Windows birth retention requires the x64 DEBUG_EVENT layout")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.WaitForDebugEvent.argtypes = [ctypes.POINTER(DebugEvent), wintypes.DWORD]
        kernel.WaitForDebugEvent.restype = wintypes.BOOL
        kernel.ContinueDebugEvent.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
        kernel.ContinueDebugEvent.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel = kernel
        self.event_type = DebugEvent

    def _handle_event(self, event):
        """Handle only birth identity/file ownership and initial loader traps."""
        status = 0x00010002  # DBG_CONTINUE
        if event.code == 3:  # CREATE_PROCESS_DEBUG_EVENT: child is suspended.
            try:
                if event.pid in self.handles or len(self.handles) >= 64:
                    raise RuntimeError("Invalid or excessive owned Windows process births")
                retained = RetainedWindowsProcess(event.pid)
                self.handles[event.pid] = retained
                self.births[event.pid] = retained.query()
                self.active.add(event.pid)
                if event.pid == self.process.pid:
                    self.host_process = self.births[event.pid]
                    self.ready.set()
            finally:
                if event.data.create.file:
                    self.kernel.CloseHandle(event.data.create.file)
        elif event.code == 5:  # EXIT_PROCESS_DEBUG_EVENT
            if event.pid not in self.active:
                raise RuntimeError("Exit event has no matching owned process birth")
            self.active.remove(event.pid)
        elif event.code == 6 and event.data.dll.file:  # LOAD_DLL_DEBUG_EVENT
            self.kernel.CloseHandle(event.data.dll.file)
        elif event.code == 1:  # Preserve application exception handling.
            if (
                event.data.exception.record.code == 0x80000003
                and event.data.exception.first_chance == 1
                and event.pid in self.births
                and event.pid not in self.loader_breakpoints
            ):
                self.loader_breakpoints.add(event.pid)
            else:
                status = 0x80010001  # DBG_EXCEPTION_NOT_HANDLED
        return status

    def _launch(self):
        import ctypes
        import ntpath
        import subprocess

        self.active = set()
        self.loader_breakpoints = set()
        try:
            self._api()
            # Creation and Wait/Continue occur on this same debugger thread.
            self.process = subprocess.Popen(
                self.command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self.environment,
                shell=False,
                creationflags=0x1 | getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            seen_root = False
            events = 0
            while not self.stop.is_set() and (not seen_root or self.active):
                event = self.event_type()
                if not self.kernel.WaitForDebugEvent(ctypes.byref(event), 50):
                    error = ctypes.get_last_error()
                    if error == 121:  # ERROR_SEM_TIMEOUT: bounded event wait.
                        continue
                    raise ctypes.WinError(error)
                events += 1
                try:
                    if events > 30000:
                        raise RuntimeError("The owned Windows process exceeded its native event bound")
                    status = self._handle_event(event)
                    seen_root = seen_root or event.pid == self.process.pid
                except Exception:
                    # Continue only this delivered event before failing closed.
                    self.kernel.ContinueDebugEvent(event.pid, event.tid, 0x80010001)
                    raise
                if not self.kernel.ContinueDebugEvent(event.pid, event.tid, status):
                    raise ctypes.WinError(ctypes.get_last_error())
            if self.stop.is_set():
                raise RuntimeError("Owned native event collection was stopped before all processes exited")
        except Exception as exc:
            self.errors.append(str(exc))
        finally:
            self.ready.set()
            reports = []
            for pid, retained in self.handles.items():
                try:
                    reports.append(retained.query())
                except Exception as exc:
                    reports.append({"requested_pid": pid, "error": str(exc)})
                    self.errors.append("Final owned process query failed for PID " + str(pid) + ": " + str(exc))
                finally:
                    retained.close()
            helpers = [
                item
                for item in reports
                if ntpath.basename(item.get("image_native", "")).lower()
                in {"gspawn-win64-helper.exe", "gspawn-win64-helper-console.exe"}
            ]
            self.report = {
                "mode": "windows-debug-birth-retention",
                "helpers": helpers,
                "birth_processes": list(self.births.values()),
                "errors": list(self.errors),
            }
            # Windows' default debugger-thread exit policy terminates only this
            # freshly created debug tree if event collection failed. It is not
            # modified and no pre-existing process is attached.

    def finish(self):
        """Bound event completion and expose independently retained identities."""
        self.thread.join(5)
        if self.thread.is_alive():
            self.stop.set()
            self.thread.join(2)
        if self.thread.is_alive() or self.report is None:
            raise RuntimeError("Owned Windows event collection did not finish within its bound")
        return self.report
