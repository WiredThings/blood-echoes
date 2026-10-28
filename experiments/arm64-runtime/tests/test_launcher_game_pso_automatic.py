"""Original final-exec automatic phase order; no Metal, guest data or credentials."""
from pathlib import Path
import hashlib, json, sys, tempfile, unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import launcher_game_pso as launcher


class Entry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name).resolve()
        self.project = self.root / 'prototype'; self.project.mkdir()
        self.stage = self.root / 'stage'; self.stage.mkdir()
        self.gpu = self.project / 'state/gpu'; self.gpu.mkdir(parents=True)
        self.game = self.root / 'game'; self.game.mkdir()
        self.env = dict(BB_GPU_PROCESS=str(self.stage / 'gpu'), BB_GPU_USER_DIR=str(self.gpu), BB_LANGUAGE='11')
        self.args = ['original-boot', '--app0', str(self.game), '--user', 'do-not-touch']
        self.calls = []
        self.component = dict(library_sha256='a'*64, native_layout={}, source_root=self.stage / 'headers')

    def tearDown(self): self.tmp.cleanup()

    def catalogue(self, **kw):
        self.calls.append(('catalogue', kw))
        return dict(report=dict(status='indexed'), cache_root=self.gpu / 'automatic-game-inputs',
                    game_root=self.game,catalog=self.gpu/'catalog.json',index=self.gpu/'index.jsonl')

    def source_view(self, **kw):
        self.calls.append(('source-view',kw))
        return dict(game_root=self.game,report=dict(status='snapshotted'))

    def candidates(self, **kw):
        self.calls.append(('candidates', kw)); kw['output'].write_bytes(b'')
        return dict(complete=True, eligible=0, excluded=0, cache_inventory_audits=[],
                    manifest_sha256=hashlib.sha256(b'').hexdigest())

    def modules(self, context, **kw):
        self.calls.append(('modules', kw))
        return dict(status='prepared', geometry_resolver=None)

    def prepare(self, **kw):
        from warm_archive_manifest import resolve_gpu_identity
        with patch('warm_archive_manifest.resolve_gpu_identity', return_value={'library_sha256':'a'*64,'library':str(self.stage/'library')}), patch.object(launcher, 'title_id', return_value='CUSA03173'):
            args = dict(project=self.project, execution_root=self.stage, arguments=self.args, environment=self.env,
                cohort='b'*64, options={}, observe=lambda:dict(pressure=1, locked=False, swap_mib=0),
                component_loader=lambda stage:self.component, toolchain_probe=lambda:True,
                catalogue_prepare=self.catalogue, candidate_builder=self.candidates, module_prepare=self.modules,
                source_view_prepare=self.source_view)
            args.update(kw); return launcher.prepare(**args)

    def test_default_empty_cache_still_runs_static_then_module_phase(self):
        before_args = self.args[:]; before_env = self.env.copy()
        result = self.prepare()
        self.assertEqual([name for name, _ in self.calls], ['source-view', 'catalogue', 'candidates', 'modules'])
        self.assertEqual(result['scan']['automatic_static_inputs']['status'], 'indexed')
        self.assertEqual(result['scan']['automatic_modules']['status'], 'prepared')
        self.assertEqual(self.args, before_args); self.assertEqual(self.env, before_env)
        self.assertFalse((self.root / 'do-not-touch').exists())

    def test_original_manifest_identity_passed_without_rescan(self):
        self.prepare(); config = next(kw for name, kw in self.calls if name == 'modules')
        ref = config['candidate_manifest']
        self.assertEqual(hashlib.sha256(Path(ref['path']).read_bytes()).hexdigest(), ref['sha256'])
        self.assertEqual(config['selected_renderer'], Path(self.env['BB_GPU_PROCESS']))
        self.assertEqual(config['backend_root'], self.stage)

    def test_missing_component_never_claims_or_starts_preparation(self):
        result = self.prepare(component_loader=lambda stage:None)
        self.assertEqual(result['status'], 'unavailable'); self.assertEqual(self.calls, [])

    def test_actual_renderer_mismatch_stops_before_source_scan(self):
        self.component['library_sha256'] = 'c'*64
        with self.assertRaisesRegex(ValueError, 'DSO mismatch'): self.prepare()
        self.assertEqual(self.calls, [])

    def test_missing_coherent_consumer_is_visible_not_false_completion(self):
        from launcher_game_modules import prepare
        result = self.prepare(module_prepare=prepare)
        self.assertEqual(result['scan']['automatic_modules']['status'], 'unavailable')
        self.assertNotIn('geometry_resolver', result['scan']['automatic_modules'])

    def test_default_capture_configuration_uses_actual_selection_even_without_pso_cache(self):
        self.component['automatic_capture_schema']=1
        def configure(**kw):
            self.calls.append(('capture',kw))
            return dict(status='configured',capture_environment={
                'BB_FETCH_CAPTURE_ROOT':str(kw['root']),'BB_FETCH_CAPTURE_GAME_ROOT':str(kw['game_root'])})
        result=self.prepare(capture_configure=configure)
        self.assertEqual([name for name,_ in self.calls],['source-view','catalogue','capture','candidates','modules'])
        config=next(kw for name,kw in self.calls if name=='capture')
        self.assertEqual(config['backend'],self.stage/'library')
        self.assertEqual(config['runtime_source_root'],self.component['source_root'])
        self.assertEqual(result['capture_environment']['BB_FETCH_CAPTURE_GAME_ROOT'],str(self.game))
        self.assertTrue(config['root'].is_relative_to(self.gpu/'automatic-game-inputs'))
        self.assertNotIn('capture_environment',result['scan']['automatic_runtime_capture'])

    def test_deferred_candidate_scan_keeps_verified_capture_but_does_not_run_modules(self):
        self.component['automatic_capture_schema']=1
        capture={'BB_FETCH_CAPTURE_ROOT':str(self.gpu/'automatic-game-inputs/runtime'),
                 'BB_FETCH_CAPTURE_GAME_ROOT':str(self.game)}
        def incomplete(**kw):
            result=self.candidates(**kw);result['complete']=False;return result
        result=self.prepare(candidate_builder=incomplete,capture_configure=lambda **kw:dict(
            status='configured',capture_environment=capture))
        self.assertEqual(result['status'],'deferred')
        self.assertEqual(result['capture_environment'],capture)
        self.assertNotIn('modules',[name for name,_ in self.calls])

    def completed_static_context(self):
        from launcher_game_catalog import output_receipt,write_json
        cache=self.gpu/'automatic-game-inputs';generation=cache/('c'*64);generation.mkdir(parents=True)
        game=cache/'source-views'/('d'*64)/'view';game.mkdir(parents=True);game.chmod(0o500)
        write_json(game.parent/'READY.json',dict(schema=1,identity='d'*64,proof=dict(authored=True)))
        (generation/'catalog.json').write_text('{}');(generation/'static-program-index.jsonl').write_text('authored static index\n')
        write_json(generation/'READY.json',dict(schema=1,identity='c'*64,files=output_receipt(generation)))
        return dict(generation=generation,identity='c'*64,game_root=game,catalog=generation/'catalog.json',
                    index=generation/'static-program-index.jsonl',cache_root=cache,report=dict(status='indexed'))

    def configured_capture(self,**kw):
        return dict(status='configured',capture_environment={'BB_FETCH_CAPTURE_ROOT':str(kw['root']),
                    'BB_FETCH_CAPTURE_GAME_ROOT':str(kw['game_root'])})

    def test_complete_empty_queue_supplies_strict_context_without_native_ready_claim(self):
        import quick_restart_preparation as quick
        self.component['automatic_capture_schema']=1;context=self.completed_static_context()
        result=self.prepare(prepared_inputs=context,restart_binding=dict(authored=True),capture_configure=self.configured_capture)
        self.assertEqual(result['status'],'empty');self.assertIs(result['scan']['complete'],True)
        self.assertEqual(result['scan']['eligible'],0);self.assertNotIn('preparation',result)
        self.assertNotIn('full_manifest_preparation_verified',result)
        verified=quick._context(result['_restart_context'],self.gpu,lambda:None)
        self.assertEqual(verified['game_root'],context['game_root'])
        self.assertEqual(verified['catalog'],context['catalog'])
        self.assertEqual([name for name,_ in self.calls],['candidates','modules'])

    def test_incomplete_empty_scan_cannot_supply_restart_context(self):
        import quick_restart_preparation as quick
        self.component['automatic_capture_schema']=1;context=self.completed_static_context()
        def incomplete(**kw):
            summary=self.candidates(**kw);summary['complete']=False;return summary
        with patch.object(quick,'pending',side_effect=AssertionError('incomplete scan cannot publish')):
            result=self.prepare(prepared_inputs=context,restart_binding=dict(authored=True),capture_configure=self.configured_capture,candidate_builder=incomplete)
        self.assertEqual(result['status'],'deferred');self.assertNotIn('_restart_context',result)
        self.assertEqual([name for name,_ in self.calls],['candidates'])

    def test_empty_without_capture_or_with_missing_static_ready_keeps_full_restart_path(self):
        import quick_restart_preparation as quick
        context=self.completed_static_context()
        with patch.object(quick,'pending',side_effect=AssertionError('capture is missing')):
            result=self.prepare(prepared_inputs=context,restart_binding=dict(authored=True))
        self.assertEqual(result['status'],'empty');self.assertNotIn('_restart_context',result)
        self.component['automatic_capture_schema']=1;(context['generation']/'READY.json').unlink()
        result=self.prepare(prepared_inputs=context,restart_binding=dict(authored=True),capture_configure=self.configured_capture)
        self.assertEqual(result['status'],'empty');self.assertNotIn('_restart_context',result)


class HealthPolling(unittest.TestCase):
    def setUp(self):
        self.now=0.0;self.sample=dict(pressure=1,locked=False,swap_mib=0);self.reads=0
        from qa_memory_policy import MemoryPolicy
        def read():self.reads+=1;return self.sample.copy()
        self.guard=launcher.StartupHealth(read,MemoryPolicy,clock=lambda:self.now)
    def test_many_file_checks_share_only_the_recent_os_sample(self):
        self.guard.check(force=True)
        for _ in range(10000):self.guard.check()
        self.assertEqual(self.reads,2)
        self.assertEqual(self.guard.stats['coalesced'],10000)
        self.now=.101;self.guard.check();self.assertEqual(self.reads,3)
    def test_forced_handoff_observes_lock_without_waiting_for_interval(self):
        self.guard.check();self.now=.001;self.sample['locked']=True
        with self.assertRaisesRegex(RuntimeError,'screen locked'):self.guard.check(force=True)
    def test_poll_interval_observes_severe_pressure_for_original_five_seconds(self):
        self.guard.check();self.sample['pressure']=4
        self.now=.101;self.guard.check()
        self.now=5.102
        with self.assertRaisesRegex(RuntimeError,'severe pressure sustained5s'):self.guard.check()
    def test_swap_growth_and_unknown_samples_keep_original_rejection(self):
        self.sample['swap_mib']=2049
        with self.assertRaisesRegex(RuntimeError,'2GiB'):self.guard.check(force=True)
        self.sample['pressure']=3
        with self.assertRaisesRegex(ValueError,'pressure'):self.guard.check(force=True)
    def test_clock_reversal_is_rejected_even_with_coalescing(self):
        self.guard.check();self.now=-.01
        with self.assertRaisesRegex(ValueError,'clock reversed'):self.guard.check()


class MixedSchedule(unittest.TestCase):
    def report(self):
        return dict(status='completed',required_preparation_complete=True,index_complete=True,
            unique_units=575,completed=575,scheduled_reused=567,verified_artifact_hits=8,
            collection_strict_hits=8,strict_lookup_attempts=8,deferred=0,rejected=0,
            protection_stops=0,index_rejected=0,source_late_deferred=0,
            final_environment_verification=dict(status='environment-verified',environment_identity='a'*64),
            recipe_schedule=dict(reused=567,published=0,refused=24),full_manifest_preparation_verified=False)

    def test_actual_verified_gs_plus_persistent_plain_does_not_need_all_persistence(self):
        result=self.report()
        self.assertTrue(launcher.verified_mixed_schedule(result))
        self.assertFalse(result['full_manifest_preparation_verified'])
        self.assertEqual(result['recipe_schedule']['published'],0)

    def test_warm_strict_collection_uses_its_real_environment_verification_field(self):
        result=self.report();result['warm_fast_path']=True
        result['warm_environment_verification']=result.pop('final_environment_verification')
        self.assertTrue(launcher.verified_mixed_schedule(result))
        result['warm_fast_path']=False
        self.assertFalse(launcher.verified_mixed_schedule(result))
        result['warm_fast_path']=True
        result['warm_environment_verification']['status']='environment-captured'
        self.assertFalse(launcher.verified_mixed_schedule(result))

    def test_missing_partial_failed_bool_or_unverified_environment_never_complete(self):
        import copy
        cases=[('status','partial'),('required_preparation_complete',False),('index_complete',False),
            ('completed',574),('verified_artifact_hits',7),('collection_strict_hits',7),
            ('strict_lookup_attempts',9),('deferred',1),('rejected',1),('protection_stops',1),
            ('index_rejected',1),('source_late_deferred',1),('scheduled_reused',True),
            ('final_environment_verification',dict(status='environment-captured',environment_identity='a'*64)),
            ('final_environment_verification',dict(status='environment-verified',environment_identity='invalid'))]
        for field,value in cases:
            with self.subTest(field=field,value=value):
                result=copy.deepcopy(self.report());result[field]=value
                self.assertFalse(launcher.verified_mixed_schedule(result))
        for key in self.report():
            result=self.report();del result[key]
            if key not in('recipe_schedule','full_manifest_preparation_verified'):
                self.assertFalse(launcher.verified_mixed_schedule(result),key)

    def test_real_controller_publishes_mixed_only_with_strict_static_context(self):
        fixture=Entry();fixture.setUp()
        try:
            fixture.component.update(automatic_capture_schema=1,worker=fixture.stage/'worker',
                worker_sha256='a'*64,layout=fixture.stage/'layout',layout_sha256='b'*64)
            context=fixture.completed_static_context()
            def candidates(**kw):
                value=fixture.candidates(**kw);value['eligible']=575;return value
            result=fixture.prepare(prepared_inputs=context,restart_binding=dict(authored=True),
                capture_configure=fixture.configured_capture,candidate_builder=candidates,runner=lambda config:self.report())
            self.assertIn('_restart_context',result)
            self.assertFalse(result['runtime_cache_import_allowed'])
            self.assertFalse(result['preparation']['full_manifest_preparation_verified'])
            report=self.report();report['strict_lookup_attempts']=7
            result=fixture.prepare(prepared_inputs=context,restart_binding=dict(authored=True),
                capture_configure=fixture.configured_capture,candidate_builder=candidates,runner=lambda config:report)
            self.assertNotIn('_restart_context',result)
        finally:fixture.tearDown()


if __name__ == '__main__': unittest.main()
