"""Real, tiny child-process controls; no game, Metal or existing cache reads.

Set BB_WORKER_WAIT_TEST_ROOT to a fresh Shared evidence directory. State never
falls back into the game, Documents QA or a user's home directory.
"""
from pathlib import Path
import os,signal,subprocess,sys,time,unittest

sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from prepare_game_psos import wait_owned_worker,Deferred,Stopped

class WorkerWait(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  raw=os.environ.get('BB_WORKER_WAIT_TEST_ROOT')
  if not raw:raise unittest.SkipTest('explicit fresh Shared evidence root required')
  cls.root=Path(raw).absolute();shared=Path('/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/evidence')
  if cls.root!=cls.root.resolve(strict=True)or not cls.root.is_relative_to(shared)or cls.root==shared or list(cls.root.iterdir()):raise ValueError('fresh Shared test root required')
 def child(self,code):
  case=self.root/self._testMethodName;case.mkdir(mode=0o700);log=case/'child.log';stream=log.open('w')
  proc=subprocess.Popen([sys.executable,'-I','-c',code],stdout=stream,stderr=subprocess.STDOUT,start_new_session=True,cwd=case,
   env={'PATH':'/usr/bin:/bin','HOME':str(case),'TMPDIR':str(case),'PYTHONDONTWRITEBYTECODE':'1'})
  def cleanup():
   if proc.poll()is None:
    os.killpg(proc.pid,signal.SIGKILL);proc.wait(timeout=2)
   stream.close()
  self.addCleanup(cleanup);return proc,log
 def verify_reaped(self,proc):
  self.assertIsNotNone(proc.returncode)
  with self.assertRaises(ProcessLookupError):os.kill(proc.pid,0)
 def test_already_exited_and_code_preserved(self):
  proc,log=self.child('raise SystemExit(23)');self.assertEqual(proc.wait(timeout=2),23)
  def unused():raise AssertionError('completed child must not enter wait loop')
  wait_owned_worker(proc,log,unused,time.monotonic,time.monotonic(),1);self.assertEqual(proc.returncode,23);self.verify_reaped(proc)
 def test_fast_exit_wakes_without_fixed_sleep(self):
  proc,log=self.child('import time;time.sleep(.005)');seen=[]
  original=proc.wait
  def traced(*args,**kwargs):seen.append(kwargs.get('timeout'));return original(*args,**kwargs)
  proc.wait=traced
  wait_owned_worker(proc,log,lambda:None,time.monotonic,time.monotonic(),2)
  self.assertIn(.1,seen);self.assertEqual(proc.returncode,0);self.verify_reaped(proc)
 def test_slow_exit_retains_periodic_guard(self):
  proc,log=self.child('import time;time.sleep(.23)');checks=[]
  wait_owned_worker(proc,log,lambda:checks.append(time.monotonic()),time.monotonic,time.monotonic(),2)
  self.assertGreaterEqual(len(checks),2);self.assertEqual(proc.returncode,0);self.verify_reaped(proc)
 def test_cancel_preserves_exception_and_reaps_group(self):
  proc,log=self.child('import time;time.sleep(30)')
  def cancelled():raise KeyboardInterrupt('authored cancellation')
  with self.assertRaises(KeyboardInterrupt):wait_owned_worker(proc,log,cancelled,time.monotonic,time.monotonic(),2)
  self.assertEqual(proc.returncode,-signal.SIGTERM);self.verify_reaped(proc)
 def test_original_deadline_not_reset_for_child(self):
  proc,log=self.child('import time;time.sleep(30)')
  with self.assertRaisesRegex(Deferred,'preparation time budget'):wait_owned_worker(proc,log,lambda:None,lambda:10,0,9)
  self.assertEqual(proc.returncode,-signal.SIGTERM);self.verify_reaped(proc)
 def test_log_limit_still_stops_and_cleans(self):
  proc,log=self.child('import time;time.sleep(30)');log.touch()
  with log.open('r+b')as stream:stream.truncate(8*1024*1024+1)
  with self.assertRaisesRegex(Stopped,'bounded worker log budget'):wait_owned_worker(proc,log,lambda:None,time.monotonic,time.monotonic(),2)
  self.assertEqual(proc.returncode,-signal.SIGTERM);self.verify_reaped(proc)

if __name__=='__main__':unittest.main(verbosity=2)
