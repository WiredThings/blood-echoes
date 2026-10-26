"""Distinct CPU processes, author disk state only; no native/GPU/game worker."""
from pathlib import Path
import collections,json,os,subprocess,sys,tempfile,unittest
E=Path(__file__).resolve().parent
sys.path.insert(0,str(E))
from test_game_pso_queue import unit
class Controls(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve();(self.root/'progress').mkdir();(self.root/'manifest.jsonl').write_text(''.join(json.dumps(unit(i))+'\n'for i in range(20)))
 def tearDown(self):self.temp.cleanup()
 def call(self,n=None):
  done=subprocess.run([sys.executable,'-B',str(E/'game_pso_resume_author.py'),str(self.root),'none'if n is None else str(n),'20'],capture_output=True,timeout=5,env={k:v for k,v in os.environ.items()if k not in('PYTHONPATH','PYTHONHOME')});self.assertEqual(done.returncode,0,done.stderr.decode());return json.loads(done.stdout)
 def test_source_collect_resumes_in_new_process_and_warm_does_not_record(self):
  a=self.call(7);self.assertEqual((a['completed'],a['recorded'],a['cycle_collected']),(0,7,7));b=self.call();self.assertEqual((b['completed'],b['recorded']),(20,13));self.assertEqual(a['cycle_id'],b['cycle_id'])
  rows=[json.loads(x)for x in(self.root/'record-log.jsonl').read_text().splitlines()];self.assertEqual((len(rows),len(set(rows))),(20,20));c=self.call();self.assertTrue(c['warm_fast_path']);self.assertEqual((c['completed'],c['recorded']),(20,0))
 def test_final_native_progress_cross_process_not_authority(self):
  a=self.call(20);self.assertEqual(a['cycle_stage'],'build');b=self.call(5);self.assertEqual((b['completed'],b['provisional_native']),(0,5));c=self.call();self.assertEqual(c['completed'],20)
  rows=[json.loads(x)for x in(self.root/'compile-log.jsonl').read_text().splitlines()];self.assertEqual((len(rows),len(set(rows))),(20,20))
 def test_deleted_request_after_collection_cannot_be_false_completion(self):
  a=self.call(20);self.assertEqual(a['cycle_stage'],'build');next((self.root/'requests').iterdir()).unlink();b=self.call();self.assertEqual(b['status'],'partial');self.assertLess(b['completed'],20);self.assertFalse(b['full_manifest_preparation_verified']);self.assertGreater(b['source_late_deferred'],0)
if __name__=='__main__':unittest.main(verbosity=2)
