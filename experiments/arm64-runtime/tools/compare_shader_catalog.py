"""Offline hash/code identity coverage; no runtime specialization equivalence claim."""
import argparse,collections,json
from pathlib import Path

def compare(catalog,phase_report):
 groups=collections.defaultdict(list)
 for p in catalog['programs']:
  if not p['debug']:groups[p['program_hash']].append(p)
 modules=phase_report['shader_compile_phases']['modules'];hashes={m['program_hash']for m in modules if m['identity_complete']};matched=hashes&groups.keys();ambiguous={h:len({x['code_sha256']for x in groups[h]if x['code_prefix_verified']})for h in matched};ambiguous={h:n for h,n in ambiguous.items()if n>1}
 return dict(observed_program_hashes=len(hashes),matched_program_hashes=len(matched),absent_program_hashes=sorted(hashes-groups.keys()),observed_hashes_with_distinct_stored_codes=ambiguous,module_events=len(modules),complete_module_events=sum(x['complete']for x in modules),qualification='Hash presence in verified stored programs, not proof of exact live code, material/variant coverage, runtime specialization equivalence or all-game completeness. Stored shared hashes remain distinct by code digest.')
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('catalog',type=Path);ap.add_argument('report',type=Path);a=ap.parse_args();print(json.dumps(compare(json.loads(a.catalog.read_text()),json.loads(a.report.read_text())),indent=2))
