"""Authored scheduling proofs; no game, native compiler or Metal admission."""
from pathlib import Path
import hashlib,json,os,sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import module_product_schedule as schedule

class Products(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name).resolve()
        self.root=self.base/('a'*64);self.root.mkdir()
        self.source=self.base/'source';self.source.mkdir();self.source.chmod(0o500)
        self.catalog=self.base/'catalog.json';self.catalog.write_text('{"author":"catalog"}')
        self.uid='b'*64;self.case=self.root/('module-'+self.uid+'-'+'c'*16)
        inp=self.case/'input';out=self.case/'output';inp.mkdir(parents=True);out.mkdir()
        packet=dict(walker=None,walker_bytes=0)
        for name in ('code','profile','spec','flat','expected_spirv'):
            path=inp/(name+'.bin');raw=('author '+name).encode();path.write_bytes(raw)
            packet[name]=dict(path=str(path),name=path.name,sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw))
        self.packet=inp/'packet.json';self.packet.write_text(json.dumps(packet))
        (out/'result.json').write_text('{"author":"checked separately"}');(out/'module.spv').write_bytes(b'author SPV')
        self.entry=dict(packet=str(self.packet),packet_sha256=hashlib.sha256(self.packet.read_bytes()).hexdigest(),receipt=str(out/'result.json'),spirv=str(out/'module.spv'))
        self.history=True;self.query=patch.object(schedule,'query',return_value=dict(author='journal'));self.query.start()
        self.unchanged=patch.object(schedule,'unchanged',side_effect=lambda *args:self.history);self.unchanged.start()
        self.addCleanup(self.query.stop);self.addCleanup(self.unchanged.stop)
    def tearDown(self):self.source.chmod(0o700);self.tmp.cleanup()
    def create(self):return schedule.ModuleProducts(root=self.root,source=self.source,compiler='a'*64,catalog=self.catalog,check=lambda:None)
    def registered(self):
        first=self.create();first.record(self.uid,self.entry);self.assertTrue(first.publish())
        return self.create()
    def test_only_previously_source_validated_exact_product_can_skip_extraction(self):
        self.assertIsNone(self.create().packet(self.uid,self.entry))
        next=self.registered();self.assertIsNotNone(next.packet(self.uid,self.entry));self.assertEqual(next.hits,1)
        self.assertIsNone(next.packet('d'*64,self.entry));changed={**self.entry,'packet_sha256':'d'*64}
        self.assertIsNone(next.packet(self.uid,changed))
    def test_partial_unrecorded_and_new_capture_inventory_remain_on_original_path(self):
        first=self.create();self.assertFalse(first.publish());next=self.registered()
        inventory=self.root/('companion-inventory-'+'1'*24);inventory.mkdir();(inventory/'result.json').write_text('new owned capture')
        (self.root/'stage-links-123.jsonl').write_text('new links');(self.root/'recipe-links-123.jsonl').write_text('new recipes');(self.root/'report.json').write_text('new report')
        resumed=self.create();self.assertIsNotNone(resumed.packet(self.uid,self.entry))
        self.assertIsNone(resumed.packet('e'*64,self.entry))
    def test_actual_packet_frozen_output_catalog_or_validator_change_cannot_reuse(self):
        for path in (self.packet,self.case/'input/code.bin',self.case/'output/module.spv',self.catalog):
            self.registered();old=path.read_bytes();path.write_bytes(b'changed')
            self.assertIsNone(self.create().packet(self.uid,self.entry));path.write_bytes(old)
    def test_history_loss_or_writable_source_does_not_authorize_shortcut(self):
        self.registered();self.history=False;self.assertIsNone(self.create().packet(self.uid,self.entry))
        self.history=True;self.source.chmod(0o700);self.assertIsNone(self.create().packet(self.uid,self.entry))
    def test_source_change_during_use_refuses_publication(self):
        next=self.registered();self.assertIsNotNone(next.packet(self.uid,self.entry));next.record(self.uid,self.entry)
        self.history=False
        with self.assertRaisesRegex(ValueError,'source changed'):next.publish()
    def test_changed_frozen_bytes_after_initial_proof_are_still_checked(self):
        next=self.registered();(self.case/'input/spec.bin').write_bytes(b'changed after proof')
        with self.assertRaises(ValueError):next.packet(self.uid,self.entry)
    def test_no_reference_into_skipped_inventory_or_foreign_root_is_accepted(self):
        next=self.registered();wrong={**self.entry,'packet':str(self.root/('companion-inventory-'+'1'*24)/'input/packet.json')}
        self.assertIsNone(next.identity(wrong));wrong={**self.entry,'receipt':str(self.base/'outside.json')}
        self.assertIsNone(next.identity(wrong))
    def test_symlink_or_hardlink_in_product_set_never_grants_byte_proof(self):
        self.registered();p=self.case/'input/code.bin';old=p.read_bytes();outside=self.base/'outside';outside.write_bytes(old)
        p.unlink();p.symlink_to(outside)
        self.assertIsNone(self.create().packet(self.uid,self.entry))
        p.unlink();p.write_bytes(old);os.link(p,self.base/'extra-link')
        self.assertIsNone(self.create().packet(self.uid,self.entry))

if __name__=='__main__':unittest.main(verbosity=2)
