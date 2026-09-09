"""Atomic, per-task Windows process creation.

``subprocess.Popen`` on CPython 3.8 creates a primary-thread handle internally,
but closes it before returning.  A suspended Popen therefore cannot be resumed
reliably through its public API.  ``SuspendedPopen`` keeps that one handle until
the owning :class:`TaskProcessContext` has assigned the process to its Job.
"""

import ctypes
import os
import subprocess
import sys


# WinBase.h CREATE_SUSPENDED.  Python 3.8 exposes no named wrapper for it.
CREATE_SUSPENDED = 0x00000004


class AtomicProcessSpawnError(RuntimeError):
    """A suspended process could not be made safe to run."""


def _kernel32():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.ResumeThread.argtypes = [ctypes.c_void_p]
    kernel.ResumeThread.restype = ctypes.c_ulong
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_int
    return kernel


class SuspendedPopen(subprocess.Popen):
    """Python 3.8 Windows ``Popen`` retaining its primary thread handle.

    This is deliberately a narrow CPython-3.8 compatibility shim, not a new
    shell/process API.  Its ``_execute_child`` is the CPython 3.8 Windows
    implementation with only the final ``CloseHandle(ht)`` deferred.
    """

    def __init__(self, *args, **kwargs):
        if os.name != "nt":
            raise AtomicProcessSpawnError("SuspendedPopen is Windows-only")
        flags = kwargs.get("creationflags", 0)
        # CPython 3.8 exposes this documented Win32 flag through _winapi, not
        # through subprocess' public constants.
        suspended_flag = getattr(subprocess, "CREATE_SUSPENDED", CREATE_SUSPENDED)
        kwargs["creationflags"] = flags | suspended_flag
        self._primary_thread_handle = None
        subprocess.Popen.__init__(self, *args, **kwargs)

    def _execute_child(self, args, executable, preexec_fn, close_fds,
                       pass_fds, cwd, env, startupinfo, creationflags, shell,
                       p2cread, p2cwrite, c2pread, c2pwrite, errread, errwrite,
                       unused_restore_signals, unused_start_new_session):
        # Kept in sync with CPython 3.8.3 Lib/subprocess.py.  It preserves the
        # standard Popen handling for cwd/env/stdio/startupinfo/close_fds and
        # uses CPython's CreateProcessW binding (the documented Win32 API).
        assert not pass_fds, "pass_fds not supported on Windows."
        if isinstance(args, str):
            pass
        elif isinstance(args, bytes):
            if shell:
                raise TypeError("bytes args is not allowed on Windows")
            args = subprocess.list2cmdline([args])
        elif isinstance(args, os.PathLike):
            if shell:
                raise TypeError("path-like args is not allowed when shell is true")
            args = subprocess.list2cmdline([args])
        else:
            args = subprocess.list2cmdline(args)
        if executable is not None:
            executable = os.fsdecode(executable)
        if startupinfo is None:
            startupinfo = subprocess.STARTUPINFO()
        else:
            startupinfo = startupinfo.copy()
        use_std_handles = -1 not in (p2cread, c2pwrite, errwrite)
        if use_std_handles:
            startupinfo.dwFlags |= subprocess._winapi.STARTF_USESTDHANDLES
            startupinfo.hStdInput = p2cread
            startupinfo.hStdOutput = c2pwrite
            startupinfo.hStdError = errwrite
        attribute_list = startupinfo.lpAttributeList
        have_handle_list = bool(attribute_list and "handle_list" in attribute_list)
        if have_handle_list or (use_std_handles and close_fds):
            if attribute_list is None:
                attribute_list = startupinfo.lpAttributeList = {}
            handle_list = attribute_list["handle_list"] = list(
                attribute_list.get("handle_list", []))
            if use_std_handles:
                handle_list += [int(p2cread), int(c2pwrite), int(errwrite)]
            handle_list[:] = self._filter_handle_list(handle_list)
            if handle_list:
                if not close_fds:
                    import warnings
                    warnings.warn("startupinfo.lpAttributeList['handle_list'] "
                                  "overriding close_fds", RuntimeWarning)
                close_fds = False
        if shell:
            startupinfo.dwFlags |= subprocess._winapi.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess._winapi.SW_HIDE
            comspec = os.environ.get("COMSPEC", "cmd.exe")
            args = '{} /c "{}"'.format(comspec, args)
        if cwd is not None:
            cwd = os.fsdecode(cwd)
        sys.audit("subprocess.Popen", executable, args, cwd, env)
        try:
            hp, ht, pid, tid = subprocess._winapi.CreateProcess(
                executable, args, None, None, int(not close_fds), creationflags,
                env, cwd, startupinfo)
        finally:
            self._close_pipe_fds(p2cread, p2cwrite, c2pread, c2pwrite, errread, errwrite)
        self._child_created = True
        self._handle = subprocess.Handle(hp)
        self.pid = pid
        self._primary_thread_handle = ht

    def resume(self):
        """Resume once, then release the primary-thread handle."""
        thread_handle = self._primary_thread_handle
        if not thread_handle:
            raise AtomicProcessSpawnError("primary thread handle is unavailable")
        try:
            if _kernel32().ResumeThread(thread_handle) == 0xFFFFFFFF:
                raise AtomicProcessSpawnError(
                    "ResumeThread failed: winerror={}".format(ctypes.get_last_error()))
        finally:
            self._close_primary_thread_handle()

    def abort_before_resume(self):
        """Ensure a suspended child can never execute user code, then reap it."""
        try:
            self.terminate()
            self.wait()
        finally:
            self._close_primary_thread_handle()

    def _close_primary_thread_handle(self):
        handle, self._primary_thread_handle = self._primary_thread_handle, None
        if handle:
            _kernel32().CloseHandle(handle)
