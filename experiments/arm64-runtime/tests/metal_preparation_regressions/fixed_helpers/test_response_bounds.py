"""Pure CPU protocol controls; written but deliberately not run in GPU/game window."""
from pathlib import Path
import copy,importlib.util,unittest
import argparse,sys
args=argparse.ArgumentParser(add_help=False);args.add_argument('--source-root',type=Path,default=Path(__file__).resolve().parents[1]);selected,remaining=args.parse_known_args();sys.argv=[sys.argv[0],*remaining]
ROOT=selected.source_root.resolve(strict=True);spec=importlib.util.spec_from_file_location('fixed',ROOT/'tools/launcher_fixed_helpers.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
from tiling_fixture import libraries
def row(ready=True):return dict(exit=0,result=dict(status='completed'if ready else'inputs-collected',fixed_helper_seed_schema=2,known=8,ready=8 if ready else 0,all_fixed_native_ready=ready,artifact_counts={'compile_attempts':0},tiling_libraries=libraries(),items=[dict(kind=k,outcome=1 if ready else 3,request_sha256='a'*64,descriptor_sha256='b'*64,inputs_verified=True,native_ready=ready,archive_sha256='c'*64 if ready else'',actual_routes=[2,2]if k==10 else[2])for k in [4,5,6,9,10,11,12,13]]))
class Boundaries(unittest.TestCase):
 def test_exact_complete(self):self.assertTrue(module.validate_result(row(),'helpers-probe')[1])
 def test_deferred_not_native_success(self):self.assertFalse(module.validate_result(row(False),'helpers-probe')[1])
 def test_critical_stop(self):
  r=row();r['result']['status']='stopped'
  with self.assertRaises(RuntimeError):module.validate_result(r,'helpers-probe')
 def test_exit_failure(self):
  r=row();r['exit']=80
  with self.assertRaises(RuntimeError):module.validate_result(r,'helpers-probe')
 def test_duplicate_kind(self):
  r=row();r['result']['items'][1]['kind']=4
  with self.assertRaises(ValueError):module.validate_result(r,'helpers-prepare')
 def test_dynamic_kind_not_seeded(self):
  for kind in [1,2,3,7,8,14]:
   r=row();r['result']['items'][0]['kind']=kind
   with self.assertRaises(ValueError):module.validate_result(r,'helpers-prepare')
 def test_missing_completed_item(self):
  r=row();r['result']['items'].pop()
  with self.assertRaises(ValueError):module.validate_result(r,'helpers-prepare')
 def test_ready_requires_input_and_receipt(self):
  for name,value in [('inputs_verified',False),('archive_sha256','')]:
   r=row();r['result']['items'][0][name]=value
   with self.assertRaises(ValueError):module.validate_result(r,'helpers-prepare')
 def test_probe_cannot_compile(self):
  r=row();r['result']['artifact_counts']['compile_attempts']=1
  with self.assertRaises(ValueError):module.validate_result(r,'helpers-probe')
 def test_unverified_unsupported_descriptor_cannot_be_ready(self):
  r=row(False);r['result']['items'][0].update(inputs_verified=False,descriptor_sha256='',request_sha256='')
  self.assertFalse(module.validate_result(r,'helpers-probe')[1])
 def test_source_hit_is_not_local_complete(self):
  r=row();r['result']['items'][0]['actual_routes']=[1]
  with self.assertRaises(ValueError):module.validate_result(r,'helpers-probe')
  r['result']['all_fixed_native_ready']=False
  self.assertFalse(module.validate_result(r,'helpers-probe')[1])
 def test_ready_outcome_cannot_be_deferred(self):
  r=row();r['result']['items'][0]['outcome']=3
  with self.assertRaises(ValueError):module.validate_result(r,'helpers-prepare')
 def test_probe_cannot_claim_built(self):
  r=row();r['result']['items'][0]['outcome']=2
  with self.assertRaises(ValueError):module.validate_result(r,'helpers-probe')
 def test_critical_item_always_stops(self):
  r=row(False);r['result']['items'][0]['outcome']=5
  with self.assertRaises(RuntimeError):module.validate_result(r,'helpers-probe')
 def test_bool_schema_is_not_integer_schema(self):
  r=row();r['result']['fixed_helper_seed_schema']=True
  with self.assertRaises(ValueError):module.validate_result(r,'helpers-probe')
 def test_bool_compile_count_is_invalid(self):
  r=row();r['result']['artifact_counts']['compile_attempts']=False
  with self.assertRaises(ValueError):module.validate_result(r,'helpers-probe')
 def test_bad_schema_and_count(self):
  for name,value in [('known',9),('fixed_helper_seed_schema',1)]:
   r=row();r['result'][name]=value
   with self.assertRaises(ValueError):module.validate_result(r,'helpers-probe')
if __name__=='__main__':unittest.main(verbosity=2)
