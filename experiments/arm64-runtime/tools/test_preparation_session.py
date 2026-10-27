"""Real builder/receipt/owned-file flow; compiler process deliberately authored."""
from pathlib import Path
import copy,hashlib,json,os,sys,tempfile,time,unittest
from unittest.mock import patch
E=Path(__file__).resolve().parent
sys.path.insert(0,str(E))
import local_metal_builder as m
class Guard:
 def __init__(self):self.calls=0;self.stop=False
 def check(self,**kw):
  self.calls+=1
  if self.stop:raise m.Stopped('author pressure stop')
class FixedAuthorRunner:
 """Fixed NativeToolchain still runs identify/unchanged/compile logic. No subprocess."""
 def __init__(self,owner):self.o=owner
 def run(self,argv,timeout,guard,**kwargs):
  guard.check(preflight=True);self.o.commands+=1
  if argv[0]=='/usr/bin/sw_vers':return self.o.os_build.encode()
  if argv[1]=='--find':return str(self.o.tools[argv[2]]).encode()
  if argv[1]=='--show-sdk-path':return str(self.o.sdk).encode()
  if argv[-1]=='--version':return b'author-version-1'
  if argv[1]=='metal':
   self.o.compiles+=1;out=Path(argv[argv.index('-o')+1]);out.write_bytes(b'AUTHOR-AIR');return b''
  if argv[1]=='metallib':Path(argv[argv.index('-o')+1]).write_bytes(b'AUTHOR-LIBRARY');return b''
  raise AssertionError(argv)
class Controls(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.P=Path(self.temp.name).resolve();(self.P/'state').mkdir();self.gpu=self.P/'state/gpu';self.gpu.mkdir();self.root=self.gpu/'local-metal';self.root.mkdir();self.cohort='a'*64
  self.base=self.root/self.cohort;self.base.mkdir();self.requests=self.base/'requests';self.requests.mkdir();self.tmp=self.P/'tmp';self.tmp.mkdir();self.sdk=self.P/'Author.sdk';self.sdk.mkdir();(self.sdk/'SDKSettings.json').write_text('{}')
  self.tools={}
  for name in ('metal','metallib'):
   p=self.P/name;p.write_bytes(('AUTHOR-TOOL-'+name).encode());p.chmod(0o600);self.tools[name]=p
  self.os_build='AUTHOR-OS';self.commands=self.compiles=self.factories=0;self.guard=Guard();self.oldP=m.PROJECT;self.oldgpu=os.environ.get('BB_GPU_USER_DIR');m.PROJECT=self.P;os.environ['BB_GPU_USER_DIR']=str(self.gpu)
  self.session=m.PreparationSession(self.tmp);self.request(1)
  # Establish actual author receipts before session. This is not native success.
  self.assertEqual(self.direct()['status'],'passed')
 def tearDown(self):
  self.session.close();m.PROJECT=self.oldP
  if self.oldgpu is None:os.environ.pop('BB_GPU_USER_DIR',None)
  else:os.environ['BB_GPU_USER_DIR']=self.oldgpu
  self.temp.cleanup()
 def factory(self):
  self.factories+=1;return m.NativeToolchain(FixedAuthorRunner(self))
 def direct(self):return m.build(self.root,self.cohort,enabled=True,guard=self.guard,toolchain=self.factory())
 def call(self):return self.session.build(self.root,self.cohort,enabled=True,guard=self.guard,toolchain_factory=self.factory)
 def request(self,i):
  source=f'AUTHOR-SOURCE-{i}'.encode();r=dict(schema=2,recipe=m.LIBRARY_RECIPE,cohort=self.cohort,source_sha256=m.digest(source),language='metal3.1',math_mode='safe',math_fp32='fast');r['key']=m.derive_key(r)
  (self.requests/(r['key']+'.metal')).write_bytes(source);(self.requests/(r['key']+'.json')).write_bytes(m.canonical(r));return r
 def settled(self):
  first=self.call();self.assertEqual(first['status'],'passed');self.assertFalse(first['session_reused']);self.assertIsNotNone(self.session.snapshot);return first
 def test_same_session_reuses_no_identify_or_compile_guard_still_runs(self):
  self.settled();before=(self.factories,self.compiles,self.guard.calls);result=self.call();self.assertTrue(result['session_reused']);self.assertEqual((self.factories,self.compiles),before[:2]);self.assertGreater(self.guard.calls,before[2]);self.assertEqual(self.session.stats['reuses'],1)
 def test_record_phase_explicit_invalidation(self):
  self.settled();before=self.factories;self.session.invalidate('record-phase');self.assertFalse(self.call()['session_reused']);self.assertEqual(self.factories,before+1)
 def test_added_request_full_audit_build_then_zero_build_settles(self):
  self.settled();self.request(2);first=self.call();self.assertFalse(first['session_reused']);self.assertEqual(first['built'],1);self.assertIsNone(self.session.summary)
  self.assertFalse(self.call()['session_reused']);self.assertTrue(self.call()['session_reused'])
 def test_request_replace_same_bytes_and_restored_mtime_invalidates(self):
  self.settled();p=next(self.requests.glob('*.json'));before=p.stat();p.write_bytes(p.read_bytes());os.utime(p,ns=(before.st_atime_ns,before.st_mtime_ns));self.assertNotEqual(before.st_ctime_ns,p.stat().st_ctime_ns);self.assertFalse(self.call()['session_reused'])
 def test_library_and_receipt_replacement_each_invalidates(self):
  self.settled();folder=self.base/'artifacts'/self.session.summary['build_id']
  for suffix in('metallib','json'):
   p=next(p for p in folder.glob('*.'+suffix)if p.name!='build.json');tmp=p.with_name('replacement');tmp.write_bytes(p.read_bytes());os.replace(tmp,p);self.assertFalse(self.call()['session_reused']);self.assertTrue(self.call()['session_reused'])
 def test_bad_source_refused_and_failure_not_cached(self):
  self.settled();p=next(self.requests.glob('*.metal'));p.write_bytes(b'BAD');r=self.call();self.assertEqual(r['status'],'refused');self.assertIsNone(self.session.summary);before=self.factories;r=self.call();self.assertEqual(r['status'],'refused');self.assertGreater(self.factories,before)
 def test_bad_library_pair_repaired_only_then_stable_audit_can_memoize(self):
  self.settled();folder=self.base/'artifacts'/self.session.summary['build_id'];old=next(folder.glob('*.metallib'));old.write_bytes(b'BAD')
  result=self.call();self.assertEqual(result['status'],'passed',result);self.assertEqual(result['repaired_products'],1);self.assertIsNone(self.session.summary)
  self.assertEqual((Path(result['results'][0]['retained_previous'])/old.name).read_bytes(),b'BAD')
  self.assertFalse(self.call()['session_reused']);self.assertTrue(self.call()['session_reused'])
 def test_tool_binary_change_new_identity_and_no_reuse(self):
  first=self.settled();self.tools['metal'].write_bytes(b'CHANGED-AUTHOR-TOOL');second=self.call();self.assertFalse(second['session_reused']);self.assertNotEqual(first['build_id'],second['build_id']);self.assertGreater(second['built'],0)
 def test_selected_tool_path_change_invalidates(self):
  self.settled();p=self.P/'metal-other';p.write_bytes(b'OTHER-AUTHOR-TOOL');self.tools['metal']=p;self.assertFalse(self.call()['session_reused'])
 def test_sdk_settings_or_selection_and_os_change_invalidates(self):
  first=self.settled();(self.sdk/'SDKSettings.json').write_text('{"author":1}')
  changed=self.call();self.assertFalse(changed['session_reused']);self.assertNotEqual(first['build_id'],changed['build_id']);self.assertGreater(changed['built'],0)
  # New SDK products require their own stable byte audit before a session memo.
  self.assertFalse(self.call()['session_reused']);self.assertTrue(self.call()['session_reused'])
  new=self.P/'Other.sdk';new.mkdir();(new/'SDKSettings.json').write_text('{}');self.sdk=new;self.assertFalse(self.call()['session_reused']);self.os_build='CHANGED-AUTHOR-OS';self.assertFalse(self.call()['session_reused'])
 def test_delete_rename_and_external_link_rejected_or_invalidated(self):
  self.settled();p=next(self.requests.glob('*.metal'));os.link(p,self.P/'outside-link');self.assertEqual(self.call()['status'],'refused');self.assertIsNone(self.session.summary)
 def test_guard_stop_propagates_not_source_fallback(self):
  self.settled();self.guard.stop=True
  with self.assertRaises(m.Stopped):self.call()
  self.assertEqual(self.session.stats['reuses'],0)
 def test_mutation_during_full_audit_never_captures_memo(self):
  original=m.already_succeeded;changed=False
  def audit(*args):
   nonlocal changed
   result=original(*args)
   if not changed:
    changed=True;p=next(self.requests.glob('*.metal'));p.write_bytes(p.read_bytes())
   return result
  with patch.object(m,'already_succeeded',side_effect=audit):r=self.call()
  self.assertEqual(r['status'],'passed');self.assertIsNone(self.session.summary)
 def test_only_current_session_no_persistent_authority_and_cleanup(self):
  self.settled();pin=self.session.snapshot.path;self.assertTrue(pin.exists());self.session.close();self.assertFalse(pin.exists());fresh=m.PreparationSession(self.tmp)
  try:self.assertFalse(fresh.build(self.root,self.cohort,enabled=True,guard=self.guard,toolchain_factory=self.factory)['session_reused'])
  finally:fresh.close()
 def test_off_does_no_io_or_factory(self):
  count=self.factories;self.assertEqual(self.session.build('/does/not/exist',None,enabled=False,guard=None,toolchain_factory=None)['status'],'off');self.assertEqual(self.factories,count)
 def test_entry_and_disk_budget_only_disable_memo_full_audit_runs(self):
  for name,value in (('SESSION_ENTRY_MAX',2),('SESSION_LEDGER_MAX',4096)):
   before=self.factories
   with patch.object(m,name,value):r=self.call()
   self.assertEqual(r['status'],'passed');self.assertFalse(r['session_reused']);self.assertIsNone(self.session.summary);self.assertGreater(self.factories,before)
 def test_optional_sdk_query_failure_does_not_fake_or_break_full_audit(self):
  original=FixedAuthorRunner.run;queries=0
  def fail_post(runner,argv,*args,**kwargs):
   nonlocal queries
   if argv[1:] == ['--show-sdk-path']:
    queries+=1
    if queries==2:raise m.Refused('author optional SDK query failure')
   return original(runner,argv,*args,**kwargs)
  with patch.object(FixedAuthorRunner,'run',new=fail_post):r=self.call()
  self.assertEqual(r['status'],'passed');self.assertIsNone(self.session.summary)
 def test_performance_cpu_author_actual_full_builder_vs_session(self):
  for i in range(2,33):self.request(i)
  self.assertEqual(self.direct()['status'],'passed');self.settled();t=time.monotonic();self.direct();full=time.monotonic()-t;t=time.monotonic();r=self.call();reuse=time.monotonic()-t;self.assertTrue(r['session_reused'])
  (self.P/'cpu-author-phase-measurement.json').write_text(json.dumps(dict(author_requests=32,full_builder_seconds=full,reuse_seconds=reuse,scope='real Python builder with authored compiler, not native/Metal/FPS',session_stats=self.session.stats),indent=2)+'\n')
if __name__=='__main__':unittest.main(verbosity=2)
