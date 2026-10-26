"""Real isolated Python import of tiny authored data, no GPU/game/native build.

CLI -X pycache_prefix confines even potential startup stdlib cache files to a
fresh authored scratch directory. Never run the unsafe baseline against a bundle
without that interpreter-level redirection.
"""
from pathlib import Path
import os,subprocess,sys,tempfile,unittest

D=Path(__file__).resolve().parents[1]
PYTHON=Path(os.environ.get('BB_AUTHOR_BYTECODE_PYTHON',sys.executable)).resolve(strict=True)

class Bytecode(unittest.TestCase):
 def run_author(self,no_bytecode):
  with tempfile.TemporaryDirectory(prefix='module-bytecode-author-')as temporary:
   root=Path(temporary).resolve();source=root/'authored-source';source.mkdir()
   module=source/'be_author_bytecode.py';module.write_text('VALUE = 42\n')
   cache=root/'authored-cache';cache.mkdir()
   program='import sys;sys.path.insert(0,sys.argv[1]);import be_author_bytecode as m;print(sys.flags.ignore_environment,sys.flags.isolated,sys.dont_write_bytecode,m.VALUE);print(m.__cached__)'
   command=[str(PYTHON),'-I','-S','-X','pycache_prefix='+str(cache)]
   if no_bytecode:command+=['-B']
   command+=['-c',program,str(source)]
   result=subprocess.run(command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=10,
    env={'PATH':'/usr/bin:/bin:/usr/sbin:/sbin','PYTHONDONTWRITEBYTECODE':'1'})
   self.assertEqual(result.returncode,0,result.stderr)
   lines=result.stdout.splitlines();self.assertEqual(len(lines),2)
   flags=lines[0].split();self.assertEqual(flags[:2],['1','1']);self.assertEqual(flags[-1],'42')
   authored_cache=Path(lines[1]);self.assertTrue(authored_cache.is_relative_to(cache))
   if no_bytecode:
    self.assertEqual(flags[2],'True');self.assertFalse(authored_cache.exists());self.assertEqual(list(cache.rglob('*.pyc')),[])
   else:
    self.assertEqual(flags[2],'False');self.assertTrue(authored_cache.is_file())
   self.assertEqual(module.read_text(),'VALUE = 42\n');self.assertFalse((source/'__pycache__').exists())
 def test_isolation_ignores_environment_bytecode_preference(self):self.run_author(False)
 def test_isolation_with_interpreter_B_prevents_all_bytecode_writes(self):self.run_author(True)
 def test_native_helper_retains_isolation_and_places_B_before_script(self):
  text=(D/'src/prepare_guest_module_info.h').read_text()
  self.assertIn('args{python,"-I","-B",selected.helper.string()',text)
  self.assertIn('posix_spawn(&child,python',text)

if __name__=='__main__':unittest.main(verbosity=2)
