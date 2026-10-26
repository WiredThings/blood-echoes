"""Representative real CPU companion retranslation against one coherent DSO.

Uses the existing authored receipt fixture and a tiny real compiler-input shader.
No actual game/save/cache, renderer Device, window, or formal application is used.
"""
from pathlib import Path
import argparse,copy,hashlib,json,os,secrets,signal,struct,subprocess,sys,time
from complete_pipeline_inputs import directory,file_sha
from game_module_inputs import need
from game_pso_owned_audit import checked_bytes
from launcher_game_modules import _automatic_packet,run_module,_capability
from game_companion_modules import _packet,validate_call
from prepare_game_psos import dyld_gpu_image,verify_loaded_gpu

D=Path(__file__).resolve().parents[1]
def run(*,project,build_result,output,repair=None,check=lambda:None):
 project=directory(project);out=Path(output);need(out.is_relative_to(project/'evidence')and not out.exists(),'fresh typed test evidence');out.mkdir(mode=0o700)
 result=json.loads(checked_bytes(build_result,limit=32*1024*1024)[0]);need(result.get('coherent_renderer')is True and result.get('targeted_compiler_overrides')is False,'coherent source build required');plan=json.loads(checked_bytes(Path(build_result).parent/'plan.json',limit=32*1024*1024)[0])
 if repair is not None:
  fixed=json.loads(checked_bytes(Path(repair),limit=16*1024*1024)[0]);need(fixed['base_result_sha256']==file_sha(build_result)and fixed['library_sha256']==result['library_sha256']and fixed['shared_abi_headers_unchanged']is True,'exact native-only repair identity');result.update(module_consumer=fixed['module_consumer'],module_consumer_sha256=fixed['module_consumer_sha256']);result['binaries']['module']['dependencies']=fixed['dependencies']
 library=Path(result['library']);module=Path(result['module_consumer']);checked_bytes(library,limit=256*1024*1024,expected=result['library_sha256']);checked_bytes(module,limit=64*1024*1024,expected=result['module_consumer_sha256'])
 state=project/'state'/('typed-companion-contracts-'+str(time.time_ns()));gpu=state/'gpu';gpu.mkdir(parents=True,mode=0o700);auto=gpu/'automatic-game-inputs';auto.mkdir(mode=0o700);capture=auto/'capture';capture.mkdir(mode=0o700);nonce=secrets.token_hex(32);cohort=secrets.token_hex(32)
 env={k:v for k,v in os.environ.items()if not k.startswith(('BB_','DYLD_'))};env['BB_GPU_USER_DIR']=str(gpu)
 def command(argv,log,extra=None):
  environment=dict(env);environment.update(extra or {})
  with Path(log).open('x')as stream:
   child=subprocess.Popen(argv,stdout=stream,stderr=subprocess.STDOUT,env=environment,start_new_session=True);started=time.monotonic()
   try:
    while child.poll()is None:
     check();need(time.monotonic()-started<=60,'bounded fixture compiler/child');need(Path(log).stat().st_size<=8*1024*1024,'fixture log budget');time.sleep(.05)
   finally:
    if child.poll()is None:
     os.killpg(child.pid,signal.SIGTERM)
     try:child.wait(timeout=3)
     except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait()
  return child.returncode
 # Test executable adds its authored main only. The production host object and
 # all translator/emitter implementations are exactly the coherent build's DSO.
 template=next(u for u in plan['native_units']if u['name']=='module');compile=template['command'][:];compile[-1]=str(D/'src/test_game_companion_fixture.cpp');compile[compile.index('-o')+1]=str(out/'fixture.o');need(command(compile,out/'compile.log')==0,'fixture main compile failed')
 link=result['binaries']['module']['link'][:];old=template['object'];need(link.count(old)==1,'one authored fixture main');link[link.index(old)]=str(out/'fixture.o');link[link.index('-o')+1]=str(out/'fixture');need(command(link,out/'link.log')==0,'fixture same-DSO link failed');expected={Path(edge).name:edge for edge in result['binaries']['module']['dependencies']}
 def deps(path):return [line.strip().split(' (compatibility')[0]for line in subprocess.check_output(['/usr/bin/otool','-L',str(path)],text=True).splitlines()[1:]]
 for n,edge in enumerate(deps(out/'fixture')):
  target=str(library)if Path(edge).name.startswith('libbbgpu')else expected.get(Path(edge).name)
  if target and target!=edge:need(command(['/usr/bin/install_name_tool','-change',edge,target,str(out/'fixture')],out/f'edge-{n}.log')==0,'fixture dependency normalization')
 need(command(['/usr/bin/codesign','--force','--sign','-',str(out/'fixture')],out/'codesign.log')==0,'fixture sign')
 from warm_archive_manifest import resolve_gpu_identity
 identity=resolve_gpu_identity(module,project/'evidence');fixture_identity=resolve_gpu_identity(out/'fixture',project/'evidence');need(identity['library_sha256']==fixture_identity['library_sha256']==result['library_sha256'],'actual fixture/consumer share DSO')
 sys.path.insert(0,str(D/'tests'));from test_fetch_compile_producer import fixture
 from prepare_fetch_capture_receipt import configure_capture
 authored=fixture(state,library)
 # The codec test's two-word sentinel ends in a literal, not S_ENDPGM.
 # This execution test uses a complete three-word authored shader.
 code_sha=hashlib.sha256(struct.pack('<3I',0xBEEB03FF,0,0xBF810000)).hexdigest()
 cat=json.loads(authored['catalog'].read_text());record=cat['packages'][0]['entries'][0]['binary_info'][0];record.update(code_sha256=code_sha,code_bytes=12);authored['catalog'].write_text(json.dumps(cat))
 idx=json.loads(authored['index'].read_text());idx['identity']['code_sha256']=code_sha;idx['variants'][0]['code_bytes']=12;authored['index'].write_text(json.dumps(idx)+'\n')
 configure_capture(root=capture,runtime_source_root=Path(plan['snapshot'])/'vendor/metal_api/shadps4',**authored)
 fixture_out=state/'fixture';fixture_out.mkdir(mode=0o700);need(command([str(out/'fixture'),str(capture),str(authored['game_root']),str(library),str(fixture_out)],out/'fixture.log')==0,'actual authored GCN compilation/publication failed')
 component=dict(module_consumer=module,module_consumer_sha256=result['module_consumer_sha256'],library=str(library),library_sha256=result['library_sha256']);cap=_capability(component,gpu,require_companion=True);profile=dict(path=str(fixture_out/'profile.bin'),sha256=file_sha(fixture_out/'profile.bin'));cases=[]
 def packet_case(name,kind='owned-fetch-companion-module-input'):
  case=auto/name;case.mkdir(mode=0o700);p=_packet(kind=kind,case=case,profile_ref=profile,capture_root=capture,game_root=authored['game_root'],component=component,nonce=nonce,cohort=cohort);return case,p
 inv,p=packet_case('inventory','owned-fetch-companion-inventory');path=inv/'input/packet.json';_automatic_packet(p,path,nonce,cohort);inventory=run_module(component,path,inv/'output',inv,gpu,nonce,cohort,check,identity);need(inventory['accepted']==1,'one actual authored compound');facts=json.loads((inv/'output/inventory.jsonl').read_text());cases.append(dict(name='native-inventory',status='passed'))
 case,p=packet_case('positive');p.update(companion_key=facts['companion_key'],expected=facts,target_specs=[]);spec=(fixture_out/'target-spec.bin').read_bytes();from prepare_game_psos import new_bytes;p['target_specs']=[new_bytes(case/'input','target-spec.bin',spec)];path=case/'input/packet.json';_automatic_packet(p,path,nonce,cohort);proof=run_module(component,path,case/'output',case,gpu,nonce,cohort,check,identity);cases.append(dict(name='actual-owned-GCN-SPIRV-byte-equality',status='passed',program_base_from_owned_snapshot=proof['program_base_from_owned_snapshot']))
 def negative(name,mutate,receipt_mutate=None):
  case,p=packet_case(name);p.update(companion_key=facts['companion_key'],expected=copy.deepcopy(facts),target_specs=[]);mutate(p,case);path=case/'input/packet.json';_automatic_packet(p,path,nonce,cohort);output=case/'output';output.mkdir(mode=0o700);receipt=capture/'fetch-producer.receipt';original=receipt.read_bytes()
  try:
   if receipt_mutate:receipt.write_bytes(receipt_mutate(original))
   code=command([str(module),str(path),str(output)],case/'native.log',dict(BB_MODULE_PREPARATION_NONCE=nonce,BB_MODULE_PREPARATION_COHORT=cohort,DYLD_PRINT_LIBRARIES='1'))
  finally:
   if receipt_mutate:receipt.write_bytes(original)
  from run_game_module_contracts import result_record
  text=(case/'native.log').read_text();r=result_record(text);need(code==65 and r.get('status')=='rejected'and not list(output.iterdir()),'clean typed native refusal');need(all(r.get(k)is False for k in('gpu_started','native_info_srt_deserialized','old_walker_executed','guest_memory_read')),'negative never performed forbidden action');cases.append(dict(name=name,status='passed',reason=r.get('error')))
 negative('wrong-real-permutation',lambda p,c:p['expected'].__setitem__('permutation_hash','f'*16))
 negative('wrong-input-integrity',lambda p,c:p['expected'].__setitem__('input_integrity_sha256','f'*64))
 negative('wrong-reference-SPIRV',lambda p,c:p['expected'].__setitem__('observed_spirv_sha256','f'*64))
 def forbidden_tail(p,case):
  from prepare_game_psos import new_bytes
  p['target_specs']=[new_bytes(case/'input','forbidden-tail.bin',(fixture_out/'target-spec.bin').read_bytes()+b'\0')]
 negative('masked-error-clean-spec-tail-rejection',forbidden_tail)
 def profile_change(p,case):
  ref=p['profile'];path=Path(ref['path']);b=bytearray(path.read_bytes());b[16]^=1;path.write_bytes(b);ref['sha256']=file_sha(path)
 negative('wrong-current-profile',profile_change)
 negative('wrong-source-compiler-receipt',lambda p,c:None,lambda b:b[:48]+bytes([b[48]^1])+b[49:])
 negative('wrong-code-registry-receipt',lambda p,c:None,lambda b:b[:172]+bytes([b[172]^1])+b[173:])
 def symlink(p,case):
  alias=auto/'capture-link';alias.symlink_to(capture,target_is_directory=True);p['companion_root']=str(alias)
 negative('linked-companion-root',symlink)
 report=dict(schema=1,status='passed',cases=cases,coherent_result_sha256=file_sha(build_result),library_sha256=result['library_sha256'],consumer_sha256=result['module_consumer_sha256'],native_repair_sha256=file_sha(repair)if repair is not None else None,actual_tiny_authored_GCN_retranslated=True,actual_game_coverage_proven=False,authored_fixture_source_sha256=file_sha(D/'src/test_game_companion_fixture.cpp'),unknown_program_base_proof_used=False,capability=cap,game_started=False,GPU_started=False,formal_data_modified=False,existing_cache_modified=False)
 (out/'result.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n');return report

def main():
 p=argparse.ArgumentParser(description=__doc__)
 for name in('project','build-result','output'):p.add_argument('--'+name,type=Path,required=True)
 p.add_argument('--repair',type=Path);a=p.parse_args();from qa_build_guard import require_no_games
 from native_observer import NativeObserver
 from qa_memory_policy import MemoryPolicy
 require_no_games();observer=NativeObserver();before=observer.system();need(before['pressure']!=4 and not before['locked'],'safe typed CPU controls');policy=MemoryPolicy(before);started=time.monotonic()
 def check():
  reason=policy.observe(time.monotonic()-started,observer.system())
  if reason:raise RuntimeError(reason)
 try:r=run(project=a.project,build_result=a.build_result,output=a.output,repair=a.repair,check=check)
 finally:observer.close()
 print(json.dumps(dict(status=r['status'],cases=len(r['cases']),result=str(a.output/'result.json'))))
if __name__=='__main__':main()
