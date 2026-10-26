"""Authored normal-wrapper receipts; no native executable or GPU is run."""
from pathlib import Path
from unittest.mock import patch
import json,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import prepare_gpu_candidate_app as tool
class Admission(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.project=Path(self.tmp.name).resolve();e=self.project/'evidence';e.mkdir()
  self.source=e/'source';self.main=self.source/'vendor/metal_api/bb_gpu/bb_gpu_main.cpp';self.main.parent.mkdir(parents=True);self.main.write_text('authored CMake main source')
  self.build=e/'build';self.build.mkdir();self.exe=self.build/'bb-gpu';self.exe.write_bytes(b'authored main, not executable');self.lib=self.build/'libbbgpu.dylib';self.lib.write_bytes(b'authored renderer')
  self.original=e/'original';self.original.mkdir();self.new=e/'new';self.new.mkdir()
  self.plan=dict(selected=dict(source=str(self.source/'vendor/metal_api'),build=str(self.build)),gpu_executable_source=dict(path=str(self.main),sha256=tool.sha(self.main)),source_sha256={str(self.main):tool.sha(self.main)})
  self.record=dict(status='normal-targets-built',source_root=str(self.source),renderer_build=str(self.build),artifacts={x.name:dict(path=str(x),sha256=tool.sha(x))for x in(self.exe,self.lib)})
  self.write(self.original/'result.json',self.record);self.write(self.original/'plan.json',self.plan);self.write(self.new/'result.json',self.record);self.write(self.new/'plan.json',self.plan)
  self.component=dict(library_sha256=tool.sha(self.lib),normal_provenance=dict(normal_gpu_result=str(self.original/'result.json'),normal_gpu_result_sha256=tool.sha(self.original/'result.json'),normal_gpu_plan_sha256=tool.sha(self.original/'plan.json')))
  self.identity=dict(library=str(self.lib),library_sha256=tool.sha(self.lib))
 def write(self,path,value):path.write_text(json.dumps(value))
 def admit(self,identity=None):
  with patch('warm_archive_manifest.resolve_gpu_identity',return_value=self.identity if identity is None else identity):return tool.normal_source(self.new/'result.json',self.component,self.project)
 def test_exact_normal_source_and_renderer(self):
  source,proof=self.admit();self.assertEqual(source,self.exe);self.assertEqual(proof['actual_gpu_identity'],self.identity)
 def test_changed_wrapper_artifact(self):
  self.exe.write_bytes(b'changed')
  with self.assertRaisesRegex(ValueError,'artifact changed'):self.admit()
 def test_missing_normal_gpu_main(self):
  self.plan['gpu_executable_source']=None;self.write(self.new/'plan.json',self.plan)
  with self.assertRaisesRegex(ValueError,'main source identity'):self.admit()
 def test_other_source_graph(self):
  self.plan['source_sha256']={str(self.main):'0'*64};self.write(self.new/'plan.json',self.plan)
  with self.assertRaisesRegex(ValueError,'source mismatch'):self.admit()
 def test_changed_provenance_receipt(self):
  self.record['untrusted']=True;self.write(self.original/'result.json',self.record)
  with self.assertRaisesRegex(ValueError,'source mismatch'):self.admit()
 def test_wrong_actual_renderer(self):
  with self.assertRaisesRegex(ValueError,'actual renderer mismatch'):self.admit(dict(library=str(self.exe),library_sha256=tool.sha(self.lib)))
 def test_symlinked_receipt(self):
  alias=self.new/'alias.json';alias.symlink_to(self.new/'result.json')
  with self.assertRaisesRegex(ValueError,'canonical'):tool.normal_source(alias,self.component,self.project)
if __name__=='__main__':unittest.main()
