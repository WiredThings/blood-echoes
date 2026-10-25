from pathlib import Path
import hashlib,json,os,tempfile,unittest
import prepare_game_psos as p
UUID='0B1C9362-130B-3D13-88A4-8E5883E91AC8'
class Controls(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve();self.lib=self.root/'Blood Echoes ARM64 GUI QA author.app/libbbgpu.dylib';self.lib.parent.mkdir();self.lib.write_bytes(b'AUTHOR GPU LIBRARY')
  self.identity=dict(library=str(self.lib),library_sha256=hashlib.sha256(self.lib.read_bytes()).hexdigest())
 def tearDown(self):self.temp.cleanup()
 def parsed(self,lines,pid=123):
  found=set()
  for line in lines:
   value=p.dyld_gpu_image(line,pid)
   if value is not None:found.add(value)
  return p.verify_loaded_gpu(found,self.identity)
 def test_space_path_uuid_and_pid_full_record(self):
  r=self.parsed([f'dyld[123]: <{UUID}> {self.lib}\n'])
  self.assertEqual(r['library'],str(self.lib));self.assertTrue(r['owned_post_worker_sha_verified'])
 def test_no_space_old_pid_record(self):
  lib=self.root/'libbbgpu.dylib';lib.write_bytes(b'AUTHOR GPU LIBRARY');self.identity['library']=str(lib)
  self.assertEqual(self.parsed([f'dyld[123]: {lib}\n'])['library'],str(lib))
 def test_loaded_prefix_with_pid_record(self):
  self.assertEqual(self.parsed([f'dyld[123]: loaded: {self.lib}\n'])['library'],str(self.lib))
 def test_forged_line_without_dyld_prefix_not_admitted(self):
  for text in (f'log says {self.lib}\n',f'prefix dyld[123]: <{UUID}> {self.lib}\n',f'{self.lib}\n'):
   with self.subTest(text=text),self.assertRaisesRegex(p.Refused,'load edge'):self.parsed([text])
 def test_pid_mismatch_or_unbound_pid_not_admitted(self):
  for line in (f'dyld[124]: <{UUID}> {self.lib}\n',f'dyld: loaded: {self.lib}\n'):
   with self.subTest(line=line),self.assertRaisesRegex(p.Refused,'PID'):self.parsed([line])
 def test_wrong_library_path_not_admitted(self):
  wrong=self.root/'libbbgpu-wrong.dylib';wrong.write_bytes(b'AUTHOR GPU LIBRARY')
  with self.assertRaisesRegex(p.Refused,'load edge'):self.parsed([f'dyld[123]: <{UUID}> {wrong}\n'])
 def test_multiple_library_paths_not_admitted(self):
  second=self.root/'libbbgpu-second.dylib';second.write_bytes(b'AUTHOR GPU LIBRARY')
  with self.assertRaisesRegex(p.Refused,'load edge'):self.parsed([f'dyld[123]: <{UUID}> {self.lib}\n',f'dyld[123]: <{UUID}> {second}\n'])
 def test_identical_duplicate_record_is_one_image(self):
  line=f'dyld[123]: <{UUID}> {self.lib}\n';self.assertTrue(self.parsed([line,line])['single_gpu_load_edge'])
 def test_same_path_changed_bytes_not_admitted(self):
  self.lib.write_bytes(b'AUTHOR WRONG BYTES')
  with self.assertRaisesRegex(p.Refused,'SHA mismatch'):self.parsed([f'dyld[123]: <{UUID}> {self.lib}\n'])
 def test_hardlinked_library_not_admitted(self):
  os.link(self.lib,self.root/'other-link')
  with self.assertRaisesRegex(p.Refused,'owned bounded'):self.parsed([f'dyld[123]: <{UUID}> {self.lib}\n'])
 def test_invalid_uuid_or_nul_does_not_create_image(self):
  for line in (f'dyld[123]: <invalid> {self.lib}\n',f'dyld[123]: <{UUID}> {self.lib}\0bad\n'):
   with self.assertRaisesRegex(p.Refused,'load edge'):self.parsed([line])
 def test_oversized_record_refused(self):
  with self.assertRaisesRegex(p.Refused,'bounded dyld'):p.dyld_gpu_image('dyld[123]: /'+('x'*p.MAX_LINE),123)

if __name__=='__main__':unittest.main()
