"""OS resource limits for trusted parser workers processing untrusted bytes."""
import os


class ParserLimit:
    def __init__(self,process):
        self.handle=None
        if os.name!="nt":
            return  # The worker sets POSIX limits before importing any parser.
        import ctypes
        from ctypes import wintypes
        size=ctypes.c_size_t
        class Basic(ctypes.Structure):
            _fields_=[("process_time",ctypes.c_int64),("job_time",ctypes.c_int64),("flags",wintypes.DWORD),
                ("min_working",size),("max_working",size),("active_processes",wintypes.DWORD),
                ("affinity",size),("priority",wintypes.DWORD),("scheduling",wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_=[(name,ctypes.c_uint64) for name in ("reads","writes","other","read_bytes","write_bytes","other_bytes")]
        class Extended(ctypes.Structure):
            _fields_=[("basic",Basic),("io",IO),("process_memory",size),("job_memory",size),("peak_process",size),("peak_job",size)]
        kernel=ctypes.WinDLL("kernel32",use_last_error=True)
        kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR];kernel.CreateJobObjectW.restype=wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD];kernel.SetInformationJobObject.restype=wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE];kernel.AssignProcessToJobObject.restype=wintypes.BOOL
        kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle.restype=wintypes.BOOL
        handle=kernel.CreateJobObjectW(None,None)
        if not handle:raise ValueError("Cannot create the isolated parser resource boundary.")
        info=Extended()
        info.basic.flags=0x2000|0x100|0x8|0x2  # kill-on-close, process memory, process count, CPU time
        info.basic.active_processes=1
        info.basic.process_time=25*10_000_000
        info.process_memory=512*1024*1024
        if not kernel.SetInformationJobObject(handle,9,ctypes.byref(info),ctypes.sizeof(info)) or not kernel.AssignProcessToJobObject(handle,int(process._handle)):
            kernel.CloseHandle(handle)
            raise ValueError("Cannot enforce parser process limits in this environment.")
        self.handle=handle;self.kernel=kernel

    def close(self):
        if self.handle is not None:
            self.kernel.CloseHandle(self.handle);self.handle=None


def worker_limits():
    if os.name!="nt":
        import resource
        resource.setrlimit(resource.RLIMIT_AS,(512*1024*1024,512*1024*1024))
        resource.setrlimit(resource.RLIMIT_CPU,(25,25))
        resource.setrlimit(resource.RLIMIT_FSIZE,(8*1024*1024,8*1024*1024))
