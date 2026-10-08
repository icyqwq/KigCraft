"""Host-local exclusion for application-owned Codex profile processes.

Kernel locking is combined with a durable opening/running marker. Parent death
must not make a possibly surviving App Server eligible for automatic replacement.
This is coordination, not a tenant filesystem or network sandbox.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import stat
from dataclasses import dataclass

from app.codex_runtime.rpc import RpcError

MAX_RECORD = 1024


class ProfileLockError(RpcError):
    def __init__(self, code):
        super().__init__(code)


@dataclass(frozen=True)
class ProcessState:
    alive: bool
    token: str | None = None


def process_state(pid):
    if type(pid) is not int or not 0<pid<=4294967295:
        raise ProfileLockError("runtime_profile_uncertain")
    if os.name=="nt":
        from ctypes import wintypes
        kernel=ctypes.WinDLL("kernel32",use_last_error=True)
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
        kernel.WaitForSingleObject.restype=wintypes.DWORD
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        kernel.GetProcessTimes.argtypes=[wintypes.HANDLE,*([ctypes.POINTER(wintypes.FILETIME)]*4)]
        kernel.GetProcessTimes.restype=wintypes.BOOL
        handle=kernel.OpenProcess(0x00100000|0x1000,False,pid)
        if not handle:
            return ProcessState(ctypes.get_last_error()!=87)
        try:
            if kernel.WaitForSingleObject(handle,0)==0:
                return ProcessState(False)
            values=[wintypes.FILETIME() for _ in range(4)]
            token=None
            if kernel.GetProcessTimes(handle,*(ctypes.byref(value) for value in values)):
                token=str((values[0].dwHighDateTime<<32)|values[0].dwLowDateTime)
            return ProcessState(True,token)
        finally:
            kernel.CloseHandle(handle)
    # Never use os.kill(pid,0) on Windows: its semantics can terminate a process.
    try:
        os.kill(pid,0)
    except ProcessLookupError:
        return ProcessState(False)
    except PermissionError:
        return ProcessState(True)
    token=None
    try:
        fields=Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")",1)[1].split()
        boot=Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
        token=hashlib.sha256((boot+":"+fields[19]).encode()).hexdigest()
        if fields[0]=="Z":
            return ProcessState(False,token)
    except (OSError,ValueError,IndexError):
        pass
    return ProcessState(True,token)


def check_components(path):
    for part in (path,*path.parents):
        if part.is_symlink() or part.is_junction():
            raise ProfileLockError("runtime_profile_lock_path_unsafe")


class ProfileLease:
    def __init__(self, profile):
        profile=Path(profile).expanduser().resolve()
        key=hashlib.sha256(os.path.normcase(str(profile)).encode("utf-8")).hexdigest()
        # Outside CODEX_HOME, so later isolated runtimes need not mount lock files.
        self.directory=profile.parent/".kigcraft-runtime-locks"
        self.path=self.directory/(key+".lock")
        self.fd=None
        self.fresh=False

    def _open(self):
        if self.fd is not None:
            raise ProfileLockError("runtime_profile_lock_already_open")
        check_components(self.directory)
        self.directory.mkdir(mode=0o700,parents=True,exist_ok=True)
        check_components(self.path)
        flags=os.O_RDWR|getattr(os,"O_NOFOLLOW",0)|getattr(os,"O_BINARY",0)
        try:
            try:
                fd=os.open(self.path,flags|os.O_CREAT|os.O_EXCL,0o600)
                self.fresh=True
            except FileExistsError:
                fd=os.open(self.path,flags)
            os.set_inheritable(fd,False)
            info=os.fstat(fd)
            current=self.path.stat(follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or (info.st_dev,info.st_ino)!=(current.st_dev,current.st_ino):
                raise ProfileLockError("runtime_profile_lock_path_unsafe")
            try:
                if os.name=="nt":
                    import msvcrt
                    os.lseek(fd,0,os.SEEK_SET)
                    msvcrt.locking(fd,msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError:
                raise ProfileLockError("runtime_profile_busy") from None
            self.fd=fd
            if self.fresh and os.fstat(fd).st_size==0:
                self._write("closed")
        except BaseException:
            self.fd=None
            if "fd" in locals():
                os.close(fd)
            raise

    def _read(self):
        os.lseek(self.fd,1,os.SEEK_SET)
        raw=os.read(self.fd,MAX_RECORD+1)
        if not raw and self.fresh:
            return {"version":1,"state":"closed","pid":None,"token":None}
        try:
            record=json.loads(raw)
            if (len(raw)>MAX_RECORD or set(record)!={"version","state","pid","token"} or type(record["version"]) is not int or record["version"]!=1
                    or record["state"] not in {"closed","opening","running"}
                    or (record["state"]=="running" and (type(record["pid"]) is not int or not 0<record["pid"]<=4294967295))
                    or (record["state"]!="running" and record["pid"] is not None)
                    or (record["token"] is not None and (not isinstance(record["token"],str) or len(record["token"])>128))):
                raise ValueError()
            return record
        except (ValueError,TypeError,KeyError):
            raise ProfileLockError("runtime_profile_uncertain") from None

    def _write(self,state,pid=None,token=None):
        raw=json.dumps({"version":1,"state":state,"pid":pid,"token":token},separators=(",",":")).encode()
        os.lseek(self.fd,0,os.SEEK_SET)
        written=os.write(self.fd,b"\0"+raw)
        if written!=len(raw)+1:
            raise OSError("Incomplete profile marker write")
        os.ftruncate(self.fd,len(raw)+1)
        os.fsync(self.fd)
        self.fresh=False

    @staticmethod
    def _still_running(record):
        current=process_state(record["pid"])
        if not current.alive:
            return False
        # A different creation identity proves PID reuse, not a surviving child.
        return not (record["token"] is not None and current.token is not None and record["token"]!=current.token)

    def acquire(self):
        self._open()
        try:
            record=self._read()
            if record["state"]=="opening":
                raise ProfileLockError("runtime_profile_uncertain")
            if record["state"]=="running" and self._still_running(record):
                raise ProfileLockError("runtime_profile_busy")
            self._write("opening")
        except BaseException:
            self.release(clean=False)
            raise
        return self

    def running(self,pid):
        current=process_state(pid)
        self._write("running",pid,current.token)

    def release(self, *, clean=False):
        if self.fd is None:
            return
        fd=self.fd
        try:
            if clean:
                self._write("closed")
        finally:
            self.fd=None
            # Closing releases the OS lock. Never unlink/replace the lock inode.
            os.close(fd)

    def inspect(self):
        self._open()
        try:
            record=self._read()
            return {**record,"process_alive":self._still_running(record) if record["state"]=="running" else None}
        finally:
            self.release(clean=False)

    def reset(self, *, processes_stopped=False):
        if not processes_stopped:
            raise ProfileLockError("runtime_profile_stop_confirmation_required")
        self._open()
        try:
            try:
                record=self._read()
            except ProfileLockError:
                record=None
            if record and record["state"]=="running" and self._still_running(record):
                raise ProfileLockError("runtime_profile_busy")
            self._write("closed")
        finally:
            self.release(clean=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=["inspect","reset"])
    parser.add_argument("--profile",type=Path,required=True)
    parser.add_argument("--processes-stopped",action="store_true")
    args=parser.parse_args()
    try:
        if not args.profile.is_absolute():
            raise ProfileLockError("absolute_profile_required")
        lease=ProfileLease(args.profile)
        if args.action=="inspect":
            result=lease.inspect()
        else:
            lease.reset(processes_stopped=args.processes_stopped)
            result={"state":"closed"}
    except Exception as exc:
        print(json.dumps({"status":"refused","code":exc.code if isinstance(exc,ProfileLockError) else "profile_lock_unavailable"}))
        raise SystemExit(1) from None
    print(json.dumps(result))


if __name__=="__main__":
    main()
