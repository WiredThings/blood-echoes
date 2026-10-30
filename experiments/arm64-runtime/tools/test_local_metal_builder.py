#!/usr/bin/env python3
"""Authored files + in-process fake tool runner only; never spawns xcrun/Metal/GPU/game."""
from pathlib import Path
import copy,hashlib,json,os,sys,tempfile,unittest
from unittest.mock import patch
import local_metal_builder as builder
sys.path.insert(0,str(builder.PROJECT/'tools'))
from qa_memory_policy import MemoryPolicy

class FakeGuard:
 def __init__(self):self.calls=0;self.stop=False
 def check(self,**kwargs):
  self.calls+=1
  if self.stop:raise builder.Stopped('authored protection stop')

class FakeRunner:
 def __init__(self,folder):
  self.folder=folder;self.calls=[];self.fail=None;self.library_size=None;self.change_tool=False
  self.sdk=folder/'Author.sdk';self.sdk.mkdir();(self.sdk/'SDKSettings.json').write_text('{}')
  self.tools={name:folder/name for name in('metal','metallib','xcrun')}
  for name,path in self.tools.items():path.write_bytes(('authored fake tool '+name).encode())
 def fingerprint(self,path):
  return builder.tool_fingerprint(self.tools['xcrun']if str(path)=='/usr/bin/xcrun'else path)
 def run(self,argv,timeout,guard,**kwargs):
  guard.check(preflight=True);self.calls.append(list(argv))
  if argv[:3]==['/usr/bin/xcrun','--sdk','macosx']:argv=[argv[0],*argv[3:]]
  if argv[:2]==['/usr/bin/xcrun','--find']:return(str(self.tools[argv[2]])+'\n').encode()
  if argv==['/usr/bin/xcrun','--version']:return b'authored xcrun 1\n'
  if argv==['/usr/bin/xcrun','--show-sdk-path']:return(str(self.sdk)+'\n').encode()
  if argv==['/usr/bin/sw_vers','-buildVersion']:return b'AUTHORED-OS-1\n'
  if len(argv)==3 and argv[0]=='/usr/bin/xcrun'and argv[2]=='--version':return('authored '+argv[1]+' version 1\n').encode()
  if argv[:2]==['/usr/bin/xcrun','metal']:
   self.assert_metal(argv)
   if self.fail=='metal':raise builder.Refused('authored metal failure')
   source=Path(argv[argv.index('-c')+1]);out=Path(argv[argv.index('-o')+1])
   out.write_bytes(b'AUTHORED-AIR\0'+source.read_bytes()+argv[3].encode())
   return b''
  if argv[:2]==['/usr/bin/xcrun','metallib']:
   assert len(argv)==5 and argv[3]=='-o'
   if self.fail=='metallib':raise builder.Refused('authored metallib failure')
   out=Path(argv[4]);out.write_bytes(b'X'*self.library_size if self.library_size is not None else b'AUTHORED-LIB\0'+Path(argv[2]).read_bytes())
   if self.change_tool:self.tools['metal'].write_bytes(b'authored tool replacement')
   return b''
  raise AssertionError('unexpected command '+repr(argv))
 @staticmethod
 def assert_metal(argv):
  assert len(argv)==9 and argv[:3]==['/usr/bin/xcrun','metal','-std=metal3.1']
  assert argv[3]in('-fmetal-math-mode=safe','-fmetal-math-mode=relaxed')
  assert argv[4]=='-fmetal-math-fp32-functions=fast'and argv[5]=='-c'and argv[7]=='-o'
 def compiles(self):return[argv for argv in self.calls if '-c'in argv]

class BuilderTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.top=Path(tempfile.mkdtemp(prefix='local-aot-builder-authored-',dir=builder.PROJECT/'state'))
 def setUp(self):
  self.case=Path(tempfile.mkdtemp(prefix='case-',dir=self.top));self.root=self.case/'local-metal';self.root.mkdir()
  self.cohort='a'*64;self.folder=self.root/self.cohort;self.folder.mkdir();self.requests=self.folder/'requests';self.requests.mkdir()
  self.tools=self.case/'fake-tools';self.tools.mkdir();self.runner=FakeRunner(self.tools);self.guard=FakeGuard()
  self.env=patch.dict(os.environ,{'BB_GPU_USER_DIR':str(self.case)});self.env.start();self.addCleanup(self.env.stop)
 def export(self,source=b'kernel void main0() {}\n',**fields):
  request=dict(schema=1,cohort=self.cohort,source_sha256=builder.digest(source),entry='main0',language='metal3.1',math_mode='safe',math_fp32='fast',recipe=builder.RECIPE)
  request.update(fields);request['key']=builder.derive_key(request);key=request['key']
  (self.requests/(key+'.metal')).write_bytes(source);(self.requests/(key+'.json')).write_bytes(builder.canonical(request));return key,request
 def run_builder(self):
  chain=builder.NativeToolchain(self.runner,fingerprint=self.runner.fingerprint)
  return builder.build(self.root,self.cohort,enabled=True,guard=self.guard,toolchain=chain)
 def artifact(self,result,key,suffix):return self.folder/'artifacts'/result['build_id']/(key+suffix)
 def select_cohort(self,value):
  self.cohort=value;self.folder=self.root/value;self.folder.mkdir(exist_ok=True)
  self.requests=self.folder/'requests';self.requests.mkdir(exist_ok=True)
 def test_retained_full_source_product_reused_across_cohorts_and_back(self):
  key,request=self.export();first=self.run_builder();self.assertEqual(first['status'],'passed',first)
  previous=self.artifact(first,key,'.metallib');old=previous.read_bytes();receipt=self.artifact(first,key,'.json');old_receipt=receipt.read_bytes();compiles=len(self.runner.compiles())
  self.select_cohort('b'*64);newkey,newrequest=self.export();self.assertNotEqual(key,newkey)
  second=self.run_builder();self.assertEqual(second['status'],'passed',second)
  self.assertEqual(second['built'],0);self.assertEqual(second['reused_products'],1);self.assertEqual(len(self.runner.compiles()),compiles)
  self.assertEqual(self.artifact(second,newkey,'.metallib').read_bytes(),old)
  self.assertTrue(builder.already_succeeded(self.folder/'artifacts'/second['build_id'],newrequest,second['build_id']))
  adoption=json.loads(Path(second['results'][0]['adoption_provenance']).read_bytes());self.assertEqual(adoption['original_request'],request);self.assertEqual(adoption['current_request'],newrequest)
  self.assertFalse(adoption['native_entry_admitted']);self.assertFalse(adoption['pipeline_ready'])
  self.assertEqual(receipt.read_bytes(),old_receipt);self.assertEqual(previous.read_bytes(),old)
  self.select_cohort('a'*64);third=self.run_builder();self.assertEqual(third['built'],0);self.assertEqual(len(self.runner.compiles()),compiles)
 def test_real_source_entry_math_or_sdk_change_cannot_alias_retained_product(self):
  self.export();first=self.run_builder();self.assertEqual(first['status'],'passed',first)
  for i,changes in enumerate((dict(source=b'kernel void another() {}\n'),dict(entry='other'),dict(math_mode='relaxed'))):
   self.select_cohort(str(i+1)*64);self.export(**changes);second=self.run_builder()
   self.assertEqual(second['status'],'passed',second);self.assertEqual(second['built'],1);self.assertEqual(second.get('reused_products',0),0)
 def test_bad_retained_origin_is_preserved_and_only_current_product_compiles(self):
  key,_=self.export();first=self.run_builder();self.assertEqual(first['status'],'passed',first)
  old=self.artifact(first,key,'.metallib');old.write_bytes(b'bad retained bytes')
  self.select_cohort('b'*64);self.export();count=len(self.runner.compiles());second=self.run_builder()
  self.assertEqual(second['status'],'passed',second);self.assertEqual(second['built'],1)
  self.assertEqual(len(self.runner.compiles()),count+1);self.assertEqual(old.read_bytes(),b'bad retained bytes')
 def test_legacy_without_sdk_product_identity_does_not_gain_adoption_authority(self):
  _,request=self.export();self.assertFalse(builder.remember_product(self.root,request,'c'*64,dict(schema=1)))
  self.assertIsNone(builder.retained_product(self.root,request,b'author source','c'*64,dict(schema=1),self.guard))
 def test_v2_complete_library_reuse_uses_real_v2_key_without_inventing_entry(self):
  def emit():
   source=b'kernel void main0() {}\n'
   request=dict(schema=2,recipe=builder.LIBRARY_RECIPE,cohort=self.cohort,source_sha256=builder.digest(source),language='metal3.1',math_mode='safe',math_fp32='fast')
   request['key']=builder.derive_key(request);key=request['key']
   (self.requests/(key+'.metal')).write_bytes(source);(self.requests/(key+'.json')).write_bytes(builder.canonical(request))
   return key,request
  oldkey,old=emit();first=self.run_builder();self.assertEqual(first['status'],'passed',first);count=len(self.runner.compiles())
  self.select_cohort('b'*64);key,request=emit();second=self.run_builder()
  self.assertEqual(second['status'],'passed',second);self.assertEqual(second['reused_products'],1);self.assertEqual(second['built'],0)
  self.assertNotEqual(oldkey,key);self.assertNotIn('entry',request);self.assertNotIn('entry',old)
  self.assertEqual(len(self.runner.compiles()),count)
 def test_off_does_not_resolve_scan_hash_or_construct_services(self):
  with patch.object(builder,'checked_root',side_effect=AssertionError('IO')),patch.object(builder,'digest',side_effect=AssertionError('hash')),patch.object(builder,'real_services',side_effect=AssertionError('native')):
   self.assertEqual(builder.build('/nonexistent','bad',enabled=False)['status'],'off')
 def test_exact_utf8_length_prefixed_key(self):
  _,request=self.export(entry='é_main')
  fields=[request[k]for k in('recipe','cohort','source_sha256','entry','language','math_mode','math_fp32')]
  expected=hashlib.sha256(b''.join(str(len(x.encode())).encode()+b':'+x.encode()for x in fields)).hexdigest()
  self.assertEqual(request['key'],expected)
 def test_cold_build_then_warm_verified_skip(self):
  key,request=self.export();result=self.run_builder();self.assertEqual(result['status'],'passed');self.assertEqual(result['built'],1)
  receipt=json.loads(self.artifact(result,key,'.json').read_text());library=self.artifact(result,key,'.metallib').read_bytes()
  self.assertEqual(receipt,builder.expected_receipt(request,result['build_id'],library));count=len(self.runner.compiles())
  warmed=self.run_builder();self.assertEqual(warmed['status'],'passed');self.assertEqual(warmed['skipped'],1);self.assertEqual(len(self.runner.compiles()),count)
  self.assertTrue(all(path.is_relative_to(self.folder/'artifacts')for path in self.folder.rglob('*')if path.is_file()and not path.is_relative_to(self.requests)))
 def test_actual_sdk_metadata_changes_product_identity_and_keeps_old_artifacts(self):
  key,_=self.export();first=self.run_builder();self.assertEqual(first['status'],'passed',first)
  old=self.artifact(first,key,'.metallib');before=old.read_bytes()
  (self.runner.sdk/'SDKSettings.json').write_text('{"Version":"next SDK"}')
  changed=self.run_builder();self.assertEqual(changed['status'],'passed',changed)
  self.assertNotEqual(first['build_id'],changed['build_id']);self.assertEqual(changed['built'],1)
  self.assertEqual(old.read_bytes(),before)
 def test_sdk_timestamp_only_touch_does_not_change_compile_product(self):
  self.export();first=self.run_builder();os.utime(self.runner.sdk/'SDKSettings.json',None)
  second=self.run_builder();self.assertEqual(second['status'],'passed',second)
  self.assertEqual(first['build_id'],second['build_id']);self.assertEqual(second['built'],0)
 def test_same_sdk_metadata_at_another_location_does_not_change_product(self):
  import shutil
  self.export();first=self.run_builder();moved=self.tools/'Relocated.sdk'
  shutil.copytree(self.runner.sdk,moved);self.runner.sdk=moved;second=self.run_builder()
  self.assertEqual(second['status'],'passed',second);self.assertEqual(first['build_id'],second['build_id']);self.assertEqual(second['built'],0)
 def test_sdk_change_mid_compile_is_refused_without_publish(self):
  self.export();original=self.runner.run
  def run(argv,*args,**kw):
   value=original(argv,*args,**kw)
   if argv[:4]==['/usr/bin/xcrun','--sdk','macosx','metallib']and '-o'in argv:
    (self.runner.sdk/'SDKSettings.json').write_text('{"Version":"changed during compile"}')
   return value
  self.runner.run=run;result=self.run_builder()
  self.assertEqual(result['status'],'refused',result);self.assertEqual(result['built'],0)
  self.assertIn('SDK changed',result['error'])
 def test_relaxed_uses_only_fixed_recipe_argv(self):
  self.export(math_mode='relaxed');result=self.run_builder();self.assertEqual(result['status'],'passed')
  self.assertEqual(self.runner.compiles()[0][5],'-fmetal-math-mode=relaxed')
 def test_source_digest_mismatch_refused_before_compile(self):
  key,_=self.export();(self.requests/(key+'.metal')).write_bytes(b'changed source')
  result=self.run_builder();self.assertEqual(result['status'],'refused');self.assertFalse(self.runner.compiles())
 def test_recomputed_key_and_cohort_mismatch_refused(self):
  key,request=self.export();request['entry']='other';(self.requests/(key+'.json')).write_bytes(builder.canonical(request))
  result=self.run_builder();self.assertEqual(result['status'],'refused');self.assertFalse(self.runner.compiles())
 def test_extra_flags_or_wrong_recipe_refused(self):
  key,request=self.export();request['flags']=['-unsafe'];(self.requests/(key+'.json')).write_bytes(builder.canonical(request))
  self.assertEqual(self.run_builder()['status'],'refused');self.assertFalse(self.runner.compiles())
 def test_duplicate_json_fields_refused(self):
  key,_=self.export();path=self.requests/(key+'.json');data=path.read_bytes();path.write_bytes(b'{"schema":1,'+data[1:])
  self.assertEqual(self.run_builder()['status'],'refused');self.assertFalse(self.runner.compiles())
 def test_request_symlink_and_hardlink_refused(self):
  key,_=self.export();path=self.requests/(key+'.metal');target=self.case/'target';path.rename(target);path.symlink_to(target)
  self.assertEqual(self.run_builder()['status'],'refused');path.unlink();os.link(target,path)
  self.assertEqual(self.run_builder()['status'],'refused');self.assertFalse(self.runner.compiles())
 def test_nested_root_and_actual_gpu_boundary(self):
  self.export();self.assertEqual(self.run_builder()['status'],'passed')
  with patch.dict(os.environ,{'BB_GPU_USER_DIR':str(self.root)}):self.assertEqual(self.run_builder()['status'],'refused')
  link=self.case/'linked-root';link.symlink_to(self.root)
  result=builder.build(link,self.cohort,enabled=True,guard=self.guard,toolchain=builder.NativeToolchain(self.runner,fingerprint=self.runner.fingerprint));self.assertEqual(result['status'],'refused')
 def test_corrupt_success_is_retained_then_only_current_pair_repaired(self):
  key,_=self.export();result=self.run_builder();library=self.artifact(result,key,'.metallib');library.write_bytes(b'corrupt existing library');count=len(self.runner.compiles())
  result=self.run_builder();self.assertEqual(result['status'],'passed',result);self.assertEqual(result['repaired_products'],1)
  retained=Path(result['results'][0]['retained_previous']);self.assertEqual((retained/library.name).read_bytes(),b'corrupt existing library')
  self.assertNotEqual(library.read_bytes(),b'corrupt existing library');self.assertEqual(len(self.runner.compiles()),count+1)
 def test_corrupt_pair_repaired_from_compatible_other_cohort_without_compile_or_touching_healthy_pair(self):
  key,_=self.export();first=self.run_builder();bad=self.artifact(first,key,'.metallib')
  self.select_cohort('b'*64);otherkey,_=self.export();second=self.run_builder()
  healthy=self.artifact(second,otherkey,'.metallib');healthy_receipt=self.artifact(second,otherkey,'.json')
  old=(healthy.read_bytes(),healthy_receipt.read_bytes());count=len(self.runner.compiles())
  self.select_cohort('a'*64);bad.write_bytes(b'corrupt current generation')
  repaired=self.run_builder();self.assertEqual(repaired['status'],'passed',repaired)
  self.assertEqual(repaired['repaired_products'],1);self.assertEqual(repaired['reused_products'],1);self.assertEqual(repaired['built'],0)
  self.assertEqual(len(self.runner.compiles()),count);self.assertEqual(bad.read_bytes(),old[0])
  self.assertEqual((healthy.read_bytes(),healthy_receipt.read_bytes()),old)
  self.assertEqual((Path(repaired['results'][0]['retained_previous'])/bad.name).read_bytes(),b'corrupt current generation')
 def test_receipt_boolean_schema_is_not_integer_match(self):
  key,_=self.export();result=self.run_builder();path=self.artifact(result,key,'.json');receipt=json.loads(path.read_text());receipt['schema']=True;path.write_bytes(builder.canonical(receipt))
  result=self.run_builder();self.assertEqual(result['status'],'passed',result);self.assertEqual(result['repaired_products'],1)
  self.assertIs(type(json.loads(path.read_bytes())['schema']),int)
  retained=Path(result['results'][0]['retained_previous']);self.assertIs(json.loads((retained/path.name).read_bytes())['schema'],True)
 def test_compiler_failure_preserves_existing_success(self):
  key,_=self.export();result=self.run_builder();old=[self.artifact(result,key,suffix).read_bytes()for suffix in('.metallib','.json')]
  self.export(b'kernel void other() {}\n');self.runner.fail='metallib';failed=self.run_builder();self.assertEqual(failed['status'],'refused')
  self.assertEqual(old,[self.artifact(result,key,suffix).read_bytes()for suffix in('.metallib','.json')])
 def test_receipt_publish_failure_leaves_untrusted_orphan_and_retry_recovers(self):
  key,_=self.export();real_replace=builder.publish_new_file
  def replace(source,target):
   if Path(target).name==key+'.json':raise OSError('authored receipt publish failure')
   return real_replace(source,target)
  with patch.object(builder,'publish_new_file',side_effect=replace):failed=self.run_builder()
  self.assertEqual(failed['status'],'refused');self.assertTrue(self.artifact(failed,key,'.metallib').exists());self.assertFalse(self.artifact(failed,key,'.json').exists())
  retried=self.run_builder();self.assertEqual(retried['status'],'passed');self.assertEqual(retried['built'],1)
  self.assertEqual(retried['repaired_products'],1)
 def test_failed_repair_leaves_current_pair_in_place(self):
  key,_=self.export();first=self.run_builder();path=self.artifact(first,key,'.metallib');path.write_bytes(b'bad current bytes')
  old=[self.artifact(first,key,s).read_bytes()for s in('.metallib','.json')];self.runner.fail='metal';failed=self.run_builder()
  self.assertEqual(failed['status'],'refused');self.assertEqual(old,[self.artifact(first,key,s).read_bytes()for s in('.metallib','.json')])
  self.assertFalse((self.root/'.retained-invalid-v1').exists())
 def test_unsafe_current_artifact_link_is_never_moved_for_repair(self):
  key,_=self.export();first=self.run_builder();path=self.artifact(first,key,'.metallib');outside=self.case/'outside';outside.write_bytes(b'not a cache node')
  path.unlink();path.symlink_to(outside);failed=self.run_builder();self.assertEqual(failed['status'],'refused')
  self.assertTrue(path.is_symlink());self.assertEqual(outside.read_bytes(),b'not a cache node');self.assertFalse((self.root/'.retained-invalid-v1').exists())
 def test_immutable_publication_does_not_overwrite_an_existing_file(self):
  source=self.case/'candidate';target=self.case/'existing';source.write_bytes(b'new');target.write_bytes(b'old')
  with self.assertRaises(OSError):builder.publish_new_file(source,target)
  self.assertEqual(source.read_bytes(),b'new');self.assertEqual(target.read_bytes(),b'old')
 def test_tool_binary_change_changes_build_id_and_midbuild_change_refuses(self):
  self.export();before=self.run_builder();self.runner.tools['metal'].write_bytes(b'new authored tool binary')
  after=self.run_builder();self.assertEqual(after['status'],'passed');self.assertNotEqual(before['build_id'],after['build_id'])
  self.export(b'kernel void second() {}\n');self.runner.change_tool=True;changed=self.run_builder();self.assertEqual(changed['status'],'refused')
 def test_source_library_and_request_limits(self):
  self.export(b'X'*(builder.SOURCE_MAX+1));self.assertEqual(self.run_builder()['status'],'refused')
 def test_library_size_limit(self):
  self.export();self.runner.library_size=65
  with patch.object(builder,'LIBRARY_MAX',64):self.assertEqual(self.run_builder()['status'],'refused')
 def test_main_build_uses_pages_instead_of_legacy_flat_count_limit(self):
  for i in range(3):self.export(('kernel '+str(i)).encode())
  with patch.object(builder,'REQUEST_MAX',2):self.assertEqual(self.run_builder()['status'],'passed')
  self.assertEqual(len(self.runner.compiles()),3)
 def test_protection_stop_prevents_any_tool_call(self):
  self.export();self.guard.stop=True;self.assertEqual(self.run_builder()['status'],'stopped');self.assertFalse(self.runner.calls)
 def test_fixed_runner_nonzero_retains_only_private_bounded_diagnostic_tail(self):
  class Process:
   pid=876547;returncode=7
   def poll(self):return self.returncode
  private_marker=b'AUTHORED_PRIVATE_SOURCE_DIAGNOSTIC'
  def launch(*args,**kwargs):
   kwargs['stdout'].write(b'X'*3000+private_marker+b'\n');kwargs['stdout'].flush();return Process()
  runner=builder.FixedRunner(self.case)
  with patch.object(builder.subprocess,'Popen',side_effect=launch),patch.object(builder.os,'killpg')as kill:
   with self.assertRaises(builder.Refused)as caught:runner.run(['/usr/bin/xcrun','metal','--version'],60,self.guard)
  kill.assert_not_called();self.assertNotIn(private_marker.decode(),str(caught.exception))
  self.assertIn('private diagnostic tail:',str(caught.exception))
  logs=list(self.case.glob('failed-tool-*.log'));self.assertEqual(len(logs),1)
  self.assertLessEqual(logs[0].stat().st_size,2048);self.assertTrue(logs[0].read_bytes().endswith(private_marker+b'\n'))
 def test_fixed_runner_timeout_terminates_only_owned_fake_group(self):
  class Process:
   pid=876543;returncode=None
   def poll(self):return self.returncode
   def wait(self,timeout):self.returncode=-15;return self.returncode
  process=Process();runner=builder.FixedRunner(self.case)
  with patch.object(builder.subprocess,'Popen',return_value=process)as launch,patch.object(builder.os,'killpg')as kill,patch.object(builder.time,'monotonic',side_effect=[0.0,1.0]):
   with self.assertRaises(builder.Stopped):runner.run(['/usr/bin/xcrun','metal','--version'],.5,self.guard)
  kill.assert_called_once_with(process.pid,builder.signal.SIGTERM)
  kwargs=launch.call_args.kwargs;self.assertIs(kwargs['shell'],False);self.assertTrue(kwargs['start_new_session'])
  self.assertEqual(kwargs['env'],{'PATH':'/usr/bin:/bin:/usr/sbin:/sbin','HOME':str(self.case),'TMPDIR':str(self.case)+'/'})
 def test_fixed_runner_escalates_only_its_unresponsive_fake_group(self):
  class Process:
   pid=876544;returncode=None;waits=0
   def poll(self):return self.returncode
   def wait(self,timeout):
    self.waits+=1
    if self.waits==1:raise builder.subprocess.TimeoutExpired('authored fake compiler',timeout)
    self.returncode=-9;return self.returncode
  process=Process();runner=builder.FixedRunner(self.case)
  with patch.object(builder.subprocess,'Popen',return_value=process),patch.object(builder.os,'killpg')as kill,patch.object(builder.time,'monotonic',side_effect=[0.0,1.0]):
   with self.assertRaises(builder.Stopped):runner.run(['/usr/bin/xcrun','metal','--version'],.5,self.guard)
  self.assertEqual(kill.call_args_list,[unittest.mock.call(process.pid,builder.signal.SIGTERM),unittest.mock.call(process.pid,builder.signal.SIGKILL)])
 def test_fixed_runner_protection_stop_terminates_owned_fake_child(self):
  class Process:
   pid=876545;returncode=None
   def poll(self):return self.returncode
   def wait(self,timeout):self.returncode=-15;return self.returncode
  class Guard:
   calls=0
   def check(self,**kwargs):
    self.calls+=1
    if self.calls>1:raise builder.Stopped('authored locked screen')
  process=Process();runner=builder.FixedRunner(self.case)
  with patch.object(builder.subprocess,'Popen',return_value=process),patch.object(builder.os,'killpg')as kill:
   with self.assertRaisesRegex(builder.Stopped,'locked screen'):runner.run(['/usr/bin/xcrun','metal','--version'],60,Guard())
  kill.assert_called_once_with(process.pid,builder.signal.SIGTERM)
 def test_fixed_runner_bounds_live_compiler_output_without_full_reads(self):
  class Process:
   pid=876546;returncode=None
   def poll(self):return self.returncode
   def wait(self,timeout):self.returncode=-15;return self.returncode
  process=Process();output=self.case/'oversized.air';output.write_bytes(b'123456789')
  runner=builder.FixedRunner(self.case)
  with patch.object(builder.subprocess,'Popen',return_value=process),patch.object(builder.os,'killpg')as kill,patch.object(builder,'regular_bytes',side_effect=AssertionError('must stat, not reread whole IR')):
   with self.assertRaisesRegex(builder.Stopped,'output size/type'):runner.run(['/usr/bin/xcrun','metal','--version'],60,self.guard,outputs=((output,8),))
  kill.assert_called_once_with(process.pid,builder.signal.SIGTERM)
 def test_real_policy_warning_continues_but_sustained_severe_and_total_swap_stop(self):
  class Observer:
   sample=dict(locked=False,pressure=1,swap_mib=0)
   def system(self):return self.sample.copy()
  observer=Observer();clock=[0.0];guard=builder.Safety(observer,lambda:None,MemoryPolicy,clock=lambda:clock[0])
  observer.sample['pressure']=2;clock[0]=1;guard.check()
  observer.sample['pressure']=4;clock[0]=2;guard.check();clock[0]=7
  with self.assertRaises(builder.Stopped):guard.check()
  observer.sample=dict(locked=False,pressure=1,swap_mib=0);clock[0]=0
  guard=builder.Safety(observer,lambda:None,MemoryPolicy,clock=lambda:clock[0]);clock[0]=10;observer.sample['swap_mib']=2049
  with self.assertRaises(builder.Stopped):guard.check()
 def test_real_memory_policy_is_used_with_authored_observer_only(self):
  class Observer:
   sample=dict(locked=False,pressure=1,swap_mib=0)
   def system(self):return self.sample.copy()
  observer=Observer();clock=[0.0];guard=builder.Safety(observer,lambda:None,MemoryPolicy,clock=lambda:clock[0])
  observer.sample['locked']=True
  with self.assertRaises(builder.Stopped):guard.check()
  observer.sample=dict(locked=False,pressure=1,swap_mib=0);guard=builder.Safety(observer,lambda:None,MemoryPolicy,clock=lambda:clock[0])
  clock[0]=1;observer.sample['swap_mib']=1025
  with self.assertRaises(builder.Stopped):guard.check()
  observer.sample=dict(locked=False,pressure=4,swap_mib=0)
  with self.assertRaises(builder.Stopped):builder.Safety(observer,lambda:None,MemoryPolicy)
if __name__=='__main__':
 suite=unittest.defaultTestLoader.loadTestsFromTestCase(BuilderTests);result=unittest.TextTestRunner(verbosity=2).run(suite)
 record=dict(tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),passed=result.wasSuccessful(),inputs=str(BuilderTests.top),compiler_invoked=False,metal_gpu_game_invoked=False,qualification='authored files; in-process fake runner; real key/schema/path/receipt/publish/MemoryPolicy logic')
 (BuilderTests.top/'result.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2));raise SystemExit(not result.wasSuccessful())
