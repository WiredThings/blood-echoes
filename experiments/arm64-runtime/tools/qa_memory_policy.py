"""Fail-closed, bounded protection for explicitly authorized isolated game tests."""
from collections import deque
import math

class MemoryPolicy:
    MANIFEST = dict(schema=2, severe_seconds=5.0, swap_total_mib=2048.0,
                    burst_window_seconds=5.0, burst_growth_mib=1024.0,
                    warning_stops=False, lock_stops=True)

    def __init__(self, baseline):
        self._validate(0.0, baseline)
        self.baseline = baseline['swap_mib']
        self.previous = 0.0
        self.history = deque([(0.0, self.baseline)])
        self.severe_since = 0.0 if baseline['pressure'] == 4 else None

    @staticmethod
    def _validate(seconds, sample):
        if type(seconds) not in (float, int) or not math.isfinite(seconds) or seconds < 0:
            raise ValueError('invalid monotonic observation time')
        if type(sample['locked']) is not bool:
            raise ValueError('invalid lock state')
        if type(sample['pressure']) is not int or sample['pressure'] not in (1, 2, 4):
            raise ValueError('invalid pressure state')
        swap = sample['swap_mib']
        if type(swap) not in (float, int) or not math.isfinite(swap) or swap < 0:
            raise ValueError('invalid swap observation')

    def observe(self, seconds, sample):
        self._validate(seconds, sample)
        if seconds < self.previous:
            raise ValueError('observation clock moved backwards')
        self.previous = seconds
        while self.history and self.history[0][0] < seconds - 5.0:
            self.history.popleft()
        swap = sample['swap_mib']
        # Keep only candidate minima in this exact five-second window. A later
        # sample <= an earlier one dominates it for every future observation.
        # Compare against the PREVIOUS window before inserting today's sample,
        # preserving the original burst threshold, expiry and equality rules.
        recent_minimum = self.history[0][1] if self.history else swap
        while self.history and self.history[-1][1] >= swap:
            self.history.pop()
        self.history.append((seconds, swap))
        if sample['pressure'] == 4:
            if self.severe_since is None:
                self.severe_since = seconds
        else:
            self.severe_since = None
        if sample['locked']:
            return 'screen locked'
        if swap - self.baseline > 2048.0:
            return 'unexpected >2GiB swap growth'
        if swap - recent_minimum > 1024.0:
            return 'unexpected >1GiB swap growth within5s'
        if self.severe_since is not None and seconds - self.severe_since >= 5.0:
            return 'severe pressure sustained5s'
        return None
