"""Actual macOS sampler sees authored busy/wait threads, and rejects wrong ownership."""
from pathlib import Path
import json,subprocess,sys,tempfile,time
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'))
from cpu_stack_sample import CPUStackSample
from native_observer import NativeObserver
source=r'''
#include <pthread.h>
#include <stdio.h>
#include <unistd.h>
static pthread_mutex_t m=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t c=PTHREAD_COND_INITIALIZER;
static void* waiter(void*x){pthread_setname_np("bb-sample-wait");pthread_mutex_lock(&m);for(;;)pthread_cond_wait(&c,&m);}
static void* busy(void*x){pthread_setname_np("bb-sample-busy");volatile unsigned long v=1;for(;;)v=v*1664525+1013904223;}
int main(){pthread_t a,b;pthread_create(&a,0,waiter,0);pthread_create(&b,0,busy,0);puts("ready");fflush(stdout);for(;;)sleep(1);}
'''
with tempfile.TemporaryDirectory(prefix='bb-stack-owned-')as d:
 p=Path(d);c=p/'test.c';c.write_text(source);exe=p/'test';subprocess.run(['/usr/bin/clang','-O1','-g',str(c),'-o',str(exe)],check=True)
 child=subprocess.Popen([str(exe)],stdout=subprocess.PIPE,text=True);sampler=None
 try:
  assert child.stdout.readline().strip()=='ready';observer=NativeObserver();actual=observer.path(child.pid)
  sampler=CPUStackSample(p,observer,duration=1,interval_ms=10)
  try:sampler.start(child.pid,actual,{})
  except RuntimeError:pass
  else:raise AssertionError('unowned process admitted')
  sampler.start(child.pid,actual,{child.pid:actual})
  deadline=time.monotonic()+15
  while not sampler.poll():
   assert time.monotonic()<deadline;time.sleep(.05)
  assert sampler.report['passed'],sampler.report
  text=(p/'cpu-stacks.txt').read_text();assert 'bb-sample-wait'in text and 'bb-sample-busy'in text and 'pthread_cond_wait'in text
  print(json.dumps(dict(passed=True,bytes=sampler.report['bytes'],owned_target=True,native_wait_and_busy_seen=True,game_started=False)))
 finally:
  if sampler:sampler.close()
  child.terminate();child.wait(timeout=3)
