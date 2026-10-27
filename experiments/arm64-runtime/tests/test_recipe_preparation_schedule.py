"""Authored native-event/file proofs; never asserts a real GPU prepared a PSO."""
from pathlib import Path
import copy,hashlib,json,os,sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from recipe_preparation_schedule import Recipes
from game_pso_queue import run_manifest,digest
from test_game_pso_queue import unit,NativeAuthor
from local_metal_builder import RECIPE,derive_key,expected_receipt,canonical
from prepare_game_psos import preparation_ready

class RecipeProof(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve()
  self.gpu=self.root/'gpu';self.cache=self.gpu/'cache/source';self.artifact=self.gpu/'cache/CUSA03173/metal';self.progress=self.gpu/'progress'
  self.cohort='a'*64;self.build='b'*64;self.local=self.gpu/'local-metal';self.requests=self.local/self.cohort/'requests';self.libs=self.local/self.cohort/'artifacts'/self.build
  for p in(self.cache,self.artifact,self.progress,self.requests,self.libs):p.mkdir(parents=True)
  self.env=dict(BB_MTL_LOCAL_AOT_MODE='load',BB_MTL_LOCAL_AOT_ROOT=str(self.local),BB_MTL_LOCAL_AOT_COHORT=self.cohort,BB_MTL_LOCAL_AOT_BUILD_ID=self.build,BB_MTL_LOCAL_AOT_ENVIRONMENT_ID='c'*64)
  self.u=unit(1);self.u['namespace']['cache']=str(self.cache);self.u['source_provenance']['enabled']=False;self.u['profile']['compiler_compatibility_proof']=False;self.u['execution_policy']['actual_final_read_admission']=dict(allowed=False)
  refs=[self.u['profile']['reference'],self.u['key']['reference'],self.u['stages'][0]['metadata'],self.u['stages'][0]['spirv']]
  for i,ref in enumerate(refs):
   raw=('authored frozen reference '+str(i)).encode();p=self.cache/ref['name'];p.write_bytes(raw);ref.update(path=str(p),bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
  self.u['execution_policy']['candidate_preparation']['frozen_input_set_identity']=digest(refs)
  source=b'kernel void main0() {}';request=dict(schema=1,cohort=self.cohort,source_sha256=hashlib.sha256(source).hexdigest(),entry='main0',language='metal3.1',math_mode='safe',math_fp32='fast',recipe=RECIPE);request['key']=derive_key(request);self.key=request['key']
  (self.requests/(self.key+'.metal')).write_bytes(source);(self.requests/(self.key+'.json')).write_bytes(canonical(request))
  library=b'authored not-a-real-metallib';(self.libs/(self.key+'.metallib')).write_bytes(library);(self.libs/(self.key+'.json')).write_bytes(canonical(expected_receipt(request,self.build,library)));(self.libs/'build.json').write_text('{"schema":2,"authored":true}')
  native=NativeAuthor();native.artifacts.add(self.u['recipe_blob_identity']);self.row=native.execute(self.u,'prepare',False,1)
  event=self.row['artifact_events'][0];self.archive_key=event['key'];artifact_root=self.artifact/('native-pipeline-artifacts-v1-'+'d'*64);artifact_root.mkdir();name=self.archive_key+'-author.binaryarchive';data=b'authored not-a-real-pipeline-archive'
  self.archive=artifact_root/name;self.archive.write_bytes(data);event['archive_sha256']=hashlib.sha256(data).hexdigest()
  (artifact_root/(self.archive_key+'.json')).write_text(json.dumps(dict(schema=1,recipe_key=self.archive_key,preparation='d'*64,archive=name,archive_bytes=len(data),archive_sha256=event['archive_sha256'])))
  self.context=dict(worker_sha='e'*64,compile_options='f'*64);self.code=dict(protocol='1'*64);self.r=self.make();self.addCleanup(self.r.close)
 def make(self,**changes):
  args=dict(context=self.context,environment=self.env,gpu=self.gpu,artifact=self.artifact,progress=self.progress,code_identity=self.code,check=lambda:None);args.update(changes);return Recipes(**args)
 def seed(self):
  self.r.observe(self.u,self.row,[self.key]);self.assertIsNone(self.r.reuse(self.u));self.assertTrue(self.r.commit(self.u))
 def test_pending_native_result_is_not_ready_until_real_environment_commit(self):
  self.seed();hit=self.r.reuse(self.u);self.assertEqual(hit['status'],'required-recipe-schedule-reused');self.assertFalse(hit['native_worker_invoked']);self.assertFalse(hit['runtime_artifact_authority'])
 def test_new_unrelated_products_do_not_invalidate_current_recipe(self):
  self.seed();(self.cache/'new.spv').write_bytes(b'unrelated');(self.libs/'unrelated.metallib').write_bytes(b'unrelated');(self.artifact/'new.archive').write_bytes(b'unrelated')
  self.assertIsNotNone(self.r.reuse(self.u))
 def test_single_damaged_dependency_misses_and_preserves_other_products(self):
  self.seed();old=self.archive.read_bytes();s=self.archive.stat();self.archive.write_bytes(b'X'*len(old));os.utime(self.archive,ns=(s.st_atime_ns,s.st_mtime_ns))
  self.assertIsNone(self.r.reuse(self.u));self.assertEqual(self.archive.read_bytes(),b'X'*len(old));self.archive.write_bytes(old)
  self.assertIsNotNone(self.r.reuse(self.u))
 def test_source_spec_key_build_compiler_and_abi_changes_cannot_reuse(self):
  self.seed();changed=copy.deepcopy(self.u);changed['stages'][0]['serialized_spec_sha256']='2'*64;self.assertIsNone(self.r.reuse(changed))
  changed=copy.deepcopy(self.u);changed['recipe_blob_identity']='3'*64;self.assertIsNone(self.r.reuse(changed))
  for args in [dict(context={**self.context,'compile_options':'2'*64}),dict(code_identity=dict(protocol='2'*64)),dict(environment={**self.env,'BB_MTL_LOCAL_AOT_ENVIRONMENT_ID':'2'*64})]:
   other=self.make(**args)
   try:self.assertIsNone(other.reuse(self.u))
   finally:other.close()
 def test_no_local_request_event_or_failed_native_result_cannot_publish(self):
  for keys,row in [([],self.row),([self.key],dict(self.row,exit=65)),([self.key],dict(self.row,worker={**self.row['worker'],'source_fallback':True}))]:
   self.r.observe(self.u,row,keys);self.assertFalse(self.r.commit(self.u));self.assertIsNone(self.r.reuse(self.u))
 def test_failed_environment_candidate_cannot_be_promoted_by_a_later_session(self):
  self.r.observe(self.u,self.row,[self.key]);other=self.make()
  try:self.assertFalse(other.commit(self.u));self.assertIsNone(other.reuse(self.u))
  finally:other.close()
 def test_geometry_and_broken_local_request_use_original_native_path(self):
  geometry=copy.deepcopy(self.u);geometry['owned_geometry_pso']={};self.r.observe(geometry,self.row,[self.key]);self.assertIsNone(self.r.reuse(geometry))
  (self.requests/(self.key+'.json')).write_text('{}');self.r.observe(self.u,self.row,[self.key]);self.assertFalse(self.r.commit(self.u))
 def test_stat_stable_warm_path_does_not_reread_large_dependency_bytes(self):
  self.seed()
  with patch('recipe_preparation_schedule.file_sha',side_effect=AssertionError('warm recipe bytes reread')):self.assertIsNotNone(self.r.reuse(self.u))
 def test_library_changed_after_worker_before_commit_is_not_promoted(self):
  self.r.observe(self.u,self.row,[self.key]);(self.libs/(self.key+'.metallib')).write_bytes(b'changed after native event')
  self.assertFalse(self.r.commit(self.u));self.assertIsNone(self.r.reuse(self.u))
 def test_linked_dependency_is_not_admitted_by_same_contents(self):
  self.seed();outside=self.root/'outside';outside.write_bytes(self.archive.read_bytes());self.archive.unlink();self.archive.symlink_to(outside)
  self.assertIsNone(self.r.reuse(self.u));self.assertEqual(outside.read_bytes(),b'authored not-a-real-pipeline-archive')

class QueueIntegration(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve();self.progress=self.root/'progress';self.progress.mkdir();self.manifest=self.root/'manifest';self.native=NativeAuthor();self.known=set();self.context=dict(compiler='a'*64)
 def write(self,ids):self.manifest.write_text(''.join(json.dumps(unit(i))+'\n'for i in ids))
 def commit(self,u):self.known.add(u['recipe_blob_identity'])
 def reuse(self,u):
  if u['recipe_blob_identity']in self.known:return dict(status='required-recipe-schedule-reused',previous_native_verification=True,native_worker_invoked=False,runtime_artifact_authority=False)
 def run_queue(self,**changes):
  args=dict(progress_root=self.progress,context=self.context,execute=self.native.execute,prepare_phase=self.native.local,check=lambda:None,ready=preparation_ready,reuse_unit=self.reuse,commit_unit=self.commit,clock=self.native.clock,seconds=1000);args.update(changes)
  return run_manifest(self.manifest,**args)
 def test_all_known_launch_has_zero_native_worker_and_library_phase_calls(self):
  self.write([1,2]);first=self.run_queue();self.assertEqual(first['status'],'completed');self.assertEqual(len(self.known),2)
  def forbidden(*args):raise AssertionError('completed batch repeated')
  second=self.run_queue(execute=forbidden,prepare_phase=forbidden);self.assertEqual(second['status'],'completed',second)
  self.assertEqual(second['scheduled_reused'],2);self.assertEqual(second['work_units'],0);self.assertEqual(second['initial_local_calls'],0);self.assertEqual(second['final_local_calls'],0)
  self.assertTrue(second['required_preparation_complete']);self.assertFalse(second['full_manifest_preparation_verified'])
 def test_A_AB_A_only_new_recipe_uses_native_and_restored_old_recipe_is_preserved(self):
  self.write([1]);self.assertEqual(self.run_queue()['status'],'completed');old=dict(self.native.probe_calls)
  self.write([1,2]);second=self.run_queue();self.assertEqual(second['status'],'completed',second);self.assertEqual(second['scheduled_reused'],1)
  self.assertEqual(dict(self.native.probe_calls).get(unit(1)['recipe_blob_identity'],0),old.get(unit(1)['recipe_blob_identity'],0))
  self.write([1]);third=self.run_queue();self.assertEqual(third['scheduled_reused'],1);self.assertEqual(third['work_units'],0)
 def test_failed_phase_environment_does_not_commit_native_candidates(self):
  self.write([1]);original=self.native.local
  def local(phase,guard):
   result=original(phase,guard)
   if phase=='verify':result['status']='changed'
   return result
  result=self.run_queue(prepare_phase=local);self.assertEqual(result['status'],'partial');self.assertFalse(self.known);self.assertFalse(result['required_preparation_complete'])

if __name__=='__main__':unittest.main(verbosity=2)
