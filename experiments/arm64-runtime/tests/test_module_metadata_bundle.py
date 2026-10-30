"""CPU authored checks for code-role selection; no native process or game data."""
from pathlib import Path
import hashlib,importlib.util,json,shutil,tempfile,unittest
from unittest.mock import patch

D=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('metadata_bundle',D/'tools/prepare_module_metadata.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class BundleControls(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(prefix='module-bundle-author-')
  self.project=Path(self.temp.name).resolve()/'private-project';self.project.mkdir()
  self.scope=patch.object(m,'PROJECT',self.project);self.scope.start()
  self.root=self.project/'apps/Author QA.app/Contents/Resources/bbport'
  self.root.mkdir(parents=True)
  self.helper=self.root/'native-runtime-tools/prepare_module_metadata.py'
  self.helper.parent.mkdir();self.helper.write_text('# authored helper body\n')
  for rel in m.SUPPORT:
   f=self.root/rel;f.parent.mkdir(parents=True,exist_ok=True);f.write_text('# original '+rel+'\n')
  self.exe=self.root/'bin/bb-probe-metal';self.exe.parent.mkdir();self.exe.write_bytes(b'authored executable identity')
  self.freeze()
 def tearDown(self):self.scope.stop();self.temp.cleanup()
 def freeze(self,role='bin/bb-probe-metal'):
  self.manifest=dict(schema=1,host_local_only=True,runtime_project=str(self.project),runtime_tools={'prepare_module_metadata.py':m.digest(self.helper)},runtime_support={r:m.digest(self.root/r)for r in m.SUPPORT})
  (self.root/'ARM64-LOCAL-PREPARATION.json').write_text(json.dumps(self.manifest))
  (self.root/'NATIVE-METAL.json').write_text(json.dumps(dict(schema=1,production=False,game_runtime='fex-arm64',frozen_runtime={role:m.digest(self.exe)})))
 def select(self,**kwargs):
  args=dict(code_root=self.root,executable=self.exe,script=self.helper);args.update(kwargs)
  return m.select_loader(**args)
 def test_top_and_nested_declared_roles(self):
  self.assertEqual(self.select(),self.root/'vendor/blood_echoes/loader')
  self.exe=self.root/'bin/Bloodborne Game.app/Contents/MacOS/bb-probe-metal';self.exe.parent.mkdir(parents=True);self.exe.write_bytes(b'authored nested executable')
  self.freeze('bin/Bloodborne Game.app/Contents/MacOS/bb-probe-metal')
  self.assertEqual(self.select(),self.root/'vendor/blood_echoes/loader')
 def test_nested_role_must_have_its_own_frozen_runtime_hash(self):
  nested=self.root/'bin/Bloodborne Game.app/Contents/MacOS/bb-probe-metal';nested.parent.mkdir(parents=True);nested.write_bytes(self.exe.read_bytes())
  with self.assertRaises(KeyError):self.select(executable=nested)
 def test_independent_cpu_requires_its_exact_component_and_capabilities(self):
  role='bin/Bloodborne bbcpu Game.app/Contents/MacOS/bb-probe-metal'
  self.exe=self.root/role;self.exe.parent.mkdir(parents=True);self.exe.write_bytes(b'authored independent CPU')
  self.freeze(role)
  with self.assertRaisesRegex(ValueError,'explicit QA capabilities'):self.select()
  native=self.root/'NATIVE-METAL.json';model=json.loads(native.read_bytes());model.update(game_runtime='arm64',cpu_backends=['fex','bbcpu']);native.write_text(json.dumps(model))
  component=self.root/'BBCPU-COMPONENT.json';doc=dict(schema=1,development_only=True,backend='bbcpu',cpu=dict(path=role,sha256=m.digest(self.exe)));component.write_text(json.dumps(doc))
  self.assertEqual(self.select(),self.root/'vendor/blood_echoes/loader')
  for change in({'backend':'fex'},{'development_only':False},{'cpu':dict(path='bin/bb-probe-metal',sha256=m.digest(self.exe))},{'cpu':dict(path=role,sha256='0'*64)}):
   component.write_text(json.dumps({**doc,**change}))
   with self.subTest(change=change),self.assertRaisesRegex(ValueError,'frozen component'):self.select()
 def test_no_bundle_failure_falls_back_to_developer_tools(self):
  dev=self.project/'tools/prepare_module_metadata.py';dev.parent.mkdir();dev.write_text('# available old developer helper\n')
  self.helper.unlink()
  with self.assertRaises(FileNotFoundError):self.select()
 def test_unknown_runtime_role_rejected(self):
  wrong=self.root/'bin/other-runtime';wrong.write_bytes(b'author')
  with self.assertRaisesRegex(ValueError,'role is not declared'):self.select(executable=wrong)
 def test_outside_apps_and_formal_application_rejected(self):
  other=self.project/'formal-app/Other.app/Contents/Resources/bbport/bin/bb-probe-metal'
  other.parent.mkdir(parents=True);other.write_bytes(b'author')
  with self.assertRaisesRegex(ValueError,'private QA application'):self.select(executable=other)
 def test_root_script_and_loader_override_mismatches(self):
  cases=[dict(code_root=self.root.parent),dict(script=self.project/'tools/prepare_module_metadata.py'),dict(loader=self.project/'vendor/blood_echoes/loader'),dict(code_root=None),dict(executable=None)]
  for case in cases:
   with self.subTest(case=case),self.assertRaises(ValueError):self.select(**case)
 def test_helper_and_parser_digest_tampering(self):
  for path in (self.helper,*(self.root/r for r in m.SUPPORT),self.exe):
   old=path.read_bytes();path.write_bytes(old+b'# changed')
   with self.subTest(path=path),self.assertRaisesRegex(ValueError,'identity changed'):self.select()
   path.write_bytes(old)
 def test_linked_helper_parser_runtime_and_parent_rejected(self):
  for path in (self.helper,*(self.root/r for r in m.SUPPORT),self.exe):
   old=path.read_bytes();other=path.with_name(path.name+'.owned');path.rename(other);path.symlink_to(other.name)
   with self.subTest(path=path),self.assertRaises(ValueError):self.select()
   path.unlink();other.rename(path);self.assertEqual(path.read_bytes(),old)
  parent=self.helper.parent;other=parent.with_name(parent.name+'.owned');parent.rename(other);parent.symlink_to(other.name,target_is_directory=True)
  with self.assertRaises(ValueError):self.select()
 def test_foreign_owner_and_hardlinked_code_rejected(self):
  import os
  duplicate=self.helper.with_name('duplicate');os.link(self.helper,duplicate)
  with self.assertRaises(ValueError):self.select()
  duplicate.unlink()
  with patch.object(m.os,'getuid',return_value=self.helper.stat().st_uid+1),self.assertRaises(ValueError):self.select()
 def test_manifest_scope_and_support_set_are_exact(self):
  path=self.root/'ARM64-LOCAL-PREPARATION.json';old=path.read_bytes()
  for key,value in [('schema',2),('host_local_only',False),('runtime_project',str(self.project.parent)),('runtime_support',{}),('runtime_tools',{})]:
   mutated=dict(self.manifest);mutated[key]=value;path.write_text(json.dumps(mutated))
   with self.subTest(key=key),self.assertRaises((ValueError,KeyError)):self.select()
  path.write_bytes(old)
  native=self.root/'NATIVE-METAL.json';model=json.loads(native.read_bytes());model['production']=True;native.write_text(json.dumps(model))
  with self.assertRaises(ValueError):self.select()
 def test_bundle_missing_role_arguments_is_not_developer_mode(self):
  with self.assertRaisesRegex(ValueError,'role arguments missing'):m.select_loader(script=self.helper)
 def test_bundle_code_does_not_authorize_non_shared_game_or_output(self):
  boot=Path(self.temp.name)/'boot';boot.write_bytes(b'authored boot')
  out=Path(self.temp.name)/'metadata';out.write_bytes(b'keep old output')
  loader=self.select()
  with self.assertRaisesRegex(ValueError,'private Shared tree'):m.prepare(boot,boot.parent,out,loader)
  self.assertEqual(out.read_bytes(),b'keep old output')
 def test_unbundled_fixed_developer_helper_still_selected(self):
  dev=self.project/'tools/prepare_module_metadata.py';dev.parent.mkdir();dev.write_text('# authored developer helper')
  self.assertEqual(m.select_loader(script=dev),self.project/'vendor/blood_echoes/loader')
  override=self.project/'other-loader';self.assertEqual(m.select_loader(script=dev,loader=override),override)
  with self.assertRaises(ValueError):m.select_loader(script=self.project/'other.py')
 def test_header_uses_actual_executable_not_environment_code_root(self):
  text=(D/'src/prepare_guest_module_info.h').read_text()
  self.assertIn('_NSGetExecutablePath(path,&size)',text)
  self.assertIn('const auto selected=ActualHelper();',text)
  self.assertIn('"--runtime-executable",selected.executable.string()',text)
  self.assertIn('module runtime application layout is not declared',text)
  self.assertNotIn('getenv("BB_MODULE',text)
  self.assertNotIn('getenv("BB_CODE_ROOT',text)

if __name__=='__main__':unittest.main(verbosity=2)
