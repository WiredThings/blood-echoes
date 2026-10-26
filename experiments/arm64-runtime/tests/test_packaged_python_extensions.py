"""Admission controls use fake providers; no installed Python/package is edited."""
from pathlib import Path
import contextlib, io, json, struct, sys, tempfile, unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import complete_packaged_python as tool

class Extensions(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.stage=Path(self.tmp.name).resolve();self.root=self.stage/'python';self.root.mkdir()
  self.target=dict(version='exact build',minor='3.11',arch='arm64',soabi='cpython-311-darwin',suffix='.cpython-311-darwin.so',sqlite=None,hashlib=None)
 def complete(self,**kw):return tool.complete(self.root,writable_stage=self.stage,module_name='_hashlib',**kw)
 def test_only_admitted_modules(self):
  with patch.object(tool,'info')as info:
   with self.assertRaisesRegex(ValueError,'unsupported'):tool.complete(self.root,writable_stage=self.stage,module_name='_ssl')
   info.assert_not_called()
 def test_source_runtime_cannot_be_destination(self):
  with self.assertRaisesRegex(ValueError,'new copied'):tool.complete(self.root,writable_stage=self.root,module_name='_hashlib')
 def test_x86_target_rejected(self):
  with patch.object(tool,'info',return_value={**self.target,'arch':'x86_64'}):
   with self.assertRaisesRegex(ValueError,'arm64'):self.complete()
 def test_provider_must_match_every_abi_field(self):
  for key in('version','arch','soabi','suffix'):
   with self.subTest(key=key),patch.object(tool,'info',side_effect=[self.target,{**self.target,key:'different'}]),patch.object(tool,'native')as native:
    with self.assertRaisesRegex(ValueError,'mismatch: '+key):self.complete()
    native.assert_not_called()
 def test_missing_provider_extension_rejected(self):
  with patch.object(tool,'info',return_value=self.target):
   with self.assertRaisesRegex(ValueError,'lacks _hashlib'):self.complete()
 def test_existing_module_cannot_escape(self):
  outside=self.stage/('_hashlib'+self.target['suffix']);outside.write_bytes(b'fake')
  with patch.object(tool,'info',return_value={**self.target,'hashlib':str(outside)}),patch.object(tool,'run')as run:
   with self.assertRaisesRegex(ValueError,'outside copied'):self.complete()
   run.assert_not_called()
 def module(self):
  module=self.root/'lib'/('_hashlib'+self.target['suffix']);module.parent.mkdir()
  module.write_bytes(struct.pack('<4I',0xfeedfacf,0,0,8));return module
 def test_unresolved_edge_rejected_before_import(self):
  module=self.module()
  with patch.object(tool,'native',side_effect=lambda p:p),patch.object(tool,'edges',return_value=['@rpath/external.dylib']),patch.object(tool,'run')as run:
   with self.assertRaisesRegex(ValueError,'unresolved existing'):tool.existing_closure(self.root,self.root/'bin/python3',{**self.target,'hashlib':str(module)},module_name='_hashlib')
   run.assert_not_called()
 def test_existing_confined_hashlib_preserved(self):
  module=self.module();before=module.read_bytes();probe=dict(module=str(module),implementation='_hashlib.openssl_sha256')
  with patch.object(tool,'native',side_effect=lambda p:p),patch.object(tool,'edges',return_value=['/usr/lib/libSystem.B.dylib']),patch.object(tool,'run',return_value=json.dumps(probe)):
   receipt=tool.existing_closure(self.root,self.root/'bin/python3',{**self.target,'hashlib':str(module)},module_name='_hashlib')
  self.assertEqual(module.read_bytes(),before);self.assertFalse(receipt['provider_consulted']);self.assertFalse(receipt['native_bytes_modified']);self.assertEqual(receipt['kind'],'packaged-python-hashlib');self.assertTrue((self.root/'HASHLIB-CLOSURE.json').is_file());self.assertFalse((self.root/'SQLITE-CLOSURE.json').exists())
 def test_sha256_probe_checks_real_standard_vectors_and_context_copy(self):
  with contextlib.redirect_stdout(io.StringIO())as out:exec(tool.probe_code('_hashlib'),{})
  self.assertEqual(json.loads(out.getvalue())['implementation'],'_hashlib.openssl_sha256')

if __name__=='__main__':unittest.main()
