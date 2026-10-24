"""One bounded, diagnostic-only stack sample of an owned QA process.

No GUI or settings changes. The native sampler suspends threads briefly, so its
frames must never be accepted as a performance comparison.
"""
from pathlib import Path
import json,subprocess,time

class CPUStackSample:
    def __init__(self,case,observer,duration=6,interval_ms=10):
        if duration not in (1,6)or interval_ms not in (5,10):raise ValueError('unbounded sampling request')
        self.case=Path(case);self.observer=observer;self.duration=duration;self.interval=interval_ms
        self.proc=None;self.log=None;self.report=None;self.finished=False
    def start(self,pid,expected_path,owned):
        if self.proc is not None:raise RuntimeError('stack sample already started')
        if type(pid)is not int or pid<=0 or owned.get(pid)!=expected_path or self.observer.path(pid)!=expected_path:
            raise RuntimeError('stack sample target is not the current owned process')
        output=self.case/'cpu-stacks.txt';console=self.case/'cpu-stacks-console.log'
        if output.exists()or console.exists():raise RuntimeError('existing sample evidence')
        self.log=console.open('x');self.started=time.monotonic()
        command=['/usr/bin/sample',str(pid),str(self.duration),str(self.interval),'-mayDie','-file',str(output)]
        try:self.proc=subprocess.Popen(command,stdout=self.log,stderr=subprocess.STDOUT)
        except BaseException:self.log.close();raise
        self.report=dict(pid=pid,expected_path=expected_path,start_mono_ns=time.clock_gettime_ns(time.CLOCK_MONOTONIC),command=command,output=str(output),diagnostic_only=True,performance_evidence=False)
        self._save()
    def _save(self):
        (self.case/'cpu-stacks-result.json').write_text(json.dumps(self.report,indent=2)+'\n')
    def poll(self):
        if self.proc is None:return False
        if self.finished:return True
        status=self.proc.poll()
        if status is None:
            output=Path(self.report['output'])
            if output.is_file()and output.stat().st_size>8*1024*1024:self.close('sampler exceeded output budget')
            if not self.finished and time.monotonic()-self.started>30:self.close('sampler exceeded 30 second budget')
            return self.finished
        self.log.close();self.finished=True
        output=Path(self.report['output']);size=output.stat().st_size if output.is_file()else 0
        self.report.update(exit=status,end_mono_ns=time.clock_gettime_ns(time.CLOCK_MONOTONIC),bytes=size,passed=(status==0 and 0<size<=8*1024*1024))
        self._save();return True
    def close(self,reason='test ended'):
        if self.proc is None or self.finished:return
        if self.proc.poll()is None:
            self.proc.terminate()
            try:self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:self.proc.kill();self.proc.wait()
        self.log.close();self.finished=True
        self.report.update(exit=self.proc.returncode,end_mono_ns=time.clock_gettime_ns(time.CLOCK_MONOTONIC),passed=False,interrupted=reason)
        self._save()
