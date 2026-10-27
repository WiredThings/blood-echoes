"""Native worker failure diagnostics, no game/Metal/tool execution."""
from pathlib import Path
import sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from launcher_fixed_helpers import result_record
class Records(unittest.TestCase):
 def test_exact_success(self):
  row={'status':'inputs-collected'};self.assertIs(result_record([row],[],0),row)
 def test_native_rejection_preserves_real_reason(self):
  with self.assertRaisesRegex(RuntimeError,'rejected request: fresh empty Shared source scratch required'):
   result_record([],[{'status':'rejected','error':'fresh empty Shared source scratch required'}],65)
 def test_rejection_is_never_overridden_by_success(self):
  with self.assertRaises(RuntimeError):result_record([{'status':'completed'}],[{'status':'rejected','error':'wrong input'}],0)
 def test_missing_result_keeps_exit(self):
  with self.assertRaisesRegex(RuntimeError,'worker exit 65'):result_record([],[],65)
 def test_duplicate_success_is_not_accepted(self):
  with self.assertRaises(RuntimeError):result_record([{},{}],[],0)
 def test_generic_result_cannot_claim_helper_success(self):
  with self.assertRaises(RuntimeError):result_record([],[{'status':'completed'}],0)
 def test_unbounded_error_is_rejected(self):
  with self.assertRaisesRegex(RuntimeError,'invalid or conflicting'):result_record([],[{'status':'rejected','error':'x'*4097}],65)
if __name__=='__main__':unittest.main()
