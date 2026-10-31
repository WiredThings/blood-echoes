"""Opt-in CPU attribution for the existing isolated launcher benchmark.

This is deliberately a diagnostic run, never an unprofiled FPS comparison.
No runtime code, compile flags, caches, settings or input policies are changed.
"""
from pathlib import Path
import subprocess,time

def diagnostic_environment(environment):
    if environment.get('BB_CPU_BACKEND')!='bbcpu':
        raise ValueError('bbcpu diagnostics require the selected bbcpu backend')
    if environment.get('BB_RECOMP_TARGETS') not in (None,'0','1'):
        raise ValueError('conflicting bbcpu target diagnostic')
    return dict(environment,BB_RECOMP_TARGETS='1')

class CpuSample:
    Delay=10.0
    Duration=10
    IntervalMs=5
    def __init__(self,root,executable):
        self.root=Path(root)
        self.allowed={str(executable)}if isinstance(executable,(str,Path))else set(map(str,executable))
        if not self.allowed:raise ValueError('selected CPU paths required')
        self.executable=None
        self.process=None;self.started=None;self.pid=None;self.log=None
        self.result=dict(requested=True,profiler=True,performance_sample=False)
    def observe(self,rows,seconds,scene_ready,observer):
        if self.process is not None or scene_ready is None or seconds-scene_ready<self.Delay:return
        matches=[(pid,path)for pid,ppid,path in rows if path in self.allowed]
        if len(matches)!=1:raise ValueError('diagnostic needs one actual selected CPU process')
        pid,executable=matches[0]
        if observer.path(pid)!=executable:raise ValueError('diagnostic CPU identity changed')
        output=self.root/'cpu-stacks.txt'
        if output.exists()or output.is_symlink():raise ValueError('diagnostic output already exists')
        self.log=(self.root/'cpu-sample-console.log').open('x')
        try:
            self.process=subprocess.Popen(['/usr/bin/sample',str(pid),str(self.Duration),str(self.IntervalMs),'-file',str(output)],stdout=self.log,stderr=subprocess.STDOUT,start_new_session=True)
        except BaseException:
            self.log.close();self.log=None;raise
        self.pid=pid;self.executable=executable;self.started=time.monotonic()
        self.result.update(cpu_pid=pid,executable=self.executable,started_scene_seconds=seconds-scene_ready,duration=self.Duration,interval_ms=self.IntervalMs,output=str(output))
    def finish(self):
        if self.process is None:
            self.result.update(complete=False,error='scene CPU sample never started');return self.result
        # Reap only our own sampler. Never signal the inspected CPU here.
        interrupted=self.process.poll()is None
        if interrupted:
            self.process.terminate()
            try:self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
        if self.log is not None:self.log.close();self.log=None
        output=self.root/'cpu-stacks.txt'
        valid=output.is_file()and not output.is_symlink()and 0<output.stat().st_size<=64*1024*1024
        self.result.update(returncode=self.process.returncode,interrupted=interrupted,complete=not interrupted and self.process.returncode==0 and valid)
        return self.result
