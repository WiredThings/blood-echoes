import hashlib,json,os,struct,subprocess,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import fex_launch as launch

class Launch(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(prefix='fex-launch-',dir='/private/tmp');self.project=Path(self.temp.name);self.stage=self.project/'stage';self.stage.mkdir()
  self.data=self.project/'state/case';self.out=self.data/'out';self.out.mkdir(parents=True)
  for name in('boot-linked.bin','eboot.elf','patches.bin'):(self.out/name).write_bytes(name.encode())
  self.doc=dict(schema=1,backend='fex',development_only=True,runtime_dependencies={})
  for role,name in(('cpu','bb-game-arm64'),('scanner','bbcpu-scan')):
   (self.stage/name).write_bytes(name.encode());self.doc[role]=dict(path=name,sha256=launch.sha(self.stage/name))
  (self.stage/'FEX-COMPONENT.json').write_text(json.dumps(self.doc));(self.stage/'NATIVE-METAL.json').write_text(json.dumps(dict(game_runtime='arm64',production=False,frozen_runtime={self.doc[role]['path']:self.doc[role]['sha256']for role in('cpu','scanner')})))
  self.env=dict(BB_CPU_BACKEND='fex',BB_DATA_DIR=str(self.data),BB_THD_TOKEN='private-token',BB_QUICKSAVE='1',BB_RESTART_NONCE='original-supervisor');self.arguments=[str(self.out/'boot-linked.bin'),'--patches',str(self.out/'patches.bin')];self.calls=[];self.key='12'*32
 def tearDown(self):self.temp.cleanup()
 def child(self,argv,**kw):
  self.calls.append((argv,kw['env']));self.assertNotIn('BB_THD_TOKEN',kw['env']);env=kw['env']
  if'BB_FEX_EXPORT_ROOT'in env:
   output=Path(env['BB_FEX_EXPORT_ROOT']);output.mkdir()
   (output/'effective.elf').write_bytes(b'authored effective input');(output/'pointers.txt').write_text('0x0\n')
   (output/'effective-input.json').write_text(json.dumps(dict(effective_identity=self.key,resume_entries=[],excluded_code_slots=[])))
   (output/'cpu-cache-environment.json').write_text(json.dumps(dict(key_sha256=self.key)))
   digest='13'*32;file_id=int.from_bytes(bytes.fromhex(digest)[:8],'little')
   manifest=dict(source=str(self.out/'boot-linked.bin'),identity_sha256=digest,file_id=f'{file_id:x}',image_bytes=16384,sections=[[0,8192]],eligible_bytes=8192)
   (output/'cpu-code-manifest.json').write_text(json.dumps(manifest))
  elif Path(argv[0]).name=='bbcpu-scan':
   (Path(argv[2])/'functions.tsv').write_text('address\tsize\n0x0\t16\n0x1000\t32\n')
   (Path(argv[2])/'entries.tsv').write_text('address\tfunction\tkind\n0x8\t0x0\tcontinuation\n0x1010\t0x1000\tdirect-target\n')
  else:
   self.assertIn('BB_FEX_PREPARE_PLAN',env);root=Path(env['BB_FEX_CACHE_DIRECTORY']);(root/'directory.bin').write_bytes(b'authored metadata, not native-execution proof')
   return subprocess.CompletedProcess(argv,0,json.dumps(dict(event='done',backend='fex',guest_executed=False,cached_entries=2))+'\n','')
  return subprocess.CompletedProcess(argv,0,'','')
 def prepare(self):return launch.prepare(self.stage/'bb-game-arm64',self.arguments,self.env,stage=self.stage,project=self.project,runner=self.child)
 def test_cold_then_warm_uses_original_role_and_no_workers(self):
  exe,env,cold=self.prepare();self.assertEqual(exe,str(self.stage/'bb-game-arm64'));self.assertEqual(cold['processes'],dict(export=1,scanner=1,compiler=1))
  self.assertEqual(env['BB_THD_TOKEN'],self.env['BB_THD_TOKEN']);self.assertEqual(env['BB_RESTART_NONCE'],self.env['BB_RESTART_NONCE']);self.assertEqual(env['BB_QUICKSAVE'],'1');self.assertNotIn('BB_RECOMP_LIB',env)
  _,_,warm=self.prepare();self.assertEqual(warm['processes'],dict(export=0,scanner=0,compiler=0));self.assertFalse(warm['effective_exported']);self.assertEqual(len(self.calls),3)
 def test_patch_change_and_switch_back_reuses_old_generation(self):
  _,_,first=self.prepare();patch=self.out/'patches.bin';before=patch.read_bytes();patch.write_bytes(b'changed actual code');self.key='14'*32
  _,_,second=self.prepare();self.assertNotEqual(first['generation'],second['generation'])
  patch.write_bytes(before);self.key='12'*32;_,_,again=self.prepare();self.assertEqual(again['generation'],first['generation']);self.assertEqual(again['processes'],dict(export=0,scanner=0,compiler=0))
 def test_ui_and_unrelated_settings_do_not_recompile(self):
  self.prepare();self.env.update(BB_SHOW_FPS='0',BB_VOLUME='0',BB_UI_LANGUAGE='ja');_,_,again=self.prepare();self.assertEqual(again['processes'],dict(export=0,scanner=0,compiler=0))
 def test_real_codegen_environment_invalidates_registration(self):
  self.prepare();self.env['BB_ARM_HW_TSO']='1';_,_,again=self.prepare();self.assertTrue(again['effective_exported'])
 def test_repair_request_runs_only_native_incremental_producer(self):
  _,_,first=self.prepare();root=Path(first['generation']);(root/'repair-requests.bin').write_bytes(struct.pack('<Q',0));_,_,again=self.prepare();self.assertEqual(again['processes'],dict(export=0,scanner=0,compiler=1));self.assertFalse((root/'repair-requests.bin').exists());self.assertTrue(list(root.glob('repair-processed-*')))
 def test_corrupt_index_does_not_delete_old_products(self):
  _,_,first=self.prepare();root=Path(first['generation']);(root/'unrelated-cache').write_bytes(b'keep');(root/'directory.bin').write_bytes(b'damaged');_,_,again=self.prepare();self.assertEqual(again['processes']['compiler'],1);self.assertEqual((root/'unrelated-cache').read_bytes(),b'keep')
 def test_incomplete_or_corrupt_scan_is_repaired_locally(self):
  _,_,first=self.prepare();root=Path(first['generation']);(root/'scan/functions.tsv').write_text('broken');_,_,again=self.prepare();self.assertEqual(again['processes']['scanner'],1);self.assertEqual(again['processes']['compiler'],0);self.assertTrue(list(root.glob('scan-previous-*')))
 def test_reject_other_backend_unfrozen_role_and_other_executable(self):
  self.env['BB_CPU_BACKEND']='bbcpu'
  with self.assertRaises(ValueError):self.prepare()
  self.env['BB_CPU_BACKEND']='fex';(self.stage/'bbcpu-scan').write_bytes(b'changed')
  with self.assertRaises(ValueError):self.prepare()

if __name__=='__main__':unittest.main()
