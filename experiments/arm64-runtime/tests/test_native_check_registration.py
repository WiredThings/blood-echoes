"""Native-check receipt policy fixtures, never native/signature acceptance."""
from pathlib import Path
from types import SimpleNamespace
import copy,hashlib,json,os,sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import launcher_runtime_profile as p
import code_registration as r
import startup_change_journal as j

class NativeRegistration(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve();self.app=self.root/'Test.app';self.stage=self.app/'Contents/Resources/bbport';self.stage.mkdir(parents=True);self.work=self.root/'work';self.work.mkdir(mode=0o700)
  self.target=self.stage/'bin/worker';self.lib=self.stage/'bin/libbbgpu.dylib';self.target.parent.mkdir()
  self.target.write_bytes(b'authored worker bytes');self.lib.write_bytes(b'authored library bytes')
  self.roles={str(f.relative_to(self.stage)):hashlib.sha256(f.read_bytes()).hexdigest()for f in(self.target,self.lib)}
  def code(path):
   path=Path(path)
   if not path.is_dir():r.code_stamp(path)
   if not path.is_relative_to(self.stage):raise ValueError('authored code root')
   return path
  self.profile=SimpleNamespace(code=code,work_root=self.work,bundle=SimpleNamespace(stage=self.stage,app=self.app,native=dict(frozen_runtime=self.roles)))
  self.proof=dict(target=str(self.target),target_sha256=self.roles['bin/worker'],library=str(self.lib),library_sha256=self.roles['bin/libbbgpu.dylib'],load_edge_verified=True,strict_signatures=True,architecture='arm64',declared_gpu_load_edges=['@loader_path/libbbgpu.dylib'],declared_rpaths=[])
  self.calls=0;self.q=patch.object(j,'query',return_value=dict(schema=1,complete=True,code_roots=True));self.u=patch.object(j,'unchanged',return_value=True);self.q.start();self.u.start()
 def tearDown(self):self.u.stop();self.q.stop();self.tmp.cleanup()
 def resolve(self,*args,**kw):self.calls+=1;return copy.deepcopy(self.proof)
 def run_case(self):return r.native_gpu_identity(self.target,self.stage,self.profile,resolver=self.resolve)
 def test_actual_first_result_then_no_external_verifier(self):
  a=self.run_case();b=self.run_case();self.assertEqual(a,b);self.assertEqual(self.calls,1)
 def test_history_unavailable_runs_actual_verifier(self):
  self.run_case()
  with patch.object(j,'unchanged',return_value=False):self.run_case()
  self.assertEqual(self.calls,2)
 def test_changed_library_not_blessed_by_cached_result(self):
  self.run_case();self.lib.write_bytes(b'changed after validation')
  with self.assertRaises(ValueError):self.run_case()
 def test_role_and_bounds_are_not_supplied_by_receipt(self):
  self.run_case();self.roles.pop('bin/worker')
  with self.assertRaises(ValueError):self.run_case()
 def test_valid_checksum_cannot_hide_wrong_library_or_load_edge(self):
  self.run_case();path=next((self.work/'native-code-checks').glob('*.json'));doc=json.loads(path.read_text());doc['body']['result']['declared_gpu_load_edges']=['@loader_path/missing.dylib'];doc['sha256']=r.digest(doc['body']);path.write_text(json.dumps(doc));got=self.run_case();self.assertEqual(self.calls,2);self.assertEqual(got['declared_gpu_load_edges'],self.proof['declared_gpu_load_edges'])
 def test_symlink_dependency_refused_even_with_same_bytes(self):
  self.run_case();original=self.lib.with_name('saved');self.lib.rename(original);self.lib.symlink_to(original)
  with self.assertRaises(ValueError):self.run_case()
 def test_incomplete_signature_result_cannot_publish(self):
  self.proof.pop('strict_signatures')
  with self.assertRaises(ValueError):self.run_case()
  self.assertEqual(list((self.work/'native-code-checks').glob('*.json')),[])

if __name__=='__main__':unittest.main()
