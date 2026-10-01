"""Kernel ownership proof for a fresh Windows native control window.

An unnamed default job is assigned before the host's first thread executes.
Only the created process and its inherited descendants are in that job. No
limits, security policies, breakaway flags or kill-on-close settings are set.
"""

import os
import subprocess
from pathlib import Path

from dcc_mcp_inkscape.windows_process import RetainedWindowsProcess
from dcc_mcp_inkscape.windows_process import query_process


class WindowsMenuProcess:
    def __init__(self, command, environment, workspace):
        if os.name != "nt":
            raise ValueError("Verified native control_open currently supports Windows only")
        import ctypes
        from ctypes import wintypes

        class StartupInfo(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("reserved", wintypes.LPWSTR),
                ("desktop", wintypes.LPWSTR),
                ("title", wintypes.LPWSTR),
                ("x", wintypes.DWORD),
                ("y", wintypes.DWORD),
                ("x_size", wintypes.DWORD),
                ("y_size", wintypes.DWORD),
                ("x_chars", wintypes.DWORD),
                ("y_chars", wintypes.DWORD),
                ("fill", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("show", wintypes.WORD),
                ("reserved_size", wintypes.WORD),
                ("reserved_data", ctypes.c_void_p),
                ("stdin", wintypes.HANDLE),
                ("stdout", wintypes.HANDLE),
                ("stderr", wintypes.HANDLE),
            ]

        class ProcessInfo(ctypes.Structure):
            _fields_ = [
                ("process", wintypes.HANDLE),
                ("thread", wintypes.HANDLE),
                ("pid", wintypes.DWORD),
                ("tid", wintypes.DWORD),
            ]

        self.ctypes = ctypes
        self.wintypes = wintypes
        self.kernel = kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.CreateProcessW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.LPCWSTR,
            ctypes.POINTER(StartupInfo),
            ctypes.POINTER(ProcessInfo),
        ]
        kernel.CreateProcessW.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
        kernel.IsProcessInJob.restype = wintypes.BOOL
        kernel.ResumeThread.argtypes = [wintypes.HANDLE]
        kernel.ResumeThread.restype = wintypes.DWORD
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.TerminateProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        self.job = kernel.CreateJobObjectW(None, None)
        self.handle = None
        info = ProcessInfo()
        resumed = False
        try:
            if not self.job:
                raise ctypes.WinError(ctypes.get_last_error())
            startup = StartupInfo()
            startup.cb = ctypes.sizeof(startup)
            command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
            env = ctypes.create_unicode_buffer(
                "\0".join(
                    key + "=" + value for key, value in sorted(environment.items(), key=lambda item: item[0].upper())
                )
                + "\0\0"
            )
            # CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT; no inherited handles.
            if not kernel.CreateProcessW(
                command[0],
                command_line,
                None,
                None,
                False,
                0x4 | 0x400,
                env,
                str(workspace),
                ctypes.byref(startup),
                ctypes.byref(info),
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle = info.process
            self.pid = info.pid
            self.executable = Path(command[0]).resolve()
            self.host_identity = query_process(self.pid, retained_handle=self.handle)
            if Path(self.host_identity["image_win32"]).resolve() != self.executable:
                raise RuntimeError("Created menu host image does not match the configured Inkscape executable")
            if not kernel.AssignProcessToJobObject(self.job, self.handle):
                raise ctypes.WinError(ctypes.get_last_error())
            if kernel.ResumeThread(info.thread) != 1:
                raise RuntimeError("Created menu host did not have exactly one initial suspension")
            resumed = True
        except Exception:
            # Before execution, clean up only the failed fresh suspended host.
            if self.handle and not resumed:
                kernel.TerminateProcess(self.handle, 1)
            self.close()
            raise
        finally:
            if info.thread:
                kernel.CloseHandle(info.thread)

    def poll(self):
        code = self.wintypes.DWORD()
        if not self.kernel.GetExitCodeProcess(self.handle, self.ctypes.byref(code)):
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        return None if code.value == 259 else code.value

    def verify_menu(self, identity):
        """Validate the reported PID against this exact kernel-owned fresh tree."""
        child = RetainedWindowsProcess(identity["menu_pid"])
        try:
            actual = child.query()
            member = self.wintypes.BOOL()
            if not self.kernel.IsProcessInJob(child.handle, self.job, self.ctypes.byref(member)):
                raise self.ctypes.WinError(self.ctypes.get_last_error())
            if not member.value:
                raise RuntimeError("Reported menu PID is outside this owned native process tree")
            host = query_process(self.pid, retained_handle=self.handle)
            allowed = {self.executable.parent / "python.exe", self.executable.parent / "pythonw.exe"}
            image = Path(actual["image_win32"]).resolve()
            if (
                actual["pid"] == self.pid
                or image not in allowed
                or Path(identity["menu_executable"]).resolve() != image
                or actual["parent_pid"] != identity["menu_parent_pid"]
                or actual["exit_time"] != 0
                or host["exit_time"] != 0
                or host["creation_time"] != self.host_identity["creation_time"]
                or not host["creation_time"] <= actual["creation_time"]
            ):
                raise RuntimeError("Native menu image, birth or lifetime does not match the owned host")
            return {
                "mode": "windows-owned-job",
                "job_membership_verified": True,
                "host_birth_identity": self.host_identity,
                "host_current_identity": host,
                "menu_process_identity": actual,
            }
        finally:
            child.close()

    def close(self):
        # Default jobs have no kill-on-close limit; releasing proof handles preserves the GUI.
        for name in ("handle", "job"):
            handle = getattr(self, name, None)
            if handle:
                self.kernel.CloseHandle(handle)
                setattr(self, name, None)
