#!/usr/bin/env python3
import errno,threading,time,unittest
from bounded_mlock_model import BoundedMlock,PAGE,GUEST_LIMIT
BASE=0x7000000000
class Backend:
 def __init__(self):self.owned=set(range(BASE,BASE+8*PAGE,PAGE));self.pins=set();self.calls=[];self.fail_lock=None;self.fail_unlock=None;self.pause=None
 def require_owned_span(self,a,b):
  if any(p not in self.owned for p in range(a,b,PAGE)):raise OSError(errno.ENOMEM,'hole/reserved/host owned')
 def lock(self,p,n):
  self.calls.append(('lock',p,n))
  if self.pause:self.pause[0].set();self.pause[1].wait(2)
  if p==self.fail_lock:raise OSError(errno.EAGAIN,'injected host failure')
  assert p not in self.pins;self.pins.add(p)
 def unlock(self,p,n):
  self.calls.append(('unlock',p,n))
  if p==self.fail_unlock:raise OSError(errno.ENOMEM,'injected host failure')
  assert p in self.pins;self.pins.remove(p)
class Contract(unittest.TestCase):
 def setUp(self):self.b=Backend();self.m=BoundedMlock(self.b,max_call=4*PAGE,max_total=6*PAGE,max_entries=6)
 def assert_error(self,code,call):
  with self.assertRaises(OSError)as c:call()
  self.assertEqual(c.exception.errno,code)
 def test_real_backend_mandatory(self):
  self.m.lock(BASE,PAGE);self.assertEqual(self.b.calls,[('lock',BASE,PAGE)]);self.assertEqual(self.m.wired_bytes,PAGE)
 def test_repeat_is_idempotent_not_nested_refcount(self):
  self.m.lock(BASE,PAGE);self.m.lock(BASE,PAGE);self.assertEqual(len(self.b.calls),1);self.m.unlock(BASE,PAGE);self.assertFalse(self.b.pins)
 def test_overlap_only_new_pages(self):
  self.m.lock(BASE,2*PAGE);self.m.lock(BASE+PAGE,2*PAGE);self.assertEqual(len(self.b.calls),3);self.m.unlock(BASE+PAGE,PAGE);self.assertEqual(self.b.pins,{BASE,BASE+2*PAGE})
 def test_unaligned_rounding(self):
  self.m.lock(BASE+1,PAGE);self.assertEqual(self.m.wired_bytes,2*PAGE)
 def test_zero_length_aligned_is_empty(self):
  self.m.lock(BASE,0);self.assertFalse(self.b.calls)
 def test_zero_length_unaligned_rounds_page(self):
  self.m.lock(BASE+1,0);self.assertEqual(self.m.wired_bytes,PAGE)
 def test_size_bound_before_any_host_call(self):
  self.assert_error(errno.ENOMEM,lambda:self.m.lock(BASE,5*PAGE));self.assertFalse(self.b.calls)
 def test_total_bound_counts_unique_pages(self):
  self.m.lock(BASE,4*PAGE);self.m.lock(BASE+2*PAGE,4*PAGE);self.assertEqual(self.m.wired_bytes,6*PAGE);before=list(self.b.calls);self.assert_error(errno.ENOMEM,lambda:self.m.lock(BASE+6*PAGE,PAGE));self.assertEqual(before,self.b.calls)
 def test_entry_limit_independent(self):
  self.m.max_entries=1;self.assert_error(errno.ENOMEM,lambda:self.m.lock(BASE,2*PAGE));self.assertFalse(self.b.calls)
 def test_hole_rejected_atomically(self):
  self.b.owned.remove(BASE+PAGE);self.assert_error(errno.ENOMEM,lambda:self.m.lock(BASE,2*PAGE));self.assertFalse(self.b.calls)
 def test_unpublished_host_address(self):self.assert_error(errno.ENOMEM,lambda:self.m.lock(0x100000000,PAGE));self.assertFalse(self.b.calls)
 def test_overflow(self):
  for a,n in [(GUEST_LIMIT,0),(GUEST_LIMIT-1,2),(-1,1),(BASE,-1)]:self.assert_error(errno.EINVAL,lambda a=a,n=n:self.m.lock(a,n))
 def test_host_failure_rolls_back_only_new_pages(self):
  self.m.lock(BASE,PAGE);self.b.fail_lock=BASE+2*PAGE;self.assert_error(errno.EAGAIN,lambda:self.m.lock(BASE,3*PAGE));self.assertEqual(self.b.pins,{BASE});self.assertEqual(self.m.pages,{BASE})
 def test_rollback_failure_retains_accounting_and_blocks(self):
  self.b.fail_lock=BASE+PAGE;self.b.fail_unlock=BASE
  with self.assertRaises(RuntimeError):self.m.lock(BASE,2*PAGE)
  self.assertTrue(self.m.poisoned);self.assertEqual(self.m.pages,self.b.pins);self.assertEqual(self.m.wired_bytes,PAGE)
  with self.assertRaises(RuntimeError):self.m.lock(BASE,PAGE)
 def test_unlock_failure_restores_previous_state(self):
  self.m.lock(BASE,3*PAGE);self.b.fail_unlock=BASE+PAGE;self.assert_error(errno.ENOMEM,lambda:self.m.unlock(BASE,3*PAGE));self.assertEqual(self.m.pages,self.b.pins);self.assertEqual(self.m.wired_bytes,3*PAGE)
 def test_unlock_rollback_failure_blocks_with_exact_accounting(self):
  self.m.lock(BASE,2*PAGE);self.b.fail_unlock=BASE+PAGE;self.b.fail_lock=BASE
  with self.assertRaises(RuntimeError):self.m.unlock(BASE,2*PAGE)
  self.assertTrue(self.m.poisoned);self.assertEqual(self.m.pages,self.b.pins);self.assertEqual(self.m.wired_bytes,PAGE)
 def test_successful_unmap_implicit_cleanup_no_second_unlock(self):
  self.m.lock(BASE,2*PAGE);self.b.pins.remove(BASE);self.b.owned.remove(BASE);before=list(self.b.calls);self.m.committed_removal(BASE,PAGE);self.assertEqual(self.m.wired_bytes,PAGE);self.assertEqual(before,self.b.calls)
 def test_failed_unmap_must_not_retire(self):
  self.m.lock(BASE,PAGE);self.assertEqual(self.m.wired_bytes,PAGE) # no committed_removal on failed host map
 def test_reuse_does_not_inherit_old_pin(self):
  self.m.lock(BASE,PAGE);self.b.pins.remove(BASE);self.m.committed_removal(BASE,PAGE);self.m.lock(BASE,PAGE);self.assertEqual(len([c for c in self.b.calls if c[0]=='lock']),2)
 def test_alias_addresses_charged_conservatively(self):
  self.m.lock(BASE,PAGE);self.m.lock(BASE+PAGE,PAGE);self.assertEqual(self.m.wired_bytes,2*PAGE) # same PFN, if aliased, still two lock obligations
 def test_concurrent_duplicate_has_one_host_wiring(self):
  entered=threading.Event();release=threading.Event();self.b.pause=(entered,release)
  one=threading.Thread(target=lambda:self.m.lock(BASE,PAGE));two=threading.Thread(target=lambda:self.m.lock(BASE,PAGE));one.start();self.assertTrue(entered.wait(1));two.start();time.sleep(.02);release.set();one.join();two.join();self.assertEqual(self.b.calls,[('lock',BASE,PAGE)])
 def test_absolute_policy_ceiling(self):
  for kw in [dict(max_call=32<<20),dict(max_total=128<<20),dict(max_entries=8192)]:
   with self.assertRaises(ValueError):BoundedMlock(self.b,**kw)
if __name__=='__main__':unittest.main(verbosity=2)
