"""Windows-only, task-owned process-tree cleanup helpers.

The caller supplies a registered root PID.  No executable-name, user-name, or
session-wide matching is used: descendants are selected only by the parent PID
links present in a Toolhelp process snapshot.
"""

import ctypes
import os
import time
from ctypes import wintypes


TH32CS_SNAPPROCESS = 0x00000002
PROCESS_TERMINATE = 0x0001
SYNCHRONIZE = 0x00100000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
ERROR_ACCESS_DENIED = 5
ERROR_INVALID_PARAMETER = 87
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258


class WindowsProcessTreeError(RuntimeError):
    """A native tree operation failed before its process had exited."""


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


def _kernel32():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel.Process32FirstW.restype = wintypes.BOOL
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel.Process32NextW.restype = wintypes.BOOL
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateProcess.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    return kernel


def _snapshot_parent_map():
    """Return ``pid -> parent_pid`` from one closed Toolhelp snapshot."""
    if os.name != "nt":
        return {}
    kernel = _kernel32()
    snapshot = kernel.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        raise WindowsProcessTreeError(
            "CreateToolhelp32Snapshot failed: winerror={}".format(ctypes.get_last_error())
        )
    parents = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not kernel.Process32FirstW(snapshot, ctypes.byref(entry)):
            error = ctypes.get_last_error()
            raise WindowsProcessTreeError("Process32FirstW failed: winerror={}".format(error))
        while True:
            parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            if not kernel.Process32NextW(snapshot, ctypes.byref(entry)):
                error = ctypes.get_last_error()
                if error == ERROR_INVALID_PARAMETER:
                    break
                # ERROR_NO_MORE_FILES is 18; avoid importing a platform-only constant.
                if error == 18:
                    break
                raise WindowsProcessTreeError("Process32NextW failed: winerror={}".format(error))
    finally:
        kernel.CloseHandle(snapshot)
    return parents


def descendant_pids(root_pid):
    """Return the current descendant closure and depth for one owned root."""
    root_pid = int(root_pid)
    if os.name != "nt" or root_pid <= 0:
        return []
    parents = _snapshot_parent_map()
    depths = {root_pid: 0}
    changed = True
    while changed:
        changed = False
        for pid, parent_pid in parents.items():
            if pid not in depths and parent_pid in depths:
                depths[pid] = depths[parent_pid] + 1
                changed = True
    return sorted(
        ((pid, depth) for pid, depth in depths.items() if pid != root_pid),
        key=lambda item: (-item[1], item[0]),
    )


def _terminate_pid(pid):
    """Terminate and close a process handle; a vanished PID is converged."""
    kernel = _kernel32()
    handle = kernel.OpenProcess(PROCESS_TERMINATE | SYNCHRONIZE, False, int(pid))
    if not handle:
        error = ctypes.get_last_error()
        if error == ERROR_INVALID_PARAMETER:
            return {"pid": int(pid), "status": "already-exited"}
        return {"pid": int(pid), "status": "open-failed", "winerror": error}
    try:
        if not kernel.TerminateProcess(handle, 1):
            error = ctypes.get_last_error()
            if error != ERROR_ACCESS_DENIED:
                return {"pid": int(pid), "status": "terminate-failed", "winerror": error}
        return {"pid": int(pid), "status": "terminate-requested"}
    finally:
        kernel.CloseHandle(handle)


def terminate_owned_tree(root_pid, deadline=None, terminate_root=True):
    """Boundedly terminate an owned root and snapshots of its descendants.

    The first snapshot captures descendants while the root is known alive.  The
    root is then stopped before a second closure scan, which catches children
    spawned during that first snapshot.  Waiting is deliberately left to the
    caller's shared shutdown deadline loop.
    """
    result = {"root_pid": int(root_pid), "strategy": "toolhelp-native", "attempts": []}
    if os.name != "nt" or int(root_pid) <= 0:
        return result
    if deadline is not None and time.monotonic() >= deadline:
        result["deadline_exhausted"] = True
        return result
    initial = descendant_pids(root_pid)
    result["initial_descendants"] = [pid for pid, _depth in initial]
    if terminate_root:
        result["attempts"].append(_terminate_pid(root_pid))
    if deadline is not None and time.monotonic() >= deadline:
        result["deadline_exhausted"] = True
        return result
    current = descendant_pids(root_pid)
    known = {pid: depth for pid, depth in initial}
    known.update(dict(current))
    for pid, _depth in sorted(known.items(), key=lambda item: (-item[1], item[0])):
        if deadline is not None and time.monotonic() >= deadline:
            result["deadline_exhausted"] = True
            break
        result["attempts"].append(_terminate_pid(pid))
    result["descendants_after"] = [pid for pid, _depth in descendant_pids(root_pid)]
    return result
