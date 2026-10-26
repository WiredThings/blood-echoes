"""Fresh authored source -> code-only closure. No old Shared/QA inputs."""
from pathlib import Path
import ast,hashlib,importlib.util,json,os,sys,tempfile,unittest
from unittest.mock import patch
D=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('source_closure_author',D/'tools/prepare_local_aot_stage.py');writer=importlib.util.module_from_spec(spec);spec.loader.exec_module(writer)

class SourceClosure(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve()
  self.repo=self.root/'checkout';self.repo.mkdir();self.source=self.repo/'experiments/arm64-runtime';self.tools=self.source/'tools';self.tools.mkdir(parents=True)
  self.output=self.root/'new-code';self.project=writer.PRIVATE_PROJECT
  self.run='#!/bin/bash\nprobe_args=(author)\n'+writer.HOOK_ANCHOR+'    :\nfi\n'+writer.FINAL_EXEC
  for name in writer.SOURCE_SCRIPTS:
   self.put(self.repo/name,(self.run if name=='run.sh'else'# author wrapper\n'+writer.LEGACY_RENDER_LOG if name=='tools/macos/run_optimized.sh'else'# author script '+name+'\n').encode())
  for name in writer.TOOLS:self.put(self.tools/name,b'# author frozen helper\n')
  self.put(self.tools/'prepare_game_psos.py',b'def prepare(config,*,services=None):pass\n')
  self.put(self.tools/'launcher_local_aot.py',b'def main(*,project=None,execution_root=None):pass\ndef cli(*,project=None,execution_root=None):pass\n')
  self.before=self.inventory(self.repo)
 def put(self,path,raw):path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
 def inventory(self,root):return {str(p.relative_to(root)):p.read_bytes()for p in root.rglob('*')if p.is_file()}
 def build(self,**kw):
  options=dict(source=self.source,repository=self.repo,runtime_tools=self.tools,destination=self.output,project=self.project);options.update(kw)
  return writer.write_source_closure(**options)
 def test_no_old_stage_state_game_cache_or_native_input_required(self):
  def forbidden(path,*args,**kw):
   if Path(path).is_relative_to(self.project):raise AssertionError('private QA path was read')
   return original(path,*args,**kw)
  original=Path.open
  with patch.object(Path,'open',forbidden):result=self.build()
  self.assertEqual(self.inventory(self.repo),self.before)
  expected=set(writer.SOURCE_SCRIPTS)|{'native-runtime-tools/'+n for n in writer.TOOLS}|set(writer.SUPPORT)|{'tools/macos/local_metal_startup.py','SOURCE-PARITY.json','ARM64-LOCAL-PREPARATION.json'}
  self.assertEqual(set(self.inventory(self.output)),expected)
  self.assertFalse(any((self.output/n).exists()for n in('bin','python','deps-metal-arm64','.packaged','NATIVE-METAL.json','GAME-PSO-COMPONENT.json')))
  self.assertFalse(result['formal_profile_enabled']);self.assertFalse(result['native_components_included']);self.assertTrue(result['manifest']['code_closure_only']);self.assertIs(result['manifest']['production'],False)
 def test_missing_hook_is_added_at_original_final_exec_boundary(self):
  result=self.build();effective=(self.output/'run.sh').read_text()
  self.assertEqual(effective,self.run.replace(writer.HOOK_ANCHOR,writer.HOOK+writer.HOOK_ANCHOR));self.assertEqual(effective.count(writer.HOOK),1)
  self.assertEqual(result['parity']['original_sources']['run.sh'],hashlib.sha256(self.run.encode()).hexdigest())
  self.assertFalse(result['parity']['source_projection']['hook_already_present'])
 def test_existing_exact_hook_and_log_are_projected_and_forward_verified(self):
  hooked=self.run.replace(writer.HOOK_ANCHOR,writer.HOOK+writer.HOOK_ANCHOR);self.put(self.repo/'run.sh',hooked.encode())
  old=(self.repo/'tools/macos/run_optimized.sh').read_text();new=writer.runtime_log_revision(old);self.put(self.repo/'tools/macos/run_optimized.sh',new.encode())
  result=self.build();self.assertEqual((self.output/'run.sh').read_text(),hooked);self.assertEqual((self.output/'tools/macos/run_optimized.sh').read_text(),new)
  self.assertTrue(all(result['parity']['source_projection'][k]for k in('hook_already_present','log_already_present')))
  self.assertEqual(result['parity']['original_sources']['tools/macos/run_optimized.sh'],hashlib.sha256(old.encode()).hexdigest())
 def test_duplicate_partial_moved_hook_or_missing_anchor_rejected_before_output(self):
  for run in [self.run+writer.HOOK*2,self.run+'local_metal_startup=broken\n',writer.HOOK+self.run,self.run.replace(writer.HOOK_ANCHOR,'if wrong; then\n')]:
   self.put(self.repo/'run.sh',run.encode())
   with self.assertRaises(ValueError):self.build()
   self.assertFalse(self.output.exists())
 def test_ambiguous_runtime_log_rejected(self):
  for log in [writer.FEX_RENDER_LOG*2,'echo "partial FEX game runtime"\n',writer.LEGACY_RENDER_LOG*2]:
   self.put(self.repo/'tools/macos/run_optimized.sh',log.encode())
   with self.assertRaises(ValueError):self.build()
   self.assertFalse(self.output.exists())
 def test_same_tools_import_and_api_guards_are_reused(self):
  self.put(self.tools/'not_frozen.py',b'# exists but never packaged\n');self.put(self.tools/'launcher_local_aot.py',b'def main(*,project=None,execution_root=None):pass\ndef cli(*,project=None,execution_root=None):\n import not_frozen\n')
  with self.assertRaisesRegex(ValueError,'not frozen'):self.build()
  self.assertFalse(self.output.exists())
 def test_missing_tool_and_bad_api_rejected_before_output(self):
  self.put(self.tools/'prepare_game_psos.py',b'def prepare(config):pass\n')
  with self.assertRaisesRegex(ValueError,'API not ready'):self.build()
  self.assertFalse(self.output.exists())
  (self.tools/'gpu_candidate_identity.py').unlink()
  with self.assertRaises(FileNotFoundError):self.build()
 def test_existing_linked_overlapping_or_wrong_fallback_output_rejected(self):
  self.output.mkdir()
  with self.assertRaises(ValueError):self.build()
  self.output.rmdir();self.output.symlink_to(self.repo,target_is_directory=True)
  with self.assertRaises(ValueError):self.build()
  self.output.unlink()
  with self.assertRaisesRegex(ValueError,'overlap'):self.build(destination=self.repo/'new-output')
  with self.assertRaisesRegex(ValueError,'fallback literal'):self.build(project=self.root/'relocated-QA')
 def test_linked_or_hardlinked_source_never_copied(self):
  path=self.repo/'scripts/prepare.py';before=path.read_bytes();other=self.root/'outside';other.write_bytes(before);path.unlink();path.symlink_to(other)
  with self.assertRaises(ValueError):self.build()
  path.unlink();os.link(other,path)
  with self.assertRaises(ValueError):self.build()
  self.assertFalse(self.output.exists())
 def test_support_and_all_metadata_hashes_match_actual_copied_code(self):
  result=self.build()
  for relative,origin in writer.SUPPORT.items():self.assertEqual((self.output/relative).read_bytes(),(self.repo/origin).read_bytes())
  for relative,want in result['parity']['effective_sources'].items():self.assertEqual(writer.sha(self.output/relative),want)
  for name,want in result['manifest']['runtime_tools'].items():self.assertEqual(writer.sha(self.output/'native-runtime-tools'/name),want)
  for name,want in result['manifest']['runtime_support'].items():self.assertEqual(writer.sha(self.output/name),want)
  self.assertEqual(writer.sha(self.output/'tools/macos/local_metal_startup.py'),result['manifest']['startup_bootstrap_sha256'])
  ast.parse((self.output/'tools/macos/local_metal_startup.py').read_text());self.assertFalse(result['parity']['previous_stage_read'])
 def test_writable_group_umask_does_not_create_unloadable_code_permissions(self):
  old=os.umask(0o002)
  try:self.build()
  finally:os.umask(old)
  for path in self.output.rglob('*'):self.assertFalse(path.stat().st_mode&0o022)
 def test_source_changed_during_copy_is_reported_without_ready_claim(self):
  original=writer._source_bytes;changed=False
  def mutate(path,*args,**kw):
   nonlocal changed
   raw=original(path,*args,**kw)
   if Path(path)==self.repo/'scripts/prepare.py'and not changed:changed=True;Path(path).write_bytes(raw+b'# concurrent edit\n')
   return raw
  with patch.object(writer,'_source_bytes',mutate):
   with self.assertRaisesRegex(ValueError,'input changed'):self.build()
  self.assertFalse(self.output.exists())

 def test_current_checkout_projection_and_consumer_schema_are_exact(self):
  repo=D.parents[1];run=(repo/'run.sh').read_text();log=(repo/'tools/macos/run_optimized.sh').read_text()
  original,effective,oldlog,newlog,projection=writer.projected_launcher_scripts(run,log)
  self.assertEqual(effective,run);self.assertEqual(newlog,log);self.assertEqual(effective.count(writer.HOOK),1)
  tree=ast.parse((D/'tools/launcher_runtime_profile.py').read_text())
  value=next(n.value for n in tree.body if isinstance(n,ast.Assign)and any(isinstance(t,ast.Name)and t.id=='ORIGINAL_SCRIPTS'for t in n.targets))
  self.assertEqual(tuple(writer.SOURCE_SCRIPTS),ast.literal_eval(value))
  writer.validate_runtime_tools(D/'tools')

if __name__=='__main__':unittest.main()
