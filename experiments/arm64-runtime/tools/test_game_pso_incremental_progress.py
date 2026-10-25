"""Small CPU authors only. No native/Metal/game execution or production input writes."""
from pathlib import Path
import copy,hashlib,json,sys,tempfile,unittest
from unittest.mock import patch
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import game_pso_queue as q
import game_pso_progress_identity as ident
import game_pso_candidates as candidates
import prepare_game_psos as runner
H=lambda b:hashlib.sha256(b).hexdigest()
class Controls(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve();self.cache=self.root/'cache';self.cache.mkdir();self.progress=self.root/'progress';self.progress.mkdir()
  self.context={'worker_sha256':'1'*64,'library_sha256':'2'*64,'layout_sha256':'3'*64,'cohort':'4'*64,'options_identity':'5'*64,'artifact_cache_dir':'/author/artifact','runner_sha256':'6'*64}
  self.abi='a'*64;self.profile='b'*64;self.count=0
 def tearDown(self):self.tmp.cleanup()
 def ref(self,name,data):
  p=self.cache/name;p.write_bytes(data);return dict(path=str(p),name=name,bytes=len(data),sha256=H(data))
 def unit(self,n):
  pref=self.ref('profile.bin',b'author-profile');k=self.ref(f'{n}.key',f'key{n}'.encode());spec=f'spec{n}'.encode();m=self.ref(f'{n}.meta',b'x'*24+spec+b'FORBIDDEN-INFO-SRT-WALKER');s=self.ref(f'{n}.spv',b'spv'+str(n).encode())
  stage=dict(logical_stage='vertex',hardware_stage='vertex',program_hash=f'{n:016x}',cache_local_permutation=n,permutation_hash=f'{n+100:016x}',metadata=m,spirv=s,serialized_spec_sha256=H(spec))
  refs=[pref,k,m,s]
  return dict(schema=2,record_type='offline-pso-input',namespace=dict(cache=str(self.cache),cache_id=ident.cache_scope(self.cache,self.profile,self.abi),scope_version=2,serialization_abi_sha256=self.abi),recipe_blob_identity=H(str(n).encode()),profile=dict(reference=pref,semantic_sha256=self.profile,compiler_compatibility_proof=False),key=dict(reference=k,decoded=dict(kind='graphics')),stages=[stage],classification=dict(family='author'),source_provenance=dict(status='unknown',enabled=False),execution_policy=dict(runtime_cache_import_allowed=False,candidate_preparation=dict(policy_eligible=True,frozen_input_set_identity=q.digest(refs)),actual_final_read_admission=dict(allowed=False)))
 def manifest(self,units):
  self.count+=1;p=self.root/f'{self.count}.jsonl';p.write_bytes(b''.join(q.canonical(u)+b'\n'for u in units));return p
 def queue(self,units,context=None,check=lambda:None):return q.Queue(self.manifest(units),self.progress,context or self.context,check)
 def seed(self,units):
  x=self.queue(units);x.index()
  for item in x.page('seed',64):x.mark(item,'seed',ready=True,recorded=True)
  folder=x.folder;job=x.job;x.close();return folder,job
 def test_added_recipe_preserves_exact_ready_hint_and_job(self):
  a,b,c=[self.unit(i)for i in(1,2,3)];folder,job=self.seed([a,b]);x=self.queue([a,b,c]);x.index();rows=x.page('new',64)
  self.assertEqual((x.folder,x.job),(folder,job));self.assertEqual(x.hint_count(),2);self.assertEqual(x.total(),3);self.assertEqual(sum(r['ready_hint']for r in rows),2);x.close()
 def test_removed_inactive_reappears_without_losing_hint(self):
  a,b=self.unit(1),self.unit(2);self.seed([a,b]);x=self.queue([a]);x.index();self.assertEqual(x.total(),1);self.assertEqual(x._sql('SELECT count(*)FROM units').fetchone()[0],2);x.close()
  x=self.queue([a,b]);x.index();self.assertEqual(x.hint_count(),2);x.close()
 def test_partial_index_never_executes_old_active_then_resumes(self):
  a,b=self.unit(1),self.unit(2);self.seed([a]);rows=[a,b];x=self.queue(rows);self._interrupt_after_first(x)
  self.assertFalse(x.get('indexed'));self.assertEqual(x.total(),1)
  with self.assertRaises(q.Rejected):x.page('blocked',16)
  offset=x.get('offset');self.assertGreater(offset,0);x.close()
  x=self.queue(rows);x.index();self.assertTrue(x.get('indexed'));self.assertEqual((x.total(),x.hint_count()),(2,1));x.close()
 def _interrupt_after_first(self,x):
  calls=0
  def check():
   nonlocal calls
   calls+=1
   if calls==3:raise q.Deferred('author bounded interruption')
  x.check=check
  with self.assertRaises(q.Deferred):x.index()
  x.check=lambda:None;x.interrupted=None
 def test_new_manifest_restarts_only_pending_snapshot(self):
  a,b,c=[self.unit(i)for i in(1,2,3)];self.seed([a]);x=self.queue([a,b]);self._interrupt_after_first(x);x.close()
  x=self.queue([a,c]);x.index();self.assertEqual({r['unit']['recipe_blob_identity']for r in x.page('new',16)},{a['recipe_blob_identity'],c['recipe_blob_identity']});self.assertEqual(x.hint_count(),1);x.close()
 def test_exact_duplicate_allowed_same_uid_conflict_excluded(self):
  a=self.unit(1);x=self.queue([a,copy.deepcopy(a)]);x.index();self.assertEqual((x.total(),x.get('duplicates')),(1,1));x.close()
  conflict=copy.deepcopy(a);conflict['key']['decoded']['another_compile_field']=1
  x=self.queue([a,conflict]);x.index();self.assertEqual((x.total(),x.get('conflicts'),x.get('rejected')),(0,1,1));x.close()
 def test_input_changes_and_directory_domains_not_merged(self):
  a=self.unit(1);base=q.digest(ident.input_identity(a))
  for field in('profile','key','metadata','spirv','permutation','spec'):
   u=copy.deepcopy(a)
   if field=='permutation':u['stages'][0]['cache_local_permutation']+=1
   elif field=='spec':u['stages'][0]['serialized_spec_sha256']='c'*64
   else:
    ref=u[field]['reference']if field in('profile','key')else u['stages'][0][field]
    ref['sha256']='d'*64
    refs=[u['profile']['reference'],u['key']['reference'],u['stages'][0]['metadata'],u['stages'][0]['spirv']]
    u['execution_policy']['candidate_preparation']['frozen_input_set_identity']=q.digest(refs)
   self.assertNotEqual(q.digest(ident.input_identity(u)),base)
  u=copy.deepcopy(a);new=self.root/'other';new.mkdir();u['namespace']['cache']=str(new);u['namespace']['cache_id']=ident.cache_scope(new,self.profile,self.abi)
  for ref in [u['profile']['reference'],u['key']['reference'],u['stages'][0]['metadata'],u['stages'][0]['spirv']]:ref['path']=str(new/ref['name'])
  u['execution_policy']['candidate_preparation']['frozen_input_set_identity']=q.digest([u['profile']['reference'],u['key']['reference'],u['stages'][0]['metadata'],u['stages'][0]['spirv']])
  self.assertNotEqual(q.digest(ident.input_identity(u)),base)
 def test_context_change_separates_domain_old_schema_untouched(self):
  legacy=self.progress/('f'*64);legacy.mkdir();p=legacy/'queue.sqlite';p.write_bytes(b'author-old-v1-not-sqlite');before=H(p.read_bytes())
  a=self.unit(1);folder,job=self.seed([a]);context=dict(self.context,options_identity='e'*64);x=self.queue([a],context);x.index();self.assertNotEqual(x.job,job);self.assertEqual(x.hint_count(),0);x.close();self.assertEqual(H(p.read_bytes()),before)
 def test_hint_still_calls_current_native_gate_not_state_authority(self):
  a,b=self.unit(1),self.unit(2);self.seed([a]);events=[]
  def execute(u,phase,allow,ordinal):
   events.append((u['recipe_blob_identity'],phase,allow))
   return dict(exit=0,phase=phase,worker=dict(status='source-requests-recorded'if phase=='record'else'ready',artifact_counts=dict(built=0)),native_author_gate=True)
  result=q.run_manifest(self.manifest([a,b]),progress_root=self.progress,context=self.context,execute=execute,prepare_phase=lambda *args:dict(status='prepared'),check=lambda:None,ready=lambda row:row['native_author_gate'],seconds=1)
  self.assertEqual(result['completed'],2);self.assertEqual([e[1:]for e in events if e[0]==a['recipe_blob_identity']],[('prepare',False)]);self.assertEqual([e[1:]for e in events if e[0]==b['recipe_blob_identity']],[('record',False),('prepare',True)])
 def test_guard_stop_and_false_ready_hint_not_swallowed(self):
  a=self.unit(1);self.seed([a]);called=[]
  def execute(u,phase,allow,ordinal):
   called.append(phase)
   return dict(exit=79,worker={})
  result=q.run_manifest(self.manifest([a]),progress_root=self.progress,context=self.context,execute=execute,prepare_phase=lambda *args:dict(status='prepared'),check=lambda:None,ready=lambda row:False,seconds=1)
  self.assertEqual(result['status'],'stopped');self.assertEqual(result['completed'],0);self.assertEqual(called,['prepare'])
 def test_emitter_inventory_audit_not_payload_and_stable_namespace(self):
  profile=dict(name='profile.bin',bytes=1,sha256='1'*64)
  inv=dict(profile=profile,pipeline_keys=[],paired_specializations=[],errors=[dict(name='author',reason='fixture')])
  layout=dict(pointer_bytes=8,little_endian=True,profile_fields={'field':{'offset':0,'size':1}},**{'GraphicsPipelineKey.size':352})
  audits=[]
  with patch.object(candidates,'inventory',return_value=inv),patch.object(candidates,'reference',return_value=(b'x',dict(path=str(self.cache/'profile.bin'),**profile))),patch.object(candidates,'semantic_profile',return_value=dict(semantic_sha256=self.profile)):
   first=list(candidates.cache_records(self.cache,layout,snapshot_sink=audits.append))[0]
   inv['pipeline_keys']=[dict(name='unrelated.key',bytes=1,sha256='2'*64)]
   # First error is emitted before key parsing; no author key decoder needed.
   second=next(candidates.cache_records(self.cache,layout,snapshot_sink=audits.append))
  self.assertEqual(first['namespace'],second['namespace']);self.assertNotEqual(audits[0]['inventory_sha256'],audits[1]['inventory_sha256']);self.assertNotIn('inventory_snapshot_sha256',first['namespace'])
 def test_native_packet_original_schema_spec_only_and_owned_sha(self):
  a=self.unit(1);packetroot=self.root/'packet';packetroot.mkdir();spec=b'spec1'
  layout={'Backend::Bindings.size':4,'RuntimeInfo.size':8,'GraphicsPipelineKey.size':352}
  astage=lambda *args:dict(sections=dict(specialization=dict(offset=24,bytes=len(spec),sha256=H(spec),native_spec_only_reader_allowed=True)))
  packet,held=runner.freeze(a,layout,packetroot,'f'*64,False,cache_root=self.root,artifact_cache_dir=self.root/'artifact',audit_stage=astage,audit_key_profile=lambda *args:None)
  self.assertEqual((packet['packet_schema'],packet['manifest_schema']),(1,2));self.assertFalse(packet['native_info_srt_allowed']);self.assertEqual(packet['walker_bytes_passed'],0);self.assertEqual(set(packet['stages'][0]),{'logical_stage','hardware_stage','program_hash','permutation','permutation_hash','spec','spirv'});self.assertEqual(Path(packet['stages'][0]['spec']['path']).read_bytes(),spec)
  Path(a['key']['reference']['path']).write_bytes(b'bad');p2=self.root/'packet2';p2.mkdir()
  with self.assertRaises(runner.Refused):runner.freeze(a,layout,p2,'f'*64,False,cache_root=self.root,artifact_cache_dir=self.root/'artifact',audit_stage=astage,audit_key_profile=lambda *args:None)
if __name__=='__main__':unittest.main(verbosity=2)
