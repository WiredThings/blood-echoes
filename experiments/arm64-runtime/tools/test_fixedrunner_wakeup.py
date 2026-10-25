from pathlib import Path
import os,signal,subprocess,sys,tempfile,time,unittest
from unittest.mock import patch
import local_metal_builder as new
class Guard:
 def __init__(self,stop=None):self.calls=[];self.stop=stop
 def check(self,**kw):
  self.calls.append(time.monotonic())
  if self.stop and len(self.calls)>=self.stop:raise new.Stopped('author protection')
class Controls(unittest.TestCase):
 def setUp(self):self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve()
 def tearDown(self):self.temp.cleanup()
 def test_real_short_command_preserves_final_guard(self):
  guard=Guard();out=new.FixedRunner(self.root).run([sys.executable,'-I','-S','-c','print("OK")'],2,guard)
  self.assertEqual(out,b'OK\n');self.assertGreaterEqual(len(guard.calls),2)
 def test_real_timeout_reaps_only_owned_child(self):
  captured=[];original=new.subprocess.Popen
  def launch(*a,**kw):p=original(*a,**kw);captured.append(p);return p
  start=time.monotonic()
  with patch.object(new.subprocess,'Popen',side_effect=launch):
   with self.assertRaises(new.Stopped):new.FixedRunner(self.root).run([sys.executable,'-I','-S','-c','import time;time.sleep(2)'],.03,Guard())
  self.assertLess(time.monotonic()-start,2);self.assertIsNotNone(captured[0].poll())
 def test_real_periodic_protection_and_final_guard_reap(self):
  captured=[];original=new.subprocess.Popen;guard=Guard(stop=3)
  def launch(*a,**kw):p=original(*a,**kw);captured.append(p);return p
  with patch.object(new.subprocess,'Popen',side_effect=launch):
   with self.assertRaisesRegex(new.Stopped,'protection'):new.FixedRunner(self.root).run([sys.executable,'-I','-S','-c','import time;time.sleep(2)'],2,guard)
  self.assertGreaterEqual(len(guard.calls),3);self.assertIsNotNone(captured[0].poll())
 def test_real_large_stdout_is_not_success(self):
  with self.assertRaises((new.Refused,new.Stopped)):
   self.large_stdout()
 def large_stdout(self):
  with patch.object(new,'LOG_MAX',1024):return new.FixedRunner(self.root).run([sys.executable,'-I','-S','-c','import sys;sys.stdout.write("x"*2000)'],2,Guard())
 def test_real_quick_exit_final_output_bounds_checked(self):
  output=self.root/'oversized.air'
  with self.assertRaisesRegex(new.Stopped,'output size/type'):
   new.FixedRunner(self.root).run([sys.executable,'-I','-S','-c','from pathlib import Path;import sys;Path(sys.argv[1]).write_bytes(b"123456789")',str(output)],2,Guard(),outputs=((output,8),))
 def test_final_protection_after_quick_exit_not_skipped(self):
  class P:
   pid=555001;returncode=0
   def poll(self):return 0
  guard=Guard(stop=2)
  with patch.object(new.subprocess,'Popen',return_value=P()):
   with self.assertRaisesRegex(new.Stopped,'protection'):new.FixedRunner(self.root).run(['/author/fake'],2,guard)
 def test_fake_still_running_wait_preserves_timeout_and_escalation(self):
  waits=[];signals=[]
  class P:
   pid=555002;returncode=None
   def poll(self):return self.returncode
   def wait(self,timeout):
    waits.append(timeout)
    if signals and signals[-1]==signal.SIGKILL:self.returncode=-9;return -9
    raise subprocess.TimeoutExpired('author fake',timeout)
  proc=P()
  with patch.object(new.subprocess,'Popen',return_value=proc),patch.object(new.os,'killpg',side_effect=lambda pid,sig:signals.append(sig)),patch.object(new.time,'monotonic',side_effect=[0,0,1]):
   # Guard's own real clock is replaced by this patch too; avoid extra reads.
   class G:
    def check(self,**kw):pass
   with self.assertRaises(new.Stopped):new.FixedRunner(self.root).run(['/author/fake'],.5,G())
  self.assertEqual(waits,[.1,.75,.75]);self.assertEqual(signals,[signal.SIGTERM,signal.SIGKILL]);self.assertEqual(proc.returncode,-9)
 def test_final_hardlinked_output_refused_without_io_through_link(self):
  output=self.root/'output';output.write_bytes(b'x');os.link(output,self.root/'external')
  class P:
   pid=555003;returncode=0
   def poll(self):return 0
  with patch.object(new.subprocess,'Popen',return_value=P()):
   with self.assertRaisesRegex(new.Stopped,'output size/type'):new.FixedRunner(self.root).run(['/author/fake'],2,Guard(),outputs=((output,8),))

if __name__=='__main__':unittest.main()
