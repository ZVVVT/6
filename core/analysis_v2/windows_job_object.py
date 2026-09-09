"""Small, explicit Windows Job Object wrapper for one task's processes.

The wrapper deliberately has no process creation policy.  In particular, it
cannot close the Popen-to-AssignProcessToJobObject ownership window; callers
must report that limitation rather than infer atomic ownership from it.
"""

import ctypes
import os
from ctypes import wintypes


JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION_CLASS = 1
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800
JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK = 0x00001000
ERROR_ACCESS_DENIED = 5


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_longlong),
        ("TotalKernelTime", ctypes.c_longlong),
        ("ThisPeriodTotalUserTime", ctypes.c_longlong),
        ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


class WindowsJobObjectError(RuntimeError):
    def __init__(self, operation, winerror):
        self.operation = operation
        self.winerror = int(winerror or 0)
        RuntimeError.__init__(self, "{} failed: winerror={}".format(operation, self.winerror))


def _kernel32():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                ctypes.c_void_p, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateJobObject.restype = wintypes.BOOL
    kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                  ctypes.c_void_p, wintypes.DWORD,
                                                  ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryInformationJobObject.restype = wintypes.BOOL
    kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE,
                                      ctypes.POINTER(wintypes.BOOL)]
    kernel.IsProcessInJob.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    return kernel


class WindowsJobObject:
    """One explicitly-owned Job Object; unavailable safely off Windows."""

    def __init__(self):
        self.handle = None
        self.creation_error = None
        if os.name != "nt":
            return
        kernel = _kernel32()
        handle = kernel.CreateJobObjectW(None, None)
        if not handle:
            self.creation_error = ctypes.get_last_error()
            return
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(
                handle, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
                ctypes.byref(info), ctypes.sizeof(info)):
            error = ctypes.get_last_error()
            kernel.CloseHandle(handle)
            self.creation_error = error
            return
        self.handle = handle

    @property
    def available(self):
        return bool(self.handle)

    def is_process_in_any_job(self, process_handle):
        if not self.available:
            return None
        result = wintypes.BOOL()
        if not _kernel32().IsProcessInJob(process_handle, None, ctypes.byref(result)):
            raise WindowsJobObjectError("IsProcessInJob", ctypes.get_last_error())
        return bool(result.value)

    def is_process_in_job(self, process_handle):
        """Return whether ``process_handle`` belongs to this exact Job."""
        if not self.available:
            return None
        result = wintypes.BOOL()
        if not _kernel32().IsProcessInJob(process_handle, self.handle, ctypes.byref(result)):
            raise WindowsJobObjectError("IsProcessInJob", ctypes.get_last_error())
        return bool(result.value)

    def limit_flags(self):
        """Return the configured Job limit flags for diagnostic assertions."""
        if not self.available:
            return None
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        returned = wintypes.DWORD()
        if not _kernel32().QueryInformationJobObject(
                self.handle, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
                ctypes.byref(info), ctypes.sizeof(info), ctypes.byref(returned)):
            raise WindowsJobObjectError("QueryInformationJobObject", ctypes.get_last_error())
        return int(info.BasicLimitInformation.LimitFlags)

    def assign_process(self, process_handle):
        if not self.available:
            return {"assigned": False, "winerror": self.creation_error,
                    "reason": "job-unavailable"}
        before = None
        try:
            before = self.is_process_in_any_job(process_handle)
        except WindowsJobObjectError as error:
            before = "error:{}".format(error.winerror)
        if not _kernel32().AssignProcessToJobObject(self.handle, process_handle):
            error = ctypes.get_last_error()
            return {"assigned": False, "winerror": error, "in_job_before": before,
                    "access_denied": error == ERROR_ACCESS_DENIED}
        return {"assigned": True, "winerror": 0, "in_job_before": before}

    def terminate(self, exit_code=1):
        if not self.available:
            return {"requested": False, "winerror": self.creation_error}
        result = {"requested": False, "winerror": 0, "job_handle": int(self.handle)}
        try:
            result["active_processes_before"] = self.active_process_count()
        except WindowsJobObjectError as error:
            result["active_before_query_winerror"] = error.winerror
        if not _kernel32().TerminateJobObject(self.handle, int(exit_code)):
            result["winerror"] = ctypes.get_last_error()
            return result
        result["requested"] = True
        try:
            result["active_processes_after"] = self.active_process_count()
        except WindowsJobObjectError as error:
            result["active_after_query_winerror"] = error.winerror
        return result

    def active_process_count(self):
        if not self.available:
            return None
        info = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
        returned = wintypes.DWORD()
        if not _kernel32().QueryInformationJobObject(
                self.handle, JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION_CLASS,
                ctypes.byref(info), ctypes.sizeof(info), ctypes.byref(returned)):
            raise WindowsJobObjectError("QueryInformationJobObject", ctypes.get_last_error())
        return int(info.ActiveProcesses)

    def close(self):
        if not self.handle:
            return False
        handle, self.handle = self.handle, None
        return bool(_kernel32().CloseHandle(handle))
