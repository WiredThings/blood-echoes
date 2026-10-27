"""Authored identity separation; no native cache admission or hardware claim."""
from pathlib import Path
import copy,json,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from compilation_domains import split_manifest,library_product_key,pso_schedule_identity,PSO_SCHEDULE_PROTOCOLS
from local_metal_builder import derive_key,RECIPE,LIBRARY_RECIPE
from warm_archive_manifest import make_manifest,prepare_startup_archive_policy

class Domains(unittest.TestCase):
 def test_ui_log_restart_and_bundle_additions_do_not_invalidate_pso_schedule(self):
  all_tools={k:'a'*64 for k in PSO_SCHEDULE_PROTOCOLS};before=pso_schedule_identity(all_tools)
  all_tools.update({'texture_binding_request.py':'b'*64,'launcher_local_aot.py':'c'*64,'unrelated_new_tool.py':'d'*64})
  self.assertEqual(before,pso_schedule_identity(all_tools))
  for name in PSO_SCHEDULE_PROTOCOLS:
   changed={**all_tools,name:'e'*64};self.assertNotEqual(before,pso_schedule_identity(changed))
  with self.assertRaises(ValueError):pso_schedule_identity({})
  with self.assertRaises(ValueError):pso_schedule_identity({**all_tools,'prepare_game_psos.py':'invalid'})
 def manifest(self):return dict(schema=1,translator=dict(gpu_library=dict(sha256='a'*64,bytes=12)),game=dict(boot=dict(sha256='b'*64,bytes=12),settings=dict(sha256='c'*64,bytes=12)),options=dict(BB_MTL_VERTEX_MATH='1',BB_MTL_PRECISE_RESIDENCY='1'),profile=dict(engine='arm64',profile_abi='SPV10-meta7-key5-msl3'))
 def request(self,schema=1,**changes):
  r=dict(schema=schema,recipe=RECIPE if schema==1 else LIBRARY_RECIPE,cohort='a'*64,source_sha256='b'*64,language='metal3.1',math_mode='safe',math_fp32='fast')
  if schema==1:r['entry']='main0'
  r.update(changes);r['key']=derive_key(r);return r
 def test_mod_A_AB_A_and_original_resource_do_not_change_compiler_identity(self):
  base=self.manifest();one=split_manifest(base);both=copy.deepcopy(base);both['game']['boot']['sha256']='d'*64;two=split_manifest(both);restored=split_manifest(copy.deepcopy(base))
  self.assertEqual(one['compilation_token'],two['compilation_token']);self.assertNotEqual(one['selection_token'],two['selection_token']);self.assertEqual(one,restored);self.assertEqual(two['selection_manifest']['game'],both['game']);self.assertFalse(two['artifact_authority'])
 def test_unrelated_settings_and_execution_options_are_only_launch_selection(self):
  before=split_manifest(self.manifest());changed=self.manifest();changed['game']['settings']['sha256']='e'*64;changed['options']['BB_MTL_PRECISE_RESIDENCY']='0';after=split_manifest(changed);self.assertEqual(before['compilation_token'],after['compilation_token']);self.assertNotEqual(before['selection_token'],after['selection_token'])
 def test_actual_compiler_abi_or_source_affecting_options_still_invalidate(self):
  base=self.manifest();before=split_manifest(base)['compilation_token']
  for mutate in [lambda m:m['translator']['gpu_library'].update(sha256='d'*64),lambda m:m['profile'].update(profile_abi='changed'),lambda m:m['options'].update(BB_MTL_VERTEX_MATH='0')]:
   changed=copy.deepcopy(base);mutate(changed);self.assertNotEqual(before,split_manifest(changed)['compilation_token'])
 def test_unknown_future_control_is_not_silently_discarded(self):
  before=split_manifest(self.manifest());changed=self.manifest();changed['options']['BB_MTL_FUTURE_COMPILER_OPTION']='1';after=split_manifest(changed);self.assertNotEqual(before['compilation_token'],after['compilation_token']);self.assertIn('BB_MTL_FUTURE_COMPILER_OPTION',after['unclassified_options'])
 def test_library_source_is_reusable_across_provenance_cohorts_without_forging_old_key(self):
  for schema in (1,2):
   a=self.request(schema);b=self.request(schema,cohort='c'*64);self.assertNotEqual(a['key'],b['key']);self.assertEqual(library_product_key(a,'d'*64),library_product_key(b,'d'*64))
 def test_source_all_compile_options_and_toolchain_are_product_inputs(self):
  for schema in (1,2):
   a=self.request(schema);key=library_product_key(a,'d'*64)
   for changes in [dict(source_sha256='e'*64),dict(language='metal3.2'),dict(math_mode='relaxed'),dict(math_fp32='precise')]+([dict(entry='another')]if schema==1 else[]):self.assertNotEqual(key,library_product_key(self.request(schema,**changes),'d'*64))
   self.assertNotEqual(key,library_product_key(a,'e'*64))
 def test_bad_original_key_extra_fields_and_unsupported_schema_rejected(self):
  for change in [dict(key='0'*64),dict(extra_flag='unsafe'),dict(schema=True),dict(recipe='invented')]:
   with self.assertRaises(ValueError):library_product_key(dict(self.request(),**change),'d'*64)
 def test_actual_existing_manifest_producer_exposes_both_domains_without_switching_legacy_namespace(self):
  with tempfile.TemporaryDirectory()as tmp:
   root=Path(tmp);lib=root/'gpu';game=root/'boot';lib.write_bytes(b'author compiler');game.write_bytes(b'author game')
   def resolve(*args):return dict(library=str(lib))
   args=dict(enabled=True,engine='arm64',selected_executable=lib,backend_root=root,game_artifacts={'boot':game},options={},profile={'abi':'author'},resolver=resolve)
   env,report=prepare_startup_archive_policy(**args);self.assertIn('domains',report);self.assertEqual(env['BB_MTL_ARCHIVE_COMPATIBILITY_ID'],report['token']);self.assertNotEqual(report['token'],report['domains']['compilation_token']);before=report['domains']['compilation_token'];game.write_bytes(b'author mod');_,changed=prepare_startup_archive_policy(**args);self.assertEqual(before,changed['domains']['compilation_token']);self.assertNotEqual(report['token'],changed['token'])

if __name__=='__main__':unittest.main(verbosity=2)
