from pathlib import Path
import json,os,subprocess,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from qa_present_diagnostics import environment,arm,parse,diagnostic_run
from run_qa_launch_benchmark import benchmark_changes,execution_environment
D=Path(__file__).resolve().parents[1]
class PresentDiagnostics(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory();cls.binary=Path(cls.tmp.name)/'probe'
  subprocess.run(['/usr/bin/c++','-std=c++20','-O2','-UNDEBUG','-pthread','-I',str(D/'src'),str(D/'tests/test_present_latency.cpp'),'-o',str(cls.binary)],check=True)
 @classmethod
 def tearDownClass(cls):cls.tmp.cleanup()
 def run_probe(self,mode,env):return subprocess.run([str(self.binary),mode],env=env,capture_output=True,text=True,check=True).stderr
 def test_default_off_and_scene_gate(self):
  clean={k:v for k,v in os.environ.items()if not k.startswith('BB_PRESENT_LATENCY_')}
  self.assertEqual(self.run_probe('phase',clean),'')
  with tempfile.TemporaryDirectory()as d:
   e=environment(clean,d);self.assertEqual(Path(e['BB_PRESENT_LATENCY_GATE']).read_bytes(),bytes(4))
   closed=parse(self.run_probe('phase',e).splitlines());self.assertTrue(closed['complete']);self.assertFalse(closed['events'])
   arm(e);active=parse(self.run_probe('phase',e).splitlines());self.assertTrue(active['complete']);self.assertEqual(len(active['events']),1)
   row=active['events'][0];self.assertEqual(row['phase'],'next-drawable');self.assertEqual(row['token'],42);self.assertGreaterEqual(row['wall_ns'],50000000)
 def test_limit_overflow_is_not_complete(self):
  with tempfile.TemporaryDirectory()as d:
   e=environment({},d);arm(e);r=parse(self.run_probe('budget',e).splitlines());self.assertEqual(len(r['events']),512);self.assertTrue(r['overflow']);self.assertFalse(r['complete'])
 def test_missing_or_linked_gate_refused(self):
  r=parse(self.run_probe('phase',{'BB_PRESENT_LATENCY_DIAGNOSTIC':'1'}).splitlines());self.assertFalse(r['complete']);self.assertTrue(r['errors'])
  with tempfile.TemporaryDirectory()as d:
   e=environment({},d);p=Path(e['BB_PRESENT_LATENCY_GATE']);target=p.with_name('target');p.rename(target);p.symlink_to(target)
   with self.assertRaises(OSError):arm(e)
   self.assertFalse(parse(self.run_probe('phase',e).splitlines())['complete'])
 def test_explicit_override_only_and_inherited_refused(self):
  with tempfile.TemporaryDirectory()as d:
   source={'environment':{'BB_AUDIO':'none'}};case={'allowed_changes':[]};e=environment(source['environment'],d)
   with self.assertRaisesRegex(ValueError,'unapproved'):benchmark_changes(source,case,e)
   changes,allowed=benchmark_changes(source,case,e,present_diagnostic=True);self.assertEqual(len(changes),2)
   with self.assertRaises(ValueError):environment(e,d)
   with self.assertRaisesRegex(ValueError,'diagnostic'):execution_environment({'stage':Path('/author/package')},{'data_root':Path('/author/data'),'environment':e})
 def test_invalid_intervals_and_missing_origin_are_not_acceptance(self):
  row=dict(phase='next-drawable',pid=1,token=2,start_ns=1,end_ns=60000001,wall_ns=60000000,cpu_ns=-1)
  self.assertFalse(parse(['PRESENT_PHASE '+json.dumps(row)])['complete'])
  for change in({'wall_ns':1},{'phase':'unknown'},{'token':-1},{'cpu_ns':-2}):
   r=parse(['PRESENT_ORIGIN {"pid":1,"steady_ns":1}','PRESENT_PHASE '+json.dumps(dict(row,**change))]);self.assertFalse(r['complete']);self.assertTrue(r['errors'])
 def test_only_last_run_is_diagnostic(self):
  self.assertEqual([diagnostic_run(True,i,3)for i in range(3)],[False,False,True])
  self.assertFalse(diagnostic_run(False,0,1))
  with self.assertRaises(ValueError):diagnostic_run(True,0,2)
if __name__=='__main__':unittest.main()
