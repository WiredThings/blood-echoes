"""Authored outer app/data roles and captured child env; no native/GPU launch."""
from pathlib import Path
from types import SimpleNamespace
import copy,hashlib,json,os,plistlib,shutil,subprocess,sys,tempfile,unittest
from unittest.mock import patch
D=Path(__file__).resolve().parents[1];sys.path.insert(0,str(D/'tools'))
import launcher_runtime_profile as profile
import prepare_local_aot_stage as stage_tool
import launcher_local_aot as local
import launcher_game_modules as modules
import game_pso_component as component
import game_pso_batch as batch
import prepare_module_metadata as metadata
from game_pso_owned_audit import checked_bytes

class StopBeforeSpawn(RuntimeError):pass

class Formal(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve()
  self.app=self.root/'Blood Echoes.app';self.stage=self.app/'Contents/Resources/bbport';self.stage.mkdir(parents=True)
  (self.app/'Contents/Info.plist').write_bytes(plistlib.dumps(dict(CFBundleIdentifier=profile.FORMAL_ID)))
  self.roles=tuple(profile.ROLES)+('bin/Bloodborne Game.app/Contents/MacOS/bb-probe-metal','bin/gpu-net/libbbnet.dylib')
  for role in self.roles:
   p=self.stage/role;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(('author role '+role).encode())
  tools=self.stage/'native-runtime-tools';tools.mkdir()
  for name in stage_tool.TOOLS:shutil.copyfile(D/'tools'/name,tools/name)
  support={}
  for relative,original in stage_tool.SUPPORT.items():
   p=self.stage/relative;p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(D.parents[1]/original,p);support[relative]=stage_tool.sha(p)
  from test_prepare_local_aot_stage import ORIGINAL_PATHS
  original={}
  for rel in ORIGINAL_PATHS:
   p=self.stage/rel;p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(D.parents[1]/rel,p);original[rel]=stage_tool.sha(p)
  (self.stage/'SOURCE-PARITY.json').write_text(json.dumps(dict(effective_sources=original)))
  startup=self.stage/'tools/macos/local_metal_startup.py';startup.write_text('# authored bootstrap role\n')
  self.native=dict(schema=1,game_runtime='fex-arm64',production=True,frozen_runtime={r:stage_tool.sha(self.stage/r)for r in self.roles})
  self.manifest=dict(schema=1,production=True,runtime_tools={n:stage_tool.sha(tools/n)for n in stage_tool.TOOLS},runtime_support=support,startup_bootstrap_sha256=stage_tool.sha(startup))
  self.write_manifests()
  self.data=self.root/'original-data';(self.data/'out').mkdir(parents=True);(self.data/'bbport.ini').write_text('author config')
  for n in('boot-linked.bin','content.bin','patches.bin'):(self.data/'out'/n).write_bytes(('author '+n).encode())
  self.game=self.root/'game';self.game.mkdir();self.user=self.root/'user';self.user.mkdir();(self.user/'keep').write_bytes(b'author progress never read by preparation')
  self.base=self.data/'gpu-cache/metal1';self.base.mkdir(parents=True);(self.base/'old-cache').write_bytes(b'keep old cache')
  self.env=dict(BB_CONFIG=str(self.data/'bbport.ini'),BB_DATA_DIR=str(self.data),BB_GAME_DIR=str(self.game),BB_GPU_USER_DIR=str(self.base),BB_GPU_PROCESS=str(self.stage/'bin/Bloodborne Metal.app/Contents/MacOS/bb-gpu'),BB_RENDERER='metal',BB_ARM_LAUNCHER_COMPAT='1',BB_ONLINE='0',BB_ARM_SESSION_NONCE='1'*32,BB_ARM_RESTART_NONCE='2'*32)
  self.args=[str(self.data/'out/boot-linked.bin'),'--content-profile',str(self.data/'out/content.bin'),'--patches',str(self.data/'out/patches.bin'),'--app0',str(self.game),'--user',str(self.user)]
 def tearDown(self):self.temp.cleanup()
 def write_manifests(self):
  (self.stage/'NATIVE-METAL.json').write_text(json.dumps(self.native));(self.stage/'ARM64-LOCAL-PREPARATION.json').write_text(json.dumps(self.manifest))
 def scope(self):return profile.derive(self.stage,self.args,self.env)
 def test_eight_actual_roles_select_original_data_and_additive_namespace(self):
  original=copy.deepcopy(self.env);scope=self.scope();env=scope.environment(self.env)
  self.assertEqual(scope.work_root,self.data/'arm64-runtime-work');self.assertEqual(scope.gpu_root,self.base/'arm64-native-v1')
  self.assertEqual(env['BB_ARM_GPU_BASE_DIR'],str(self.base));self.assertEqual(env['BB_GPU_USER_DIR'],str(scope.gpu_root))
  self.assertFalse((scope.work_root/'state').exists());self.assertEqual(self.env,original)
  self.assertEqual(scope.cpu(self.stage/'bin/Bloodborne Game.app/Contents/MacOS/bb-probe-metal'),self.stage/'bin/Bloodborne Game.app/Contents/MacOS/bb-probe-metal')
  self.assertEqual((self.base/'old-cache').read_bytes(),b'keep old cache');self.assertEqual(list(self.user.iterdir()),[self.user/'keep'])
 def test_info_or_production_boolean_cannot_substitute_for_formal_closure(self):
  self.native['production']=False;self.write_manifests()
  with self.assertRaises(ValueError):self.scope()
  self.native['production']=True;self.write_manifests();(self.stage/self.roles[0]).write_bytes(b'bad cpu')
  with self.assertRaisesRegex(ValueError,'identity changed'):self.scope()
 def test_unknown_extra_runtime_role_and_malformed_digest_refused(self):
  extra=self.stage/'bin/undeclared';extra.write_bytes(b'author extra')
  self.native['frozen_runtime']['bin/undeclared']=stage_tool.sha(extra);self.write_manifests()
  with self.assertRaisesRegex(ValueError,'declared formal runtime roles'):self.scope()
  del self.native['frozen_runtime']['bin/undeclared'];self.native['frozen_runtime'][self.roles[0]]='not-a-sha';self.write_manifests()
  with self.assertRaisesRegex(ValueError,'digest shape'):self.scope()
 def test_local_profile_missing_tool_parser_or_bootstrap_is_refused(self):
  for relative in('native-runtime-tools/launcher_runtime_profile.py','vendor/blood_echoes/loader/prepare.py','tools/macos/local_metal_startup.py'):
   p=self.stage/relative;old=p.read_bytes();p.write_bytes(b'changed')
   with self.subTest(relative=relative),self.assertRaises(ValueError):self.scope()
   p.write_bytes(old)
 def test_qa_outer_id_is_not_promoted_by_production_metadata(self):
  (self.app/'Contents/Info.plist').write_bytes(plistlib.dumps(dict(CFBundleIdentifier='io.github.bbport.optimized.options-qa.author')))
  self.assertIsNone(self.scope())
 def test_off_still_admits_code_and_selects_namespace_without_services(self):
  env,report=local.prepare_launch(str(self.stage/'bin/bb-probe-metal'),self.args,{**self.env,'BB_MTL_LOCAL_AOT_MODE':'off'},execution_root=self.stage,project='/does-not-exist')
  self.assertEqual(report['status'],'off');self.assertEqual(env['BB_GPU_USER_DIR'],str(self.base/'arm64-native-v1'))
  (self.stage/self.roles[0]).write_bytes(b'bad')
  with self.assertRaises(ValueError):local.prepare_launch(str(self.stage/'bin/bb-probe-metal'),self.args,{**self.env,'BB_MTL_LOCAL_AOT_MODE':'off'},execution_root=self.stage,project='/does-not-exist')
 def test_wrong_config_out_app0_and_user_work_overlap_rejected(self):
  bad={**self.env,'BB_CONFIG':str(self.data/'out/content.bin')}
  with self.assertRaises(ValueError):profile.derive(self.stage,self.args,bad)
  for flag,value in[('--patches',str(self.stage/'NATIVE-METAL.json')),('--app0',str(self.stage)),('--user',str(self.data/'arm64-runtime-work/saves'))]:
   args=self.args[:];args[args.index(flag)+1]=value
   with self.subTest(flag=flag),self.assertRaises(ValueError):profile.derive(self.stage,args,self.env)
 def test_scope_cache_link_and_old_namespace_reads_refused(self):
  scope=self.scope();p=scope.gpu_root/'owned';p.write_bytes(b'author');link=scope.gpu_root/'linked';link.symlink_to(p.name)
  self.assertEqual(scope.cache(p),p)
  with self.assertRaises(ValueError):scope.cache(link)
  with self.assertRaises(ValueError):scope.cache(self.base/'old-cache')
 def test_group_world_writable_code_and_foreign_data_owner_refused(self):
  p=self.stage/self.roles[0];p.chmod(0o666)
  with self.assertRaises(ValueError):self.scope()
  p.chmod(0o644);scope=self.scope()
  with patch.object(profile.os,'getuid',return_value=os.getuid()+1),self.assertRaises(ValueError):scope.gpu(scope.gpu_root)
 def test_root_code_owner_is_explicit_and_does_not_authorize_data(self):
  original=os.fstat
  def root_owned(fd):
   s=original(fd);fields={n:getattr(s,n)for n in('st_mode','st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns','st_nlink')};return SimpleNamespace(**fields,st_uid=0)
  with patch.object(os,'fstat',side_effect=root_owned):
   self.assertIsNotNone(profile.bundle(self.stage))
   with self.assertRaises(ValueError):checked_bytes(self.data/'bbport.ini')
   self.assertEqual(checked_bytes(self.stage/'NATIVE-METAL.json',code_owners=(0,os.getuid()))[0],(self.stage/'NATIVE-METAL.json').read_bytes())
 def test_all_scalar_capabilities_use_fresh_work_root_and_no_home_inheritance(self):
  scope=self.scope();seen=[]
  def check_env(env):
   p=Path(env['BB_GPU_USER_DIR']);self.assertEqual(env['HOME'],str(p));self.assertEqual(env['TMPDIR'],str(p));self.assertEqual(p.parent,scope.work_root/'capabilities')
   self.assertEqual(p.stat().st_mode&0o777,0o700);self.assertEqual(env['BB_DATA_DIR'],str(self.data));self.assertNotIn('BB_THD_TOKEN',env);self.assertNotEqual(p,scope.gpu_root);seen.append(p)
  def fixed(*args,**kwargs):check_env(kwargs['env']);return subprocess.CompletedProcess([],0,b'FIXED_HELPER_CAPABILITY 2\n')
  component.fixed_helper_capability(self.stage/'bin/game-pso-preparer',scope.work_root,runner=fixed,runtime_profile=scope)
  def page(*args,**kwargs):check_env(kwargs['env']);return subprocess.CompletedProcess([],0,('GAME_PSO_BATCH_CAPABILITY '+json.dumps(batch.CAPABILITY)+'\n').encode())
  self.assertTrue(batch.probe_capability(self.stage/'bin/game-pso-preparer',scope.work_root,lambda:None,runner=page,runtime_profile=scope))
  def module(*args,**kwargs):
   check_env(kwargs['env']);return subprocess.CompletedProcess([],0,('GAME_MODULE_CAPABILITY '+json.dumps(dict(schema=1,cpu_only=True,automatic_input_root=modules.ROOT_POLICY,requires_nonce_and_cohort=True,diagnostic_only=False,packet_schema=1))) .encode())
  modules._capability(dict(module_consumer=self.stage/'bin/game-module-preparer'),scope.gpu_root,runner=module,runtime_profile=scope)
  self.assertEqual(len(seen),3);self.assertTrue(all(not p.exists()for p in seen))
 def test_normal_module_worker_retains_actual_gpu_base_before_spawn(self):
  scope=self.scope();case=scope.work_root/'module';case.mkdir();packet=case/'packet';packet.write_text('{}');seen=[]
  def stop(*args,**kwargs):seen.append(kwargs['env']);raise StopBeforeSpawn()
  with patch.object(modules.subprocess,'Popen',side_effect=stop),self.assertRaises(StopBeforeSpawn):
   modules.run_module(dict(_runtime_profile=scope,module_consumer=self.stage/'bin/game-module-preparer'),packet,case/'output',case,scope.gpu_root,'a'*64,'b'*64,lambda:None,{})
  self.assertEqual(seen[0]['BB_GPU_USER_DIR'],str(scope.gpu_root));self.assertEqual(seen[0]['BB_ARM_GPU_BASE_DIR'],str(self.base));self.assertEqual(seen[0]['BB_DATA_DIR'],str(self.data))
 def test_original_local_flow_passes_scope_and_preserves_original_user_arguments(self):
  scope=self.scope();seen=[];original=self.args[:]
  def archive(**kw):return {},dict(token='a'*64)
  def libraries(**kw):seen.append(('local',kw));return {},dict(status='prepared')
  def helpers(**kw):seen.append(('fixed',kw));return {},dict(status='completed')
  def game(**kw):seen.append(('game',kw));return dict(status='empty')
  env,_=local.prepare_launch(str(self.stage/'bin/bb-probe-metal'),self.args,self.env,project='/missing-private-P',execution_root=self.stage,
      archive_prepare=archive,local_prepare=libraries,fixed_helper_prepare=helpers,game_prepare=game,observe=lambda:dict(locked=False,pressure=1,swap_mib=0),runtime_profile=scope)
  self.assertEqual([x[0]for x in seen],['local','fixed','game'])
  self.assertTrue(all(kw['runtime_profile']is scope for _,kw in seen));self.assertEqual(self.args,original)
  self.assertEqual(env['BB_GPU_USER_DIR'],str(scope.gpu_root));self.assertFalse((scope.work_root/'state').exists())
 def test_normal_fixed_worker_retains_base_namespace_and_data_before_injected_call(self):
  import launcher_fixed_helpers as fixed
  scope=self.scope();seen=[];c=dict(fixed_helper_seed_schema=2,library_sha256='a'*64,worker=self.stage/'bin/game-pso-preparer')
  def stop(command,env,*args):seen.append(env);raise StopBeforeSpawn()
  with patch('warm_archive_manifest.resolve_gpu_identity',return_value=dict(library_sha256='a'*64)),patch('launcher_game_pso.title_id',return_value='CUSA03173'),patch('qa_build_guard.require_no_games',return_value=None):
   with self.assertRaises(StopBeforeSpawn):fixed.prepare(project=scope.work_root,execution_root=self.stage,arguments=self.args,environment=scope.environment(self.env),cohort='b'*64,options={},observe=lambda:dict(locked=False,pressure=1,swap_mib=0),component_loader=lambda *a,**k:c,toolchain_probe=lambda:True,runner=stop,runtime_profile=scope)
  self.assertEqual(seen[0]['BB_GPU_USER_DIR'],str(scope.gpu_root));self.assertEqual(seen[0]['BB_ARM_GPU_BASE_DIR'],str(self.base));self.assertEqual(seen[0]['BB_DATA_DIR'],str(self.data))
 def test_normal_pso_worker_retains_namespace_after_cleanenv(self):
  import prepare_game_psos as psos
  from qa_memory_policy import MemoryPolicy
  scope=self.scope();run=scope.work_root/'pso';run.mkdir();artifact=scope.gpu_root/'cache/CUSA03173/metal';artifact.mkdir(parents=True)
  manifest=scope.work_root/'candidates.jsonl';manifest.write_text('author');layout=self.stage/'native-runtime-inputs/layout.json';layout.parent.mkdir();layout.write_text('{}')
  observed=[];observer=SimpleNamespace(system=lambda:dict(locked=False,pressure=1,swap_mib=0),close=lambda:None)
  services=dict(require_no_games=lambda:None,observer=observer,memory_policy=MemoryPolicy,resolve_identity=lambda *a,**k:dict(library_sha256='a'*64),load_layout=lambda *a,**k:{},audit_stage=None,audit_key_profile=None,local_prepare=lambda **kwargs:({},dict(status='prepared')),builder_stopped=RuntimeError)
  config=dict(project=scope.work_root,worker=self.stage/'bin/game-pso-preparer',library_sha256='a'*64,layout=layout,layout_sha256='b'*64,source_root=layout.parent,cohort='c'*64,manifest=manifest,gpu_root=scope.gpu_root,run_root=run,artifact_cache_dir=artifact,runtime_profile=scope)
  def freeze(unit,layout,packetdir,*args,**kwargs):(packetdir/'packet.json').write_text('{}');return {},{}
  def run_manifest(*args,**kwargs):kwargs['prepare_phase']('prepare',lambda:None);return kwargs['execute']({},'prepare',False,1)
  def stop(*args,**kwargs):observed.append(kwargs['env']);raise StopBeforeSpawn()
  with patch('game_pso_queue.run_manifest',side_effect=run_manifest),patch.object(psos,'freeze',side_effect=freeze),patch.object(psos.subprocess,'Popen',side_effect=stop),self.assertRaises(StopBeforeSpawn):psos.prepare(config,services=services)
  self.assertEqual(observed[0]['BB_GPU_USER_DIR'],str(scope.gpu_root));self.assertEqual(observed[0]['BB_ARM_GPU_BASE_DIR'],str(self.base));self.assertEqual(observed[0]['BB_DATA_DIR'],str(self.data))
 def test_metadata_uses_actual_bundled_parser_and_separate_session_output(self):
  scope=self.scope();nonce=self.env['BB_ARM_SESSION_NONCE'];out=self.data/'out/runtime-sessions'/nonce/'.arm-module-info.bin';out.parent.mkdir(parents=True)
  exe=self.stage/'bin/bb-probe-metal'
  loader=metadata.packaged_loader(self.stage,exe,script=self.stage/'native-runtime-tools/prepare_module_metadata.py')
  self.assertEqual(loader,self.stage/'vendor/blood_echoes/loader')
  inputs=metadata.formal_inputs(self.stage,exe,self.args[0],self.game,out,self.env)
  self.assertEqual(inputs.out,out);self.assertFalse(inputs.out.is_relative_to(scope.work_root))
  with self.assertRaises(ValueError):metadata.formal_inputs(self.stage,exe,self.args[0],self.game,scope.work_root/'.arm-module-info.bin',self.env)
 def test_formal_component_requires_scope_and_exact_roles_before_native_identity(self):
  scope=self.scope();layout=self.stage/'native-runtime-inputs/layout.json';layout.parent.mkdir();layout.write_text('{}');source=layout.parent/'layout-source';source.mkdir()
  c=dict(schema=1,kind='blood-echoes-game-pso-component',enabled=True,production=True,worker='bin/game-pso-preparer',module_consumer='bin/game-module-preparer',library='bin/gpu-metal-arm64/libbbgpu.dylib',layout='native-runtime-inputs/layout.json',source_root='native-runtime-inputs/layout-source',coherent_renderer=True,diagnostic_only=False)
  for key in('worker','module_consumer','library','layout'):c[key+'_sha256']=stage_tool.sha(self.stage/c[key])
  path=self.stage/'GAME-PSO-COMPONENT.json';path.write_text(json.dumps(c))
  with self.assertRaises(ValueError):component.load(self.stage)
  actual=dict(library=str(self.stage/c['library']),library_sha256=c['library_sha256'])
  with patch('warm_archive_manifest.resolve_gpu_identity',return_value=actual),patch.object(component,'load_canonical_layout',return_value={}):
   self.assertIs(component.load(self.stage,runtime_profile=scope)['production'],True)
   c['worker']='bin/bb-probe-metal';path.write_text(json.dumps(c))
   with self.assertRaisesRegex(ValueError,'role mismatch'):component.load(self.stage,runtime_profile=scope)
 def test_formal_id_inside_private_apps_is_still_formal_not_qa_fallback(self):
  private=self.root/'developer-project';(private/'apps').mkdir(parents=True);target=private/'apps/Formal Author.app';shutil.move(self.app,target)
  self.app=target;self.stage=target/'Contents/Resources/bbport';exe=self.stage/'bin/bb-probe-metal'
  with patch.object(metadata,'PROJECT',private):
   self.assertEqual(metadata.packaged_loader(self.stage,exe,script=self.stage/'native-runtime-tools/prepare_module_metadata.py'),self.stage/'vendor/blood_echoes/loader')
 def test_formal_bootstrap_without_private_project_uses_only_frozen_bundle_tools(self):
  fake=self.stage/'native-runtime-tools/launcher_local_aot.py'
  fake.write_text('''from pathlib import Path
import json,os
def cli(*,project,execution_root,runtime_profile):
 assert project==runtime_profile.work_root and not (project/'state').exists()
 Path(os.environ['AUTHOR_BOOTSTRAP_OUTPUT']).write_text(json.dumps(dict(project=str(project),gpu=runtime_profile.environment(os.environ)['BB_GPU_USER_DIR'],code=str(Path(__file__).parent))))
''')
  self.manifest['runtime_tools'][fake.name]=stage_tool.sha(fake)
  startup=self.stage/'tools/macos/local_metal_startup.py';startup.write_text(stage_tool.bootstrap(self.root/'missing-private-project'))
  self.manifest['startup_bootstrap_sha256']=stage_tool.sha(startup);self.write_manifests()
  output=self.root/'bootstrap-observed.json';env={**os.environ,**self.env,'AUTHOR_BOOTSTRAP_OUTPUT':str(output)}
  result=subprocess.run([sys.executable,str(startup),str(self.stage/'bin/bb-probe-metal'),*self.args],env=env,capture_output=True,text=True,timeout=10)
  self.assertEqual(result.returncode,0,result.stderr)
  seen=json.loads(output.read_text());self.assertEqual(seen['project'],str(self.data/'arm64-runtime-work'))
  self.assertEqual(seen['gpu'],str(self.base/'arm64-native-v1'));self.assertEqual(seen['code'],str(self.stage/'native-runtime-tools'))
  self.assertFalse((self.root/'missing-private-project').exists())

if __name__=='__main__':unittest.main(verbosity=2)
