"""Scene/timing controls only; no process is started."""
from pathlib import Path
import sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from run_qa_launch_benchmark import scene_seen,deadline
class Scene(unittest.TestCase):
 def row(self,**kw):return dict(draws='220',cam_x='1.5',cam_y='-2',cam_z='0',**kw)
 def test_real_camera_with_draws(self):self.assertTrue(scene_seen([self.row()]))
 def test_ui_or_empty_camera_is_not_scene(self):
  self.assertFalse(scene_seen([dict(self.row(),cam_x='')]));self.assertFalse(scene_seen([dict(self.row(),draws='100')]));self.assertFalse(scene_seen([]))
 def test_nonfinite_or_partial_rows_do_not_qualify(self):
  for value in ('NaN','inf','-inf','invalid',None):self.assertFalse(scene_seen([dict(self.row(),cam_z=value)]))
  self.assertFalse(scene_seen([{}]))
 def test_old_camera_outside_tail_not_scene(self):self.assertFalse(scene_seen([self.row()]+[dict(self.row(),cam_x='')]*30))
 def test_startup_and_scene_are_separate_budgets(self):
  self.assertIsNone(deadline(900,None,1800,90));self.assertEqual(deadline(1800,None,1800,90),'startup time budget');self.assertIsNone(deadline(920,910,1800,90));self.assertEqual(deadline(1000,910,1800,90),'scene sample completed')
 def test_zero_ready_time_is_a_real_value(self):self.assertEqual(deadline(90,0,1800,90),'scene sample completed')
if __name__=='__main__':unittest.main()
