"""Original merge algorithm plus authored APFS views; no installed game inputs."""
from pathlib import Path
import hashlib, json, os, shutil, sys, tempfile, unittest
from collections import Counter
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import launcher_source_view as view

class SourceView(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name).resolve()
        self.project=self.base/'prototype';self.project.mkdir();self.gpu=self.project/'state/gpu';self.gpu.mkdir(parents=True)
        self.game=self.base/'game';(self.game/'dvdroot_ps4/shader').mkdir(parents=True)
        (self.game/'eboot.bin').write_bytes(b'authored ELF placeholder; never executed')
        (self.game/'dvdroot_ps4/shader/example.bin').write_bytes(b'authored base shader')
        self.stage=self.base/'stage';(self.stage/'scripts').mkdir(parents=True)
        source=Path(__file__).resolve().parents[3]/'scripts/mods.py'
        shutil.copyfile(source,self.stage/'scripts/mods.py')
        (self.stage/'SOURCE-PARITY.json').write_text(json.dumps(dict(effective_sources={
            'scripts/mods.py':hashlib.sha256(source.read_bytes()).hexdigest()})))
        self.mods=self.base/'mods';self.mods.mkdir();self.out=self.base/'out';self.out.mkdir()
        self.env=dict(BB_GAME_DIR=str(self.game),BB_DATA_DIR=str(self.base),BB_MODS_DIR=str(self.mods))
        self.clones=0

    def tearDown(self):
        for parent,dirs,files in os.walk(self.base):
            Path(parent).chmod(0o700)
        self.tmp.cleanup()

    def module(self):return view.load_original(self.stage)[0]

    def actual(self):
        module=self.module()
        layers=[(name,self.mods/name)for name in module.selected(self.mods,None)]
        return module.build_overlay(self.game,self.out,layers)

    def clone(self,source,target):
        self.clones+=1;view.clone(source,target)

    def prepare(self,actual=None,**kw):
        return view.prepare(project=self.project,gpu_root=self.gpu,stage=self.stage,
            arguments=['boot','--app0',str(actual or self.actual()),'--user','untouched'],
            environment=self.env,clone_file=kw.pop('clone_file',self.clone),**kw)

    def test_real_clone_has_distinct_inode_and_no_source_link_or_content_change(self):
        source=self.game/'eboot.bin';before=source.stat();raw=source.read_bytes()
        result=self.prepare();target=result['game_root']/'eboot.bin'
        self.assertEqual(target.read_bytes(),raw);self.assertNotEqual(target.stat().st_ino,before.st_ino)
        self.assertEqual(source.stat().st_nlink,before.st_nlink);self.assertEqual(source.read_bytes(),raw)
        self.assertEqual(target.stat().st_mode&0o777,0o400)
        self.assertTrue(result['report']['CoW'])

    def test_mod_override_matches_original_merge_not_base_game(self):
        mod=self.mods/'authored/dvdroot_ps4/shader';mod.mkdir(parents=True)
        (mod/'example.bin').write_bytes(b'authored mod shader')
        original=self.actual();before=(self.game/'dvdroot_ps4/shader/example.bin').read_bytes()
        result=self.prepare(original)
        self.assertEqual((result['game_root']/'dvdroot_ps4/shader/example.bin').read_bytes(),b'authored mod shader')
        self.assertEqual((self.game/'dvdroot_ps4/shader/example.bin').read_bytes(),before)
        self.assertTrue((original/'eboot.bin').is_symlink())

    def test_new_ephemeral_overlay_reuses_same_stable_snapshot(self):
        mod=self.mods/'authored/dvdroot_ps4/msg';mod.mkdir(parents=True);(mod/'text.bin').write_bytes(b'authored text')
        first=self.prepare();count=self.clones;second=self.prepare()
        self.assertEqual(first['game_root'],second['game_root']);self.assertEqual(self.clones,count)
        self.assertEqual(second['report']['status'],'reused')

    def test_unknown_link_target_rejected_before_clone(self):
        mod=self.mods/'authored/dvdroot_ps4/msg';mod.mkdir(parents=True);(mod/'text.bin').write_bytes(b'text')
        actual=self.actual();outside=self.base/'unselected';outside.write_bytes(b'private unrelated bytes')
        (actual/'eboot.bin').unlink();(actual/'eboot.bin').symlink_to(outside)
        with self.assertRaisesRegex(ValueError,'escape'):self.prepare(actual)
        self.assertEqual(self.clones,0)

    def test_different_selected_source_not_accepted_by_matching_contents(self):
        mod=self.mods/'authored/dvdroot_ps4/msg';mod.mkdir(parents=True);(mod/'text.bin').write_bytes(b'text')
        actual=self.actual();wrong=self.game/'other.bin';wrong.write_bytes((self.game/'eboot.bin').read_bytes())
        (actual/'eboot.bin').unlink();(actual/'eboot.bin').symlink_to(wrong)
        with self.assertRaisesRegex(ValueError,'differs'):self.prepare(actual)
        self.assertEqual(self.clones,0)

    def test_clone_failure_does_not_fall_back_to_copy_or_modify_source(self):
        source=self.game/'eboot.bin';before=source.read_bytes()
        def unsupported(*_):raise view.SnapshotUnavailable(18,'authored cross-volume refusal')
        with self.assertRaises(view.SnapshotUnavailable):self.prepare(clone_file=unsupported)
        self.assertEqual(source.read_bytes(),before)
        result=self.prepare();count=self.clones;again=self.prepare()
        self.assertEqual(result['game_root'],again['game_root']);self.assertEqual(self.clones,count)

    def test_source_tree_symlinks_are_not_laundered(self):
        (self.game/'alias').symlink_to(self.game/'eboot.bin')
        with self.assertRaisesRegex(ValueError,'linked original'):self.prepare()
        self.assertEqual(self.clones,0)

    def test_original_mod_algorithm_tamper_refused(self):
        (self.stage/'scripts/mods.py').write_text('raise AssertionError("never execute")')
        with self.assertRaisesRegex(ValueError,'algorithm changed'):self.prepare(self.game)

    def test_original_unset_game_directory_default_is_preserved(self):
        target=self.base/'CUSA03173';self.game.rename(target);self.game=target
        self.env.pop('BB_GAME_DIR')
        result=self.prepare(self.game)
        self.assertEqual((result['game_root']/'eboot.bin').read_bytes(),(self.game/'eboot.bin').read_bytes())

    def test_original_relative_game_mod_config_and_translation_paths(self):
        config=self.base/'selected.json';config.write_text('{}')
        translation=self.base/'translation';(translation/'dvdroot_ps4/msg').mkdir(parents=True)
        (translation/'dvdroot_ps4/msg/text.bin').write_bytes(b'authored translated text')
        self.env.update(BB_GAME_DIR='../game',BB_MODS_DIR='../mods',BB_MODS_CONFIG='../selected.json',
            BB_CHINESE_SOURCE='community',BB_CHINESE_LANGUAGE='11',BB_CHINESE_TRANSLATION_DIR='../translation')
        actual=self.module().build_overlay(self.game,self.out,[('Chinese Retranslation',translation)])
        result=self.prepare(actual)
        self.assertEqual((result['game_root']/'dvdroot_ps4/msg/text.bin').read_bytes(),b'authored translated text')

    def test_community_translation_keeps_original_last_mod_precedence(self):
        normal=self.mods/'normal/dvdroot_ps4/msg';normal.mkdir(parents=True);(normal/'text.bin').write_bytes(b'normal mod text')
        chinese=self.base/'translation';(chinese/'dvdroot_ps4/msg').mkdir(parents=True)
        (chinese/'dvdroot_ps4/msg/text.bin').write_bytes(b'authored community text')
        self.env.update(BB_CHINESE_SOURCE='community',BB_CHINESE_LANGUAGE='11',BB_CHINESE_TRANSLATION_DIR=str(chinese))
        actual=self.module().build_overlay(self.game,self.out,[('normal',self.mods/'normal'),('Chinese Retranslation',chinese)])
        result=self.prepare(actual)
        self.assertEqual((result['game_root']/'dvdroot_ps4/msg/text.bin').read_bytes(),b'authored community text')
        self.assertEqual((normal/'text.bin').read_bytes(),b'normal mod text')

    def test_mod_disable_policy_uses_original_configuration(self):
        mod=self.mods/'authored/dvdroot_ps4/shader';mod.mkdir(parents=True);(mod/'example.bin').write_bytes(b'modified')
        config=self.base/'mods.json';config.write_text(json.dumps(dict(disabled=['authored'])))
        self.env['BB_MODS_CONFIG']=str(config)
        result=self.prepare(self.game)
        self.assertEqual((result['game_root']/'dvdroot_ps4/shader/example.bin').read_bytes(),b'authored base shader')

    def test_modified_cache_snapshot_is_refused_and_preserved(self):
        first=self.prepare();p=first['game_root']/'eboot.bin';p.chmod(0o600);p.write_bytes(b'changed private snapshot')
        with self.assertRaisesRegex(ValueError,'snapshot contents changed'):self.prepare()
        self.assertEqual(p.read_bytes(),b'changed private snapshot')

    def test_source_content_change_creates_new_snapshot_and_keeps_previous(self):
        first=self.prepare();old=(first['game_root']/'eboot.bin').read_bytes()
        (self.game/'eboot.bin').write_bytes(b'new authored input')
        second=self.prepare();self.assertNotEqual(first['game_root'],second['game_root'])
        self.assertEqual((first['game_root']/'eboot.bin').read_bytes(),old)
        self.assertEqual((second['game_root']/'eboot.bin').read_bytes(),b'new authored input')

    def test_earlier_source_change_during_later_clone_prevents_ready(self):
        shader=self.game/'dvdroot_ps4/shader/example.bin'
        def mutate(source,target):
            view.clone(source,target)
            if source.name=='eboot.bin':shader.write_bytes(b'changed during later clone')
        with self.assertRaisesRegex(ValueError,'selected source contents changed'):
            self.prepare(clone_file=mutate)
        self.assertFalse(list((self.gpu/'automatic-game-inputs/source-views').glob('*/READY.json')))
        self.assertEqual(shader.read_bytes(),b'changed during later clone')

    def test_warm_snapshot_extra_file_is_refused_without_deletion(self):
        first=self.prepare();root=first['game_root'];root.chmod(0o700)
        extra=root/'unexpected.bin';extra.write_bytes(b'authored unadmitted file');root.chmod(0o500)
        with self.assertRaisesRegex(ValueError,'snapshot file set changed'):self.prepare()
        self.assertEqual(extra.read_bytes(),b'authored unadmitted file')

    def no_ready(self):
        self.assertFalse(list((self.gpu/'automatic-game-inputs/source-views').glob('*/READY.json')))

    def test_cold_hashes_each_source_twice_and_each_clone_once(self):
        counts=Counter();original=view.file_sha
        def count(path,*args,**kwargs):
            counts[Path(path)]+=1;return original(path,*args,**kwargs)
        sources=[p for p in self.game.rglob('*')if p.is_file()]
        with patch.object(view,'file_sha',side_effect=count):result=self.prepare()
        for source in sources:
            self.assertEqual(counts[source],2,'initial records and final live source SHA remain')
            destination=result['game_root']/source.relative_to(self.game)
            self.assertEqual(counts[destination],1,'final complete snapshot SHA remains')
            self.assertEqual(destination.stat().st_mode&0o777,0o400)
        self.assertEqual(result['game_root'].stat().st_mode&0o777,0o500)

    def test_warm_hash_passes_unchanged_and_no_more_clones(self):
        first=self.prepare();clones=self.clones;counts=Counter();original=view.file_sha
        def count(path,*args,**kwargs):counts[Path(path)]+=1;return original(path,*args,**kwargs)
        with patch.object(view,'file_sha',side_effect=count):second=self.prepare()
        self.assertEqual(first['game_root'],second['game_root']);self.assertEqual(self.clones,clones)
        for source in self.game.rglob('*'):
            if source.is_file():
                self.assertEqual(counts[source],2)
                self.assertEqual(counts[first['game_root']/source.relative_to(self.game)],1)

    def test_same_size_change_before_clone_is_rejected_by_final_snapshot_sha(self):
        source=self.game/'eboot.bin';old=source.read_bytes();stamp=source.stat()
        def mutate(path,target):
            if path==source:
                path.write_bytes(b'X'*len(old));os.utime(path,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
            view.clone(path,target)
        with self.assertRaisesRegex(ValueError,'snapshot contents changed'):self.prepare(clone_file=mutate)
        self.no_ready()

    def test_corrupt_clone_even_with_restored_source_is_rejected(self):
        source=self.game/'eboot.bin';old=source.read_bytes()
        def mutate(path,target):
            if path==source:path.write_bytes(b'X'*len(old))
            view.clone(path,target)
            if path==source:path.write_bytes(old)
        with self.assertRaisesRegex(ValueError,'snapshot contents changed'):self.prepare(clone_file=mutate)
        self.assertEqual(source.read_bytes(),old);self.no_ready()

    def test_same_size_clone_tampering_is_rejected_by_final_snapshot_sha(self):
        def mutate(source,target):
            view.clone(source,target)
            if source.name=='eboot.bin':target.write_bytes(b'X'*target.stat().st_size)
        with self.assertRaisesRegex(ValueError,'snapshot contents changed'):self.prepare(clone_file=mutate)
        self.no_ready()

    def test_source_change_after_snapshot_validation_is_rejected_by_final_live_sha(self):
        original=view.verify_snapshot;source=self.game/'eboot.bin';old=source.read_bytes();stamp=source.stat()
        def verify(*args,**kwargs):
            original(*args,**kwargs)
            source.write_bytes(b'X'*len(old));os.utime(source,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
        with patch.object(view,'verify_snapshot',side_effect=verify):
            with self.assertRaisesRegex(ValueError,'selected source contents changed'):self.prepare()
        self.no_ready()

    def test_final_snapshot_file_set_cannot_gain_unhashed_extra_file(self):
        def add_extra(source,target):
            view.clone(source,target)
            if source.name=='eboot.bin':(target.parent/'not-in-records').write_bytes(b'author extra')
        with self.assertRaisesRegex(ValueError,'snapshot file set changed'):self.prepare(clone_file=add_extra)
        self.no_ready()

    def test_after_clone_source_link_and_hardlink_destination_are_refused(self):
        source=self.game/'eboot.bin';other=self.game/'other.bin';other.write_bytes(source.read_bytes())
        def replace(path,target):
            view.clone(path,target)
            if path==source:path.unlink();path.symlink_to(other.name)
        with self.assertRaisesRegex(ValueError,'linked CoW'):self.prepare(clone_file=replace)
        self.no_ready();source.unlink();source.write_bytes(other.read_bytes())
        def hardlink(path,target):os.link(path,target)
        with self.assertRaisesRegex(ValueError,'must not alias'):self.prepare(clone_file=hardlink)
        self.no_ready()

    def test_late_original_mod_selection_change_prevents_ready(self):
        actual=self.actual();original=view.verify_snapshot
        def verify(*args,**kwargs):
            original(*args,**kwargs);self.env['BB_MODS_ENABLED']='0'
        with patch.object(view,'verify_snapshot',side_effect=verify):
            with self.assertRaisesRegex(ValueError,'mod selection changed'):self.prepare(actual)
        self.no_ready()

    def test_late_mapping_change_is_rejected_even_with_equal_bytes(self):
        mod=self.mods/'authored/dvdroot_ps4/msg';mod.mkdir(parents=True);(mod/'text.bin').write_bytes(b'author mod')
        duplicate=self.game/'duplicate.bin';duplicate.write_bytes((self.game/'eboot.bin').read_bytes())
        actual=self.actual();original=view.verify_snapshot
        def verify(*args,**kwargs):
            original(*args,**kwargs);link=actual/'eboot.bin';link.unlink();link.symlink_to(duplicate)
        with patch.object(view,'verify_snapshot',side_effect=verify):
            with self.assertRaisesRegex(ValueError,'mapping changed'):self.prepare(actual)
        self.no_ready()


if __name__=='__main__':unittest.main()
