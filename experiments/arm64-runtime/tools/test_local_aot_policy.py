"""Actual startup policy dispatch and cumulative-memory contract, authored files only."""
from pathlib import Path
import json,os,tempfile,unittest
from unittest.mock import Mock
from local_aot_policy import prepare,gameplay_guard,P
class Policy(unittest.TestCase):
 def setUp(self):
  self.gpu=Path(tempfile.mkdtemp(prefix='local-aot-policy-',dir=P/'state'));self.cohort='a'*64
 def call(self,**changes):
  values=dict(mode='record',gpu_root=self.gpu,cohort=self.cohort,engine='arm64',graphics=True);values.update(changes);return prepare(**values)
 def test_off_no_path_or_builder(self):
  builder=Mock(side_effect=AssertionError('must not build'));u,r=self.call(mode='off',gpu_root='/nonexistent',engine='rosetta',graphics=False,builder=builder);self.assertEqual(u,{});self.assertEqual(r['status'],'off');builder.assert_not_called()
 def test_record_and_first_load_have_no_compiler(self):
  builder=Mock(side_effect=AssertionError('no complete requests'));u,r=self.call(mode='load',builder=builder);self.assertEqual(u['BB_MTL_LOCAL_AOT_MODE'],'record');self.assertEqual(r['requested'],'load');builder.assert_not_called()
 def requests(self):
  d=self.gpu/'local-metal'/self.cohort/'requests';d.mkdir(parents=True);return d
 def test_prepared_uses_builder_id_and_restores_parent_environment(self):
  self.requests();before=os.environ.get('BB_GPU_USER_DIR');seen=[]
  def builder(root,cohort,enabled):
   seen.append(os.environ['BB_GPU_USER_DIR']);return dict(status='passed',requests=4,built=0,skipped=4,build_id='b'*64)
  u,r=self.call(mode='load',builder=builder);self.assertEqual(seen,[str(self.gpu)]);self.assertEqual(os.environ.get('BB_GPU_USER_DIR'),before);self.assertEqual(u['BB_MTL_LOCAL_AOT_BUILD_ID'],'b'*64);self.assertEqual(r['status'],'prepared');self.assertEqual(r['skipped'],4)
 def test_failed_or_stopped_preparation_never_launches_load(self):
  self.requests()
  for status in ['stopped','refused','failed']:
   u,r=self.call(mode='load',builder=lambda *a,**k:dict(status=status,error='authored failure'));self.assertEqual(u,{});self.assertEqual(r['status'],status)
 def test_invalid_identity_and_wrong_engine_refuse(self):
  for kwargs in [dict(engine='rosetta'),dict(graphics=False),dict(cohort='not-hex'),dict(gpu_root=Path('/Applications'))]:
   with self.assertRaises(ValueError):self.call(**kwargs)
 def test_burst_history_reset_does_not_expand_total_budget(self):
  before=dict(pressure=1,locked=False,swap_mib=1000.);after=dict(before,swap_mib=2500.)
  p=gameplay_guard(before,after);self.assertIsNone(p.observe(.1,after));self.assertEqual(p.observe(.2,dict(after,swap_mib=3100.)),'unexpected >2GiB swap growth')
  p=gameplay_guard(after,before);self.assertEqual(p.baseline,1000.)
if __name__=='__main__':unittest.main(verbosity=2)
