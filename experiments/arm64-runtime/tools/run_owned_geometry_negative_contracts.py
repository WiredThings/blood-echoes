"""Native GS-copy rejection probes; all mutations confined to fresh clones."""
from pathlib import Path
import argparse,json,struct,time
from complete_pipeline_inputs import directory,file_sha
from game_module_inputs import need
from game_pso_owned_audit import checked_bytes
from run_game_module_contracts import run_child,validate_call,canonical_packet
from run_game_module_negative_contracts import clone_packet,mutate_blob,setword,setbyte
from run_owned_geometry_contracts import canonical_geometry_packet

def cases(base):
 rows=[];span=base['spec_rebinding']['runtime_span_offset'];hw=span-56
 def packet(name,error,fn):rows.append((name,error,fn))
 def spec(name,error,fn):packet(name,error,lambda p,r:mutate_blob(p,r,'spec',fn))
 spec('retired-pointer-nonzero','retired geometry span',lambda b:struct.pack_into('<Q',b,span,0xDEADBEEF))
 spec('retired-span-length-nonzero','retired geometry span',lambda b:struct.pack_into('<Q',b,span+8,1))
 packet('copy-words-mismatch','owned geometry exact copy identity',lambda p,r:p['gs_copy'].update(words=p['gs_copy']['words']+1))
 packet('copy-hash-mismatch','owned geometry exact copy identity',lambda p,r:p['gs_copy'].update(program_hash='0000000000000001'))
 packet('rebind-words-mismatch','owned geometry exact copy identity',lambda p,r:p['spec_rebinding'].update(original_copy_words=p['gs_copy']['words']+1))
 packet('rebind-hash-mismatch','owned geometry exact copy identity',lambda p,r:p['spec_rebinding'].update(original_copy_hash='0000000000000001'))
 packet('copy-bad-digest','input SHA mismatch',lambda p,r:p['gs_copy']['code'].update(sha256='0'*64))
 packet('copy-empty-digest','required file SHA256 identity',lambda p,r:p['gs_copy']['code'].update(sha256=''))
 packet('copy-wrong-stage','owned geometry copy code',lambda p,r:p['gs_copy'].update(hardware_stage='geometry'))
 packet('copy-code-truncated','owned geometry exact copy identity',lambda p,r:mutate_blob(p['gs_copy'],r,'code',lambda b:b.__delitem__(slice(-4,None))))
 packet('copy-wrong-container','same-source owned geometry copy provenance',lambda p,r:p['gs_copy']['source_provenance'].update(container_sha256='0'*64))
 packet('copy-wrong-entry','same-source owned geometry copy provenance',lambda p,r:p['gs_copy']['source_provenance'].update(entry_sha256='0'*64))
 packet('retired-pointer-policy','owned geometry span rebinding policy',lambda p,r:p['spec_rebinding'].update(retired_pointer_exported=True))
 packet('non-GS-packet-kind','runtime plain stage discriminators',lambda p,r:p.update(kind='owned-gcn-module-input'))
 spec('invocations2','bounded geometry output/invocation count',lambda b:setword(b,hw+16,2))
 spec('outputs4','bounded geometry output/invocation count',lambda b:setword(b,hw,4))
 spec('output-map-invalid','geometry output enum',lambda b:setbyte(b,hw+4,255))
 spec('vertices0','bounded geometry vertex data',lambda b:setword(b,hw+20,0))
 spec('input-vertexsize3','bounded geometry vertex data',lambda b:setword(b,hw+24,3))
 spec('output-vertexsize0','bounded geometry vertex data',lambda b:setword(b,hw+28,0))
 spec('unsupported-input-primitive','owned geometry point-list ScenarioG subset',lambda b:setword(b,hw+32,4))
 spec('unsupported-output-primitive','geometry output primitive enum',lambda b:setword(b,hw+36,3))
 spec('unsupported-mode','owned geometry point-list ScenarioG subset',lambda b:setword(b,hw+52,1))
 packet('owned-copy-policy-disabled','required owned geometry policy',lambda p,r:p['constraints'].update(requires_owned_GS_copy_rebinding=False))
 packet('geometry-scalars-policy-disabled','required owned geometry policy',lambda p,r:p['constraints'].update(requires_explicit_geometry_scalar_equality=False))
 packet('original-spec-sha-malformed','original geometry spec identity',lambda p,r:p['spec_rebinding'].update(original_spec_sha256=''))
 packet('unknown-programbase-value-nonnull','explicit unknown program base',lambda p,r:p['program_base'].update(value=1))
 return rows

def main():
 ap=argparse.ArgumentParser(description=__doc__)
 for key in('project','build','packet','catalog','output'):ap.add_argument('--'+key,type=Path,required=True)
 ap.add_argument('--ordinary-packet',type=Path);ap.add_argument('--case',action='append',dest='selected_cases')
 a=ap.parse_args();P=directory(a.project);need(not a.output.exists()and a.output.is_relative_to(P/'evidence'),'fresh GS negative evidence');a.output.mkdir(mode=0o700);directory(a.output);build=json.loads(a.build.read_text());need(not build.get('diagnostic_only'),'ordinary GS CPU consumer');binary=Path(build['binary']);checked_bytes(binary,limit=64*1024*1024,expected=build['binary_sha256']);checked_bytes(Path(build['pinned_library']),limit=256*1024*1024,expected=build['pinned_library_sha256']);base=canonical_geometry_packet(a.packet,json.loads(a.catalog.read_text()))
 from qa_build_guard import require_no_games
 from native_observer import NativeObserver
 from qa_memory_policy import MemoryPolicy
 require_no_games();observer=NativeObserver();before=observer.system();need(before['pressure']!=4 and not before['locked'],'safe GS negative CPU entry');policy=MemoryPolicy(before);started=time.monotonic();state=P/'state'/('owned-geometry-negatives-'+str(time.time_ns()));state.mkdir(mode=0o700);results=[]
 def check():
  reason=policy.observe(time.monotonic()-started,observer.system())
  if reason:raise RuntimeError(reason)
 try:
  inputs=[(name,error,mutate,base)for name,error,mutate in cases(base)]
  if a.ordinary_packet:
   ordinary=canonical_packet(a.ordinary_packet,json.loads(a.catalog.read_text()));inputs.append(('ordinary-as-GS-kind','geometry schema and stage must match',lambda p,r:p.update(kind='owned-gcn-geometry-module-input'),ordinary))
  for name,error,mutate,source in inputs:
   if a.selected_cases and name not in a.selected_cases:continue
   check();case=a.output/name;case.mkdir();child=state/name;child.mkdir();p=clone_packet(source,case/'input')
   if'gs_copy'in p:
    r=p['gs_copy']['code'];raw,_=checked_bytes(Path(r['path']),expected=r['sha256']);path=case/'input'/r['name'];path.write_bytes(raw);r['path']=str(path)
   mutate(p,case/'input');path=case/'input/packet.json';path.write_text(json.dumps(p));exit_code,result,seconds=run_child(binary,path,case/'output',case,child,check);validate_call(exit_code,result,case/'output',p,True);need(error in result['error'],'intended GS gate required: '+name);results.append(dict(name=name,exit=exit_code,error=result['error'],seconds=seconds));print(name,'rejected',result['error'],flush=True)
  report=dict(schema=1,status='passed',count=len(results),cases=results,build_sha256=file_sha(a.build),before=before,after=observer.system(),GPU_started=False,game_started=False,runtime_cache_import_allowed=False,formal_data_modified=False,original_pointer_followed=False,old_GS_worker_gate_changed=False);(a.output/'result.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
 finally:observer.close()
 print(json.dumps(dict(status=report['status'],count=report['count'],result=str(a.output/'result.json'))))
if __name__=='__main__':main()
