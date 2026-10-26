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


if __name__ == '__main__': unittest.main()
