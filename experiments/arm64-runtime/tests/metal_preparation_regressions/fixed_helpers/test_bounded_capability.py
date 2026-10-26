# SPDX-License-Identifier: GPL-2.0-or-later
"""Real CPU-only owned children: stdout flood, deadline, descendants, cancellation.
Explicit source/work roots; no private evidence constants, native GPU or game.
"""
from pathlib import Path
import argparse,ast,os,select,signal,subprocess,sys,tempfile,threading,time,unittest
from unittest.mock import patch
p=argparse.ArgumentParser(add_help=False);p.add_argument('--source-root',type=Path,required=True);p.add_argument('--work-root',type=Path,required=True);a,left=p.parse_known_args();sys.argv=[sys.argv[0],*left]
source=a.source_root/'tools/game_pso_component.py';tree=ast.parse(source.read_text());names={'need','_CapabilityExitWatch','_run_bounded_capability','_signal_capability_group','fixed_helper_capability'};nodes=[n for n in tree.body if isinstance(n,(ast.ClassDef,ast.FunctionDef))and n.name in names];assert len(nodes)==len(names)
ns=dict(Path=Path,os=os,select=select,signal=signal,subprocess=subprocess,tempfile=tempfile,time=time);exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),'exec'),ns)
def live(pid):
 try:os.kill(pid,0)
 except ProcessLookupError:return False
 state=subprocess.run(['/bin/ps','-p',str(pid),'-o','stat='],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True).stdout.strip()
 return bool(state)and not state.startswith('Z')
class RealChildren(unittest.TestCase):
 def setUp(self):self.temp=tempfile.TemporaryDirectory(dir=a.work_root);self.root=Path(self.temp.name).resolve();(self.root/'state').mkdir();self.ids=[]
 def tearDown(self):
  for pid in self.ids:self.assertFalse(live(pid),'owned author child must not remain live')
  self.temp.cleanup()
 def worker(self,name,body):
  p=self.root/name;p.write_text('#!'+sys.executable+'\nimport os,sys,time,signal,subprocess\nfrom pathlib import Path\nPath(__file__).with_suffix(".pid").write_text(str(os.getpid()))\n'+body+'\n');p.chmod(0o700);return p
 def collect(self,worker):
  for p in self.root.glob('*.pid'):
   pid=int(p.read_text());self.ids.append(pid)
  leader=int(worker.with_suffix('.pid').read_text())
  with self.assertRaises(ChildProcessError):os.waitpid(leader,os.WNOHANG)
 def run_query(self,worker,timeout=.5):return ns['_run_bounded_capability']([str(worker),'--fixed-helper-seed-schema'],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=timeout,env={'PATH':'/usr/bin:/bin','HOME':str(self.root),'TMPDIR':str(self.root)})
 def test_default_new2_actual_process(self):
  w=self.worker('new','print("FIXED_HELPER_CAPABILITY 2",flush=True)');self.assertEqual(ns['fixed_helper_capability'](w,self.root),2);self.collect(w)
 def test_default_old64_actual_process(self):
  w=self.worker('old','sys.exit(64)');self.assertEqual(ns['fixed_helper_capability'](w,self.root),0);self.collect(w)
 def test_exact_limit_is_captured(self):
  w=self.worker('limit','os.write(1,b"x"*4096)');r=self.run_query(w);self.assertEqual(r.returncode,0);self.assertEqual(len(r.stdout),4096);self.collect(w)
 def test_actual_continuous_flood_stopped_early(self):
  w=self.worker('flood','while True: os.write(1,b"x"*1024)');start=time.monotonic()
  with self.assertRaises(ValueError):self.run_query(w,2)
  self.assertLess(time.monotonic()-start,1.5);self.collect(w)
 def test_old64_cannot_bypass_stream_limit(self):
  w=self.worker('old-flood','os.write(1,b"x"*4097)\nsys.exit(64)')
  with self.assertRaises(ValueError):ns['fixed_helper_capability'](w,self.root)
  self.collect(w)
 def test_timeout_kills_term_ignoring_leader(self):
  w=self.worker('timeout','signal.signal(signal.SIGTERM,signal.SIG_IGN)\nPath(__file__).with_suffix(".ready").write_text("armed")\ntime.sleep(60)');start=time.monotonic()
  with self.assertRaises(subprocess.TimeoutExpired):self.run_query(w,.8)
  self.assertLess(time.monotonic()-start,1.5);self.collect(w)
 def fork_worker(self,name,tail):
  child='import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)'
  return self.worker(name,'child=subprocess.Popen([sys.executable,"-c",'+repr(child)+'])\nPath(__file__).with_suffix(".child.pid").write_text(str(child.pid))\n'+tail)
 def test_timeout_cleans_descendant_and_preserves_foreign_session(self):
  sentinel=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],start_new_session=True)
  try:
   w=self.fork_worker('tree-timeout','signal.signal(signal.SIGTERM,signal.SIG_IGN)\ntime.sleep(60)')
   with self.assertRaises(subprocess.TimeoutExpired):self.run_query(w,.3)
   self.collect(w);self.assertIsNone(sentinel.poll());self.assertTrue(live(sentinel.pid))
  finally:sentinel.terminate();sentinel.wait(timeout=2)
 def test_flood_cleans_descendant(self):
  w=self.fork_worker('tree-flood','while True: os.write(1,b"x"*1024)')
  with self.assertRaises(ValueError):self.run_query(w,2)
  self.collect(w)
 def test_sigint_exception_cleans_owned_group(self):
  w=self.fork_worker('tree-cancel','signal.signal(signal.SIGTERM,signal.SIG_IGN)\nPath(__file__).with_suffix(".ready").write_text("armed")\ntime.sleep(60)')
  def cancel_when_ready():
   until=time.monotonic()+2
   while not w.with_suffix('.ready').exists()and time.monotonic()<until:time.sleep(.01)
   if w.with_suffix('.ready').exists():os.kill(os.getpid(),signal.SIGINT)
  timer=threading.Thread(target=cancel_when_ready);timer.start()
  try:
   with self.assertRaises(KeyboardInterrupt):self.run_query(w,2)
  finally:timer.join()
  self.collect(w)
 def test_fast_exit_registration_race(self):
  for _ in range(20):
   r=ns['_run_bounded_capability'](['/usr/bin/true'],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=.3,env={'PATH':'/usr/bin:/bin'});self.assertEqual(r.returncode,0);self.assertEqual(r.stdout,b'')
 def test_wrong_pipe_and_timeout_rejected_before_spawn(self):
  for duration in [0,-1,11]:
   with self.assertRaises(ValueError):ns['_run_bounded_capability'](['/nonexistent'],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=duration,env={})
 def test_cleanup_sigkill_even_if_grace_interrupted(self):
  w=self.worker('grace-cancel','signal.signal(signal.SIGTERM,signal.SIG_IGN)\nprint("FIXED_HELPER_CAPABILITY 2",flush=True)')
  original=ns['time'].sleep
  def interrupt_grace(seconds):
   if seconds==.05:raise KeyboardInterrupt('author cleanup grace interruption')
   return original(seconds)
  with patch.object(ns['time'],'sleep',interrupt_grace):
   with self.assertRaises(KeyboardInterrupt):self.run_query(w)
  self.collect(w)
if __name__=='__main__':unittest.main(verbosity=2)
