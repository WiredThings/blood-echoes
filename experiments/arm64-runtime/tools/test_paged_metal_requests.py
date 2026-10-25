from pathlib import Path
import collections,json,os,shutil,sys,tempfile,tracemalloc,unittest
from unittest.mock import patch
E=Path(__file__).resolve().parent
sys.path.insert(0,str(E))
import local_metal_builder as b
from paged_metal_requests import RequestIndex,PAGE_MAX
COHORT='c'*64
class Guard:
 def __init__(self,stop_after=None):self.calls=0;self.stop_after=stop_after
 def check(self,**kwargs):
  self.calls+=1
  if self.stop_after and self.calls>=self.stop_after:raise b.Stopped('author safety stop')
class Toolchain:
 def __init__(self,stop_after=None,interrupt=False):self.calls=collections.Counter();self.stop_after=stop_after;self.interrupt=interrupt
 def identify(self,guard):
  self.manifest=dict(schema=1,authored=True,recipe=b.RECIPE);return b.digest(b.canonical(self.manifest)),self.manifest
 def compile(self,source,air,library,request,guard):
  if self.interrupt:raise KeyboardInterrupt()
  if self.stop_after is not None and sum(self.calls.values())>=self.stop_after:raise b.Stopped('author protection between entries')
  self.calls[request['key']]+=1
  self.assert_owned_snapshot(source,request)
  air.write_bytes(b'author-air');library.write_bytes(b'author-metallib:'+request['key'].encode())
 def assert_owned_snapshot(self,source,request):
  assert source.name=='source.metal' and b.digest(source.read_bytes())==request['source_sha256']

def put(folder,i,*,sharded=False,schema=1):
 source=f'// author input {i}\nkernel void author() {{}}\n'.encode()
 r=dict(schema=schema,recipe=b.RECIPE if schema==1 else b.LIBRARY_RECIPE,cohort=COHORT,source_sha256=b.digest(source),language='metal3.1',math_mode='safe',math_fp32='fast')
 if schema==1:r['entry']='author'
 r['key']=b.derive_key(r);key=r['key']
 leaf=folder/key[:2] if sharded else folder;leaf.mkdir(exist_ok=True)
 (leaf/(key+'.metal')).write_bytes(source);(leaf/(key+'.json')).write_bytes(b.canonical(r))
 return key,leaf

class Controls(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name).resolve()
  self.project=self.base/'project';self.root=self.project/'state/gpu/local-metal';self.folder=self.root/COHORT/'requests';self.folder.mkdir(parents=True)
  self.scratch=self.base/'index-scratch';self.scratch.mkdir();self.project_patch=patch.object(b,'PROJECT',self.project);self.project_patch.start()
  self.env_patch=patch.dict(os.environ,{'BB_GPU_USER_DIR':str(self.project/'state/gpu')});self.env_patch.start()
 def tearDown(self):self.env_patch.stop();self.project_patch.stop();self.temp.cleanup()
 def index(self,guard=None):return RequestIndex(self.folder,self.scratch,guard or Guard())
 def build(self,tool=None,guard=None):return b.build(self.root,COHORT,enabled=True,guard=guard or Guard(),toolchain=tool or Toolchain())
 def test_legacy_more_than_4096_continues_after_protection_without_recompiling_success(self):
  for i in range(4101):put(self.folder,i)
  tool=Toolchain(stop_after=300)
  tracemalloc.start()
  first=self.build(tool);_,peak1=tracemalloc.get_traced_memory();tracemalloc.stop()
  self.assertEqual(first['status'],'stopped');self.assertEqual(first['built'],300)
  successful=set(tool.calls);tool.stop_after=None
  tracemalloc.start()
  second=self.build(tool);_,peak2=tracemalloc.get_traced_memory();tracemalloc.stop()
  self.assertEqual(second['status'],'passed',second);self.assertEqual(second['requests'],4101)
  self.assertEqual((second['built'],second['skipped']),(3801,300))
  self.assertTrue(all(tool.calls[k]==1 for k in successful));self.assertEqual(sum(tool.calls.values()),4101)
  third=self.build(tool);self.assertEqual((third['built'],third['skipped']),(0,4101))
  self.assertEqual(len(second['results']),PAGE_MAX);self.assertTrue(second['results_preview_truncated'])
  self.assertEqual(len(Path(second['results_jsonl']).read_text().splitlines()),4101)
  self.assertLessEqual(second['request_window']['max_batch'],PAGE_MAX);self.assertLessEqual(second['request_window']['max_final_pins'],2)
  self.assertLess(max(peak1,peak2),16*1024*1024)
  (self.base/'large-control-metrics.json').write_text(json.dumps(dict(requests=4101,first_built=300,resume_built=3801,verified_skip=300,all_warm_skip=4101,peak_python_tracemalloc_bytes=max(peak1,peak2),window=second['request_window'],actual_metal_compiler_called=False),indent=2)+'\n')
 def test_sharded_and_legacy_schemas_compile(self):
  put(self.folder,1);put(self.folder,2,sharded=True,schema=2)
  r=self.build();self.assertEqual((r['status'],r['built']),('passed',2));self.assertEqual(r['request_recovery']['shards'],1)
 def test_flat_shard_duplicate_verified_and_compiled_once(self):
  key,leaf=put(self.folder,1);shard=self.folder/key[:2];shard.mkdir()
  for ext in ('metal','json'):shutil.copyfile(leaf/(key+'.'+ext),shard/(key+'.'+ext))
  tool=Toolchain();r=self.build(tool);self.assertEqual((r['status'],r['built']),('passed',1));self.assertEqual(tool.calls[key],1)
 def test_bad_duplicate_not_hidden_by_good_flat(self):
  key,leaf=put(self.folder,1);shard=self.folder/key[:2];shard.mkdir()
  for ext in ('metal','json'):shutil.copyfile(leaf/(key+'.'+ext),shard/(key+'.'+ext))
  (shard/(key+'.metal')).write_bytes(b'corrupt')
  self.assertEqual(self.build()['status'],'refused')
 def test_legacy_internal_final_temp_hardlink_recovery(self):
  key,leaf=put(self.folder,1);os.link(leaf/(key+'.metal'),leaf/(key+'.metal.tmp-123-1'))
  r=self.build();self.assertEqual(r['status'],'passed',r);self.assertEqual(r['request_recovery']['legacy_internal_link_groups'],1)
  self.assertEqual((leaf/(key+'.metal')).stat().st_nlink,2)
 def test_shard_internal_final_temp_hardlink_recovery(self):
  key,leaf=put(self.folder,1,sharded=True);os.link(leaf/(key+'.metal'),leaf/(key+'.metal.tmp-123-1'))
  self.assertEqual(self.build()['status'],'passed')
 def test_cross_shard_hardlink_does_not_count_as_internal(self):
  key,leaf=put(self.folder,1);shard=self.folder/key[:2];shard.mkdir();os.link(leaf/(key+'.metal'),shard/(key+'.metal'))
  with self.assertRaisesRegex(b.Refused,'cross-shard'):self.index()
 def test_external_hardlink_rejected(self):
  key,leaf=put(self.folder,1);os.link(leaf/(key+'.metal'),self.base/'external')
  with self.assertRaisesRegex(b.Refused,'hardlinks'):self.index()
 def test_cross_family_hardlink_rejected(self):
  a,leaf=put(self.folder,1);other='0'*64;os.link(leaf/(a+'.metal'),leaf/(other+'.metal'))
  with self.assertRaisesRegex(b.Refused,'hardlinks'):self.index()
 def test_symlink_file_rejected(self):
  key,leaf=put(self.folder,1);p=leaf/(key+'.metal');out=self.base/'outside';p.rename(out);p.symlink_to(out)
  with self.assertRaisesRegex(b.Refused,'nonregular'):self.index()
 def test_symlink_shard_rejected(self):
  target=self.base/'other';target.mkdir();(self.folder/'ab').symlink_to(target,target_is_directory=True)
  with self.assertRaises(b.Refused):self.index()
 def test_wrong_prefix_shard_rejected(self):
  key,leaf=put(self.folder,1);wrong='00' if key[:2]!='00' else '01';shard=self.folder/wrong;shard.mkdir()
  shutil.copyfile(leaf/(key+'.metal'),shard/(key+'.metal'))
  with self.assertRaisesRegex(b.Refused,'wrong shard'):self.index()
 def test_nested_or_nonhex_directory_rejected(self):
  (self.folder/'unexpected').mkdir()
  with self.assertRaisesRegex(b.Refused,'subdirectory'):self.index()
 def test_unpaired_preserved_deferred_partial(self):
  key,leaf=put(self.folder,1);(leaf/(key+'.json')).unlink()
  r=self.build();self.assertEqual((r['status'],r['built']),('partial',0));self.assertEqual(r['request_recovery']['deferred_unpaired_count'],1)
  self.assertTrue((leaf/(key+'.metal')).exists())
 def test_many_unpaired_preview_is_bounded(self):
  for i in range(300):
   key,leaf=put(self.folder,i);(leaf/(key+'.json')).unlink()
  r=self.build();self.assertEqual(r['status'],'partial');self.assertEqual(len(r['request_recovery']['deferred_unpaired']),PAGE_MAX)
  self.assertTrue(r['request_recovery']['deferred_preview_truncated'])
  self.assertEqual(len(Path(r['results_jsonl']).read_text().splitlines()),300)
 def test_index_safety_stop_not_swallowed(self):
  for i in range(150):put(self.folder,i)
  r=self.build(guard=Guard(stop_after=2));self.assertEqual(r['status'],'stopped');self.assertIn('safety',r['error'])
  self.assertFalse(list((self.root/COHORT/'artifacts/.builder-tmp').glob('request-index-*')))
 def test_keyboard_interrupt_not_refused_or_fallback(self):
  put(self.folder,1);r=self.build(Toolchain(interrupt=True));self.assertEqual(r['status'],'stopped')
 def test_sql_progress_preserves_original_safety_stop(self):
  for i in range(10):put(self.folder,i)
  guard=Guard()
  with self.index(guard) as index:
   guard.stop_after=guard.calls+1
   with self.assertRaisesRegex(b.Stopped,'safety'):
    index._sql('SELECT COUNT(*) FROM entries a,entries b,entries c,entries d')
 def test_native_patch_wires_both_publishers_and_preserves_atomic_publish(self):
  aot=(E.parent/'vendor/metal_api/shadps4/video_core/renderer_metal/mtl_local_aot.h').read_text();library=(E.parent/'vendor/metal_api/shadps4/video_core/renderer_metal/mtl_local_library.h').read_text()
  self.assertIn('RequestShard(folder,result.key,requests)',aot)
  self.assertIn('LocalAot::RequestShard(folder,key,requests)',library)
  self.assertIn('key.substr(0,2)',aot);self.assertNotIn('MaxRequests',aot+library)
  self.assertNotIn('request_attempts',aot+library);self.assertIn('RENAME_EXCL',aot)
 def test_off_still_no_io(self):
  r=b.build(self.base/'absent','not-a-cohort',enabled=False);self.assertEqual(r['status'],'off')
 def test_changed_final_inode_after_inventory_rejected(self):
  key,leaf=put(self.folder,1)
  with self.index() as index:
   f=leaf/(key+'.metal');f.rename(self.base/'old');shutil.copyfile(self.base/'old',f)
   with self.assertRaisesRegex(b.Refused,'inode changed'):index.load_owned(key,COHORT)
 def test_malformed_json_pair_rejected_before_compile(self):
  key,leaf=put(self.folder,1);(leaf/(key+'.json')).write_text('{}');tool=Toolchain();r=self.build(tool)
  self.assertEqual(r['status'],'refused');self.assertFalse(tool.calls)

if __name__=='__main__':unittest.main()
