"""Authored inputs and strict cross-program derivation controls; no game/GPU."""
import copy, hashlib, json, struct, sys, tempfile, unittest
from pathlib import Path
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1]/'tools')]
import test_game_module_inputs as source_fixture
from test_game_shader_catalog import shader, binder, wrap
from test_static_program_links import header
import static_variant_inputs as tool
from run_game_module_contracts import canonical_packet


class Inputs(unittest.TestCase):
    def setUp(self):
        self.fixture = source_fixture.ModuleInputs(); self.fixture.setUp(); f=self.fixture
        raw=shader(hash_value=0x5678); package=wrap(binder(raw)); rel='target.shaderbnd.dcx'
        (f.game/rel).write_bytes(package)
        f.catalog['packages'].append(dict(path=rel,kind='shader',debug=False,sha256=hashlib.sha256(package).hexdigest()))
        self.catalog=f.catalog; self.catalog['game_root']=str(f.game)
        identity=dict(f.identity,program_hash='0000000000005678')
        evidence=dict(package=rel,entry='Authored.vpo',source_sha256=hashlib.sha256(raw).hexdigest(),code_offset=0)
        facts=header(4); facts={k:v for k,v in facts.items()if k not in ('program_hash','code_sha256','code_offset')}
        self.source=dict(identity=f.identity,variants=[dict(id='a'*64,facts=facts,code_bytes=16,evidence_refs=[f.ev])])
        self.target=dict(identity=identity,variants=[dict(id='b'*64,facts=copy.deepcopy(facts),code_bytes=16,evidence_refs=[evidence])])
        self.unit=f.unit(); self.item=dict(unit=self.unit,stage=self.unit['stages'][0],source=self.source,target=self.target)
        self.table={(r['identity']['program_hash'],'compute'):[r]for r in (self.source,self.target)}
    def tearDown(self): self.fixture.tearDown()
    def freeze(self):
        return tool.freeze(self.item,layout=source_fixture.layout(),catalog=self.catalog,output=self.fixture.p/'derived')
    def test_complete_input_is_reconstructed_without_changing_observed_fields(self):
        original=copy.deepcopy(self.item); packet=self.freeze(); f=self.fixture
        self.assertEqual(self.item,original); self.assertEqual(packet['program_hash'],'0000000000005678')
        self.assertEqual(packet['source_metadata']['opaque_Info_bytes_forwarded'],0)
        self.assertFalse(packet['static_derivation']['descriptors_synthesized'])
        self.assertFalse(packet['static_derivation']['effective_target_context_claim'])
        template=f.freeze(unit=self.unit,output=f.p/'normal')
        for key in ('code','spec','flat','walker','profile','expected_spirv'):
            self.assertEqual(packet[key]['sha256']if packet[key]else None,
                             template[key]['sha256']if template[key]else None)
        tool.validate_derivation(packet,self.catalog,self.table)
        canonical_packet(f.p/'derived/packet.json',self.catalog)
    def test_different_code_is_not_assumed_equivalent(self):
        self.target['identity']['code_sha256']='f'*64
        with self.assertRaisesRegex(ValueError,'identical independently'): self.freeze()
        self.assertFalse((self.fixture.p/'derived').exists())
    def test_float_or_other_header_difference_refuses_derivation(self):
        self.target['variants'][0]['facts']['registers']['rsrc1']^=1<<12
        with self.assertRaisesRegex(ValueError,'header contract differs'): self.freeze()
    def test_only_file_code_relocations_are_ignored(self):
        self.target['variants'][0]['facts']['registers']['program_low']^=8
        self.assertEqual(tool.header_contract(self.source),tool.header_contract(self.target))
        self.target['variants'][0]['facts']['scratch_dwords_per_thread']+=1
        self.assertNotEqual(tool.header_contract(self.source),tool.header_contract(self.target))
    def test_multiple_header_variants_are_not_merged(self):
        self.target['variants'].append(copy.deepcopy(self.target['variants'][0]))
        with self.assertRaisesRegex(ValueError,'unambiguous'): self.freeze()
    def test_source_tamper_is_detected_by_original_reader(self):
        (self.fixture.game/'target.shaderbnd.dcx').write_bytes(b'changed')
        with self.assertRaises(ValueError): self.freeze()
    def test_derivation_receipt_is_recomputed(self):
        packet=self.freeze(); packet['static_derivation']['complete_header_contract_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'header contract differs'):
            tool.validate_derivation(packet,self.catalog,self.table)
    def test_ambiguous_source_identity_is_not_used(self):
        rows=[self.source,self.target,copy.deepcopy(self.source)]
        self.assertEqual(tool.candidates([self.unit],rows),[])
    def test_one_context_per_target_is_deterministic(self):
        other=copy.deepcopy(self.unit); other['stages'][0]['serialized_spec_sha256']='0'*64
        a=tool.candidates([self.unit,other],[self.source,self.target])
        b=tool.candidates([other,self.unit],[self.target,self.source])
        self.assertEqual(a,b); self.assertEqual(len(a),1)
        self.assertEqual(a[0]['stage']['serialized_spec_sha256'],'0'*64)


def binary(marker,tail=(0x00010000,)):
    text=(marker.encode()+b'\0'); text+=b'\0'*((-len(text))%4)
    words=(0x07230203,0x00010500,1,8,0,((2+len(text)//4)<<16)|7,1)
    return struct.pack('<'+'I'*len(words),*words)+text+struct.pack('<'+'I'*len(tail),*tail)


class Spirv(unittest.TestCase):
    def check(self,a,b):return tool.program_marker_equivalent(a,b,'0000000000001234','0000000000005678')
    def test_only_the_documented_program_debug_marker_can_differ(self):
        self.assertTrue(self.check(binary('0x1234'),binary('0x5678')))
        self.assertFalse(self.check(binary('0x1234'),binary('0x9999')))
        self.assertFalse(self.check(binary('0x1234'),binary('0x5678',(0x00010001,))))
    def test_header_result_id_and_other_debug_data_remain_exact(self):
        a=binary('0x1234'); b=bytearray(binary('0x5678'))
        for at in (8,12,24):
            mutated=bytearray(b); mutated[at]^=1
            self.assertFalse(self.check(a,mutated))
    def test_truncated_zero_length_and_duplicate_markers_are_rejected(self):
        a=binary('0x1234'); b=binary('0x5678')
        self.assertFalse(self.check(a,b[:-1])); self.assertFalse(self.check(a[:-4]+b'\0'*4,b[:-4]+b'\0'*4))
        self.assertFalse(self.check(a[:-4]+a[20:],b[:-4]+b[20:]))
    def test_no_reference_binary_is_rewritten(self):
        a=binary('0x1234'); b=binary('0x5678'); saved=(bytes(a),bytes(b)); self.check(a,b)
        self.assertEqual((a,b),saved)

if __name__=='__main__':unittest.main()
