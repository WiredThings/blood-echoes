"""Admitted formal roles on ordinary local paths, no native game execution."""
from pathlib import Path
import copy,json,os,sys,unittest
from test_formal_runtime_profile import Formal
from test_fex_launch import Launch
import fex_launch as launch
import fex_component as kit
import prepare_local_aot_stage as stage_tool

class FormalFex(Formal):
 def setUp(self):
  super().setUp()
  scanner=self.stage/kit.SCANNER;scanner.parent.mkdir(parents=True);scanner.write_bytes(b'author decoder')
  deps={}
  self.doc=kit.declaration(self.stage,deps)
  (self.stage/'FEX-COMPONENT.json').write_text(json.dumps(self.doc))
  self.native.update(fex_preparation='auto',fex_component_sha256=stage_tool.sha(self.stage/'FEX-COMPONENT.json'))
  self.native['frozen_runtime'][kit.SCANNER]=stage_tool.sha(scanner);self.write_manifests()
  self.out=self.data/'out';(self.out/'eboot.elf').write_bytes(b'author original ELF')
  self.calls=[];self.key='12'*32
  self.env.update(BB_CPU_BACKEND='fex',BB_THD_TOKEN='private-token')
 child=Launch.child
 def prepare(self,**kw):
  scope=self.scope()
  return launch.prepare(self.stage/kit.CPU[0],self.args,self.env,stage=self.stage,project=scope.work_root,runner=self.child,runtime_profile=scope,**kw)
 def test_formal_preparation_uses_existing_roles_and_warm_reuses(self):
  _,env,cold=self.prepare();self.assertEqual(cold['processes'],dict(export=1,scanner=1,compiler=1))
  self.assertTrue(Path(cold['generation']).is_relative_to(self.data/'fex-preparation'))
  _,_,warm=self.prepare();self.assertEqual(warm['processes'],dict(export=0,scanner=0,compiler=0))
  self.assertEqual(env['BB_THD_TOKEN'],'private-token');self.assertEqual((self.user/'keep').read_bytes(),b'author progress never read by preparation')
 def test_f3_reuses_index_then_ordinary_handles_new_capture(self):
  _,_,first=self.prepare();root=Path(first['generation'])
  (root/'cpu-code-777.map').write_bytes(b'not a real map; ordinary captures reject explicitly')
  _,_,f3=self.prepare(reuse_session=True);self.assertTrue(f3['capture_deferred']);self.assertEqual(f3['processes'],dict(export=0,scanner=0,compiler=0))
  _,_,normal=self.prepare();self.assertFalse(normal['capture_deferred']);self.assertEqual(normal['processes']['compiler'],0)
 def test_f3_does_not_bypass_index_corruption_or_repair(self):
  _,_,first=self.prepare();root=Path(first['generation']);(root/'directory.bin').write_bytes(b'corrupt')
  _,_,repaired=self.prepare(reuse_session=True);self.assertEqual(repaired['processes']['compiler'],1)
  (root/'repair-requests.bin').write_bytes(b'12345678');_,_,again=self.prepare(reuse_session=True);self.assertEqual(again['processes']['compiler'],1)
 def test_online_input_uses_actual_network_module_and_switch_back(self):
  self.prepare();self.env.update(BB_ONLINE='1',BB_ARM_ALLOW_NETWORK='1',BB_NET_MODULE=str(self.stage/'bin/gpu-net/libbbnet.dylib'))
  self.key='14'*32;_,_,online=self.prepare();self.assertTrue(online['effective_exported'])
  self.env['BB_ONLINE']='0';self.key='12'*32;_,_,offline=self.prepare();self.assertFalse(offline['effective_exported'])
 def test_online_default_gate_matches_native_policy_and_records_module(self):
  self.env.update(BB_ONLINE='1',BB_NET_MODULE=str(self.stage/'bin/gpu-net/libbbnet.dylib'))
  self.env.pop('BB_ARM_ALLOW_NETWORK',None);self.prepare()
  registrations=list((self.data/'fex-preparation/effective').glob('*.json'))
  docs=[json.loads(path.read_text())for path in registrations if path.name!='input-register.json']
  self.assertEqual(len(docs),1);self.assertTrue(docs[0]['context']['network_enabled'])
  self.assertIn(str(self.stage/'bin/gpu-net/libbbnet.dylib'),[row[0]for row in docs[0]['inputs']])
  self.env['BB_ARM_ALLOW_NETWORK']='0';_,_,disabled=self.prepare();self.assertTrue(disabled['effective_exported'])
 def test_actual_formal_component_cannot_use_private_scope(self):
  with self.assertRaises(ValueError):launch.component(self.stage)
 def test_descriptor_tamper_and_unknown_role_rejected(self):
  self.doc['scanner']['path']='bin/other';(self.stage/'FEX-COMPONENT.json').write_text(json.dumps(self.doc))
  self.native['fex_component_sha256']=stage_tool.sha(self.stage/'FEX-COMPONENT.json');self.write_manifests()
  with self.assertRaisesRegex(ValueError,'role path mismatch'):self.scope()
 def test_formal_effective_directory_link_is_refused_before_write(self):
  root=self.data/'fex-preparation';root.mkdir();(root/'effective').symlink_to(self.user,target_is_directory=True)
  with self.assertRaises(ValueError):self.prepare()
  self.assertEqual(list(self.user.iterdir()),[self.user/'keep'])
 def test_formal_preparation_progress_overlap_refused(self):
  self.args[self.args.index('--user')+1]=str(self.data/'fex-preparation')
  with self.assertRaises(ValueError):self.prepare()

if __name__=='__main__':unittest.main()
