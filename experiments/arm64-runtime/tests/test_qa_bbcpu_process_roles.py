"""Selected CPU role, not a basename or a different backend, drives F3 state."""
from pathlib import Path
import sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from run_qa_launch_benchmark import cpu_paths,CPU_ROLES,BBCPU_CPU_ROLE

class Roles(unittest.TestCase):
 def setUp(self):
  self.runtime={name:Path('/author/package')/name for name in(*CPU_ROLES,BBCPU_CPU_ROLE,'bin/bbcpu-code-preparer')}
  self.conditions=dict(runtime=self.runtime,environment={'BB_CPU_BACKEND':'bbcpu'})
  self.identity=dict(cpu_backend='bbcpu',cpu=dict(target=str(self.runtime[BBCPU_CPU_ROLE])))
 def test_bbcpu_recognized_without_fex_or_preparer(self):
  self.assertEqual(cpu_paths(self.conditions,admitted=self.identity),{str(self.runtime[BBCPU_CPU_ROLE])})
 def test_fex_and_legacy_roles_preserved(self):
  for env in({},dict(BB_CPU_BACKEND='fex')):
   self.assertEqual(cpu_paths(dict(self.conditions,environment=env)),{str(self.runtime[n])for n in CPU_ROLES})
 def test_missing_or_wrong_admission_rejected_before_game(self):
  for identity in(None,{},dict(cpu_backend='fex',cpu=self.identity['cpu']),dict(cpu_backend='bbcpu',cpu=dict(target=str(self.runtime[CPU_ROLES[0]]))),dict(cpu_backend='bbcpu',cpu=dict(target=str(self.runtime['bin/bbcpu-code-preparer'])))):
   with self.assertRaisesRegex(ValueError,'actual admitted CPU'):cpu_paths(self.conditions,admitted=identity)
 def test_unregistered_or_unknown_backend_rejected(self):
  del self.runtime[BBCPU_CPU_ROLE]
  with self.assertRaises(ValueError):cpu_paths(self.conditions,admitted=self.identity)
  self.conditions['environment']['BB_CPU_BACKEND']='other'
  with self.assertRaises(ValueError):cpu_paths(self.conditions,admitted=self.identity)
if __name__=='__main__':unittest.main()
