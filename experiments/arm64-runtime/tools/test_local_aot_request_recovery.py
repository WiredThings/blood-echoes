#!/usr/bin/env python3
"""Authored proposal controls; deliberately not executed by the review author."""
from pathlib import Path
import json,os,tempfile,unittest
from unittest.mock import patch
import local_metal_builder as builder
class RecoveryTests(unittest.TestCase):
 def setUp(self):
  self.root=Path(tempfile.mkdtemp(prefix='local-aot-recovery-authored-',dir=builder.PROJECT/'state'));self.requests=self.root/'requests';self.requests.mkdir();self.cohort='a'*64
 def export(self,source=b'kernel void main0(){}'):
  item=dict(schema=1,cohort=self.cohort,source_sha256=builder.digest(source),entry='main0',language='metal3.1',math_mode='safe',math_fp32='fast',recipe=builder.RECIPE);item['key']=builder.derive_key(item);key=item['key']
  (self.requests/(key+'.metal')).write_bytes(source);(self.requests/(key+'.json')).write_bytes(builder.canonical(item));return key
 def scan(self):
  recovery={};pins={};keys=builder.request_keys(self.requests,recovery=recovery,pins=pins);return keys,recovery,pins
 def test_known_temporary_is_ignored_and_never_removed(self):
  key=self.export();temp=self.requests/(key+'.metal.tmp-999-0');temp.write_bytes(b'partial')
  keys,recovery,pins=self.scan();self.assertEqual(keys,[key]);self.assertEqual(recovery['ignored_temporaries'],1);self.assertTrue(temp.exists());builder.load_request(self.requests,key,self.cohort,pins=pins)
 def test_legacy_final_and_temp_links_are_accounted_without_deleting_either(self):
  key=self.export();source=self.requests/(key+'.metal');temp=self.requests/(key+'.metal.tmp-999-1');os.link(source,temp)
  before=source.read_bytes();keys,recovery,pins=self.scan();self.assertEqual(keys,[key]);self.assertEqual(recovery['legacy_internal_link_groups'],1)
  builder.load_request(self.requests,key,self.cohort,pins=pins);self.assertEqual(source.read_bytes(),before);self.assertTrue(temp.exists());self.assertEqual(source.stat().st_nlink,2)
 def test_orphan_preserved_and_other_complete_pair_continues(self):
  key=self.export();orphan='b'*64;(self.requests/(orphan+'.metal')).write_bytes(b'authored orphan')
  keys,recovery,pins=self.scan();self.assertEqual(keys,[key]);self.assertEqual(recovery['deferred_unpaired'],[orphan]);self.assertTrue((self.requests/(orphan+'.metal')).exists())
 def test_external_hardlink_is_still_refused(self):
  key=self.export();os.link(self.requests/(key+'.metal'),self.root/'external-link')
  with self.assertRaises(builder.Refused):self.scan()
 def test_cross_key_or_extension_hardlinks_are_refused(self):
  key=self.export();os.link(self.requests/(key+'.metal'),self.requests/('b'*64+'.metal'))
  with self.assertRaises(builder.Refused):self.scan()
 def test_temporary_symlink_and_unknown_entry_are_refused(self):
  key=self.export();temp=self.requests/(key+'.metal.tmp-999-2');temp.symlink_to(self.requests/(key+'.metal'))
  with self.assertRaises(builder.Refused):self.scan()
  temp.unlink();(self.requests/'arbitrary-file').write_bytes(b'x')
  with self.assertRaises(builder.Refused):self.scan()
 def test_inventory_does_not_approve_corrupt_pair_content(self):
  key=self.export();(self.requests/(key+'.metal')).write_bytes(b'corrupt')
  keys,recovery,pins=self.scan();self.assertEqual(keys,[key])
  with self.assertRaises(builder.Refused):builder.load_request(self.requests,key,self.cohort,pins=pins)
 def test_link_or_inode_change_after_inventory_is_refused(self):
  key=self.export();keys,recovery,pins=self.scan();os.link(self.requests/(key+'.metal'),self.requests/(key+'.metal.tmp-999-3'))
  with self.assertRaises(builder.Refused):builder.load_request(self.requests,key,self.cohort,pins=pins)
 def test_recovery_enumeration_itself_remains_bounded(self):
  key=self.export()
  for n in range(3):(self.requests/(key+'.metal.tmp-999-'+str(n))).write_bytes(b'x')
  with patch.object(builder,'REQUEST_MAX',1):
   with self.assertRaises(builder.Refused):self.scan()
if __name__=='__main__':unittest.main()
