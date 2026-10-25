#!/usr/bin/env python3
"""Execute the same archive producer branches as run_game, without a native/game process."""
from pathlib import Path
import json,tempfile,unittest
from unittest.mock import Mock
from warm_archive_manifest import prepare_startup_archive_policy
P=Path('/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype')
class ProducerBranchTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.root=Path(tempfile.mkdtemp(prefix='warm-archive-producer-branches-',dir=P/'state'))
 def setUp(self):
  self.case=Path(tempfile.mkdtemp(prefix='case-',dir=self.root))
  self.library=self.case/'authored-translator';self.library.write_bytes(b'authored translator compiled identity v1\n')
  self.boot=self.case/'authored-boot';self.boot.write_bytes(b'authored game image v1\n')
  self.patch=self.case/'authored-startup';self.patch.write_bytes(b'authored applied startup patches v1\n')
  self.selected=self.case/'authored-selected-executable'
  self.options={'math':'Safe','language':'3.1'};self.profile={'abi':'authored-profile-v1'}
  self.resolver=Mock(return_value={'target':str(self.selected),'library':str(self.library),'library_sha256':'authored-verifier-sentinel','load_edge_verified':True})
 def produce(self,**changes):
  args=dict(enabled=True,engine='arm64',selected_executable=self.selected,backend_root=self.case,
            game_artifacts={'boot':self.boot,'patch':self.patch},options=self.options,profile=self.profile,resolver=self.resolver)
  args.update(changes);return prepare_startup_archive_policy(**args)
 def test_arm64_uses_resolved_library_and_real_file_hashes(self):
  updates,report=self.produce();self.resolver.assert_called_once_with(self.selected,self.case)
  self.assertEqual(report['mode'],'arm64-v3');self.assertTrue(report['manifest_created']);self.assertTrue(report['namespaced'])
  self.assertEqual(updates['BB_MTL_ARCHIVE_COMPATIBILITY_ID'],report['token']);self.assertEqual(len(report['token']),64)
  self.assertEqual(report['manifest']['translator']['gpu_library']['bytes'],self.library.stat().st_size)
  self.assertEqual(updates,self.produce()[0])
 def test_rosetta_preserves_existing_legacy_environment_without_native_work(self):
  resolver=Mock(side_effect=AssertionError('must not resolve ARM library'))
  builder=Mock(side_effect=AssertionError('must not hash legacy files'))
  env={'BB_MTL_PERSIST_WARM_ARCHIVE':'1','BB_MTL_RELEASE_WARM_ARCHIVE':'1','BB_MTL_COMPILE_AUDIT':'1'};before=env.copy()
  updates,report=self.produce(engine='rosetta',resolver=resolver,manifest_builder=builder,
       game_artifacts=None,options=None,profile=None,selected_executable='/nonexistent/legacy-x86')
  env.update(updates);self.assertEqual(env,before);self.assertNotIn('BB_MTL_ARCHIVE_COMPATIBILITY_ID',env)
  resolver.assert_not_called();builder.assert_not_called();self.assertEqual(report['mode'],'legacy-rosetta')
  self.assertFalse(report['manifest_created']);self.assertFalse(report['namespaced'])
 def test_inactive_and_non_graphics_return_before_native_work(self):
  resolver=Mock(side_effect=AssertionError('inactive native resolver'))
  builder=Mock(side_effect=AssertionError('inactive file hasher'))
  for engine in ['arm64','rosetta']:
   updates,report=self.produce(enabled=False,engine=engine,resolver=resolver,manifest_builder=builder,
       game_artifacts=None,options=None,profile=None,selected_executable='/nonexistent')
   self.assertEqual(updates,{});self.assertEqual(report['mode'],'inactive')
  resolver.assert_not_called();builder.assert_not_called()
 def test_actual_translator_artifact_change_changes_token(self):
  before=self.produce()[0];self.library.write_bytes(b'authored translator changed\n');self.assertNotEqual(before,self.produce()[0])
 def test_actual_game_and_patch_changes_change_token(self):
  before=self.produce()[0]
  for file in [self.boot,self.patch]:
   saved=file.read_bytes();file.write_bytes(saved+b'changed\n');self.assertNotEqual(before,self.produce()[0]);file.write_bytes(saved)
 def test_options_and_profile_changes_change_token(self):
  before=self.produce()[0];self.assertNotEqual(before,self.produce(options={'math':'Relaxed','language':'3.1'})[0])
  self.assertNotEqual(before,self.produce(profile={'abi':'different-profile'})[0])
 def test_arm64_identity_failure_never_silently_falls_back(self):
  with self.assertRaisesRegex(ValueError,'authored signature rejection'):
   self.produce(resolver=Mock(side_effect=ValueError('authored signature rejection')))
 def test_arm64_requires_actual_complete_inputs(self):
  with self.assertRaises(ValueError):self.produce(game_artifacts={})
 def test_unsupported_engine_fails_without_native_work(self):
  with self.assertRaises(ValueError):self.produce(engine='unknown')
  self.resolver.assert_not_called()
if __name__=='__main__':
 suite=unittest.defaultTestLoader.loadTestsFromTestCase(ProducerBranchTests)
 result=unittest.TextTestRunner(verbosity=2).run(suite)
 if hasattr(ProducerBranchTests,'root'):
  report=dict(tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),passed=result.wasSuccessful(),game_launched=False,native_backend_loaded=False,inputs=str(ProducerBranchTests.root),boundary='actual producer dispatch and real authored file hashing; native resolver injected only')
  (ProducerBranchTests.root/'result.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
 raise SystemExit(not result.wasSuccessful())
