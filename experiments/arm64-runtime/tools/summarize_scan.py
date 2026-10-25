"""Metadata-only consolidation; never overwrite an input scan or imply all PSOs."""
from pathlib import Path
import argparse,collections,json,time

def rows(path):
 if path.exists():
  with path.open()as f:
   for line in f:yield json.loads(line)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--scan',type=Path,required=True);ap.add_argument('--recovery',type=Path,action='append',default=[]);ap.add_argument('--prior-catalog',type=Path,required=True);ap.add_argument('--bindings',type=Path);ap.add_argument('--out',type=Path);a=ap.parse_args()
 s=json.loads((a.scan/'summary.json').read_text());recovery=[json.loads((p/'summary.json').read_text())for p in a.recovery];b=json.loads((a.bindings or a.scan/'binding-summary.json').read_text());prior=json.loads(a.prior_catalog.read_text());recovered={p for r in recovery for p in r['recovered_paths']}
 unresolved=[x for x in rows(a.scan/'errors.jsonl')if x['path']not in recovered]
 for r in recovery:unresolved.extend(x for x in r['errors']if x['path']not in recovered)
 sources={};seen_records=set();conflicts=[];kind=collections.Counter();directories=[a.scan,*a.recovery]
 for directory in directories:
  for x in rows(directory/'resources.jsonl'):
   old=sources.get(x['path'])
   if old and old!=x['sha256']:conflicts.append(x['path'])
   sources.setdefault(x['path'],x['sha256']);record=(x['path'],x['sha256'],x['kind'])
   if record not in seen_records:seen_records.add(record);kind[x['kind']]+=1
 actual_programs=set();actual_codes=set();models={}
 for directory in directories:
  for x in rows(directory/'programs.jsonl'):
   if not x['debug']:
    actual_programs.add(x['program_hash'])
    if x.get('code_prefix_verified'):actual_codes.add(x['code_sha256'])
  for x in rows(directory/'models.jsonl'):models[(x['resource'],x['sha256'])]=True
 prior_programs={x['program_hash']for x in prior['programs']if not x['debug']}
 unresolved_attempts=len(unresolved);unresolved=list({(x['path'],x['error']):x for x in unresolved}.values())
 attempts=s['counts'].get('binders_parsed',0)+sum(r['counts'].get('binders_parsed',0)for r in recovery)
 out=dict(physical_files=s['files'],physical_bytes=s['bytes'],physical_hash_ledger=str(a.scan/'files.jsonl'),prior_packages_reused_by_sha=s['reused_prior_packages'],shader_entries_nondebug=prior['summary']['shader_entries'],shader_entries_debug=prior['summary']['debug_entries'],unique_nondebug_program_hashes=len(actual_programs),unique_nondebug_code_sha256=len(actual_codes),new_program_hashes_from_other_resource_containers=sorted(actual_programs-prior_programs),prior_program_hashes_not_seen=sorted(prior_programs-actual_programs),structurally_parsed_flver=len(models),binder_parse_attempts=attempts,unique_binder_headers_with_explicit_ledger=kind.get('binder-header',0),unique_bxf_headers_with_explicit_ledger=kind.get('split-header',0),legacy_initial_bxf_table_parses=s['counts'].get('split_headers_parsed',0),unique_resource_rows_by_kind=dict(kind),resource_identity_conflicts=sorted(set(conflicts)),recovered_inputs=len(recovered),unresolved_inputs=unresolved,unresolved_attempt_count=unresolved_attempts,initial_optional_segment_gaps=list(rows(a.scan/'gaps.jsonl')),bindings=b,full_pso_recipes_from_static_scan=0,qualification='Physical hashes, explicitly emitted format metadata and stated gaps only. Parse attempts are not blindly summed as unique containers; old scans without explicit header ledger retain that limitation. No universal format/full-PSO coverage claim; no game bytes or compiled assets exported.')
 output=a.out or a.scan.parent/('final-summary-'+str(time.time_ns()));assert output.is_absolute()and not output.exists(),'new absolute metadata output required';output.mkdir(parents=True);(output/'final-summary.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n');print('OUTPUT',output);print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
