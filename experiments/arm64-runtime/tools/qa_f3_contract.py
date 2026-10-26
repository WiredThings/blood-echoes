"""Finite isolated F2/F3 API check; no process, file or keyboard access here."""
from dataclasses import dataclass
import os,stat

class NewLogLines:
    """Read fresh complete lines only, with fixed memory and append-only identity."""
    def __init__(self):self.offset=0;self.partial=b'';self.identity=None
    def read(self,path):
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:
            info=os.fstat(fd);identity=(info.st_dev,info.st_ino)
            if not stat.S_ISREG(info.st_mode)or info.st_uid!=os.getuid()or info.st_nlink!=1:raise RuntimeError('functional console is not an owned regular file')
            if self.identity is not None and self.identity!=identity:raise RuntimeError('functional console was replaced')
            if info.st_size<self.offset:raise RuntimeError('functional console was truncated')
            self.identity=identity;os.lseek(fd,self.offset,os.SEEK_SET);raw=os.read(fd,256*1024);self.offset+=len(raw)
            size=os.fstat(fd).st_size
        finally:os.close(fd)
        data=self.partial+raw
        complete,separator,self.partial=data.rpartition(b'\n')
        if not separator:self.partial=data;complete=b''
        if len(self.partial)>8192:raise RuntimeError('functional console line exceeded fixed bound')
        return complete.decode('utf-8',errors='replace'),self.offset>=size and not self.partial


@dataclass
class F3Check:
    phase: str = 'entering'
    since: float = 0.0
    initial_ready: float | None = None
    original_cpu: int | None = None
    original_gpu: int | None = None
    replacement_cpu: int | None = None
    completed: float | None = None
    offline_protocol_seen: bool = False
    automatic_continue_seen: bool = False

    def step(self, now, *, scene, cpus, gpus, log):
        """Return at most one scripted token. Never Continue after quickload."""
        if len(cpus)>1 or len(gpus)>1:
            raise RuntimeError('overlapping game/backend processes in isolated F3 check')
        if self.phase == 'entering':
            if scene and len(cpus)==len(gpus)==1:
                self.phase='settling';self.since=now;self.initial_ready=now
                self.original_cpu=next(iter(cpus));self.original_gpu=next(iter(gpus))
        elif self.phase == 'settling':
            if not scene:raise RuntimeError('scene lost before checkpoint request')
            if now-self.since>=10:
                self.phase='saving';self.since=now;return 'quicksave'
        elif self.phase == 'saving':
            if 'Quicksave: Quicksaved - checkpoint ready' in log:
                self.phase='restarting';self.since=now;return 'quickload'
            if now-self.since>30:raise RuntimeError('checkpoint was not confirmed within30s')
        elif self.phase == 'restarting':
            if 'Runtime: quickload switched to offline' in log:self.offline_protocol_seen=True
            if cpus and self.original_cpu not in cpus:
                if self.original_gpu in gpus:raise RuntimeError('old backend retained after replacement CPU start')
                if not self.offline_protocol_seen:
                    raise RuntimeError('replacement did not use original offline supervisor protocol')
                self.replacement_cpu=next(iter(cpus));self.phase='resuming';self.since=now
                self.automatic_continue_seen='Quicksave: character ready; automatic menu input released' in log
            if now-self.since>900:raise RuntimeError('F3 replacement CPU did not start within900s')
        elif self.phase == 'resuming':
            if 'Quicksave: character ready; automatic menu input released' in log:self.automatic_continue_seen=True
            if self.original_cpu in cpus or self.original_gpu in gpus:raise RuntimeError('old F3 process returned')
            if scene and len(gpus)==1 and self.replacement_cpu in cpus and self.automatic_continue_seen:
                self.phase='confirming';self.since=now
            if now-self.since>180:raise RuntimeError('F3 auto-continue did not reach scene within180s')
        elif self.phase == 'confirming':
            if not scene or len(gpus)!=1 or self.replacement_cpu not in cpus:raise RuntimeError('replacement scene did not remain ready')
            if now-self.since>=15:self.phase='completed';self.completed=now
        elif self.phase != 'completed':raise ValueError('unknown F3 check phase')
        return None
