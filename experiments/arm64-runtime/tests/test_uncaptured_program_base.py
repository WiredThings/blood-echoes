"""Exercise the production preparation dependency guard with authored IR resources."""
from pathlib import Path
import subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
class ProgramBaseGuard(unittest.TestCase):
 def test_each_discovered_sharp_requires_a_captured_base_before_use(self):
  with tempfile.TemporaryDirectory(prefix='blood-echoes-base-guard-',dir='/private/tmp')as tmp:
   out=Path(tmp)/'guard'
   include=[ROOT/'vendor/metal_api/shim',ROOT/'vendor/metal_api/shadps4',ROOT/'vendor/metal_api/third_party/half/include',Path('/opt/homebrew/include')]
   cmd=['/usr/bin/clang++','-std=gnu++23','-O1',*[f'-I{p}'for p in include],str(ROOT/'tests/test_uncaptured_program_base.cpp'),'-o',str(out),'-L/opt/homebrew/lib','-lfmt']
   built=subprocess.run(cmd,capture_output=True,text=True,timeout=90)
   self.assertEqual(built.returncode,0,built.stderr[-5000:])
   ran=subprocess.run([str(out)],capture_output=True,text=True,timeout=10)
   self.assertEqual(ran.returncode,0,ran.stdout+ran.stderr)
if __name__=='__main__':unittest.main()
