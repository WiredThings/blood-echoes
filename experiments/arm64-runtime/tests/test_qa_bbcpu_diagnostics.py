from pathlib import Path
import sys,tempfile,unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from qa_bbcpu_diagnostics import diagnostic_environment,CpuSample
from run_qa_launch_benchmark import benchmark_changes,execution_environment

class Diagnostic(unittest.TestCase):
 def test_environment_is_explicit_backend_specific_and_nonmutating(self):
  env={'BB_CPU_BACKEND':'bbcpu','BB_AUDIO':'none'};got=diagnostic_environment(env)
  self.assertNotIn('BB_RECOMP_TARGETS',env);self.assertEqual(got,{**env,'BB_RECOMP_TARGETS':'1'})
  self.assertEqual(diagnostic_environment({**env,'BB_RECOMP_TARGETS':'0'})['BB_RECOMP_TARGETS'],'1')
  for bad in ({},{'BB_CPU_BACKEND':'fex'},{**env,'BB_RECOMP_TARGETS':'invalid'}):
   with self.assertRaises(ValueError):diagnostic_environment(bad)
 def test_normal_comparison_still_refuses_diagnostic_override(self):
  env={'BB_CPU_BACKEND':'bbcpu'};source={'environment':env};case={'allowed_changes':[]};changed=diagnostic_environment(env)
  with self.assertRaisesRegex(ValueError,'unapproved'):benchmark_changes(source,case,changed)
  changes,allowed=benchmark_changes(source,case,changed,bbcpu_diagnostic=True)
  self.assertEqual(set(changes),{'BB_RECOMP_TARGETS'});self.assertIn('BB_RECOMP_TARGETS',allowed)
  with self.assertRaises(ValueError):benchmark_changes(source,case,{'BB_CPU_BACKEND':'fex','BB_RECOMP_TARGETS':'1'},bbcpu_diagnostic=True)
 def test_inherited_counter_cannot_masquerade_as_unprofiled_run(self):
  source={'stage':Path('/author/package')};case={'data_root':Path('/author/data'),'environment':{'BB_AUDIO':'none','BB_RECOMP_TARGETS':'1'}}
  with self.assertRaisesRegex(ValueError,'diagnostic'):execution_environment(source,case)
 def test_waits_for_scene_and_matches_actual_cpu(self):
  with tempfile.TemporaryDirectory()as d,patch('qa_bbcpu_diagnostics.subprocess.Popen')as spawn:
   s=CpuSample(d,'/selected/bbcpu');obs=Mock();obs.path.return_value='/wrong/cpu'
   s.observe([],0,None,obs);s.observe([],15,10,obs);spawn.assert_not_called()
   with self.assertRaises(ValueError):s.observe([(1,0,'/selected/bbcpu')],21,10,obs)
   with self.assertRaises(ValueError):s.observe([(1,0,'/selected/bbcpu'),(2,0,'/selected/bbcpu')],21,10,obs)
   spawn.assert_not_called();self.assertFalse(s.finish()['complete'])
 def test_sample_is_owned_bounded_and_reaped_once(self):
  with tempfile.TemporaryDirectory()as d:
   proc=Mock(returncode=0);proc.poll.return_value=0
   def start(args,**kw):
    self.assertEqual(args[:4],['/usr/bin/sample','12','10','5']);Path(args[-1]).write_text('authored stack fixture\n');return proc
   with patch('qa_bbcpu_diagnostics.subprocess.Popen',side_effect=start)as spawn:
    s=CpuSample(d,'/selected/bbcpu');obs=Mock();obs.path.return_value='/selected/bbcpu'
    rows=[(12,1,'/selected/bbcpu')];s.observe(rows,21,10,obs);s.observe(rows,22,10,obs)
    self.assertEqual(spawn.call_count,1);r=s.finish();self.assertTrue(r['complete']);self.assertFalse(r['performance_sample']);proc.terminate.assert_not_called()
 def test_interrupted_or_failed_sampler_is_not_acceptance(self):
  for code in(None,1):
   with tempfile.TemporaryDirectory()as d:
    proc=Mock(returncode=code);proc.poll.return_value=code
    with patch('qa_bbcpu_diagnostics.subprocess.Popen',return_value=proc):
     s=CpuSample(d,'/selected/bbcpu');obs=Mock();obs.path.return_value='/selected/bbcpu';s.observe([(12,1,'/selected/bbcpu')],21,10,obs)
     self.assertFalse(s.finish()['complete'])
     if code is None:proc.terminate.assert_called_once();proc.wait.assert_called_once()
 def test_existing_output_refused_without_spawning(self):
  with tempfile.TemporaryDirectory()as d,patch('qa_bbcpu_diagnostics.subprocess.Popen')as spawn:
   (Path(d)/'cpu-stacks.txt').write_text('keep');s=CpuSample(d,'/selected/bbcpu');obs=Mock();obs.path.return_value='/selected/bbcpu'
   with self.assertRaises(ValueError):s.observe([(12,1,'/selected/bbcpu')],21,10,obs)
   spawn.assert_not_called();self.assertEqual((Path(d)/'cpu-stacks.txt').read_text(),'keep')
 def test_two_fex_roles_select_only_the_live_admitted_cpu(self):
  with tempfile.TemporaryDirectory()as d:
   proc=Mock(returncode=0);proc.poll.return_value=0
   def start(args,**kw):
    self.assertEqual(args[1],'13');Path(args[-1]).write_text('authored stack fixture\n');return proc
   with patch('qa_bbcpu_diagnostics.subprocess.Popen',side_effect=start):
    s=CpuSample(d,{'/package/top','/package/nested'});obs=Mock();obs.path.return_value='/package/nested'
    s.observe([(12,1,'/other/top'),(13,1,'/package/nested')],21,10,obs)
    self.assertEqual(s.finish()['executable'],'/package/nested')
  with self.assertRaises(ValueError):CpuSample('/private/tmp',set())
if __name__=='__main__':unittest.main()
