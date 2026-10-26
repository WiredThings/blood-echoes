"""CPU-only owned-module positive/negative runner; never opens a game or Device.

Every child receives a fresh BB_GPU_USER_DIR and evidence output. Only its own
process group may be stopped, and every invocation has a 30 second ceiling.
"""
from pathlib import Path
import argparse,collections,hashlib,json,os,signal,subprocess,time
from game_module_inputs import need,source_code
from game_pso_owned_audit import checked_bytes
from complete_pipeline_inputs import directory,file_sha

def result_record(text):
 records=[json.loads(s.split(' ',1)[1])for s in text.splitlines()if s.startswith('GAME_MODULE_RESULT ')]
 need(len(records)==1,'one native result required');return records[0]

def validate_call(exit_code,result,output,packet,expected_rejection=False,rejected_diagnostic=None):
 need(all(result.get(k)is False for k in('runtime_cache_import_allowed','gpu_started','native_info_srt_deserialized','old_walker_executed','guest_memory_read')),'native ownership flags')
 need(result.get('candidate_only')is True,'candidate result only')
 if expected_rejection:
  need(exit_code==65 and result.get('status')=='rejected','clean bounded rejection required')
  if rejected_diagnostic is None:need(not list(output.iterdir()),'clean bounded rejection required')
  else:
   need(result.get('error')=='retranslated SPIR-V differs from observed module'and set(p.name for p in output.iterdir())=={'rejected-generated.spv'},'only explicit rejected diagnostic allowed')
   raw,_=checked_bytes(output/'rejected-generated.spv',expected=rejected_diagnostic['sha256']);need(len(raw)==rejected_diagnostic['bytes'],'rejected diagnostic matches unmodified control')
  return
 need(exit_code==0 and result.get('status')=='completed','native compilation did not complete')
 need(all(result.get(k)is True for k in('expected_spirv_equal','current_generated_srt_verified','unknown_program_base_rejected_before_use')),'all native equivalence gates required')
 need(result.get('full_final_pso_ready')is False and result.get('program_hash')==packet['program_hash'],'module identity only, not PSO')
 actual,_=checked_bytes(output/'module.spv',expected=packet['expected_spirv']['sha256'])
 need(len(actual)==packet['expected_spirv']['bytes'] and result.get('spirv_bytes')==len(actual)and result.get('spirv_sha256')==packet['expected_spirv']['sha256'],'observed SPIR-V byte equality')
 need(set(p.name for p in output.iterdir())=={'module.spv','result.json'},'only exact module outputs')

def canonical_packet(packet_path,catalog):
 packet_path=Path(packet_path);directory(packet_path.parent);raw,_=checked_bytes(packet_path,limit=256*1024);p=json.loads(raw)
 proof=p['source_provenance'];identity=dict(program_hash=p['program_hash'],hardware_stage=p['hardware_stage'],code_sha256=proof['code_sha256'])
 evidence=dict(package=proof['container_path'],entry=proof['entry_name'],entry_sha256=proof['entry_sha256'],code_offset=proof['code_offset'])
 source,verified=source_code(catalog['game_root'],catalog,identity,evidence)
 need(verified==proof,'independent original-source provenance changed')
 total=0
 for key in('code','profile','spec','flat','expected_spirv','walker'):
  r=p[key]
  if r is None:
   need(key=='walker'and p['walker_bytes']==0,'only empty walker can be null');continue
  path=Path(r['path']);need(path.parent==packet_path.parent and path.name==r['name'],'one frozen input root')
  data,_=checked_bytes(path,expected=r['sha256']);need(len(data)==r['bytes'],'frozen byte length');total+=len(data)
  if key=='code':need(data==source,'raw GCN equals independently re-read source')
 need(total<=2*1024*1024,'whole module input budget');return p

def run_child(binary,packet,output,case,state,check):
 output.mkdir();gpu=state/'gpu';gpu.mkdir();temp=state/'tmp';temp.mkdir();env={k:v for k,v in os.environ.items()if not k.startswith(('BB_','DYLD_'))};env.update(BB_GPU_USER_DIR=str(gpu),TMPDIR=str(temp))
 started=time.monotonic()
 with(case/'stdout.log').open('w')as out,(case/'stderr.log').open('w')as err:
  process=subprocess.Popen([str(binary),str(packet),str(output)],stdout=out,stderr=err,env=env,start_new_session=True)
  try:
   while process.poll()is None:
    check();need(time.monotonic()-started<=30,'native child exceeded30s')
    need((case/'stdout.log').stat().st_size+(case/'stderr.log').stat().st_size<=8*1024*1024,'bounded child log')
    time.sleep(.05)
  finally:
   if process.poll()is None:
    os.killpg(process.pid,signal.SIGTERM)
    try:process.wait(timeout=3)
    except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait()
 try:result=result_record((case/'stdout.log').read_text())
 except(ValueError,json.JSONDecodeError)as error:result=dict(status='native-result-unavailable',error=str(error))
 return process.returncode,result,time.monotonic()-started

def run_batch(*,project,build_path,batch_path,catalog_path,output,check=lambda:None,only_stage=None,allow_export=False):
 project=directory(Path(project));output=Path(output).absolute();need(output.is_relative_to(project/'evidence')and not output.exists(),'fresh private evidence output');output.mkdir(mode=0o700);directory(output)
 build=json.loads(Path(build_path).read_text());binary=Path(build['binary']);checked_bytes(binary,limit=64*1024*1024,expected=build['binary_sha256']);checked_bytes(Path(build['pinned_library']),limit=256*1024*1024,expected=build['pinned_library_sha256'])
 batch=json.loads(Path(batch_path).read_text());need(len(batch['packets'])<=128,'bounded batch');catalog=json.loads(Path(catalog_path).read_text());state=project/'state'/('game-module-contracts-'+str(time.time_ns()));state.mkdir(mode=0o700);counts=collections.Counter();cases=[]
 for n,item in enumerate(batch['packets']):
  if only_stage and item['hardware_stage']not in only_stage:continue
  check();packet_path=Path(item['packet']);p=canonical_packet(packet_path,catalog);case=output/f'{n:03d}-{p["hardware_stage"]}-{p["program_hash"]}';case.mkdir();child_state=state/case.name;child_state.mkdir()
  code,result,seconds=run_child(binary,packet_path,case/'output',case,child_state,check)
  expected_rejection=p['hardware_stage']=='export'and not allow_export
  try:validate_call(code,result,case/'output',p,expected_rejection);verdict='expected-rejection'if expected_rejection else'passed'
  except ValueError:verdict='unexpected-rejection'
  counts[verdict]+=1;cases.append(dict(program_hash=p['program_hash'],hardware_stage=p['hardware_stage'],exit=code,verdict=verdict,result=result,seconds=seconds,log=str(case/'stderr.log')))
  print(p['hardware_stage'],p['program_hash'],verdict,result.get('error',''),flush=True)
  need(file_sha(binary)==build['binary_sha256'],'consumer binary changed during run')
 report=dict(schema=1,status='passed'if not counts['unexpected-rejection']else'has-input-rejections',counts=dict(counts),cases=cases,state=str(state),diagnostic_consumer=bool(build.get('diagnostic_only')),runtime_preparer_eligible=False,build_sha256=file_sha(build_path),batch_sha256=file_sha(batch_path),source_catalog_sha256=file_sha(catalog_path),GPU_started=False,game_started=False,runtime_cache_import_allowed=False,formal_data_modified=False)
 (output/'result.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n');return report

def main():
 ap=argparse.ArgumentParser(description=__doc__)
 for key in('project','build','batch','catalog','output'):ap.add_argument('--'+key,type=Path,required=True)
 ap.add_argument('--only-stage',action='append',choices=('compute','fragment','vertex','export'));ap.add_argument('--allow-export',action='store_true')
 a=ap.parse_args();from native_observer import NativeObserver
 from qa_memory_policy import MemoryPolicy
 from qa_build_guard import require_no_games
 require_no_games();observer=NativeObserver();before=observer.system();need(before['pressure']!=4 and not before['locked'],'safe CPU audit entry');policy=MemoryPolicy(before);start=time.monotonic()
 def check():
  reason=policy.observe(time.monotonic()-start,observer.system())
  if reason:raise RuntimeError(reason)
 try:
  r=run_batch(project=a.project,build_path=a.build,batch_path=a.batch,catalog_path=a.catalog,output=a.output,check=check,only_stage=a.only_stage,allow_export=a.allow_export);r.update(before=before,after=observer.system(),memory_policy=MemoryPolicy.MANIFEST);(a.output/'result.json').write_text(json.dumps(r,sort_keys=True,indent=2)+'\n')
 finally:observer.close()
 print(json.dumps(dict(status=r['status'],counts=r['counts'],result=str(a.output/'result.json'))))
if __name__=='__main__':main()
