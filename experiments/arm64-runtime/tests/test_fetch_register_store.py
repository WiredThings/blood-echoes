"""Compile and run the tiny authored CPU-only disk contract, no renderer library."""
from pathlib import Path
import json,re,subprocess,sys,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]

class FetchStore(unittest.TestCase):
 def test_complete_current_profile_field_projection(self):
  profile=(ROOT/'vendor/metal_api/shadps4/shader_recompiler/profile.h').read_text()
  fields=re.findall(r'^\s*(u32|u64|bool)\s+(\w+)\s*\{',profile,re.M)
  boolean=re.findall(r'^MODULE_PROFILE_BOOL_FIELD\((\w+)\)',(ROOT/'src/module_profile_bool_fields.inc').read_text(),re.M)
  explicit=['max_viewport_width','max_viewport_height','max_shared_memory_size','supported_spirv','subgroup_size','sparse_page_shift','buffer_copy_shader_hash']
  self.assertEqual([n for t,n in fields if t!='bool'],explicit)
  self.assertEqual([n for t,n in fields if t=='bool'],boolean)
  self.assertEqual(24+8+len(boolean),74)
  self.assertEqual(boolean[-2:],['relaxed_fragment_compute_float','two_level_bda'])

 def test_native_owned_capture_and_store_contracts(self):
  with tempfile.TemporaryDirectory(prefix='blood-echoes-fetch-register-',dir='/private/tmp')as temp:
   temp=Path(temp).resolve();binary=temp/'test';state=temp/'state';state.mkdir(mode=0o700)
   sources=[ROOT/'tests/test_fetch_register_store.cpp',ROOT/'src/fetch_register_store.cpp']
   includes=[ROOT/'src',ROOT/'vendor/metal_api/shim',ROOT/'vendor/metal_api/shadps4',ROOT/'vendor/metal_api/third_party/half/include',Path('/opt/homebrew/include')]
   cmd=['/usr/bin/clang++','-std=gnu++23','-O1','-Wno-deprecated-declarations',*[f'-I{p}'for p in includes],*[str(p)for p in sources],'-o',str(binary)]
   built=subprocess.run(cmd,text=True,capture_output=True,timeout=90)
   self.assertEqual(built.returncode,0,built.stderr[-6000:])
   result=subprocess.run([str(binary),str(state)],text=True,capture_output=True,timeout=15)
   self.assertEqual(result.returncode,0,result.stdout+result.stderr)
   found=[line.removeprefix('FETCH_STORE_RESULT ')for line in result.stdout.splitlines()if line.startswith('FETCH_STORE_RESULT ')]
   self.assertEqual(len(found),1);record=json.loads(found[0]);self.assertGreaterEqual(record['passed'],43)
   self.assertFalse(record['game_started']);self.assertFalse(record['gpu_started']);self.assertFalse(record['guest_reads'])
   print('FETCH_STORE_RESULT '+json.dumps(record,sort_keys=True))
if __name__=='__main__':unittest.main(verbosity=2)
