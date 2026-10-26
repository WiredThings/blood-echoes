"""Bounded authored native full-companion roundtrip, no renderer or game."""
from pathlib import Path
import json,subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
class Companion(unittest.TestCase):
 def test_native_atomic_owned_companion(self):
  with tempfile.TemporaryDirectory(prefix='fetch-companion-authored-',dir='/private/tmp')as name:
   root=Path(name).resolve();binary=root/'test';state=root/'state';state.mkdir(mode=0o700)
   sources=['tests/test_fetch_companion_store.cpp','src/fetch_companion_store.cpp','src/fetch_compile_canonical.cpp','src/fetch_register_store.cpp']
   includes=[ROOT/'src',ROOT/'vendor/metal_api/shim',ROOT/'vendor/metal_api/shadps4',ROOT/'vendor/metal_api/third_party/half/include',Path('/opt/homebrew/include')]
   command=['/usr/bin/clang++','-std=gnu++23','-DBB_FETCH_CAPTURE_TESTING=1','-O1','-Wno-deprecated-declarations',*[f'-I{x}'for x in includes],*[str(ROOT/x)for x in sources],'-o',str(binary)]
   built=subprocess.run(command,text=True,capture_output=True,timeout=90);self.assertEqual(built.returncode,0,built.stderr[-12000:])
   run=subprocess.run([str(binary),str(state)],text=True,capture_output=True,timeout=20);self.assertEqual(run.returncode,0,run.stdout+run.stderr)
   records=[json.loads(x.removeprefix('FETCH_COMPANION_RESULT '))for x in run.stdout.splitlines()if x.startswith('FETCH_COMPANION_RESULT ')];self.assertEqual(len(records),1);r=records[0];self.assertGreaterEqual(r['passed'],40);self.assertFalse(r['game_started']);self.assertFalse(r['GPU_started']);self.assertFalse(r['guest_dereferenced']);print('FETCH_COMPANION_RESULT '+json.dumps(r,sort_keys=True))
if __name__=='__main__':unittest.main(verbosity=2)
