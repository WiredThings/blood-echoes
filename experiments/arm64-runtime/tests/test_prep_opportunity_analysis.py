import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from prep_opportunity_analysis import analyze
from test_shader_phase_analysis import fixture

def rows():
 a=fixture()
 for x in a:x.update(start=x['start']+1000,end=x['end']+1000)
 a[0]['arg']=(1<<32)|3
 # fixture metadata kind 81 is identified explicitly, not by list order.
 for x in a:
  if x['kind']==81:x['arg']=(1<<32)|3
 base=dict(pid=a[0]['pid'],tid=999,start=100,end=100,wall=0,cpu=0)
 return [dict(base,seq=1,kind=84,arg=0xabcdef),dict(base,seq=2,kind=85,arg=(1<<32)|1)]+[dict(x,seq=x['seq']+2)for x in a]

class TestPreparationOpportunity(unittest.TestCase):
 def test_matching_different_thread(self):
  a=rows();h=next(x['arg']for x in a if x['kind']==78);a[0]['arg']=h
  r=analyze(a);self.assertEqual(len(r['module_leads']),1);self.assertFalse(r['module_leads'][0]['full_specialization_equivalence_proven'])
 def test_hash_mismatch(self):
  a=rows();a[0]['arg']=0x1234;self.assertFalse(analyze(a)['module_leads'])
 def test_missing_identity(self):self.assertEqual(len(analyze(rows()[1:])['invalid_probe_pairs']),1)
 def test_wrong_stage(self):
  a=rows();a[1]['arg']=(1<<32)|6;self.assertFalse(analyze(a)['module_leads'])
 def test_duplicate_anchor(self):
  a=rows();a.append(dict(a[0],seq=100));self.assertEqual(len(analyze(a)['invalid_probe_pairs']),1)
 def test_gap(self):
  a=rows();a[1]['seq']=999;self.assertEqual(len(analyze(a)['invalid_probe_pairs']),1)
 def test_invalid_reason(self):
  a=rows();a[1]['arg']=4<<32;self.assertEqual(len(analyze(a)['invalid_probe_pairs']),1)
 def test_same_clock_distinct_pairs(self):
  a=rows();a.extend([dict(a[0],seq=9,arg=0x654321),dict(a[1],seq=10)])
  r=analyze(a);self.assertEqual(len(r['probes']),2);self.assertFalse(r['invalid_probe_pairs'])
 def test_same_thread_is_not_ahead(self):
  a=rows();a[0]['tid']=a[1]['tid']=9;self.assertFalse(analyze(a)['module_leads'])
 def test_discovery_after_compile_is_not_lead(self):
  a=rows()
  for x in a[:2]:x.update(start=9999,end=9999)
  self.assertFalse(analyze(a)['module_leads'])
 def test_template_miss_does_not_claim_equivalence(self):
  a=rows();a[1]['arg']=(3<<32)|1;r=analyze(a)
  self.assertEqual(r['module_leads'][0]['reason'],'template_missing');self.assertFalse(r['module_leads'][0]['full_specialization_equivalence_proven'])
 def test_overflow_reported(self):
  a=rows();a.append(dict(a[0],kind=86,seq=100));self.assertEqual(analyze(a)['overflow_markers'],1)

if __name__=='__main__':unittest.main()
