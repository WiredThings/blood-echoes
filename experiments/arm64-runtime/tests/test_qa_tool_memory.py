"""Post-teardown sampled process-tree accounting, no game or GUI."""
from pathlib import Path
import json,os,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from run_qa_launch_benchmark import tool_memory_summary
from run_game import usage
class ToolMemory(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)/'samples.jsonl'
 def write(self,values):self.path.write_text(''.join(json.dumps(v)+'\n'for v in values))
 def sample(self,t,rows):return dict(seconds=t,tool_usage=[dict(pid=pid,role=role,footprint_bytes=size*2**20)for pid,role,size in rows])
 def test_python_compiler_and_game_are_all_accounted(self):
  self.write([self.sample(1,[(1,'python3',200),(2,'metal',100)]),self.sample(3,[(1,'python3',50)]),self.sample(10,[(3,'bb-gpu',400),(4,'bb-probe-metal',500)])]);r=tool_memory_summary(self.path,5)
  self.assertEqual(r['before_scene_peak_mib'],300);self.assertEqual(r['owned_tree_peak_mib'],900);self.assertEqual(r['role_peaks_mib']['python3'],200);self.assertTrue(r['shared_pages_may_be_counted_more_than_once'])
 def test_entire_trace_not_only_last_scene_deque(self):
  self.write([self.sample(0,[(1,'python3',500)])]+[self.sample(i,[(2,'bb-gpu',10)])for i in range(1,1500)]);self.assertEqual(tool_memory_summary(self.path,1100)['before_scene_peak_mib'],500)
 def test_legacy_trace_does_not_claim_tool_observations(self):
  self.write([dict(seconds=1,usage=[])]);self.assertEqual(tool_memory_summary(self.path,2)['observations'],0)
 def test_duplicate_pid_is_not_double_counted(self):
  self.write([self.sample(1,[(1,'python',10),(1,'python',10)])])
  with self.assertRaises(ValueError):tool_memory_summary(self.path,2)
 def test_explicit_usage_mode_collects_current_python_without_changing_default(self):
  lines=[f'{os.getpid()} 1 0 {sys.executable}'];self.assertEqual(usage(lines),[]);row=usage(lines,include_tools=True);self.assertEqual(len(row),1);self.assertGreater(row[0]['footprint_bytes'],0)
if __name__=='__main__':unittest.main()
