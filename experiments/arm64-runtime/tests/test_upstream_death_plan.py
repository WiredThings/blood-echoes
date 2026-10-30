#!/usr/bin/env python3
"""Source/command controls only: no compiler, private overlay write or game."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('upstream_death', HERE / 'run_upstream_death_regression.py')
death = importlib.util.module_from_spec(spec)
spec.loader.exec_module(death)


class DeathPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.normal = death.NORMAL
        cls.source = cls.normal / 'source/experiments/arm64-runtime'
        cls.inputs = {name: (cls.source / name).read_bytes() for name in death.EXPECTED}

    def test_current_four_sources_and_minimal_overlay(self):
        overlay = death.overlay_sources(self.inputs)
        self.assertEqual(len(overlay), 4)
        self.assertEqual(overlay['private_death_contract.h'], self.inputs['tests/private_death_contract.h'])
        self.assertEqual(overlay['private_death_runtime.h'], self.inputs['tests/private_death_runtime.h'])
        self.assertEqual(overlay['game_runtime.cpp'], b'#include "private_death_contract.h"\n' + self.inputs['src/game_runtime.cpp'])
        for marker in (b'ObserveDeath(now,world,loading,player);', b'if(!authored)PrepareDeath(image,boot,*mapping,plan.stub.size());'):
            self.assertEqual(overlay['dev_travel_runtime.h'].count(marker), 1)
        # Input bytes were not rewritten in place.
        self.assertEqual(self.inputs['src/dev_travel_runtime.h'], (self.source / 'src/dev_travel_runtime.h').read_bytes())

    def test_mutation_of_each_normal_input_is_rejected(self):
        for name in self.inputs:
            with self.subTest(name=name):
                mutant = self.inputs.copy()
                mutant[name] += b'\n'
                with self.assertRaisesRegex(ValueError, 'identity changed'):
                    death.overlay_sources(mutant)

    def test_partial_or_extra_input_is_rejected(self):
        for mutant in ({}, dict(self.inputs, extra=b'new')):
            with self.assertRaises(ValueError):
                death.overlay_sources(mutant)

    def test_duplicate_missing_and_already_injected_anchor_rejected(self):
        for raw in (b'x', b'anchor anchor'):
            with self.assertRaises(ValueError):
                death.once(raw, b'anchor', b'new')
        mutant = self.inputs.copy()
        mutant['src/game_runtime.cpp'] = b'#include "private_death_contract.h"\n' + mutant['src/game_runtime.cpp']
        with self.assertRaises(ValueError):
            death.overlay_sources(mutant)

    def build_fixture(self):
        cpu = self.normal / 'build/cpu'
        obj = 'CMakeFiles/bb-game-arm64.dir/src/game_runtime.cpp.o'
        record = dict(directory=str(cpu), file=str(self.source / 'src/game_runtime.cpp'),
                      arguments=['/usr/bin/clang++', '-std=c++20', '-DBB_NORMAL=1', '-I' + str(self.normal / 'build/generated'),
                                 '-MD', '-MT', obj, '-MF', obj + '.d', '-o', obj, '-c', str(self.source / 'src/game_runtime.cpp')])
        link = ['/usr/bin/clang++', obj, '-o', 'bb-game-arm64', 'libbb_fex_embedding.a', 'libbb_native_runtime.a',
                '_deps/fex-build/Source/Interface/Core/libFEXCore.a', str(self.normal / 'build/renderer/libbbgpu.dylib'),
                '-Wl,-dependency_info,bb-game-arm64.dat']
        return record, link

    def rewrite(self, record, link):
        return death.rewrite_build_commands(record, link, source_root=self.source,
                    cpu_build=self.normal / 'build/cpu', output=self.normal.parent / 'upstream-private-death-test-output')

    def test_rewrite_preserves_current_fex_and_normal_flags(self):
        record, link = self.build_fixture()
        before_record, before_link = copy.deepcopy(record), link[:]
        result = self.rewrite(record, link)
        self.assertEqual(record, before_record)
        self.assertEqual(link, before_link)
        self.assertIn('-DBB_NORMAL=1', result['compile'])
        self.assertIn('_deps/fex-build/Source/Interface/Core/libFEXCore.a', result['link'])
        self.assertIn(str(self.normal / 'build/renderer/libbbgpu.dylib'), result['link'])
        self.assertEqual(result['compile'].count(str(self.source / 'src/game_runtime.cpp')), 0)
        self.assertEqual(result['link'].count(result['only_replaced_normal_object']), 0)
        self.assertIn('-Wl,-dependency_info,' + str(self.normal.parent / 'upstream-private-death-test-output/link.d'), result['link'])

    def test_selects_exact_normal_target_not_old_stack_snapshot(self):
        record, link = self.build_fixture()
        snapshot = copy.deepcopy(record)
        snapshot['arguments'] = [s.replace('bb-game-arm64.dir', 'bb-game-arm64-old-stack-snapshot.dir') for s in snapshot['arguments']]
        selected = death.normal_compile_record([record, snapshot], self.source, self.normal / 'build/cpu')
        self.assertEqual(selected, record)
        for records in ([snapshot], [record, record]):
            with self.assertRaises(ValueError):
                death.normal_compile_record(records, self.source, self.normal / 'build/cpu')
        with self.assertRaises(ValueError):
            self.rewrite(snapshot, link)

    def test_compile_database_disagreeing_output_rejected(self):
        record, unused = self.build_fixture()
        record['output'] = '/tmp/old.o'
        with self.assertRaises(ValueError):
            death.normal_compile_record([record], self.source, self.normal / 'build/cpu')

    def test_old_source_build_object_or_embedding_rejected(self):
        for field in ('directory', 'file', 'object', 'embedding', 'renderer', 'target'):
            with self.subTest(field=field):
                record, link = self.build_fixture()
                if field in ('directory', 'file'):
                    record[field] = '/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/evidence/old/' + field
                elif field == 'object':
                    link[1] = 'old_game_runtime.o'
                elif field == 'embedding':
                    link[4] = '/tmp/old/libbb_fex_embedding.a'
                elif field == 'renderer':
                    link[-2] = '/tmp/old/libbbgpu.dylib'
                else:
                    link[3] = 'bb-game-arm64-old-stack-snapshot'
                with self.assertRaises(ValueError):
                    self.rewrite(record, link)

    def test_unaccounted_side_outputs_rejected(self):
        for flag in ('-MJextra.json', '-save-temps', '-fmodules-cache-path=/tmp/old'):
            record, link = self.build_fixture()
            record['arguments'].insert(1, flag)
            with self.assertRaises(ValueError):
                self.rewrite(record, link)
        record, link = self.build_fixture()
        link.append('-Wl,-map,/tmp/old.map')
        with self.assertRaises(ValueError):
            self.rewrite(record, link)

    def test_policy_projection_exports_real_defaults_without_launch_exec(self):
        original = (self.normal / 'source/tools/macos/run_optimized.sh').read_bytes()
        stage = self.normal / 'Blood Echoes.app/Contents/Resources/bbport'
        destination = self.normal.parent / 'upstream-private-death-test-output/effective-environment.bin'
        projected = death.policy_environment_projection(original, stage, destination)
        self.assertEqual(projected.count(b'/usr/bin/env -0 > '), 2)
        self.assertNotIn(b'exec bash ', projected)
        self.assertIn(b"cd -- '", projected)  # Spaces in real normal app path are shell quoted.
        self.assertEqual(death.digest(original), death.POLICY_SHA256)
        for mutant in (original + b'\n', original.replace(b'exec bash run.sh', b'exec bash other.sh')):
            with self.assertRaises(ValueError):
                death.policy_environment_projection(mutant, stage, destination)
        with self.assertRaises(ValueError):
            death.policy_environment_projection(original, Path('/Applications/Blood Echoes.app'), destination)

    def test_plan_never_compiles_or_runs_and_separates_f3(self):
        report, unused = death.build_plan(self.normal, self.normal.parent / 'upstream-private-death-test-output')
        self.assertFalse(report['compiled'])
        self.assertFalse(report['game_started'])
        self.assertFalse(report['formal_data_accessed'])
        self.assertIsNone(report['build_commands'])
        self.assertTrue(report['separate_from_performance_and_f3'])
        self.assertFalse((self.normal.parent / 'upstream-private-death-test-output').exists())
        original = json.loads((self.normal / 'build/cpu/generated/cpu_cache_build.json').read_text())
        identity = report['private_cpu_cache_build']
        self.assertEqual(identity['runtime_build'], original['runtime_build'])
        self.assertEqual(identity['runtime_parts'], original['runtime_parts'])
        self.assertNotEqual(identity['bridge_build'], original['bridge_build'])
        self.assertIn('bb_cpu_cache_build.h', unused)

    def test_bad_base_identity_or_wrong_frontend_cannot_make_private_header(self):
        record = json.loads((self.normal / 'build/cpu/generated/cpu_cache_build.json').read_text())
        spec = importlib.util.spec_from_file_location('normal_cache_builder', self.source / 'tools/make_cpu_cache_build.py')
        builder = importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
        for field in ('runtime_build', 'bridge_build', 'game-frontend', 'frontend-header-dev_travel_runtime.h'):
            mutant = copy.deepcopy(record)
            if field in mutant:mutant[field] = '0' * 64
            else:
                mutant['bridge_parts'][field] = '0' * 64
                mutant['bridge_build'] = builder.combine('BE-common-bridge-build-v1', mutant['bridge_parts'])
            with self.subTest(field=field), self.assertRaises(ValueError):
                death.private_cache_identity(mutant, death.overlay_sources(self.inputs), combine=builder.combine)

    def test_build_requires_real_normal_commands_before_any_process(self):
        with self.assertRaisesRegex(ValueError, 'normal target commands required'):
            death.build_private(dict(build_commands=None, build_commands_ready=False))

    def test_formal_output_or_normal_build_source_output_rejected(self):
        for output in (Path('/Applications/Blood Echoes.app'), self.normal, self.normal / 'source/private', self.normal / 'build/private'):
            with self.subTest(output=output), self.assertRaises(ValueError):
                death.build_plan(self.normal, output)


if __name__ == '__main__':
    unittest.main()
