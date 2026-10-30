import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from bbcpu_resume_catalog import continuations

class ResumeCatalog(unittest.TestCase):
 def site(self,at=100,covered=6,phase='resource-hooks'):
  return dict(site=at,covered=covered,resume=at+covered,phase=phase)
 def test_real_covered_prefix_yields_only_actual_remaining_body(self):
  self.assertEqual(continuations([(100,30),(150,20)],[self.site()]),[(106,24)])
 def test_inline_site_and_multiple_sites_have_distinct_real_suffixes(self):
  self.assertEqual(continuations([(100,30)],[self.site(100),self.site(113)]),[(106,24),(119,11)])
 def test_setjmp_owner_keeps_upstream_translator_restriction(self):
  self.assertEqual(continuations([(100,30)],[self.site()],blocked=[100]),[])
 def test_invalid_entries_are_not_guessed_or_clamped(self):
  for entry in(self.site(1),self.site(100,30),self.site(100,31),self.site(100,0),self.site(100,-1),self.site(100,6,'invented'),{**self.site(),'resume':107}):
   with self.subTest(entry=entry),self.assertRaises(ValueError):continuations([(100,30)],[entry])
 def test_duplicate_and_existing_start_conflicts_refused(self):
  for rows,sites in(([(100,30)],[self.site(),self.site()]),([(100,30),(106,24)],[self.site()]),([(100,30),(100,31)],[self.site()])):
   with self.subTest(rows=rows),self.assertRaises(ValueError):continuations(rows,sites)
 def test_ambiguous_owner_and_excluded_suffix_refused(self):
  with self.assertRaises(ValueError):continuations([(90,40),(100,30)],[self.site()])
  with self.assertRaises(ValueError):continuations([(100,30)],[self.site()],exclusions=[(110,8)])
 def test_count_bound_and_non_integer_fields_refused(self):
  with self.assertRaises(ValueError):continuations([(100,30)],[self.site()]*65)
  with self.assertRaises(ValueError):continuations([(100,30)],[self.site(True)])

if __name__=='__main__':unittest.main()
