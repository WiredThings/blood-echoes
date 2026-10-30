from pathlib import Path
import importlib.util,json,os,shutil,subprocess,sys,tempfile,unittest
E=Path(__file__).resolve().parents[2]
TOOLS=Path(__file__).resolve().parents[1]/'tools'
spec=importlib.util.spec_from_file_location('stage_prepare',TOOLS/'prepare_local_aot_stage.py')
stage_prepare=importlib.util.module_from_spec(spec);spec.loader.exec_module(stage_prepare)
# Authored sixteen-script fixture: no local game/runtime installation required.
ORIGINAL_PATHS=('run.sh','tools/macos/run_optimized.sh','tools/macos/supervise_restart.sh',
 'tools/macos/quicksave.py','patches/Bloodborne.xml','scripts/link_libc.py',
 'scripts/chinese_translation.py','scripts/online.py','scripts/content_profile.py',
 'scripts/link_modules.py','scripts/patches.py','scripts/game_check.py','scripts/mods.py',
 'scripts/prepare.py','scripts/ultrawide_ui.py','tools/macos/run.sh')
class StageTests(unittest.TestCase):
 def test_real_runtime_imports_are_complete(self):
  stage_prepare.validate_import_closure(TOOLS)

 def setUp(self):
  self.temp=Path(tempfile.mkdtemp(prefix='package-author-',dir=E))
  self.project=self.temp/'Shared runtime';self.project.mkdir();(self.project/'state').mkdir()
  self.original=self.project/'launcher-component-stage';self.original.mkdir()
  sources={}
  for rel in ORIGINAL_PATHS:
   f=self.original/rel;f.parent.mkdir(parents=True,exist_ok=True)
   f.write_text('#!/bin/bash\nprobe_args=(author)\nexec author\n'if rel=='run.sh'else'# authored original '+rel+'\n')
   sources[rel]=stage_prepare.sha(f)
  (self.original/'SOURCE-PARITY.json').write_text(json.dumps(dict(original_sources=sources)))
  for name in ('.packaged','NATIVE-METAL.json'):(self.original/name).write_text('{}')
  for name in ('bin','python','deps-metal-arm64'):(self.original/name).mkdir()
  (self.original/'savedata').mkdir();(self.original/'savedata/never-copy').write_text('authored excluded data')
  self.repository=self.temp/'repository';self.repository.mkdir()
  old=(self.original/'run.sh').read_text();(self.repository/'run.sh').write_text(old.replace('exec author\n',stage_prepare.HOOK+'exec author\n'))
  dst=self.repository/'tools/macos/run_optimized.sh';dst.parent.mkdir(parents=True);dst.write_text((self.original/'tools/macos/run_optimized.sh').read_text())
  self.runtime=self.temp/'runtime-tools';self.runtime.mkdir()
  for name in stage_prepare.TOOLS:(self.runtime/name).write_text('# authored isolated dependency\n')
  (self.runtime/'prepare_game_psos.py').write_text('def prepare(config,*,services=None):pass\n')
  (self.runtime/'local_aot_policy.py').write_text('from pathlib import Path\nP=Path(__file__).parents[1]\n')
  (self.runtime/'local_metal_builder.py').write_text('from pathlib import Path\nPROJECT=Path(__file__).parents[1]\n')
  (self.runtime/'launcher_local_aot.py').write_text('''from pathlib import Path
def main(*,project=None,execution_root=None):pass
def cli(*,project=None,execution_root=None):
 import os,json,local_metal_builder,local_aot_policy
 assert local_metal_builder.PROJECT==project and local_aot_policy.P==project
 Path(os.environ['AUTHOR_STAGE_OUTPUT']).write_text(json.dumps(dict(project=str(project),execution_root=str(execution_root),tool_dir=str(Path(__file__).parent))))
''')
 def tearDown(self):
  # Only this test's newly created authored tree, never a live runtime directory.
  shutil.rmtree(self.temp)
 def build(self):return stage_prepare.build_stage(project=self.project,repository=self.repository,runtime_tools=self.runtime)
 def test_preserves_old_sixteen_and_only_changes_new_hook(self):
  before={str(p.relative_to(self.original)):p.read_bytes()for p in self.original.rglob('*')if p.is_file()}
  result=self.build();new=Path(result['stage'])
  self.assertEqual(before,{str(p.relative_to(self.original)):p.read_bytes()for p in self.original.rglob('*')if p.is_file()})
  self.assertFalse((new/'savedata').exists());self.assertEqual(result['manifest']['unchanged_original_scripts'],15)
  for rel,digest in result['parity']['effective_sources'].items():self.assertEqual(stage_prepare.sha(new/rel),digest)
 def test_external_relative_dependency_still_resolves_after_new_level(self):
  external=self.project/'host-python';external.mkdir();(external/'python3').write_text('author host')
  (self.original/'python').rmdir();(self.original/'python').symlink_to('../host-python',target_is_directory=True)
  stage=Path(self.build()['stage'])
  self.assertEqual((stage/'python').resolve(strict=True),external)
  self.assertEqual(os.readlink(self.original/'python'),'../host-python')
 def test_nested_external_and_internal_links_keep_their_targets(self):
  external=self.project/'host-lib';external.write_text('host library')
  (self.original/'bin/native').write_text('author binary')
  (self.original/'bin/external').symlink_to('../../host-lib')
  (self.original/'bin/internal').symlink_to('native')
  stage=Path(self.build()['stage'])
  self.assertEqual((stage/'bin/external').resolve(strict=True),external)
  self.assertEqual((stage/'bin/internal').resolve(strict=True),stage/'bin/native')
 def test_missing_transitive_dependency_refused_before_stage(self):
  (self.runtime/'gpu_candidate_identity.py').unlink()
  with self.assertRaises(ValueError):self.build()
  self.assertFalse((self.project/'launcher-component-stages').exists())
 def test_new_local_import_cannot_escape_frozen_tool_closure(self):
  (self.runtime/'not_frozen.py').write_text('raise AssertionError("never execute")\n')
  with(self.runtime/'launcher_local_aot.py').open('a')as stream:stream.write('\ndef future_path():\n import not_frozen\n')
  with self.assertRaisesRegex(ValueError,'local dependency is not frozen'):self.build()
  self.assertFalse((self.project/'launcher-component-stages').exists())
 def test_external_tool_import_is_not_a_frozen_closure(self):
  with(self.runtime/'local_metal_builder.py').open('a')as f:f.write("\nsys.path.insert(0,str(PROJECT/'tools'))\n")
  with self.assertRaises(ValueError):self.build()
 def test_unrelated_original_script_change_is_refused(self):
  (self.original/'scripts/prepare.py').write_text('changed')
  with self.assertRaises(ValueError):self.build()
 def test_unrelated_new_run_change_is_refused(self):
  with(self.repository/'run.sh').open('a')as f:f.write('unexpected change\n')
  with self.assertRaises(ValueError):self.build()
 def test_only_exact_fex_log_revision_is_admitted(self):
  rel='tools/macos/run_optimized.sh';old='# author wrapper\n'+stage_prepare.LEGACY_RENDER_LOG
  (self.original/rel).write_text(old)
  parity=json.loads((self.original/'SOURCE-PARITY.json').read_text());parity['original_sources'][rel]=stage_prepare.sha(self.original/rel)
  (self.original/'SOURCE-PARITY.json').write_text(json.dumps(parity))
  (self.repository/rel).write_text(stage_prepare.runtime_log_revision(old))
  result=self.build();self.assertEqual(result['manifest']['unchanged_original_scripts'],14)
  self.assertTrue(result['parity']['runtime_log_only_correction'])
  self.assertEqual((self.original/rel).read_text(),old)
 def test_unrelated_wrapper_change_is_refused(self):
  with(self.repository/'tools/macos/run_optimized.sh').open('a')as f:f.write('export BB_FPS=30\n')
  with self.assertRaisesRegex(ValueError,'exact FEX runtime log'):self.build()
 def invoke(self,stage):
  output=self.temp/'observed.json';env=dict(os.environ,AUTHOR_STAGE_OUTPUT=str(output))
  r=subprocess.run([sys.executable,str(stage/'tools/macos/local_metal_startup.py')],env=env,capture_output=True,text=True,timeout=5)
  return r,output
 def test_bootstrap_binds_data_project_and_frozen_code_separately(self):
  stage=Path(self.build()['stage']);r,output=self.invoke(stage);self.assertEqual(r.returncode,0,r.stderr)
  seen=json.loads(output.read_text());self.assertEqual(seen['project'],str(self.project));self.assertEqual(seen['execution_root'],str(stage));self.assertEqual(seen['tool_dir'],str(stage/'native-runtime-tools'))
  self.assertFalse(list((stage/'native-runtime-tools').glob('__pycache__')))
 def test_relocated_code_keeps_private_data_root(self):
  stage=Path(self.build()['stage']);moved=self.temp/'Extracted QA app/Contents/Resources/bbport';moved.parent.mkdir(parents=True);shutil.copytree(stage,moved)
  r,output=self.invoke(moved);self.assertEqual(r.returncode,0,r.stderr)
  seen=json.loads(output.read_text());self.assertEqual(seen['project'],str(self.project));self.assertEqual(seen['execution_root'],str(moved))
 def test_tampered_even_unused_dependency_refused(self):
  stage=Path(self.build()['stage']);(stage/'native-runtime-tools/warm_archive_manifest.py').write_text('modified')
  r,output=self.invoke(stage);self.assertEqual(r.returncode,74);self.assertFalse(output.exists());self.assertIn('identity changed',r.stderr)
 def test_original_parser_support_is_frozen_and_relocation_safe(self):
  result=self.build();stage=Path(result['stage'])
  for relative,original in stage_prepare.SUPPORT.items():
   self.assertEqual((stage/relative).read_bytes(),(self.original/original).read_bytes())
   self.assertEqual(stage_prepare.sha(stage/relative),result['manifest']['runtime_support'][relative])
  (stage/'vendor/blood_echoes/loader/prepare.py').write_text('modified parser')
  r,output=self.invoke(stage);self.assertEqual(r.returncode,74);self.assertFalse(output.exists());self.assertIn('parser identity changed',r.stderr)
 def test_module_helper_is_frozen_once_with_existing_original_parser_pair(self):
  result=self.build();stage=Path(result['stage']);helper=stage/'native-runtime-tools/prepare_module_metadata.py'
  self.assertEqual(stage_prepare.TOOLS.count('prepare_module_metadata.py'),1)
  self.assertEqual(helper.read_bytes(),(self.runtime/helper.name).read_bytes())
  self.assertEqual(stage_prepare.sha(helper),result['manifest']['runtime_tools'][helper.name])
  self.assertEqual(set(result['manifest']['runtime_support']),set(stage_prepare.SUPPORT))
  self.assertEqual(len(result['manifest']['runtime_support']),2)
  helper.write_text('# changed module helper\n')
  r,output=self.invoke(stage);self.assertEqual(r.returncode,74);self.assertFalse(output.exists());self.assertIn('runtime tool identity changed: prepare_module_metadata.py',r.stderr)
if __name__=='__main__':unittest.main(verbosity=2)
