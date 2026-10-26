"""Source/target admission controls; do not execute a compiler or any target."""
from pathlib import Path
from unittest.mock import patch
import json,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import build_normal_preparation as tool

class BuildPlan(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  root=Path(self.tmp.name).resolve();self.source=root/'source';self.build=root/'build';self.source.mkdir();self.build.mkdir()
  self.cmake=root/'canonical-cmake';self.cmake.write_bytes(b'author executable fixture')
  alias=root/'alias-cmake';alias.symlink_to(self.cmake)
  (self.build/'CMakeCache.txt').write_text('CMAKE_COMMAND:INTERNAL='+str(alias)+'\n')
  self.file=self.source/'shader.cpp';self.file.write_bytes(b'author source fixture')
  self.units=[dict(file=str(self.file),command='c++ -o CMakeFiles/bbgpu.dir/shader.cpp.o -c '+str(self.file))];self.write()
  self.selected={'source':str(self.source),'build':str(self.build),'target':'bbgpu'}
 def write(self):(self.build/'compile_commands.json').write_text(json.dumps(self.units))
 def inspect(self):
  with patch.object(tool,'renderer_inputs',return_value=self.selected):return tool.inspect(self.source,self.build)
 def test_normal_target_only_and_canonical_tool(self):
  p=self.inspect();self.assertEqual(p['cmake'],str(self.cmake));self.assertEqual(p['command'][1:],['--build',str(self.build),'--parallel','1','--target','bbgpu','game-pso-preparer','game-module-preparer']);self.assertEqual(len(p['source_sha256']),1)
 def test_escaped_source_refused(self):
  outside=self.source.parent/'other.cpp';outside.write_bytes(b'other');self.units[0]['file']=str(outside);self.write()
  with self.assertRaisesRegex(ValueError,'outside'):self.inspect()
 def test_linked_source_refused(self):
  link=self.source/'linked.cpp';link.symlink_to(self.file);self.units[0]['file']=str(link);self.write()
  with self.assertRaisesRegex(ValueError,'outside'):self.inspect()
 def test_duplicate_translation_unit_refused(self):
  self.units.append(self.units[0]);self.write()
  with self.assertRaisesRegex(ValueError,'duplicate'):self.inspect()
 def test_no_renderer_units_refused(self):
  self.units[0]['command']='c++ -o CMakeFiles/another.dir/test.o';self.write()
  with self.assertRaisesRegex(ValueError,'no translation units'):self.inspect()
 def test_unconfigured_cmake_refused(self):
  (self.build/'CMakeCache.txt').write_text('')
  with self.assertRaisesRegex(ValueError,'missing'):self.inspect()

if __name__=='__main__':unittest.main()
