"""Compile actual canonical Profile and decoder; no GPU/game or production IO."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

class UpstreamVertexProfile(unittest.TestCase):
    def test_real_profile_abi_roundtrip_and_rejection(self):
        with tempfile.TemporaryDirectory(prefix='upstream-vertex-profile-') as td:
            binary=Path(td)/'probe'
            includes=[ROOT/'src',ROOT/'vendor/metal_api/shim',ROOT/'vendor/metal_api/shadps4',
                      ROOT/'vendor/metal_api/third_party/half/include',Path('/opt/homebrew/include')]
            command=['/usr/bin/clang++','-std=gnu++23','-O1','-Wno-deprecated-declarations',
                     *[f'-I{x}' for x in includes],str(ROOT/'tests/test_upstream_vertex_profile.cpp'),
                     str(ROOT/'src/fetch_compile_canonical.cpp'),'-o',str(binary)]
            built=subprocess.run(command,capture_output=True,text=True,timeout=90)
            self.assertEqual(built.returncode,0,built.stderr[-10000:])
            run=subprocess.run([str(binary)],capture_output=True,text=True,timeout=10)
            self.assertEqual(run.returncode,0,run.stdout+run.stderr)
            print(run.stdout.strip())

if __name__=='__main__':unittest.main()
