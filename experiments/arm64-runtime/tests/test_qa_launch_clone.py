"""Short author-only cloning controls; no real QA saves/caches or native calls."""
from pathlib import Path
import copy
import hashlib
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import test_qa_launch_conditions as receipt_fixtures
import qa_launch_conditions as reader
import qa_launch_clone as clone
import qa_gpu_cache_seed as cache_seed


class Controls(unittest.TestCase):
    def setUp(self):
        self.f = receipt_fixtures.Controls('test_complete_original_env_and_immutable_configs_without_writes')
        self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.addCleanup(patch.stopall)
        patch.object(clone, 'PRIVATE_PROJECT', self.f.project).start()
        self.source_user = Path(self.f.env['BB_USER_DIR'])
        self.savedata = self.source_user / 'savedata'
        self.source_gpu = Path(self.f.env['BB_GPU_USER_DIR'])
        self.f.file(self.savedata / '1/CUSA00207/data.bin', b'author current save')
        self.f.file(self.savedata / '1/CUSA00207/slot.bin', b'author second save')
        (self.savedata / 'empty').mkdir()
        for name, raw in [('cache/game/one.bin', b'complete compiled cache'),
                          ('local-metal/cohort/one.metallib', b'complete local library'),
                          ('custom_configs/exact.ini', b'current GPU config'),
                          ('shader/custom.spv', b'current shader config'),
                          ('game-pso-progress/old.sqlite', b'old progress')]:
            self.f.file(self.source_gpu / name, raw)
        for name in clone.EXCLUSIONS:
            self.f.file(self.source_gpu / name / 'diagnostic', b'old private diagnostic not copied')
        self.f.manifest['upstream_inputs'] = dict(gpu_library='c' * 64)
        self.f.seal()
        self.conditions = self.f.load()
        self.admission = dict(source=str(self.source_gpu), source_manifest=str(self.f.stage.parent / 'ARM64-QA-MANIFEST.json'),
            source_manifest_sha256=self.conditions['identity']['ARM64-QA-MANIFEST.json'],
            upstream_gpu_library_sha256='c' * 64,
            source_packaged_gpu_library_sha256=self.conditions['runtime_sha256']['bin/gpu-metal-arm64/libbbgpu.dylib'],
            cache_parts=list(cache_seed.CACHE_PARTS), cache_only=True, save_config_seed_changed=False, formal_data_modified=False)

    def create(self, **kw):
        return clone.create(self.conditions, self.admission, project=self.f.project, tag='2000', **kw)

    def destinations(self):
        return [self.f.project / 'state' / name for name in ('benchmark-user-2000', 'benchmark-data-2000', 'gui-gpu-local-aot-2000')]

    def test_seeded_clone_exact_options_save_bytes_config_and_cache(self):
        before = {str(p): receipt_fixtures.sha(p) for p in self.f.base.rglob('*') if p.is_file()}
        result = self.create()
        self.assertTrue(result['source_unchanged'])
        self.assertFalse(result['game_started'])
        self.assertEqual(result['source_save_manifest'], clone.inventory_savedata(self.savedata))
        self.assertEqual(result['source_save_manifest'], clone.inventory_savedata(result['user_root'] / 'savedata'))
        self.assertEqual({p: receipt_fixtures.sha(Path(p)) for p in before}, before)
        self.assertEqual(result['cache_clone']['copied_files'], 2)
        self.assertTrue(result['cache_clone']['source_cache_cloned'])
        self.assertTrue((result['cache_root'] / 'cache/game/one.bin').exists())
        self.assertTrue((result['cache_root'] / 'local-metal/cohort/one.metallib').exists())
        self.assertEqual((result['cache_root'] / 'custom_configs/exact.ini').read_bytes(), b'current GPU config')
        self.assertEqual((result['cache_root'] / 'shader/custom.spv').read_bytes(), b'current shader config')
        for name in (*clone.EXCLUSIONS, 'game-pso-progress'):
            self.assertFalse((result['cache_root'] / name).exists())
            self.assertTrue((self.source_gpu / name).exists())
        for key, snapshot in self.conditions['configs'].items():
            self.assertEqual(Path(result['environment'][key]).read_bytes(), snapshot.data)
            self.assertEqual(result['config_sha256'][key], snapshot.sha256)
        self.assertEqual(set(result['actual_changes']), set(clone.ALLOWED_CHANGES))
        for key, value in self.conditions['environment'].items():
            if key not in clone.ALLOWED_CHANGES:
                self.assertEqual(result['environment'][key], value)
        self.assertEqual(result['environment']['BB_GAME_DIR'], self.conditions['environment']['BB_GAME_DIR'])
        session = (result['user_root'] / '.arm-runtime-session').read_text().splitlines()
        self.assertEqual(session, ['ARMQA1', str(result['data_root'] / 'out/boot-linked.bin'),
            str(self.conditions['game_root']), str(result['data_root'] / 'out/content.bin'), result['environment']['BB_ARM_SESSION_NONCE']])

    def test_source_audio_quicksave_language_and_effects_are_not_reset(self):
        original = dict(self.conditions['environment'], BB_AUDIO='none', BB_QUICKSAVE='1',
                        BB_LANGUAGE='1', BB_CHINESE_LANGUAGE='11', BB_UPSCALER='off', BB_MTL_GATHER_REUSE='0')
        self.conditions = dict(self.conditions, environment=original)
        result = self.create()
        for key in ('BB_AUDIO', 'BB_QUICKSAVE', 'BB_LANGUAGE', 'BB_CHINESE_LANGUAGE', 'BB_UPSCALER', 'BB_MTL_GATHER_REUSE', *reader.PROTECTION_KEYS):
            self.assertEqual(result['environment'][key], original[key])
        self.assertNotIn('PYTHON', result['environment'])
        self.assertNotIn('PYTHONHOME', result['environment'])

    def test_empty_mode_never_copies_source_cache_then_deletes(self):
        copied_roots = []
        original = cache_seed._copy_nodes
        def copy_nodes(before, source, destination):
            copied_roots.append((source, set(before)))
            return original(before, source, destination)
        with patch.object(cache_seed, '_copy_nodes', side_effect=copy_nodes):
            result = self.create(cache_mode='empty')
        self.assertFalse(result['cache_clone']['source_cache_cloned'])
        self.assertIsNone(result['cache_clone']['copied_content_identity'])
        self.assertEqual(result['cache_clone']['copied_files'], 0)
        for name in cache_seed.CACHE_PARTS:
            self.assertEqual(list((result['cache_root'] / name).iterdir()), [])
            self.assertTrue(any((self.source_gpu / name).rglob('*')))
            self.assertTrue(all(not any(p.is_relative_to(self.source_gpu / name) for p in nodes)
                                for root, nodes in copied_roots if root == self.source_gpu))
        self.assertTrue((result['cache_root'] / 'custom_configs/exact.ini').exists())

    def test_all_output_roots_must_be_fresh_before_any_copy(self):
        target = self.destinations()[1]; target.mkdir(); (target / 'preserved').write_bytes(b'old')
        with self.assertRaisesRegex(ValueError, 'fresh'):
            self.create()
        self.assertEqual((target / 'preserved').read_bytes(), b'old')
        self.assertFalse(self.destinations()[0].exists()); self.assertFalse(self.destinations()[2].exists())

    def test_linked_destination_is_not_followed(self):
        self.destinations()[2].symlink_to(self.source_gpu, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'fresh'):
            self.create()
        self.assertFalse(self.destinations()[0].exists()); self.assertFalse(self.destinations()[1].exists())

    def test_tag_and_modes_are_bounded_explicit(self):
        for tag in ('', 'a', '../2', '1' * 25, 2000):
            with self.assertRaises(ValueError):
                clone.create(self.conditions, self.admission, project=self.f.project, tag=tag)
        with self.assertRaisesRegex(ValueError, 'cache mode'):
            self.create(cache_mode='delete-then-copy')
        self.assertTrue(all(not p.exists() for p in self.destinations()))

    def test_admission_source_manifest_and_gpu_binding_required(self):
        for key, wrong in [('source', '/formal/cache'), ('source_manifest', '/formal/manifest'),
                           ('source_manifest_sha256', 'd' * 64), ('upstream_gpu_library_sha256', 'e' * 64),
                           ('source_packaged_gpu_library_sha256', 'f' * 64), ('cache_only', False),
                           ('formal_data_modified', True), ('cache_parts', ['local-metal'])]:
            original = self.admission[key]; self.admission[key] = wrong
            with self.assertRaisesRegex(ValueError, 'admission'):
                self.create()
            self.admission[key] = original
        self.assertTrue(all(not p.exists() for p in self.destinations()))

    def test_savedata_and_output_are_not_hardlinked_or_same_inode(self):
        result = self.create()
        source = self.savedata / '1/CUSA00207/data.bin'
        copied = result['user_root'] / 'savedata/1/CUSA00207/data.bin'
        self.assertNotEqual((source.stat().st_dev, source.stat().st_ino), (copied.stat().st_dev, copied.stat().st_ino))
        copied.write_bytes(b'new benchmark progress')
        self.assertEqual(source.read_bytes(), b'author current save')

    def test_savedata_symlink_and_hardlink_refused_before_copy(self):
        other = self.savedata / 'alias'; other.symlink_to(self.savedata / '1/CUSA00207/data.bin')
        with self.assertRaisesRegex(ValueError, 'plain tree'):
            self.create()
        other.unlink(); os.link(self.savedata / '1/CUSA00207/data.bin', other)
        with self.assertRaisesRegex(ValueError, 'plain tree'):
            self.create()
        self.assertTrue(all(not p.exists() for p in self.destinations()))

    def test_cache_hardlink_or_metadata_symlink_refused(self):
        alias = self.source_gpu / 'cache/game/alias'
        os.link(self.source_gpu / 'cache/game/one.bin', alias)
        with self.assertRaisesRegex(ValueError, 'plain tree'):
            self.create()
        alias.unlink(); (self.source_gpu / 'custom_configs/alias').symlink_to(self.f.base / 'assets/game')
        with self.assertRaisesRegex(ValueError, 'plain tree'):
            self.create()

    def test_source_mod_patch_trees_copy_all_plain_files(self):
        self.f.file(self.f.data / 'mods/mod/asset.bin', b'custom mod')
        self.f.file(self.f.data / 'patches/custom.xml', b'custom patch')
        result = self.create()
        self.assertEqual((result['data_root'] / 'mods/mod/asset.bin').read_bytes(), b'custom mod')
        self.assertEqual((result['data_root'] / 'patches/custom.xml').read_bytes(), b'custom patch')

    def test_source_mod_links_refused_without_substituting_empty(self):
        (self.f.data / 'mods').mkdir()
        (self.f.data / 'mods/link').symlink_to(self.savedata / '1/CUSA00207/data.bin')
        with self.assertRaisesRegex(ValueError, 'plain tree'):
            self.create()
        self.assertTrue(all(not p.exists() for p in self.destinations()))

    def test_big_file_paths_never_use_read_bytes(self):
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('whole-file read forbidden')):
            result = self.create()
            self.assertTrue(clone.inventory_savedata(result['user_root'] / 'savedata'))

    def test_save_change_during_copy_refuses_success(self):
        original = cache_seed._copy_nodes
        def changed(before, source, destination):
            result = original(before, source, destination)
            if source == self.savedata:
                (source / '1/CUSA00207/data.bin').write_bytes(b'changed while copying')
            return result
        with patch.object(cache_seed, '_copy_nodes', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'source/destination'):
                self.create()

    def test_savedata_inventory_never_reads_formal_or_assets(self):
        with self.assertRaisesRegex(ValueError, 'private QA savedata'):
            clone.inventory_savedata(self.f.base / 'assets/game')

    def test_source_configuration_bytes_must_still_match(self):
        Path(self.f.env['BB_CONFIG']).write_bytes(b'changed INI')
        with self.assertRaisesRegex(ValueError, 'SHA mismatch'):
            self.create()
        self.assertTrue(all(not p.exists() for p in self.destinations()))

    def test_optional_source_absence_creates_only_new_empty_dirs(self):
        result = self.create()
        for name in ('mods', 'patches'):
            self.assertFalse((self.f.data / name).exists())
            self.assertEqual(list((result['data_root'] / name).iterdir()), [])

    def test_cache_exclusions_never_allow_cache_or_local_metal(self):
        for exclusions in (('cache',), ('local-metal',), ('../cache',), ('unknown',), ['log'], ('log', 'log')):
            with self.assertRaisesRegex(ValueError, 'four diagnostic'):
                cache_seed.clone(self.admission, self.destinations()[2], project=self.f.project,
                                 baseline_root=self.source_gpu, extra_exclusions=exclusions)
        self.assertFalse(self.destinations()[2].exists())

    def test_tree_budget_and_wrong_owner_refused(self):
        with self.assertRaisesRegex(ValueError, 'node budget'):
            cache_seed._tree_snapshot(self.savedata, node_limit=2)
        original = cache_seed._tree_snapshot
        def denied(root, **kw):
            if root == self.savedata:
                raise ValueError('author clone tree node budget')
            return original(root, **kw)
        with patch.object(cache_seed, '_tree_snapshot', side_effect=denied):
            with self.assertRaisesRegex(ValueError, 'node budget'):
                self.create()
        with patch.object(cache_seed.os, 'getuid', return_value=os.getuid() + 1):
            with self.assertRaisesRegex(ValueError, 'owned'):
                self.create()

    def test_cache_baseline_change_during_copy_refuses_success(self):
        original = cache_seed._copy_nodes
        def changed(before, source, destination):
            result = original(before, source, destination)
            if source == self.source_gpu:
                (source / 'custom_configs/exact.ini').write_bytes(b'changed metadata while copying')
            return result
        with patch.object(cache_seed, '_copy_nodes', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'baseline configuration changed'):
                self.create()


if __name__ == '__main__':
    unittest.main(verbosity=2)
