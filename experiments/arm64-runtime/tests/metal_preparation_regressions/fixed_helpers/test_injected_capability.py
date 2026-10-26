# SPDX-License-Identifier: GPL-2.0-or-later
"""Execute exact scalar capability function with standard-library author runners.
No native executable, private cache path, game, SDK or Metal dependency.
"""
from pathlib import Path
import argparse,ast,os,subprocess,sys,tempfile,types,unittest
p=argparse.ArgumentParser(add_help=False);p.add_argument('--source-root',type=Path,default=Path(__file__).resolve().parents[1]);a,left=p.parse_known_args();sys.argv=[sys.argv[0],*left]
source=a.source_root/'tools/game_pso_component.py';tree=ast.parse(source.read_text());selected=[n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name in('need','fixed_helper_capability')];assert len(selected)==2
ns=dict(Path=Path,os=os,subprocess=subprocess,tempfile=tempfile);exec(compile(ast.Module(body=selected,type_ignores=[]),str(source),'exec'),ns);cap=ns['fixed_helper_capability']
class Capability(unittest.TestCase):
 def call(self,code=0,data=b'FIXED_HELPER_CAPABILITY 2\n',error=None):
  with tempfile.TemporaryDirectory()as d:
   root=Path(d).resolve();(root/'state').mkdir();worker=root/'stage/bin/game-pso-preparer';seen=[]
   def runner(argv,**kw):
    seen.append((argv,kw));self.assertEqual(argv,[str(worker),'--fixed-helper-seed-schema']);self.assertEqual(kw['timeout'],10);self.assertEqual(set(kw['env']),{'PATH','HOME','TMPDIR','BB_GPU_USER_DIR'});tmp=Path(kw['env']['BB_GPU_USER_DIR']);self.assertTrue(tmp.is_dir()and tmp.parent==root/'state');self.assertEqual(str(tmp),kw['env']['HOME']);self.assertEqual(str(tmp),kw['env']['TMPDIR']);
    if error:raise error
    return types.SimpleNamespace(returncode=code,stdout=data)
   try:return cap(worker,root,runner=runner)
   finally:self.assertEqual(len(seen),1);self.assertEqual(list((root/'state').iterdir()),[])
 def test_exact_new_output(self):self.assertEqual(self.call(),2)
 def test_old_schema1_unavailable(self):self.assertEqual(self.call(0,b'FIXED_HELPER_CAPABILITY 1\n'),0)
 def test_old_usage64_unavailable(self):self.assertEqual(self.call(64,b''),0)
 def test_only64_is_old(self):
  for code in [1,65,79,80]:
   with self.assertRaises(ValueError):self.call(code,b'')
 def test_wrong_or_repeated_output(self):
  for data in [b'1\n',b'FIXED_HELPER_CAPABILITY 3\n',b'FIXED_HELPER_CAPABILITY 2\n'*2,b'',b'x'*4097]:
   with self.assertRaises(ValueError):self.call(0,data)
 def test_old_output_also_bounded(self):
  with self.assertRaises(ValueError):self.call(64,b'x'*4097)
 def test_timeout_propagates_and_cleans(self):
  with self.assertRaises(subprocess.TimeoutExpired):self.call(error=subprocess.TimeoutExpired(['authored'],10))
 def test_keyboard_cancel_propagates_and_cleans(self):
  with self.assertRaises(KeyboardInterrupt):self.call(error=KeyboardInterrupt())
if __name__=='__main__':unittest.main(verbosity=2)
