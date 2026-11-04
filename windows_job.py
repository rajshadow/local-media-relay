"""Keep an owned Windows child process tied to the supervisor's lifetime."""
import ctypes as ct
from ctypes import wintypes as wt


class BasicLimits(ct.Structure):
    _fields_ = [
        ('per_process_time', ct.c_longlong), ('per_job_time', ct.c_longlong),
        ('flags', wt.DWORD), ('min_working_set', ct.c_size_t), ('max_working_set', ct.c_size_t),
        ('active_processes', wt.DWORD), ('affinity', ct.c_size_t),
        ('priority', wt.DWORD), ('scheduling_class', wt.DWORD),
    ]


class ExtendedLimits(ct.Structure):
    _fields_ = [
        ('basic', BasicLimits), ('io_counters', ct.c_ulonglong * 6),
        ('process_memory', ct.c_size_t), ('job_memory', ct.c_size_t),
        ('peak_process_memory', ct.c_size_t), ('peak_job_memory', ct.c_size_t),
    ]


class ChildJob:
    def __init__(self):
        self.kernel = ct.WinDLL('kernel32', use_last_error=True)
        for name, result, arguments in (
            ('CreateJobObjectW', wt.HANDLE, [ct.c_void_p, wt.LPCWSTR]),
            ('SetInformationJobObject', wt.BOOL, [wt.HANDLE, ct.c_int, ct.c_void_p, wt.DWORD]),
            ('OpenProcess', wt.HANDLE, [wt.DWORD, wt.BOOL, wt.DWORD]),
            ('AssignProcessToJobObject', wt.BOOL, [wt.HANDLE, wt.HANDLE]),
            ('CloseHandle', wt.BOOL, [wt.HANDLE]),
        ):
            function = getattr(self.kernel, name)
            function.restype = result
            function.argtypes = arguments

        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ct.WinError(ct.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(self.handle, 9, ct.byref(limits), ct.sizeof(limits)):
            error = ct.WinError(ct.get_last_error())
            self.close()
            raise error

    def attach(self, process):
        # PROCESS_SET_QUOTA | PROCESS_TERMINATE
        handle = self.kernel.OpenProcess(0x0101, False, process.pid)
        if not handle:
            raise ct.WinError(ct.get_last_error())
        try:
            if not self.kernel.AssignProcessToJobObject(self.handle, handle):
                raise ct.WinError(ct.get_last_error())
        finally:
            self.kernel.CloseHandle(handle)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
