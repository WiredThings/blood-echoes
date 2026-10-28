"""Owned metadata fixtures only; no actual toolchain or compiler is mocked ready."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import copy,hashlib,json,os,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import local_metal_builder as b
import tool_environment_registration as r
from incremental_file_hashes import stat_stamp

class Guard:
 def __init__(self):self.stop=False
 def check(self,**kw):
  if self.stop:raise b.Stopped('authored protected stop')

class Registered(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve();self.cache=self.root/'cache';self.cache.mkdir(mode=0o700);self.guard=Guard();self.selection='authored-developer';self.context_calls=0
  self.sdk=self.root/'SDKs/Test.sdk';self.sdk.mkdir(parents=True);meta=self.sdk/'SDKSettings.json';meta.write_text('{}');self.meta=meta
  self.tools={}
  for name in ('metal','metallib','xcrun'):
   p=self.root/'bin'/name;p.parent.mkdir(exist_ok=True);p.write_bytes(('authored '+name).encode());p.chmod(0o600);self.tools[name]=p
  tools={k:dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest(),stat=stat_stamp(p.stat()))for k,p in self.tools.items()}
  manifest=dict(schema=2,tools={k:dict(sha256=v['sha256'],version='authored')for k,v in tools.items()},sdk={'metadata':'authored'},os_build='AUTHOR')
  self.checks=0
  def full(guard):guard.check();self.checks+=1
  env=dict(pins={str(self.sdk):stat_stamp(self.sdk.stat()),str(meta):stat_stamp(meta.stat())},sdk=str(self.sdk),sdk_selected=str(self.sdk),os_build='AUTHOR')
  self.chain=SimpleNamespace(runner=None,pins=tools,sdk_selection=dict(canonical=str(self.sdk),selected=str(self.sdk),pins={str(meta):stat_stamp(meta.stat())}),build_manifest=manifest,build_id=b.digest(b.canonical(manifest)),_unchanged_full=full,_preparation_environment_full=lambda guard:copy.deepcopy(env))
  self.reg=self.new();self.query=patch.object(r.journal,'query',return_value={'schema':1,'complete':True,'code_roots':True,'cursor':1});self.unchanged=patch.object(r.journal,'unchanged',return_value=True);self.query.start();self.unchanged.start()
 def tearDown(self):self.unchanged.stop();self.query.stop();self.tmp.cleanup()
 def quick(self,runner,guard):guard.check();self.context_calls+=1;return dict(os_build='AUTHOR',selection=self.selection)
 def new(self):return r.Registration(self.cache,quick_context=self.quick)
 def save(self):self.assertTrue(self.reg.save(self.chain,self.guard));self.assertGreater(self.checks,0)
 def test_registered_identity_reuses_without_full_probe(self):
  self.save();before=self.checks;new=self.new();got=new.load(self.chain,self.guard);self.assertEqual(got[0],self.chain.build_id);self.assertEqual(self.checks,before);self.assertTrue(new.current(self.chain,self.guard));self.assertEqual(new.stats['reused'],1)
 def test_developer_selection_change_requests_full_probe(self):
  self.save();self.selection='changed-developer';self.assertIsNone(self.new().load(self.chain,self.guard))
 def test_binary_rewrite_with_restored_mtime_is_not_reused(self):
  self.save();p=self.tools['metal'];s=p.stat();p.write_bytes(b'CHANGED metal');os.utime(p,ns=(s.st_atime_ns,s.st_mtime_ns));self.assertIsNone(self.new().load(self.chain,self.guard))
 def test_sdk_metadata_change_without_os_update_is_not_reused(self):
  self.save();self.meta.write_text('{"changed":true}');self.assertIsNone(self.new().load(self.chain,self.guard))
 def test_same_tool_bytes_replaced_inode_is_not_reused(self):
  self.save();p=self.tools['metallib'];q=p.with_name('replacement');q.write_bytes(p.read_bytes());q.chmod(0o600);os.replace(q,p);self.assertIsNone(self.new().load(self.chain,self.guard))
 def test_lost_journal_never_becomes_ready(self):
  self.save()
  with patch.object(r.journal,'unchanged',return_value=False):self.assertIsNone(self.new().load(self.chain,self.guard))
 def test_missing_file_symlink_and_writable_code_not_reused(self):
  self.save();p=self.tools['metal'];p.chmod(0o622);self.assertIsNone(self.new().load(self.chain,self.guard));p.chmod(0o600);p.unlink();self.assertIsNone(self.new().load(self.chain,self.guard));p.symlink_to(self.tools['metallib']);self.assertIsNone(self.new().load(self.chain,self.guard))
 def test_corrupt_receipt_and_removed_pin_are_rejected(self):
  self.save();doc=json.loads(self.reg.path.read_text());doc['body']['toolchain']['build_id']='0'*64;self.reg.path.write_text(json.dumps(doc));self.assertIsNone(self.new().load(self.chain,self.guard))
  self.save();doc=json.loads(self.reg.path.read_text());doc['body']['file_pins'].pop(str(self.meta));doc['sha256']=b.digest(b.canonical(doc['body']));self.reg.path.write_text(json.dumps(doc));self.assertIsNone(self.new().load(self.chain,self.guard))
 def test_unavailable_history_does_not_publish_registration(self):
  with patch.object(r.journal,'query',return_value=None):self.assertFalse(self.reg.save(self.chain,self.guard))
  self.assertFalse(self.reg.path.exists())
 def test_protection_is_not_swallowed_by_optional_registration(self):
  self.save();self.guard.stop=True
  with self.assertRaises(b.Stopped):self.new().load(self.chain,self.guard)
  with self.assertRaises(b.Stopped):self.reg.save(self.chain,self.guard)
 def test_full_tool_validation_failure_is_not_published(self):
  self.chain._unchanged_full=lambda guard:(_ for _ in()).throw(b.Refused('authored actual tool mismatch'))
  with self.assertRaises(b.Refused):self.reg.save(self.chain,self.guard)
  self.assertFalse(self.reg.path.exists())
 def test_original_registration_generations_are_preserved(self):
  self.save();before=set(self.reg.root.glob('*.json'));self.save();self.assertTrue(before<=set(self.reg.root.glob('*.json')))
 def test_readonly_tool_mount_pinned_and_mutable_selection_parent_watched(self):
  tools=str(self.tools['metal'].parent);mounted=dict(path=tools,pin=['authored-live-mount'],fsid=42,flags=1)
  def readonly(name):return copy.deepcopy(mounted)if str(name)==tools else None
  with patch.object(r,'readonly_mount',side_effect=readonly):
   self.save();body=json.loads(self.reg.path.read_text())['body'];self.assertNotIn(tools,body['roots']);self.assertIn(str(Path(tools).parent),body['roots']);self.assertEqual(body['readonly'][tools],mounted);self.assertIsNotNone(self.new().load(self.chain,self.guard));mounted['fsid']=43;self.assertIsNone(self.new().load(self.chain,self.guard))

if __name__=='__main__':unittest.main()
