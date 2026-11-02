"""Author-only native-record codec, incremental publication and full-key controls."""
import copy, hashlib, json,shutil,struct, sys, unittest
from pathlib import Path
sys.path[:0]=[str(Path(__file__).resolve().parent),str(Path(__file__).resolve().parents[1]/'tools')]
import test_static_variant_inputs as inputs
from game_pso_owned_audit import metadata_sections
from inventory_shader_cache import combine
from launcher_game_modules import atomic_json
import launcher_static_variants as tool


class Controller(unittest.TestCase):
    def setUp(self):
        self.source=inputs.Inputs();self.source.setUp();self.f=self.source.fixture
        self.layout=inputs.source_fixture.layout();self.root=self.f.p/'modules';self.root.mkdir()
        self.gpu=self.f.p/'gpu';self.gpu.mkdir();self.index={};self.calls=0
    def tearDown(self):self.source.tearDown()
    def native(self,component,path,output,case,gpu,nonce,cohort,check,identity):
        self.calls+=1;packet=json.loads(Path(path).read_text());Path(output).mkdir()
        source=self.source.unit['stages'][0];raw=bytearray(Path(source['metadata']['path']).read_bytes())
        _,sections=metadata_sections(raw,self.layout);target=int(packet['program_hash'],16);ph=combine(target,0)
        struct.pack_into('<QQ',raw,8,ph,0)
        struct.pack_into('<Q',raw,sections['info_persistent_audit_only']['offset']+self.layout['InfoPersistent.pgm_hash'],target)
        spirv=Path(source['spirv']['path']).read_bytes()
        (Path(output)/'module.meta').write_bytes(raw);(Path(output)/'module.spv').write_bytes(spirv)
        result=dict(spirv_sha256=hashlib.sha256(spirv).hexdigest(),
            static_variant_publication=dict(schema=1,permutation_index=0,permutation_hash=f'{ph:016x}',
             metadata_sha256=hashlib.sha256(raw).hexdigest(),metadata_bytes=len(raw)),
            input_packet_sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest(),
            input_code_sha256=packet['code']['sha256'],input_spec_sha256=packet['spec']['sha256'],
            input_profile_sha256=packet['profile']['sha256'],source_provenance=packet['source_provenance'])
        (Path(output)/'result.json').write_text(json.dumps(result));return result
    def prepare(self,**extra):
        args=dict(records=[self.source.source,self.source.target],units=[self.source.unit],layout=self.layout,
            catalog=self.source.catalog,table=self.source.table,component={},identity={'library_sha256':'f'*64},
            root=self.root,gpu=self.gpu,nonce='a'*64,cohort='c'*64,check=lambda:None,
            native_runner=self.native,index=self.index,index_path=self.root/'index.json',atomic_json=atomic_json,
            capabilities={'static_variant_publication_schema':1})
        args.update(extra);return tool.prepare(**args)
    def entry(self):
        name,entry=next(iter(self.index['static_variants'].items()))
        return self.gpu/'automatic-game-inputs/static-native-variants'/('f'*64)/name,entry
    def test_complete_native_record_and_original_pipeline_manifest(self):
        before={p:p.read_bytes()for p in self.f.game.rglob('*')if p.is_file()};r=self.prepare()
        self.assertEqual(r['counts']['prepared'],1);self.assertEqual(self.calls,1)
        self.assertFalse(r['bulk_runtime_preload']);self.assertFalse(r['descriptors_synthesized'])
        path,entry=self.entry();tool.verify_record(path,entry,self.source.target['identity'],self.source.unit['profile']['reference']['sha256'],'f'*64)
        recipe=json.loads(Path(entry['pipeline_input']).read_text())
        self.assertEqual(recipe['stages'][0]['program_hash'],'0000000000005678')
        self.assertEqual(recipe['stages'][0]['cache_local_permutation'],0)
        old=Path(self.source.unit['key']['reference']['path']).read_bytes();new=Path(recipe['key']['reference']['path']).read_bytes()
        self.assertEqual(old[:8],new[:8]);self.assertEqual(old[16:],new[16:])
        self.assertEqual(before,{p:p.read_bytes()for p in before})
    def test_warm_publication_reuses_without_another_compiler_child(self):
        self.prepare();r=self.prepare();self.assertEqual(r['counts']['reused'],1);self.assertEqual(self.calls,1)
    def relocated(self):
        self.prepare();newroot=self.root.with_name('relocated-modules');newgpu=self.gpu.with_name('relocated-gpu')
        shutil.copytree(self.root,newroot);shutil.copytree(self.gpu,newgpu)
        return newroot,newgpu,copy.deepcopy(self.index)
    def test_relocated_cache_reconstructs_current_inputs_without_reading_old_root(self):
        root,gpu,index=self.relocated();self.root.rename(self.root.with_name('preserved-original-modules'))
        r=self.prepare(root=root,gpu=gpu,index=index,index_path=root/'index.json')
        self.assertEqual(r['counts']['reused'],1);self.assertEqual(self.calls,1)
        recipe=json.loads(Path(r['manifest']['path']).read_text().splitlines()[0])
        self.assertTrue(Path(recipe['namespace']['cache']).is_relative_to(root))
        for s in recipe['stages']:
            self.assertTrue(Path(s['metadata']['path']).is_relative_to(root));self.assertTrue(Path(s['spirv']['path']).is_relative_to(root))
    def test_relocated_corrupt_local_bytes_are_not_hidden_by_healthy_original(self):
        root,gpu,index=self.relocated();entry=next(iter(index['static_variants'].values()))
        folder=root/Path(entry['pipeline_input']).parent.name/'pipeline-cache'
        next(folder.glob('*.spv')).write_bytes(b'bad copied SPIRV')
        r=self.prepare(root=root,gpu=gpu,index=index,index_path=root/'index.json')
        self.assertEqual(r['counts']['reused'],0);self.assertEqual(self.calls,2)
        self.assertTrue(list((gpu/'automatic-game-inputs/static-native-variants').rglob('*.retired-*')))
    def test_relocated_full_decoded_state_mismatch_never_becomes_same_input(self):
        root,gpu,index=self.relocated();entry=next(iter(index['static_variants'].values()))
        path=root/Path(entry['pipeline_input']).parent.name/'pipeline-input.json'
        recipe=json.loads(path.read_text());recipe['key']['decoded']['unexpected-state']='changed'
        path.write_text(json.dumps(recipe));entry['pipeline_input_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        r=self.prepare(root=root,gpu=gpu,index=index,index_path=root/'index.json')
        self.assertEqual(r['counts']['reused'],0);self.assertEqual(self.calls,2)
    def test_corrupt_record_is_preserved_and_locally_repaired(self):
        self.prepare();path,_=self.entry();bad=path.read_bytes()[:-1];path.write_bytes(bad)
        r=self.prepare();self.assertEqual(r['counts']['prepared'],1);self.assertEqual(self.calls,2)
        old=list(path.parent.glob('*.retired-*'));self.assertEqual(len(old),1);self.assertEqual(old[0].read_bytes(),bad)
    def test_compile_budget_defers_without_publication(self):
        r=self.prepare(seconds=0);self.assertEqual(r['counts']['deferred'],1);self.assertEqual(self.calls,0)
        self.assertFalse(list(self.gpu.rglob('*.bbsv')))
    def test_missing_native_capability_does_not_try_to_construct_or_compile(self):
        r=self.prepare(capabilities={});self.assertEqual(r['status'],'unavailable');self.assertEqual(self.calls,0)
    def test_wrong_native_input_receipt_never_becomes_a_runtime_record(self):
        def wrong(*args):
            result=self.native(*args);result['input_packet_sha256']='0'*64;return result
        r=self.prepare(native_runner=wrong);self.assertEqual(r['counts']['rejected'],1)
        self.assertFalse(list(self.gpu.rglob('*.bbsv')))
    def test_record_is_bound_to_actual_code_profile_and_compiler(self):
        self.prepare();path,entry=self.entry();target=self.source.target['identity'];profile=self.source.unit['profile']['reference']['sha256']
        for identity,p,c in ((dict(target,code_sha256='0'*64),profile,'f'*64),(target,'0'*64,'f'*64),(target,profile,'0'*64)):
            with self.assertRaises(ValueError):tool.verify_record(path,entry,identity,p,c)
    def test_extra_manifest_checksum_is_not_a_ready_boolean(self):
        r=self.prepare();source=self.f.p/'original.jsonl';source.write_text('')
        wrong=dict(r['manifest'],sha256='0'*64);destination=self.f.p/'combined.jsonl'
        with self.assertRaises(ValueError):tool.append_manifest(source,wrong,destination,lambda:None)
        self.assertFalse(destination.exists())

if __name__=='__main__':unittest.main()
