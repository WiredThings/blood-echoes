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

    def journal(self,roots,previous=None):
        # Authored deterministic event ledger; it is intentionally independent
        # of the production mapping/hash helpers asserted absent below.
        rows=[]
        for root in roots:
            p=Path(root);s=p.lstat();rows.append((str(p),s.st_ino,s.st_mode))
            for parent,dirs,names in os.walk(p):
                for name in sorted(dirs+names):
                    f=Path(parent)/name;s=f.lstat()
                    rows.append((str(f),s.st_mode,s.st_ino,hashlib.sha256(f.read_bytes()).hexdigest()if f.is_file()else None))
        return dict(schema=1,authored=view.digest(rows))

    def history(self):
        import contextlib,startup_change_journal as journal
        stack=contextlib.ExitStack();stack.enter_context(patch.object(journal,'query',side_effect=self.journal))
        stack.enter_context(patch.object(journal,'unchanged',side_effect=lambda token,roots:token==self.journal(roots)))
        return stack

    def test_registered_warm_does_not_walk_stat_or_hash_game_files(self):
        mod=self.mods/'a/dvdroot_ps4/msg';mod.mkdir(parents=True);(mod/'text.bin').write_bytes(b'text')
        with self.history():
            first=self.prepare();before=self.clones;hasher=view.file_sha
            actual=self.actual();read=[]
            def record(path,*args):read.append(Path(path));return hasher(path,*args)
            with patch.object(view,'mapping',side_effect=AssertionError('full mapping on warm launch')),patch.object(view,'plain_tree',side_effect=AssertionError('full tree on warm launch')),patch.object(view,'file_sha',side_effect=record):
                second=self.prepare(actual)
            self.assertEqual(second['game_root'],first['game_root']);self.assertEqual(before,self.clones)
            self.assertEqual(second['report']['reuse_mode'],'registered-selection')
            self.assertFalse(second['report']['full_tree_enumerated'])
            self.assertFalse(any(p.is_relative_to(self.game)or p.is_relative_to(first['game_root'])for p in read))

    def test_mod_A_AB_A_returns_to_registered_A_without_full_registration(self):
        a=self.mods/'a/dvdroot_ps4/msg';a.mkdir(parents=True);(a/'text.bin').write_bytes(b'a')
        config=self.base/'mods.json';config.write_text('{"disabled":["b"]}');self.env['BB_MODS_CONFIG']=str(config)
        with self.history():
            first=self.prepare();b=self.mods/'b/dvdroot_ps4/msg';b.mkdir(parents=True);(b/'text.bin').write_bytes(b'b')
            config.write_text('{}');both=self.prepare()
            self.assertNotEqual(first['game_root'],both['game_root'])
            config.write_text('{"disabled":["b"]}')
            actual=self.module().build_overlay(self.game,self.out,[('a',self.mods/'a')])
            with patch.object(view,'mapping',side_effect=AssertionError('full game registration repeated')):
                restored=self.prepare(actual)
            self.assertEqual(restored['game_root'],first['game_root'])
            self.assertEqual((restored['game_root']/'dvdroot_ps4/msg/text.bin').read_bytes(),b'a')

    def test_new_mod_selection_keeps_complete_base_registration_without_walking_or_hashing_base(self):
        a=self.mods/'a/dvdroot_ps4/shader';a.mkdir(parents=True);(a/'example.bin').write_bytes(b'a override')
        with self.history():
            first=self.prepare()
            b=self.mods/'b/dvdroot_ps4/msg';b.mkdir(parents=True);(b/'text.bin').write_bytes(b'b added')
            original_map=view.mapping;original_plain=view.plain_tree;original_hash=view.file_sha
            def map_(root,*args):
                self.assertNotEqual(Path(root),self.game,'base registration should survive a new Mod')
                return original_map(root,*args)
            def plain(root,*args):
                self.assertNotEqual(Path(root),self.game,'base tree should not be walked for a Mod change')
                return original_plain(root,*args)
            def hash_(path,*args):
                self.assertFalse(Path(path).is_relative_to(self.game),'base bytes should already be registered')
                return original_hash(path,*args)
            with patch.object(view,'mapping',side_effect=map_),patch.object(view,'plain_tree',side_effect=plain),patch.object(view,'file_sha',side_effect=hash_):
                second=self.prepare()
            self.assertTrue(second['report']['source_hashes']['base_inventory_reused'])
            self.assertEqual(second['report']['source_hashes']['hashed_files'],1)
            # Hidden vanilla is still registered even though neither view used
            # it. Turning all Mods off selects the original without rereading.
            self.env['BB_MODS_ENABLED']='0'
            with patch.object(view,'mapping',side_effect=map_),patch.object(view,'plain_tree',side_effect=plain),patch.object(view,'file_sha',side_effect=hash_):
                vanilla=self.prepare(self.game)
            self.assertEqual(vanilla['report']['source_hashes']['hashed_files'],0)
            self.assertEqual((vanilla['game_root']/'dvdroot_ps4/shader/example.bin').read_bytes(),b'authored base shader')
            self.assertEqual((first['game_root']/'dvdroot_ps4/shader/example.bin').read_bytes(),b'a override')

    def test_new_base_member_during_snapshot_is_not_hidden_by_registered_inventory(self):
        with self.history():
            self.prepare();mod=self.mods/'a/dvdroot_ps4/msg';mod.mkdir(parents=True);(mod/'text.bin').write_bytes(b'new')
            changed=False
            def mutate(source,target):
                nonlocal changed
                view.clone(source,target)
                if not changed:(self.game/'new-file.bin').write_bytes(b'new base member');changed=True
            with self.assertRaisesRegex(ValueError,'base file set changed'):
                self.prepare(clone_file=mutate)

    def test_registered_base_directory_replaced_by_mod_file_keeps_original_mapping(self):
        with self.history():
            self.prepare();mod=self.mods/'a/dvdroot_ps4';mod.mkdir(parents=True);(mod/'shader').write_bytes(b'directory replaced by selected leaf')
            result=self.prepare()
            self.assertEqual((result['game_root']/'dvdroot_ps4/shader').read_bytes(),b'directory replaced by selected leaf')
            self.assertFalse((result['game_root']/'dvdroot_ps4/shader/example.bin').exists())

    def test_real_kernel_clones_reuse_verified_readonly_bytes_and_only_hash_new_mod(self):
        with self.history():
            first=self.prepare(clone_file=view.KERNEL_CLONE)
            mod=self.mods/'a/dvdroot_ps4/msg';mod.mkdir(parents=True);(mod/'text.bin').write_bytes(b'new selected resource')
            second=self.prepare(clone_file=view.KERNEL_CLONE)
            self.assertEqual(second['report']['sealed_kernel_clones'],2)
            self.assertEqual(second['report']['snapshot_hashes']['hashed_files'],1)
            self.assertEqual(second['report']['source_hashes']['hashed_files'],1)
            for rel in('eboot.bin','dvdroot_ps4/shader/example.bin'):
                a=first['game_root']/rel;b=second['game_root']/rel
                self.assertEqual(a.read_bytes(),b.read_bytes());self.assertNotEqual(a.stat().st_ino,b.stat().st_ino)
                self.assertEqual(b.stat().st_nlink,1);self.assertEqual(b.stat().st_mode&0o777,0o400)
            self.assertEqual((second['game_root']/'dvdroot_ps4/msg/text.bin').read_bytes(),b'new selected resource')

    def test_custom_clone_callbacks_never_gain_kernel_equality_authority(self):
        with self.history():
            self.prepare(clone_file=view.KERNEL_CLONE);mod=self.mods/'a/dvdroot_ps4/msg';mod.mkdir(parents=True);(mod/'text.bin').write_bytes(b'new')
            def wrong(source,target):
                view.clone(source,target)
                if source.name=='eboot.bin':target.write_bytes(b'X'*target.stat().st_size)
            with self.assertRaisesRegex(ValueError,'snapshot contents changed'):self.prepare(clone_file=wrong)

    def test_lost_retained_snapshot_history_uses_original_byte_verification(self):
        import startup_change_journal as journal
        with self.history():
            first=self.prepare(clone_file=view.KERNEL_CLONE)
            mod=self.mods/'a/dvdroot_ps4/msg';mod.mkdir(parents=True);(mod/'text.bin').write_bytes(b'new')
            with patch.object(journal,'unchanged',return_value=False):second=self.prepare(clone_file=view.KERNEL_CLONE)
            self.assertEqual(second['report']['sealed_kernel_clones'],0);self.assertEqual(second['report']['snapshot_hashes']['hashed_files'],3)
            self.assertTrue((first['game_root']/'eboot.bin').exists())

    def test_sealed_source_pin_change_cannot_certify_a_clone(self):
        from incremental_file_hashes import stat_stamp
        with self.history():
            first=self.prepare(clone_file=view.KERNEL_CLONE);source=first['game_root']/'eboot.bin';data=source.read_bytes()
            entry=dict(stamp=stat_stamp(source.stat()),bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
            source.chmod(0o600);source.write_bytes(b'X'*len(data));source.chmod(0o400)
            target=self.base/'must-not-exist';self.assertIsNone(view.sealed_clone(source,target,entry));self.assertFalse(target.exists())

    def test_hidden_mod_stack_change_reuses_actual_source_mapping_without_registration_or_clone(self):
        chinese=self.base/'translation';(chinese/'dvdroot_ps4/msg').mkdir(parents=True);text=chinese/'dvdroot_ps4/msg/text.bin';text.write_bytes(b'visible translation')
        self.env.update(BB_CHINESE_SOURCE='community',BB_CHINESE_LANGUAGE='11',BB_CHINESE_TRANSLATION_DIR=str(chinese))
        with self.history():
            first=self.prepare(self.module().build_overlay(self.game,self.out,[('Chinese Retranslation',chinese)]))
            lower=self.mods/'a/dvdroot_ps4/msg';lower.mkdir(parents=True);(lower/'text.bin').write_bytes(b'hidden lower priority')
            actual=self.module().build_overlay(self.game,self.out,[('a',self.mods/'a'),('Chinese Retranslation',chinese)])
            with patch.object(view,'RegisteredSources',side_effect=AssertionError('unchanged effective resources re-enrolled')),patch.object(view,'clone',side_effect=AssertionError('unchanged view cloned')):
                second=self.prepare(actual)
            self.assertEqual(first['game_root'],second['game_root']);self.assertEqual(second['report']['reuse_mode'],'same-effective-source-selection');self.assertTrue(second['report']['current_selection_registered'])
            self.assertTrue(second['report']['original_source_mapping_unchanged'])
            # The NEW recipe now has its own normal unchanged certificate.
            again=self.prepare(self.module().build_overlay(self.game,self.out,[('a',self.mods/'a'),('Chinese Retranslation',chinese)]))
            self.assertEqual(again['report']['reuse_mode'],'registered-selection')
            text.write_bytes(b'actually changed visible translation')
            changed=self.prepare(self.module().build_overlay(self.game,self.out,[('a',self.mods/'a'),('Chinese Retranslation',chinese)]))
            self.assertNotEqual(changed['game_root'],first['game_root']);self.assertEqual((changed['game_root']/'dvdroot_ps4/msg/text.bin').read_bytes(),b'actually changed visible translation')

    def test_legacy_hash_candidates_are_rechecked_per_file_and_hidden_base_members_enrolled(self):
        from incremental_file_hashes import Hashes
        with self.history():
            module,algorithm=view.load_original(self.stage);views=self.gpu/'automatic-game-inputs/source-views';views.mkdir(parents=True)
            identity=view.digest(dict(schema=1,role='selected-file-content',algorithm=algorithm,base=str(self.game)))
            legacy=Hashes(views/('source-metadata-'+identity+'.json'),identity,hasher=view.file_sha)
            # This old mixed hint deliberately lacks one base member. It must
            # never be promoted into a complete base inventory by assumption.
            legacy.get(self.game/'eboot.bin');legacy.publish();old=(views/('source-metadata-'+identity+'.json')).read_bytes()
            current=view.RegisteredSources(views,self.game,[self.game],algorithm,lambda:None)
            self.assertFalse(current.base_registered);self.assertEqual(len(current.files),2)
            self.assertEqual(current.report()['hashed_files'],1);self.assertEqual(current.report()['legacy_base_hash_candidates'],1)
            self.assertEqual((views/('source-metadata-'+identity+'.json')).read_bytes(),old)
            current.publish();p=self.game/'eboot.bin';p.write_bytes(b'changed after legacy source proof')
            # A new independent registration must not borrow its old bytes.
            reread=view.RegisteredSources(views,self.game,[self.game],algorithm,lambda:None)
            self.assertEqual(reread.report()['hashed_files'],1)

    def test_registered_hint_missing_history_and_single_bad_receipt_fall_back_preserving_old(self):
        import startup_change_journal as journal
        with self.history():
            first=self.prepare();hint=next((self.gpu/'automatic-game-inputs/source-views').glob('selection-*.json'))
            original=first['game_root']/'eboot.bin';old=original.read_bytes()
            with patch.object(journal,'unchanged',return_value=False):
                fallback=self.prepare();self.assertNotIn('reuse_mode',fallback['report'])
            hint.write_text('broken selection hint')
            fallback=self.prepare();self.assertNotIn('reuse_mode',fallback['report'])
            self.assertEqual(original.read_bytes(),old)

    def test_registered_source_change_or_unexpected_overlay_still_rejected_or_reprepared(self):
        mod=self.mods/'a/dvdroot_ps4/msg';mod.mkdir(parents=True);(mod/'text.bin').write_bytes(b'text')
        with self.history():
            first=self.prepare();(self.game/'eboot.bin').write_bytes(b'real change')
            changed=self.prepare();self.assertNotEqual(first['game_root'],changed['game_root'])
            actual=self.actual();outside=self.base/'outside';outside.write_bytes(b'not selected')
            (actual/'eboot.bin').unlink();(actual/'eboot.bin').symlink_to(outside)
            with self.assertRaisesRegex(ValueError,'escape'):self.prepare(actual)

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
        second=self.prepare()
        self.assertNotEqual(first['game_root'],second['game_root'])
        self.assertEqual(second['report']['cache_recovery'],'source-snapshot-invalid')
        self.assertEqual((second['game_root']/'eboot.bin').read_bytes(),(self.game/'eboot.bin').read_bytes())
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
        second=self.prepare()
        self.assertNotEqual(first['game_root'],second['game_root'])
        self.assertFalse((second['game_root']/'unexpected.bin').exists())
        self.assertEqual(extra.read_bytes(),b'authored unadmitted file')

    def no_ready(self):
        self.assertFalse(list((self.gpu/'automatic-game-inputs/source-views').glob('*/READY.json')))

    def test_cold_hashes_each_source_once_and_each_clone_once(self):
        counts=Counter();original=view.file_sha
        def count(path,*args,**kwargs):
            counts[Path(path)]+=1;return original(path,*args,**kwargs)
        sources=[p for p in self.game.rglob('*')if p.is_file()]
        with patch.object(view,'file_sha',side_effect=count):result=self.prepare()
        for source in sources:
            self.assertEqual(counts[source],1,'initial source SHA plus final unchanged metadata')
            destination=result['game_root']/source.relative_to(self.game)
            self.assertEqual(counts[destination],1,'final complete snapshot SHA remains')
            self.assertEqual(destination.stat().st_mode&0o777,0o400)
        self.assertEqual(result['game_root'].stat().st_mode&0o777,0o500)

    def test_warm_does_not_read_any_game_or_snapshot_contents(self):
        first=self.prepare();clones=self.clones;counts=Counter();original=view.file_sha
        def count(path,*args,**kwargs):counts[Path(path)]+=1;return original(path,*args,**kwargs)
        with patch.object(view,'file_sha',side_effect=count):second=self.prepare()
        self.assertEqual(first['game_root'],second['game_root']);self.assertEqual(self.clones,clones)
        for source in self.game.rglob('*'):
            if source.is_file():
                self.assertEqual(counts[source],0)
                self.assertEqual(counts[first['game_root']/source.relative_to(self.game)],0)

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
