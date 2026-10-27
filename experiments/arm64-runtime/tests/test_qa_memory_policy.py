import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from qa_memory_policy import MemoryPolicy

def s(swap=6000, pressure=1, locked=False):
    return dict(swap_mib=swap, pressure=pressure, locked=locked)

class PolicyTests(unittest.TestCase):
    def test_warning_continues(self):
        p=MemoryPolicy(s())
        for t in range(601): self.assertIsNone(p.observe(t,s(pressure=2)))
    def test_severe_grace(self):
        p=MemoryPolicy(s())
        self.assertIsNone(p.observe(1,s(pressure=4)))
        self.assertIsNone(p.observe(5.999,s(pressure=4)))
        self.assertEqual(p.observe(6,s(pressure=4)), 'severe pressure sustained5s')
    def test_severe_reset(self):
        p=MemoryPolicy(s())
        for t, level in [(1,4),(5,2),(6,4),(10.999,4)]:
            self.assertIsNone(p.observe(t,s(pressure=level)))
        self.assertEqual(p.observe(11,s(pressure=4)), 'severe pressure sustained5s')
    def test_total_growth(self):
        p=MemoryPolicy(s())
        for t in range(1,9): self.assertIsNone(p.observe(t*6,s(6000+256*t)))
        self.assertEqual(p.observe(54,s(8048.01)), 'unexpected >2GiB swap growth')
    def test_burst_boundary(self):
        p=MemoryPolicy(s())
        self.assertIsNone(p.observe(4,s(7024)))
        self.assertEqual(p.observe(5,s(7024.01)), 'unexpected >1GiB swap growth within5s')
    def test_expired_history(self):
        p=MemoryPolicy(s())
        self.assertIsNone(p.observe(5.001,s(6600)))
    def test_growth_after_reclaim(self):
        p=MemoryPolicy(s())
        self.assertIsNone(p.observe(1,s(5000)))
        self.assertEqual(p.observe(2,s(6025)), 'unexpected >1GiB swap growth within5s')
    def test_lock_immediate(self):
        p=MemoryPolicy(s())
        self.assertEqual(p.observe(.01,s(locked=True)), 'screen locked')
    def test_invalid_samples_fail_closed(self):
        for bad in [s(float('nan')), s(-1), s(pressure=0), s(pressure=True),s(locked=1)]:
            with self.assertRaises(ValueError): MemoryPolicy(bad)
            with self.assertRaises(ValueError): MemoryPolicy(s()).observe(1,bad)
    def test_invalid_time(self):
        for t in [-1,float('nan'),float('inf'),True]:
            with self.assertRaises(ValueError): MemoryPolicy(s()).observe(t,s())
        p=MemoryPolicy(s());p.observe(2,s())
        with self.assertRaises(ValueError): p.observe(1,s())

class RollingMinimumTests(unittest.TestCase):
    def test_exact_original_decisions_over_dense_and_sparse_samples(self):
        import random
        from collections import deque
        random_=random.Random(5133)
        p=MemoryPolicy(s());history=deque([(0.,6000.)]);severe=None;seconds=0.
        for i in range(12000):
            seconds+=random_.choice([0.,.00001,.001,.02,1.,5.,5.000001])
            sample=s(random_.choice([4000.,5000.,6000.,7024.,7024.00001,8048.,8048.00001]),
                     random_.choice([1,2,4]),random_.randrange(97)==0)
            while history and history[0][0]<seconds-5.:history.popleft()
            swap=sample['swap_mib'];minimum=min((v for _,v in history),default=swap)
            history.append((seconds,swap))
            severe=(seconds if severe is None else severe)if sample['pressure']==4 else None
            expected=('screen locked'if sample['locked']else
                      'unexpected >2GiB swap growth'if swap-6000>2048 else
                      'unexpected >1GiB swap growth within5s'if swap-minimum>1024 else
                      'severe pressure sustained5s'if severe is not None and seconds-severe>=5. else None)
            self.assertEqual(p.observe(seconds,sample),expected)
    def test_dense_equal_observations_do_not_retain_duplicate_history(self):
        p=MemoryPolicy(s())
        for i in range(10000):self.assertIsNone(p.observe(i*.000001,s()))
        self.assertEqual(len(p.history),1)
    def test_decreasing_minimum_still_detects_following_burst(self):
        p=MemoryPolicy(s())
        for i in range(1000):self.assertIsNone(p.observe(i*.001,s(6000-i)))
        self.assertEqual(len(p.history),1)
        self.assertEqual(p.observe(1.,s(6026)),'unexpected >1GiB swap growth within5s')

if __name__=='__main__': unittest.main()
