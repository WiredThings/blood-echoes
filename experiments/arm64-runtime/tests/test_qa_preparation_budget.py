from pathlib import Path
import sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from qa_preparation_budget import PreparationBudget
class BudgetTests(unittest.TestCase):
    def test_compilation_is_not_game_time(self):
        b=PreparationBudget(7200,600)
        self.assertIsNone(b.observe(1801,[(2,1,'/private/clang'),(3,2,'/private/bbcpu-code-preparer')]))
        self.assertIsNone(b.observe(4000,[(4,1,'/private/bb-probe-metal')]))
        self.assertIsNone(b.observe(4599,[]));self.assertEqual(b.observe(4600,[]),'game time budget')
    def test_preparation_remains_bounded(self):
        b=PreparationBudget(7200,600);self.assertEqual(b.observe(7200,[]),'preparation time budget')
        for a,c in((14401,600),(7200,1801),(0,600)):
            with self.assertRaises(ValueError):PreparationBudget(a,c)
if __name__=='__main__':unittest.main()
