from pathlib import Path
import sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from qa_cpu_sample_summary import parse

class Samples(unittest.TestCase):
 def test_self_and_once_per_leaf_ancestor_accounting(self):
  text='''Call graph:
    10 Thread_12: game
    + 10 root  (in program)
    +   8 BbCpu::BbcpuBackend::Query(unsigned long)  (in program)
    +     5 BbCpu::BbcpuBackend::Query(unsigned long)  (in program)
    +       3 mutexwait  (in system)
    +   2 idle  (in system)
Total number in stack:
    999 unrelated summary
'''
  r=parse(text)['threads'][0];self.assertEqual(r['self_samples'],10)
  self.assertEqual(r['ancestor_samples']['code_query'],8)
  self.assertEqual(r['ancestor_percent']['code_query'],80)
  self.assertEqual({x['function']:x['samples']for x in r['self_top']},{'BbCpu::BbcpuBackend::Query(unsigned long)':5,'mutexwait':3,'idle':2})
 def test_repeated_thread_queue_sections_and_other_threads(self):
  text='''Call graph:
    2 Thread_1: one
    + 2 root  (in program)
    1 Thread_1: queue
    + 1 second  (in program)
    4 Thread_2: two
    + 4 idle  (in system)
Sort by top of stack:
'''
  r=parse(text)['threads'];self.assertEqual(len(r),2);self.assertEqual(r[0]['observations'],3);self.assertEqual(r[0]['labels'],[': one',': queue']);self.assertEqual(r[1]['self_samples'],4)
 def test_bad_counts_rejected(self):
  for text in('Call graph:\n    2 Thread_1\n    + 3 root\n','Call graph:\n    2 Thread_1\n    + 2 root\n    +   3 child\n'):
   with self.assertRaises(ValueError):parse(text)
 def test_missing_tree_rejected(self):
  for text in('','Sort by top of stack:\n    2 root\n'):
   with self.assertRaises(ValueError):parse(text)
 def test_fex_query_is_not_misreported_as_zero(self):
  r=parse('Call graph:\n    2 Thread_1\n    + 2 FEX::QueryGuestExecutableRange()  (in program)\n')['threads'][0]
  self.assertEqual(r['ancestor_samples']['code_query'],2)
if __name__=='__main__':unittest.main()
