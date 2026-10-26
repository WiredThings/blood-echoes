"""F3 state/timeout and supervisor false-positive controls, author data only."""
from pathlib import Path
import sys,unittest,tempfile,os
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from qa_f3_contract import F3Check,NewLogLines
class Controls(unittest.TestCase):
 def step(self,c,t,**kw):return c.step(t,**dict(dict(scene=True,cpus={10},gpus={20},log=''),**kw))
 def saved(self):
  c=F3Check();self.step(c,1);self.assertEqual(self.step(c,11),'quicksave');self.assertEqual(self.step(c,12,log='Quicksave: Quicksaved - checkpoint ready'),'quickload');return c
 def test_complete_only_original_offline_restart_and_automatic_continue(self):
  c=self.saved();self.step(c,13,scene=False,cpus=set(),gpus=set());self.step(c,100,scene=False,cpus={30},gpus={40},log='Runtime: quickload switched to offline');self.assertEqual(c.phase,'resuming')
  self.step(c,101,cpus={30},gpus={40},log='Quicksave: character ready; automatic menu input released');self.assertEqual(c.phase,'confirming');self.step(c,116,cpus={30},gpus={40});self.assertEqual(c.phase,'completed');self.assertEqual(c.replacement_cpu,30)
 def test_no_fake_continue_after_restore(self):
  c=self.saved()
  self.assertIsNone(self.step(c,15,cpus=set(),gpus=set(),scene=False))
  self.assertIsNone(self.step(c,50,cpus={30},gpus={40},scene=False,log='Runtime: quickload switched to offline'))
 def test_old_camera_or_log_cannot_complete_without_new_cpu(self):
  c=self.saved();self.step(c,20,log='Quicksave: character ready; automatic menu input released');self.assertEqual(c.phase,'restarting')
 def test_overlap_rejected(self):
  for args in(dict(cpus={10,30}),dict(gpus={20,40})):
   with self.assertRaisesRegex(RuntimeError,'overlapping'):self.step(F3Check(),1,**args)
 def test_old_backend_must_be_reaped(self):
  c=self.saved()
  with self.assertRaisesRegex(RuntimeError,'old backend'):self.step(c,30,cpus={30},gpus={20},log='Runtime: quickload switched to offline')
 def test_missing_original_offline_status_rejected(self):
  c=self.saved()
  with self.assertRaisesRegex(RuntimeError,'offline supervisor'):self.step(c,30,cpus={30},gpus={40})
 def test_save_timeout_does_not_reload_old_checkpoint(self):
  c=F3Check();self.step(c,1);self.step(c,11)
  with self.assertRaisesRegex(RuntimeError,'checkpoint'):self.step(c,42)
 def test_new_process_without_autocontinue_not_success(self):
  c=self.saved();self.step(c,30,cpus={30},gpus={40},log='Runtime: quickload switched to offline')
  self.step(c,31,cpus={30},gpus={40});self.assertEqual(c.phase,'resuming')
  with self.assertRaisesRegex(RuntimeError,'auto-continue'):self.step(c,211,cpus={30},gpus={40})
 def test_scene_loss_after_restore_rejected(self):
  c=self.saved();self.step(c,30,cpus={30},gpus={40},log='Runtime: quickload switched to offline');self.step(c,31,cpus={30},gpus={40},log='Quicksave: character ready; automatic menu input released')
  with self.assertRaisesRegex(RuntimeError,'remain ready'):self.step(c,32,cpus={30},gpus={40},scene=False)
class LogControls(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)/'console.log';self.path.write_bytes(b'');self.reader=NewLogLines()
 def append(self,raw):
  with self.path.open('ab')as f:f.write(raw)
 def test_old_confirmation_is_not_new_confirmation(self):
  self.append(b'Quicksave: Quicksaved - checkpoint ready\n');old,done=self.reader.read(self.path);self.assertTrue(done)
  c=F3Check();c.step(1,scene=True,cpus={10},gpus={20},log=old)
  self.assertEqual(c.step(11,scene=True,cpus={10},gpus={20},log=self.reader.read(self.path)[0]),'quicksave')
  self.assertIsNone(c.step(12,scene=True,cpus={10},gpus={20},log=self.reader.read(self.path)[0]))
  self.append(b'Quicksave: Quicksaved - checkpoint ready\n')
  self.assertEqual(c.step(13,scene=True,cpus={10},gpus={20},log=self.reader.read(self.path)[0]),'quickload')
 def test_partial_lines_not_reemitted(self):
  self.append(b'line');self.assertEqual(self.reader.read(self.path),('',False));self.append(b' one\nline two\n');self.assertEqual(self.reader.read(self.path),('line one\nline two',True));self.assertEqual(self.reader.read(self.path),('',True))
 def test_replacement_and_truncation_rejected(self):
  self.append(b'original\n');self.reader.read(self.path);self.path.write_bytes(b'')
  with self.assertRaisesRegex(RuntimeError,'truncated'):self.reader.read(self.path)
  self.path.unlink();self.path.write_bytes(b'other contents\n')
  with self.assertRaisesRegex(RuntimeError,'replaced'):self.reader.read(self.path)
 def test_linked_console_rejected(self):
  self.path.unlink();other=self.path.with_name('other');other.write_bytes(b'x');self.path.symlink_to(other)
  with self.assertRaises(OSError):self.reader.read(self.path)
 def test_backlog_is_incremental_and_fixed_size(self):
  self.append((b'x'*100+b'\n')*6000);total=0;reads=0;done=False
  while not done:
   text,done=self.reader.read(self.path);self.assertLessEqual(len(text),256*1024+8192);total+=text.count('\n')+(1 if text else 0);reads+=1
  self.assertEqual(total,6000);self.assertEqual(reads,3)
 def test_unbounded_line_rejected(self):
  self.append(b'x'*8193)
  with self.assertRaisesRegex(RuntimeError,'fixed bound'):self.reader.read(self.path)
if __name__=='__main__':unittest.main()
