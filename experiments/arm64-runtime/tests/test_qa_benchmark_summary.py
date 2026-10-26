"""Reject false performance successes from incomplete/non-scene telemetry."""
from pathlib import Path
import csv,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from run_qa_launch_benchmark import scene_summary
class Summary(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)/'frames.csv'
 def rows(self,count=200):
  return [dict(t_s=i*.02,frame_ms=20,draws=500,gpu_ms=12,cam_x=1,cam_y=-2,cam_z=3)for i in range(count)]
 def write(self,rows):
  with self.path.open('w')as f:
   w=csv.DictWriter(f,fieldnames=('t_s','frame_ms','draws','gpu_ms','cam_x','cam_y','cam_z'));w.writeheader();w.writerows(rows)
 def summarize(self,rows):
  self.write(rows);return scene_summary(self.path,[dict(seconds=90,usage=[dict(footprint_bytes=2**30)])],0,90)
 def test_scene_fps_and_footprint(self):
  r=self.summarize(self.rows());self.assertEqual(r['fps'],50);self.assertEqual(r['frames'],200);self.assertEqual(r['combined_footprint_mib'],1024)
 def test_menu_camera_is_not_performance_success(self):
  rows=self.rows()
  for r in rows:r['cam_x']=''
  with self.assertRaisesRegex(ValueError,'not a stable scene'):self.summarize(rows)
 def test_incomplete_and_bad_gpu_rows_not_used(self):
  rows=self.rows()
  for r in rows[:110]:r['gpu_ms']='NaN'
  with self.assertRaisesRegex(ValueError,'insufficient'):self.summarize(rows)
 def test_nonmonotonic_log_not_joined_as_one_run(self):
  rows=self.rows();rows[-1]['t_s']=0
  with self.assertRaisesRegex(ValueError,'backwards'):self.summarize(rows)
 def test_only_last_thirty_seconds(self):
  r=self.summarize(self.rows(3000));self.assertGreaterEqual(r['frames'],1500);self.assertLessEqual(r['frames'],1501)
 def test_missing_required_header_rejected(self):
  self.path.write_text('t_s,frame_ms\n0,20\n')
  with self.assertRaisesRegex(ValueError,'schema'):scene_summary(self.path,[],0,90)
if __name__=='__main__':unittest.main()
