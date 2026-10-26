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
 def test_external_tool_import_is_not_a_frozen_closure(self):
  with(self.runtime/'local_metal_builder.py').open('a')as f:f.write("\nsys.path.insert(0,str(PROJECT/'tools'))\n")
  with self.assertRaises(ValueError):self.build()
 def test_unrelated_original_script_change_is_refused(self):
  (self.original/'scripts/prepare.py').write_text('changed')
  with self.assertRaises(ValueError):self.build()
 def test_unrelated_new_run_change_is_refused(self):
  with(self.repository/'run.sh').open('a')as f:f.write('unexpected change\n')
  with self.assertRaises(ValueError):self.build()
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
if __name__=='__main__':unittest.main(verbosity=2)
