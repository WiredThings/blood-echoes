"""CMake-only author control: configure no languages and run one tiny Python rule."""
from pathlib import Path
import json,os,re,shutil,subprocess,sys,tempfile,unittest
D=Path(__file__).resolve().parents[1]
class Selection(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve();self.src=self.root/'src';self.src.mkdir();self.build=self.root/'build'
  self.cmake=Path(shutil.which('cmake')).resolve();self.ninja=Path(shutil.which('ninja')).resolve()
  text=(D/'targets.cmake').read_text();self.find=re.search(r'^find_package\(Python 3\.11 REQUIRED COMPONENTS Interpreter\)$',text,re.M).group()
  self.assertIn('COMMAND "${Python_EXECUTABLE}" ${BB_ARM64_PROTOTYPE_DIR}/tools/make_cpu_cache_build.py',text)
  (self.src/'probe.py').write_text("import hashlib,json,sys\nassert hasattr(hashlib,'file_digest')\nopen(sys.argv[1],'w').write(json.dumps({'executable':sys.executable,'version':list(sys.version_info[:3])}))\n")
  (self.src/'CMakeLists.txt').write_text('cmake_minimum_required(VERSION 3.25)\nproject(AuthorPython NONE)\n'+self.find+'\nadd_custom_command(OUTPUT "${CMAKE_BINARY_DIR}/probe.json" COMMAND "${Python_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/probe.py" "${CMAKE_BINARY_DIR}/probe.json" VERBATIM)\nadd_custom_target(probe ALL DEPENDS "${CMAKE_BINARY_DIR}/probe.json")\n')
 def configure(self,python):return subprocess.run([str(self.cmake),'-S',str(self.src),'-B',str(self.build),'-G','Ninja','-DCMAKE_MAKE_PROGRAM='+str(self.ninja),'-DPython_EXECUTABLE='+str(python)],text=True,capture_output=True,timeout=30)
 def test_selected_python_survives_restricted_build_path(self):
  selected=Path(sys.executable).resolve();r=self.configure(selected);self.assertEqual(r.returncode,0,r.stderr)
  env=dict(os.environ,PATH='/usr/bin:/bin:/usr/sbin:/sbin');r=subprocess.run([str(self.cmake),'--build',str(self.build)],env=env,text=True,capture_output=True,timeout=15);self.assertEqual(r.returncode,0,r.stdout+r.stderr)
  row=json.loads((self.build/'probe.json').read_text());self.assertEqual(Path(row['executable']).resolve(),selected);self.assertGreaterEqual(row['version'][:2],[3,11])
 def test_explicit_older_python_fails_at_configuration(self):
  old=Path('/usr/bin/python3')
  if not old.exists():self.skipTest('system Python unavailable')
  version=subprocess.check_output([str(old),'-c','import json,sys;print(json.dumps(list(sys.version_info[:2])))'],text=True)
  if json.loads(version)>=[3,11]:self.skipTest('system interpreter is already suitable')
  r=self.configure(old);self.assertNotEqual(r.returncode,0);self.assertIn('Python',r.stdout+r.stderr);self.assertFalse((self.build/'probe.json').exists())
if __name__=='__main__':unittest.main()
