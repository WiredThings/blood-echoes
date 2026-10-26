"""Actual current-TU CPU projection translation with independent source proof."""
from pathlib import Path
import argparse,collections,json,time
from complete_pipeline_inputs import directory,file_sha
from game_module_inputs import need
from game_pso_owned_audit import checked_bytes
from fetch_projection_inputs import project,verify_observed
from run_game_module_contracts import canonical_packet,run_child,validate_call

def verify_packet(path,*,catalog,rows,layout):
 p=canonical_packet(path,catalog);need(p['kind']=='owned-gcn-fetch-projection-module-input','projection-only kind');matches=[r for r in rows if(r['static_identity']['program_hash'],r['static_identity']['hardware_stage'],r['observed']['metadata']['sha256'],r['observed']['metadata']['path'])==(p['program_hash'],p['hardware_stage'],p['source_metadata']['sha256'],p['source_metadata']['original_path'])];need(len(matches)==1,'exact observed projection row');row=matches[0];need(project(row)==p['fetch_projection'],'original projection facts');metadata,meta,sections,profile,spirv,keys,_=verify_observed(row,layout)
 for name,data in [('profile',profile),('expected_spirv',spirv),('spec',metadata[sections['specialization']['offset']:sections['specialization']['offset']+sections['specialization']['bytes']])]:
  ref=p[name];owned,_=checked_bytes(Path(ref['path']),expected=ref['sha256']);need(owned==data,'reopened original '+name+' equality')
 header=p['source_metadata']['header'];need(Path(header['path']).parent==Path(path).parent,'frozen header root');owned,_=checked_bytes(Path(header['path']),expected=header['sha256']);need(owned==metadata[:24],'original metadata header equality');need(len(keys)==len(p['projection_source_keys']),'all original associated keys')
 for a,b in zip(keys,p['projection_source_keys']):
  need(all(a[k]==b[k]for k in('vertex_permutation_hash','vertex_formats','vertex_attributes')),'original key facts');ref=b['reference'];need(Path(ref['path']).parent==Path(path).parent,'frozen key root');owned,_=checked_bytes(Path(ref['path']),expected=ref['sha256']);source,_=checked_bytes(Path(a['reference']['path']),expected=a['reference']['sha256']);need(owned==source,'original full key equality')
 return p

def main():
 ap=argparse.ArgumentParser(description=__doc__)
 for key in('project','build','batch','catalog','sidecar','layout','output'):ap.add_argument('--'+key,type=Path,required=True)
 a=ap.parse_args();P=directory(a.project);need(not a.output.exists()and a.output.is_relative_to(P/'evidence'),'fresh projection test evidence');a.output.mkdir(mode=0o700);directory(a.output);b=json.loads(a.build.read_text());need(b.get('explicit_projection_callsite_TUs_compiled')and not b.get('diagnostic_only'),'actual current projection call sites required');binary=Path(b['binary']);checked_bytes(binary,limit=64*1024*1024,expected=b['binary_sha256']);checked_bytes(Path(b['pinned_library']),limit=256*1024*1024,expected=b['pinned_library_sha256']);catalog=json.loads(a.catalog.read_text());batch=json.loads(a.batch.read_text());layout=json.loads(a.layout.read_text());rows=[json.loads(line)for line in a.sidecar.open()];state=P/'state'/('fetch-projection-contracts-'+str(time.time_ns()));state.mkdir(mode=0o700)
 from qa_build_guard import require_no_games
 from native_observer import NativeObserver
 from qa_memory_policy import MemoryPolicy
 require_no_games();observer=NativeObserver();before=observer.system();need(before['pressure']!=4 and not before['locked'],'safe CPU projection entry');policy=MemoryPolicy(before);started=time.monotonic();results=[];counts=collections.Counter()
 def check():
  reason=policy.observe(time.monotonic()-started,observer.system())
  if reason:raise RuntimeError(reason)
 try:
  for i,item in enumerate(batch['packets']):
   check();path=Path(item['packet']);p=verify_packet(path,catalog=catalog,rows=rows,layout=layout);case=a.output/f'{i:03d}-{p["hardware_stage"]}-{p["program_hash"]}';case.mkdir();child=state/case.name;child.mkdir();code,result,seconds=run_child(binary,path,case/'output',case,child,check)
   try:
    validate_call(code,result,case/'output',p);need(result.get('explicit_fetch_projection_used')is True and result.get('raw_Vsharp_materialized')is False and result.get('fetch_projection_attributes')==len(p['fetch_projection']['attributes']),'explicit projection result proof');verdict='passed'
   except ValueError:verdict='rejected-or-failed'
   results.append(dict(program_hash=p['program_hash'],hardware_stage=p['hardware_stage'],exit=code,verdict=verdict,result=result,seconds=seconds,log=str(case/'stderr.log')));counts[verdict]+=1;print(case.name,verdict,result.get('error',''),flush=True)
  report=dict(schema=1,status='passed'if not counts['rejected-or-failed']else'has-input-rejections',counts=dict(counts),cases=results,build_sha256=file_sha(a.build),before=before,after=observer.system(),GPU_started=False,game_started=False,raw_Vsharp_synthesized=False,formal_data_modified=False,runtime_cache_import_allowed=False,fresh_coherent_renderer=False);(a.output/'result.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
 finally:observer.close()
 print(json.dumps(dict(status=report['status'],counts=report['counts'],result=str(a.output/'result.json'))))
if __name__=='__main__':main()
