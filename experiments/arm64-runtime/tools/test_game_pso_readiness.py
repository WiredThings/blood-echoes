import copy,unittest
from prepare_game_psos import preparation_ready
class Readiness(unittest.TestCase):
 def valid(self):
  return dict(worker=dict(native_pipeline_ready=True,artifact_publication_observed=True,artifact_counts=dict(attempts=1,hits=1,built=0,deferred=0,failed=0,critical=0,unavailable=0)),artifact_events=[dict(state=1,archive_sha256='a'*64)])
 def test_real_success(self):
  self.assertTrue(preparation_ready(self.valid()))
 def test_worker_completion_is_not_preparation(self):
  for field in('native_pipeline_ready','artifact_publication_observed'):
   row=self.valid();row['worker'][field]=False;self.assertFalse(preparation_ready(row))
 def test_deferred_or_failed_cannot_succeed(self):
  for field in('deferred','failed','critical','unavailable'):
   row=self.valid();row['worker']['artifact_counts'][field]=1;self.assertFalse(preparation_ready(row))
 def test_missing_or_unverified_actual_events(self):
  for events in([], [dict(state=3,archive_sha256='a'*64)], [dict(state=1,archive_sha256='')]):
   row=self.valid();row['artifact_events']=events;self.assertFalse(preparation_ready(row))
 def test_no_fabricated_zero_attempt_success(self):
  row=self.valid();row['worker']['artifact_counts'].update(attempts=0,hits=0);row['artifact_events']=[];self.assertFalse(preparation_ready(row))
if __name__=='__main__':unittest.main()
