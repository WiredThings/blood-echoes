"""CPU-only automatic-input root and nonce/cohort controls in a new QA root."""
from pathlib import Path
import argparse,json,os,secrets,subprocess,time
from game_module_inputs import need
from complete_pipeline_inputs import directory,file_sha
from game_pso_owned_audit import checked_bytes
from launcher_game_modules import _capability,_automatic_packet
from run_game_module_contracts import validate_call,result_record

def run(*,project,build,packet,output,check=lambda:None):
 project=directory(Path(project));output=Path(output);need(output.is_relative_to(project/'evidence')and not output.exists(),'fresh admission evidence');output.mkdir(mode=0o700)
 b=json.loads(checked_bytes(Path(build),limit=4*1024*1024)[0]);binary=Path(b['binary']);checked_bytes(binary,limit=64*1024*1024,expected=b['binary_sha256']);need(b.get('diagnostic_only')is False,'ordinary CPU consumer')
 state=project/'state'/('automatic-module-admission-'+str(time.time_ns()));gpu=state/'gpu';gpu.mkdir(parents=True,mode=0o700);automatic=gpu/'automatic-game-inputs';automatic.mkdir(mode=0o700);base=automatic/'source';base.mkdir(mode=0o700)
 original=json.loads(checked_bytes(Path(packet),limit=256*1024)[0]);oldroot=Path(packet).parent
 for path in oldroot.iterdir():
  if path.name=='packet.json':continue
  raw,ref=checked_bytes(path);(base/path.name).write_bytes(raw)
 def relocate(value):
  if isinstance(value,dict):
   result={k:relocate(v)for k,v in value.items()}
   if 'path'in result and Path(result['path']).parent==oldroot:result['path']=str(base/Path(result['path']).name)
   return result
  if isinstance(value,list):return [relocate(v)for v in value]
  return value
 p=relocate(original);nonce=secrets.token_hex(32);cohort=secrets.token_hex(32);_automatic_packet(p,base/'packet.json',nonce,cohort)
 component=dict(module_consumer=binary);cap=_capability(component,gpu)
 cases=[]
 def child(name,*,selected=gpu,actual_packet=base/'packet.json',nonce_value=nonce,cohort_value=cohort,good=False,output_base=None,constructor_reject=False):
  check();case=output/name;case.mkdir();target=(output_base or automatic)/name;target.mkdir(mode=0o700);temp=state/(name+'-tmp');temp.mkdir(mode=0o700)
  env={k:v for k,v in os.environ.items()if not k.startswith(('BB_','DYLD_'))};env.update(BB_GPU_USER_DIR=str(selected),BB_MODULE_PREPARATION_NONCE=nonce_value,BB_MODULE_PREPARATION_COHORT=cohort_value,TMPDIR=str(temp))
  with(case/'stdout.log').open('x')as out,(case/'stderr.log').open('x')as err:
   proc=subprocess.Popen([str(binary),str(actual_packet),str(target)],env=env,stdout=out,stderr=err,start_new_session=True);began=time.monotonic()
   try:
    while proc.poll()is None:
     check();need(time.monotonic()-began<30,'bounded native admission child');time.sleep(.05)
   finally:
    if proc.poll()is None:
     proc.terminate()
     try:proc.wait(timeout=3)
     except subprocess.TimeoutExpired:proc.kill();proc.wait()
  if constructor_reject and proc.returncode==78:
   need(not(case/'stdout.log').read_text()and(case/'stderr.log').read_text().strip()=='ARM PRIVATE STATE REQUIRED: use the Shared isolated runner'and not list(target.iterdir()),'existing private-state constructor refusal');result=dict(status='existing-constructor-refusal')
  else:
   result=result_record((case/'stdout.log').read_text());validate_call(proc.returncode,result,target,p,expected_rejection=not good)
  cases.append(dict(name=name,status='passed',exit=proc.returncode,native=result.get('status'),error=result.get('error')))
 child('automatic-positive',good=True)
 child('wrong-nonce',nonce_value='0'*64)
 child('wrong-cohort',cohort_value='0'*64)
 child('nonhex-nonce',nonce_value='bad')
 child('state-root-not-selected-gpu',selected=project/'state',constructor_reject=True)
 outside=state/'outside';outside.mkdir(mode=0o700);child('output-outside-automatic',output_base=outside)
 # Path components must be canonical, including ancestors.
 alias=state/'gpu-link';alias.symlink_to(gpu,target_is_directory=True);child('selected-gpu-symlink',selected=alias,constructor_reject=True)
 report=dict(schema=1,status='passed',cases=cases,capability=cap,build_sha256=file_sha(build),GPU_started=False,game_started=False,formal_data_modified=False,existing_cache_modified=False)
 (output/'result.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n');return report

def main():
 ap=argparse.ArgumentParser(description=__doc__)
 for key in('project','build','packet','output'):ap.add_argument('--'+key,type=Path,required=True)
 a=ap.parse_args()
 from qa_build_guard import require_no_games
 from native_observer import NativeObserver
 from qa_memory_policy import MemoryPolicy
 require_no_games();observer=NativeObserver();before=observer.system();need(before['pressure']!=4 and not before['locked'],'safe CPU admission test entry');policy=MemoryPolicy(before);began=time.monotonic()
 def check():
  why=policy.observe(time.monotonic()-began,observer.system())
  if why:raise RuntimeError(why)
 try:r=run(project=a.project,build=a.build,packet=a.packet,output=a.output,check=check)
 finally:observer.close()
 print(json.dumps(dict(status=r['status'],cases=len(r['cases']),result=str(a.output/'result.json'))))
if __name__=='__main__':main()
