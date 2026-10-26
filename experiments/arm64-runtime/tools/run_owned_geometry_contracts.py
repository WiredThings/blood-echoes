"""Independent CPU GS runner; ordinary module packet admission stays unchanged."""
from pathlib import Path
import argparse,collections,json,struct,time
from game_module_inputs import need,source_code
from game_pso_owned_audit import checked_bytes
from complete_pipeline_inputs import directory,file_sha
from run_game_module_contracts import run_child,validate_call

def canonical_geometry_packet(path,catalog):
 path=Path(path);directory(path.parent);raw,_=checked_bytes(path,limit=256*1024);p=json.loads(raw);need(p['kind']=='owned-gcn-geometry-module-input','independent geometry packet kind')
 need((p['hardware_stage'],p['logical_stage'])==('geometry','geometry'),'exact GS stage pair');need(p['gs_copy']['hardware_stage']=='vertex','static native copy stage')
 sources=[]
 for key,owner in [('code',p),('code',p['gs_copy'])]:
  proof=owner['source_provenance'];identity=dict(program_hash=owner['program_hash'],hardware_stage=owner['hardware_stage'],code_sha256=proof['code_sha256']);evidence=dict(package=proof['container_path'],entry=proof['entry_name'],entry_sha256=proof['entry_sha256'],code_offset=proof['code_offset']);code,verified=source_code(catalog['game_root'],catalog,identity,evidence);need(verified==proof,'independently re-read geometry source');r=owner[key];data,_=checked_bytes(Path(r['path']),expected=r['sha256']);need(data==code and len(data)==r['bytes'],'owned main/copy equals original source');sources.append(proof)
 need(all(sources[0][k]==sources[1][k]for k in('container_path','container_sha256','entry_name','entry_sha256')),'same original entry main/copy source')
 total=0
 for r in [p[k]for k in('code','profile','spec','flat','walker','expected_spirv')]+[p['gs_copy']['code']]:
  if r is None:need(p['walker_bytes']==0,'null walker only when empty');continue
  file=Path(r['path']);need(file.parent==path.parent and file.name==r['name'],'one owned GS input root');data,_=checked_bytes(file,expected=r['sha256']);need(len(data)==r['bytes'],'owned GS reference extent');total+=len(data)
 need(total<=2*1024*1024,'combined GS owned budget');spec=Path(p['spec']['path']).read_bytes();rebind=p['spec_rebinding'];at=rebind['runtime_span_offset'];need(rebind['bytes']==16 and spec[at:at+16]==bytes(16),'entire retired span zeroed');need(p['gs_copy']['words']==p['gs_copy']['code']['bytes']//4==rebind['original_copy_words'],'exact owned copy words');need(p['gs_copy']['program_hash']==rebind['original_copy_hash'],'exact original copy hash')
 return p

def require_geometry_result(result,packet):
 need(result.get('owned_geometry_copy_rebound')is True,'native owned geometry rebinding proof')
 size=result.get('geometry_copy_bytes');need(type(size)is int and size==packet['gs_copy']['code']['bytes'],'native owned geometry copy byte count')

def main():
 ap=argparse.ArgumentParser(description=__doc__)
 for key in('project','build','batch','catalog','output'):ap.add_argument('--'+key,type=Path,required=True)
 ap.add_argument('--limit',type=int,default=128)
 a=ap.parse_args();P=directory(a.project);need(not a.output.exists()and a.output.is_relative_to(P/'evidence'),'fresh GS evidence');a.output.mkdir(mode=0o700);directory(a.output);build=json.loads(a.build.read_text());need(not build.get('diagnostic_only'),'ordinary CPU consumer required');binary=Path(build['binary']);checked_bytes(binary,limit=64*1024*1024,expected=build['binary_sha256']);checked_bytes(Path(build['pinned_library']),limit=256*1024*1024,expected=build['pinned_library_sha256']);batch=json.loads(a.batch.read_text());need(0<len(batch['packets'])<=128,'bounded owned GS batch');catalog=json.loads(a.catalog.read_text())
 from qa_build_guard import require_no_games
 from native_observer import NativeObserver
 from qa_memory_policy import MemoryPolicy
 require_no_games();observer=NativeObserver();before=observer.system();need(before['pressure']!=4 and not before['locked'],'safe CPU GS entry');policy=MemoryPolicy(before);started=time.monotonic();state=P/'state'/('owned-geometry-contracts-'+str(time.time_ns()));state.mkdir(mode=0o700);results=[];counts=collections.Counter()
 def check():
  reason=policy.observe(time.monotonic()-started,observer.system())
  if reason:raise RuntimeError(reason)
 try:
  need(0<a.limit<=128,'bounded positive selection')
  for i,item in enumerate(batch['packets'][:a.limit]):
   check();path=Path(item['packet']);p=canonical_geometry_packet(path,catalog);case=a.output/f'gs-{i:03d}-{p["program_hash"]}';case.mkdir();child=state/case.name;child.mkdir();exit_code,result,seconds=run_child(binary,path,case/'output',case,child,check)
   try:validate_call(exit_code,result,case/'output',p);require_geometry_result(result,p);verdict='passed'
   except ValueError:verdict='rejected-or-failed'
   counts[verdict]+=1;results.append(dict(program_hash=p['program_hash'],copy_words=p['gs_copy']['words'],exit=exit_code,result=result,verdict=verdict,seconds=seconds,log=str(case/'stderr.log')));print(case.name,verdict,result.get('error',''),flush=True)
  report=dict(schema=1,status='passed'if not counts['rejected-or-failed']else'has-input-rejections',counts=dict(counts),cases=results,physical_GS_metadata=batch['counts']['physical_GS_metadata'],owned_GS_packets=len(results),build_sha256=file_sha(a.build),batch_sha256=file_sha(a.batch),before=before,after=observer.system(),seconds=time.monotonic()-started,GPU_started=False,game_started=False,full_final_PSO_ready=False,runtime_cache_import_allowed=False,formal_data_modified=False,old_GS_worker_gate_changed=False)
  (a.output/'result.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
 finally:observer.close()
 print(json.dumps(dict(status=report['status'],counts=report['counts'],result=str(a.output/'result.json'))))
if __name__=='__main__':main()
