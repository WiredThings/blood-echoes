"""Automatic orchestration controls; authored sources, no private game data."""
from pathlib import Path
import hashlib, json, sys, tempfile, unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import launcher_game_catalog as catalog


class AutomaticCatalogue(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.project = self.base / 'prototype'; self.project.mkdir()
        self.gpu = self.project / 'state/gpu'; self.gpu.mkdir(parents=True)
        self.game = self.base / 'game'; self.game.mkdir()
        (self.game / 'game.bin').write_bytes(b'authored static source')
        self.source = self.base / 'source'; self.source.mkdir()
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def runner(self, path, check):
        check(); request = json.loads(path.read_text()); self.calls.append(request)
        output = Path(request['output'])
        (output / 'catalog.json').write_text('{}\n')
        (output / 'static-program-index.jsonl').write_text('')
        catalog.write_json(output / 'READY.json', dict(schema=1, identity=request['identity'],
            files=catalog.output_receipt(output), complete_pso_claim=False))

    def prepare(self, **kw):
        with patch.object(catalog, 'scanner_identity', return_value='b' * 64):
            return catalog.prepare(project=self.project, gpu_root=self.gpu, game_root=self.game,
                source_root=self.source, cohort=kw.pop('cohort', 'a' * 64),
                runner=kw.pop('runner', self.runner), **kw)

    def test_empty_install_indexes_without_existing_gpu_cache(self):
        result = self.prepare()
        self.assertEqual(result['report']['status'], 'indexed')
        self.assertTrue(result['index'].exists())
        self.assertTrue(result['report']['default_enabled'])
        self.assertFalse(result['report']['full_pso_claim'])

    def test_new_catalogue_reuses_original_snapshot_byte_proof_without_rereading_game(self):
        from test_launcher_source_view import SourceView
        authored=SourceView();authored.setUp();self.addCleanup(authored.tearDown)
        with authored.history():
            selected=authored.prepare();game=selected['game_root'];original=catalog.file_sha
            def no_source_read(path,*args):
                self.assertFalse(Path(path).is_relative_to(game),'verified snapshot reread by catalogue')
                return original(path,*args)
            with patch.object(catalog,'scanner_identity',return_value='b'*64),patch.object(catalog,'file_sha',side_effect=no_source_read):
                result=catalog.prepare(project=authored.project,gpu_root=authored.gpu,game_root=game,
                    source_root=self.source,cohort='a'*64,runner=self.runner)
            self.assertEqual(result['report']['source_hashes']['hashed_files'],0)
            self.assertTrue(result['report']['source_hashes']['journal_reused'])
            self.assertEqual(len(self.calls),1)

    def test_registered_snapshot_bad_proof_or_lost_history_cannot_authorize_inventory(self):
        from test_launcher_source_view import SourceView
        authored=SourceView();authored.setUp();self.addCleanup(authored.tearDown)
        import startup_change_journal as journal
        with authored.history():
            selected=authored.prepare();game=selected['game_root'];cache=authored.gpu/'automatic-game-inputs'
            self.assertIsNotNone(catalog.registered_snapshot_hashes(game,cache,lambda:None))
            with patch.object(journal,'unchanged',return_value=False):
                self.assertIsNone(catalog.registered_snapshot_hashes(game,cache,lambda:None))
            ready=game.parent/'READY.json';old=ready.read_bytes();value=json.loads(old);value['proof']['files'][0]['sha256']='0'*64;ready.write_text(json.dumps(value))
            self.assertIsNone(catalog.registered_snapshot_hashes(game,cache,lambda:None));self.assertNotEqual(old,ready.read_bytes())

    def test_warm_start_revalidates_outputs_and_skips_decoder(self):
        first = self.prepare(); second = self.prepare()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(second['report']['status'], 'reused')
        self.assertEqual(first['generation'], second['generation'])

    def test_change_addition_and_removal_each_invalidate(self):
        previous = self.prepare()
        for action in ('change', 'add', 'remove'):
            if action == 'change': (self.game / 'game.bin').write_bytes(b'changed source')
            elif action == 'add': (self.game / 'new.bin').write_bytes(b'new source')
            else: (self.game / 'new.bin').unlink()
            current = self.prepare()
            self.assertNotEqual(previous['identity'], current['identity'])
            self.assertTrue(previous['index'].exists())
            previous = current

    def test_option_cohort_kept_separate_from_static_source_identity(self):
        first = self.prepare(); second = self.prepare(cohort='c' * 64)
        self.assertEqual(first['identity'], second['identity'])
        self.assertNotEqual(first['cohort'], second['cohort'])
        self.assertEqual(len(self.calls), 1)

    def test_content_change_with_preserved_size_and_mtime_invalidates(self):
        import os
        first = self.prepare(); p = self.game / 'game.bin'; s = p.stat()
        raw = p.read_bytes(); p.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
        os.utime(p, ns=(s.st_atime_ns, s.st_mtime_ns))
        self.assertNotEqual(first['identity'], self.prepare()['identity'])

    def test_output_tampering_refused_without_overwrite(self):
        first = self.prepare(); first['index'].write_text('tampered\n')
        second=self.prepare()
        self.assertNotEqual(first['generation'],second['generation'])
        self.assertIn('-retry-',second['generation'].name)
        self.assertEqual(second['report']['cache_recovery'],'static-metadata-invalid')
        self.assertEqual(first['index'].read_text(), 'tampered\n')
        self.assertEqual(len(self.calls), 2)

    def test_source_file_and_directory_links_refused(self):
        link = self.game / 'link'; link.symlink_to(self.game / 'game.bin')
        with self.assertRaisesRegex(ValueError, 'linked'):
            self.prepare()
        link.unlink(); link.symlink_to(self.source, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'canonical'):
            self.prepare()
        self.assertEqual(self.calls, [])

    def test_formal_state_root_refused(self):
        with self.assertRaisesRegex(ValueError, 'isolated selected'):
            catalog.prepare(project=self.project, gpu_root=self.project, game_root=self.game,
                source_root=self.source, cohort='a' * 64, runner=self.runner)

    def test_failed_worker_cannot_publish_success_and_retry_reused(self):
        def fail(request, check):
            raise RuntimeError('authored interruption')
        with self.assertRaisesRegex(RuntimeError, 'authored interruption'):
            self.prepare(runner=fail)
        second = self.prepare(); third = self.prepare()
        self.assertIn('-retry-', second['generation'].name)
        self.assertEqual(third['generation'], second['generation'])
        self.assertEqual(third['report']['status'], 'reused')

    def test_missing_ready_is_failure(self):
        with self.assertRaisesRegex(ValueError, 'did not publish'):
            self.prepare(runner=lambda request, check: None)

    def test_protection_stops_before_worker(self):
        def stop(): raise RuntimeError('authored severe pressure')
        with self.assertRaisesRegex(RuntimeError, 'severe pressure'):
            self.prepare(check=stop)
        self.assertEqual(self.calls, [])

    def test_no_sources_or_existing_caches_are_modified(self):
        old = self.gpu / 'existing-cache'; old.write_bytes(b'keep')
        before = catalog.source_inventory(self.game)
        self.prepare()
        self.assertEqual(before, catalog.source_inventory(self.game))
        self.assertEqual(old.read_bytes(), b'keep')

    def test_real_existing_decoders_publish_authored_static_index(self):
        import contextlib, io, struct
        from test_game_shader_catalog import sfo, binder, wrap
        from test_gnm_shader_metadata import fixture
        (self.game / 'sce_sys').mkdir()
        (self.game / 'sce_sys/param.sfo').write_bytes(sfo())
        shader = self.game / 'dvdroot_ps4/shader'; shader.mkdir(parents=True)
        (shader / 'authored.shaderbnd.dcx').write_bytes(wrap(binder(fixture(4))))
        material = self.game / 'dvdroot_ps4/mtd'; material.mkdir()
        (material / 'allmaterialbnd.mtdbnd.dcx').write_bytes(wrap(binder(b'authored material')))
        elf = bytearray(5000) + fixture(4, wrapper=38)
        ident = b'\x7fELF\x02\x01\x01' + bytes(9)
        struct.pack_into('<16sHHIQQQIHHHHHH', elf, 0, ident, 2, 62, 1,
                         0, 64, 0, 0, 64, 56, 1, 0, 0, 0)
        struct.pack_into('<IIQQQQQQ', elf, 64, 1, 4, 0, 0, 0, len(elf), len(elf), 1)
        (self.game / 'eboot.bin').write_bytes(elf)
        output = self.gpu / 'authored-output'; output.mkdir(mode=0o700)
        sources = catalog.source_inventory(self.game)
        native = Path(__file__).resolve().parents[1] / 'vendor/metal_api/shadps4'
        with contextlib.redirect_stdout(io.StringIO()):
            result = catalog.build_static(game_root=self.game, source_root=native,
                output=output, identity='d' * 64, source_files=sources)
        self.assertFalse(result['complete_pso_claim'])
        rows = [json.loads(line) for line in (output / 'static-program-index.jsonl').read_text().splitlines()]
        self.assertTrue(rows)
        self.assertTrue(all(row['identity']['hardware_stage'] == 'compute' for row in rows))
        embedded = next(p for p in json.loads((output / 'catalog.json').read_text())['packages'] if p['kind'] == 'embedded')
        self.assertEqual(embedded['offset_domain'], 'reconstructed-elf-bytes')
        self.assertFalse(embedded['compressed_segments_not_decoded'])
        gaps = [json.loads(line) for line in (output / 'gaps.jsonl').read_text().splitlines()]
        self.assertTrue(any(row['kind'] == 'dynamic-compile-input-required' for row in gaps))
        self.assertEqual(result, catalog.read_ready(output, 'd' * 64))
        self.assertEqual(sources, catalog.source_inventory(self.game))


if __name__ == '__main__': unittest.main()
