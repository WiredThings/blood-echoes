"""Independent, exact GS plan -> owned CPU receipts -> schema2 PSO packets.

Never enables the ordinary GS gate, runs a GPU worker, or imports old Info/SRT.
Only explicit run-cpu executes anything, using the existing owned module consumer.
"""
from pathlib import Path
import argparse,collections,hashlib,json,os,re,signal,subprocess,time
from complete_pipeline_inputs import directory,file_sha,source_profile_key
from game_pso_candidates import cache_records
from game_pso_owned_audit import checked_bytes,load_canonical_layout
from game_pso_progress_identity import input_identity,layout_abi_identity
from game_module_inputs import freeze_module,need
from owned_geometry_inputs import freeze_geometry
from static_program_links import rows,lookup_table
from prepare_game_psos import new_bytes,dyld_gpu_image,verify_loaded_gpu,wait_owned_worker

HEX=re.compile('[0-9a-f]{64}')
MAX=100000
def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def load(path,limit=16*1024*1024):return json.loads(checked_bytes(Path(path),limit=limit)[0])
def output_root(project,path):
 p=Path(path).absolute();need(not p.exists()and p.is_relative_to(project/'evidence'),'fresh Shared evidence output required');directory(p.parent);p.mkdir(mode=0o700);return directory(p)
def require_cpu_state(project,state):
 root=directory(Path(project)/'state');state=directory(Path(state))
 need(state!=root and state.is_relative_to(root),'CPU consumer GPU state must stay below isolated state root');return state
def cpu_state_root(project):
 root=directory(Path(project)/'state');state=root/('geometry-module-consumer-'+str(time.time_ns()));state.mkdir(mode=0o700);return require_cpu_state(project,state)
def records(path):
 with Path(path).open()as stream:
  for n,line in enumerate(stream):
   need(n<MAX and len(line)<=256*1024,'bounded NDJSON input');yield json.loads(line)
def save(path,row):
 raw=json.dumps(row,sort_keys=True,indent=2).encode()+b'\n';need(len(raw)<=64*1024*1024,'bounded plan/report');new_bytes(path.parent,path.name,raw)
def stage_identity(unit,stage):
 """Same cache/profile/full stage, never just program_hash."""
 return digest(dict(cache=unit['namespace']['cache'],profile=unit['profile']['reference'],stage=stage))
def module_identity(unit,stage,abi):
 # Metadata includes exact flat/walker input; GS sanitization is separately proven.
 return digest(dict(profile=unit['profile']['reference']['sha256'],metadata=stage['metadata']['sha256'],
  spirv=stage['spirv']['sha256'],spec=stage['serialized_spec_sha256'],logical=stage['logical_stage'],
  hardware=stage['hardware_stage'],program=stage['program_hash'],permutation=stage['cache_local_permutation'],
  permutation_hash=stage['permutation_hash'],serialization_abi=abi))
def require_geometry(unit,layout):
 input_identity(unit);cache=Path(unit['namespace']['cache']);stages=unit['stages']
 need(unit['key']['decoded']['kind']=='graphics','GS graphics key required')
 names={s['logical_stage']for s in stages};need(names in ({'vertex','geometry'},{'vertex','geometry','fragment'}),'complete ES/GS/FS set')
 need(len(stages)==len(names),'no duplicate stage');source_profile_key(unit,layout)
 for s in stages:
  need(Path(s['metadata']['path']).parent==cache and Path(s['spirv']['path']).parent==cache,'all stages in one actual cache')
  need((s['logical_stage'],s['hardware_stage'])in(('vertex','export'),('vertex','vertex'),('geometry','geometry'),('fragment','fragment')),'supported GS stage pair')
 need(Path(unit['profile']['reference']['path']).parent==cache,'one actual profile')
 refs=[unit['profile']['reference'],unit['key']['reference']]
 for s in stages:refs.extend((s['metadata'],s['spirv']))
 need(sum(r['bytes']for r in refs)<=16*1024*1024,'bounded complete GS read set')
 canonical=[];seen=set()
 for r in refs:
  path=Path(r['path']);need(path.parent==cache and path.name==r['name']and path not in seen,'one unique actual cache read set');seen.add(path)
  raw,_=checked_bytes(path,expected=r['sha256']);need(len(raw)==r['bytes'],'complete GS input extent');canonical.append({k:r[k]for k in('path','name','bytes','sha256')})
 need(digest(canonical)==unit['execution_policy']['candidate_preparation']['frozen_input_set_identity'],'exact GS source read-set identity')

def plan(*,project,cache_list,gaps,layout,catalog,static_index,output,check=lambda:None,
         cache_reader=cache_records,geometry_freezer=freeze_geometry,module_freezer=freeze_module):
 P=directory(Path(project));out=output_root(P,output);caches=load(cache_list);need(isinstance(caches,list)and 0<len(caches)<=128,'explicit bounded caches')
 allowed={str(directory(Path(c)))for c in caches};wanted=set()
 for row in records(gaps):
  if 'geometry-session-chain-not-admitted'in row.get('reasons',[]):
   need(row['cache']in allowed and HEX.fullmatch(row['recipe_blob_identity']),'GS gap scope');wanted.add((row['cache'],row['recipe_blob_identity']))
 need(0<len(wanted)<=MAX,'explicit GS recipes required');catalog_value=load(catalog,256*1024*1024);table=lookup_table(list(rows(Path(static_index))));abi=layout_abi_identity(layout)
 jobs={};recipes=[];seen=set();errors=[];counts=collections.Counter();module_root=out/'modules';module_root.mkdir();raw_units=out/'recipes.jsonl'
 with raw_units.open('x')as sink:
  for cache in sorted({c for c,r in wanted}):
   check();need(Path(cache).is_relative_to(P/'state'),'only isolated caches');n=0
   for unit in cache_reader(Path(cache),layout,check):
    n+=1;need(n<=MAX,'bounded cache recipe scan');check()
    if unit.get('record_type')!='offline-pso-input':continue
    key=(cache,unit['recipe_blob_identity'])
    if key not in wanted:continue
    need(key not in seen,'duplicate actual recipe');seen.add(key);row=dict(recipe=unit['recipe_blob_identity'],cache=cache,stages=[],status='planned',native_ready=False)
    try:
     require_geometry(unit,layout)
     for stage in unit['stages']:
      mid=module_identity(unit,stage,abi);association=dict(module_id=mid,stage_identity=stage_identity(unit,stage),logical_stage=stage['logical_stage'],hardware_stage=stage['hardware_stage'])
      if mid not in jobs:
       need(len(jobs)<512,'bounded exact module jobs');dest=module_root/mid;found=table.get((stage['program_hash'],stage['hardware_stage']),[]);need(len(found)==1,'unique static code identity')
       if stage['hardware_stage']=='geometry':
        packet=geometry_freezer(metadata_ref=stage['metadata'],spirv_ref=stage['spirv'],profile_ref=unit['profile']['reference'],layout=layout,game_root=catalog_value['game_root'],catalog=catalog_value,table=table,output=dest)
       else:
        evidence=[e for v in found[0]['variants']for e in v['evidence_refs']];need(evidence,'actual static source reference')
        packet=module_freezer(stage=stage,profile_reference=unit['profile']['reference'],layout=layout,game_root=catalog_value['game_root'],catalog=catalog_value,static_identity=found[0]['identity'],evidence=evidence[0],output=dest)
       path=dest/'packet.json';need(path.is_file(),'actual frozen module packet');need(packet['profile']['sha256']==unit['profile']['reference']['sha256']and packet['expected_spirv']['sha256']==stage['spirv']['sha256'],'exact cache/profile/SPV module binding')
       jobs[mid]=dict(module_id=mid,packet=str(path),packet_sha256=file_sha(path),hardware_stage=stage['hardware_stage'],logical_stage=stage['logical_stage'],profile_sha256=packet['profile']['sha256'],metadata_sha256=stage['metadata']['sha256'],expected_spirv_sha256=stage['spirv']['sha256'],uses=[],native_ready=False)
      jobs[mid]['uses'].append(dict(recipe=unit['recipe_blob_identity'],cache=cache,stage_identity=association['stage_identity']));row['stages'].append(association)
     encoded=json.dumps(dict(unit=unit,plan=row),sort_keys=True,separators=(',',':'))+'\n';need(len(encoded)<=256*1024,'bounded exact recipe');sink.write(encoded);counts['planned_recipes']+=1
    except(ValueError,OSError,KeyError)as error:
     row.update(status='rejected',reason=str(error));errors.append(row);counts['rejected_recipes']+=1
    recipes.append(row)
 missing=sorted(wanted-seen);counts['missing_actual_recipes']=len(missing);counts['exact_modules']=len(jobs)
 for j in jobs.values():counts['modules_'+j['hardware_stage']]+=1
 result=dict(schema=1,kind='owned-gcn-geometry-pso-plan',status='planned'if not errors and not missing else'partial',counts=dict(counts),modules=list(jobs.values()),recipes=recipes,rejections=errors,missing_actual_recipes=missing,recipes_path=str(raw_units),recipes_sha256=file_sha(raw_units),serialization_abi_sha256=abi,layout=layout,
  inputs=dict(cache_list_sha256=file_sha(cache_list),gaps_sha256=file_sha(gaps),catalog=str(Path(catalog)),catalog_sha256=file_sha(catalog),static_index_sha256=file_sha(static_index)),native_modules_run=False,native_PSOs_ready=False,old_GS_gate_changed=False,GPU_started=False,game_started=False,formal_data_modified=False)
 save(out/'plan.json',result);return result

def verify_receipt(packet,receipt,packet_sha):
 need(receipt.get('module_retranslation_schema')==1 and type(receipt.get('module_retranslation_schema'))is int,'new exact module receipt required')
 need(receipt.get('status')=='completed'and receipt.get('candidate_only')is True,'successful owned module receipt')
 for k in('runtime_cache_import_allowed','gpu_started','native_info_srt_deserialized','old_walker_executed','guest_memory_read'):need(receipt.get(k)is False,'native receipt forbidden state')
 for k in('expected_spirv_equal','current_generated_srt_verified','unknown_program_base_rejected_before_use'):need(receipt.get(k)is True,'full module equivalence proof')
 for k,v in dict(input_packet_sha256=packet_sha,input_code_sha256=packet['code']['sha256'],input_spec_sha256=packet['spec']['sha256'],input_profile_sha256=packet['profile']['sha256'],spirv_sha256=packet['expected_spirv']['sha256'],program_hash=packet['program_hash'],hardware_stage=packet['hardware_stage'],logical_stage=packet['logical_stage'],source_provenance=packet['source_provenance']).items():need(receipt.get(k)==v,'receipt exact binding: '+k)
 if packet['hardware_stage']=='geometry':
  need(receipt.get('owned_geometry_copy_rebound')is True and type(receipt.get('geometry_copy_bytes'))is int and receipt.get('geometry_copy_bytes')==packet['gs_copy']['code']['bytes']and receipt.get('gs_copy_sha256')==packet['gs_copy']['code']['sha256']and receipt.get('gs_copy_source_provenance')==packet['gs_copy']['source_provenance'],'owned copy receipt binding')

def verify_stage_binding(unit,s,m):
 need((m['program_hash'],m['hardware_stage'],m['logical_stage'],m['profile']['sha256'],m['expected_spirv']['sha256'])==(s['program_hash'],s['hardware_stage'],s['logical_stage'],unit['profile']['reference']['sha256'],s['spirv']['sha256']),'one actual cache/profile/stage module association')
 need(m['source_metadata']['sha256']==s['metadata']['sha256'],'one exact original stage metadata')
 if s['hardware_stage']=='geometry':need(m['spec_rebinding']['original_spec_sha256']==s['serialized_spec_sha256'],'one exact original GS specialization')
 else:need(m['spec']['sha256']==s['serialized_spec_sha256'],'one exact ordinary specialization')

def require_plan(p):
 need(p.get('schema')==1 and p.get('kind')=='owned-gcn-geometry-pso-plan','independent GS plan schema required')
 need(p.get('GPU_started')is False and p.get('game_started')is False and p.get('native_PSOs_ready')is False and p.get('old_GS_gate_changed')is False,'GS plan is never native readiness')
 jobs=p.get('modules');need(isinstance(jobs,list)and 0<len(jobs)<=512,'bounded nonempty module plan')
 ids=set();paths=set()
 for j in jobs:
  need(HEX.fullmatch(j['module_id'])and j['module_id']not in ids and j['packet']not in paths,'unique exact module identities');ids.add(j['module_id']);paths.add(j['packet'])
  need(HEX.fullmatch(j['packet_sha256'])and j['native_ready']is False,'unexecuted module packet identity')

def run_cpu(*,project,plan_path,build_path,output,check=lambda:None):
 """Explicit later phase. No GPU; actual PID/library load admission per child."""
 from run_game_module_contracts import canonical_packet,validate_call,result_record
 from run_owned_geometry_contracts import canonical_geometry_packet
 P=directory(Path(project));out=output_root(P,output);p=load(plan_path,64*1024*1024);require_plan(p);build=load(build_path);need(build.get('diagnostic_only')is False,'ordinary non-diagnostic CPU consumer required')
 binary=Path(build['binary']);library=Path(build['pinned_library']);checked_bytes(binary,limit=64*1024*1024,expected=build['binary_sha256']);checked_bytes(library,limit=256*1024*1024,expected=build['pinned_library_sha256']);catalog=load(Path(p['inputs']['catalog']),256*1024*1024);need(file_sha(p['inputs']['catalog'])==p['inputs']['catalog_sha256'],'catalog changed')
 cases=[];state=cpu_state_root(P)
 for n,job in enumerate(p['modules']):
  check();need(file_sha(job['packet'])==job['packet_sha256'],'module packet changed');packet=(canonical_geometry_packet if job['hardware_stage']=='geometry'else canonical_packet)(Path(job['packet']),catalog)
  case=out/f'{n:03d}-{job["module_id"]}';case.mkdir();dest=case/'output';dest.mkdir();tmp=case/'tmp';tmp.mkdir();child_state=state/case.name;child_state.mkdir();require_cpu_state(P,child_state);log=case/'console.log';env={k:v for k,v in os.environ.items()if not k.startswith(('BB_','DYLD_'))};env.update(BB_GPU_USER_DIR=str(child_state),HOME=str(case),TMPDIR=str(tmp),DYLD_PRINT_LIBRARIES='1')
  began=time.monotonic()
  with log.open('w')as stream:
   child=subprocess.Popen([str(binary),job['packet'],str(dest)],env=env,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
   wait_owned_worker(child,log,check,time.monotonic,began,30)
  text=log.read_text();need(len(text)<=8*1024*1024,'bounded CPU log');r=result_record(text);images={value for line in text.splitlines()if(value:=dyld_gpu_image(line,child.pid))}
  admitted=verify_loaded_gpu(images,dict(library=str(library),library_sha256=build['pinned_library_sha256']))
  try:
   validate_call(child.returncode,r,dest,packet);verify_receipt(packet,r,job['packet_sha256']);status='passed'
  except ValueError:status='rejected'
  need(file_sha(binary)==build['binary_sha256'],'CPU worker changed');cases.append(dict(module_id=job['module_id'],status=status,exit=child.returncode,output=str(dest),packet_sha256=job['packet_sha256'],receipt_sha256=file_sha(dest/'result.json')if(dest/'result.json').is_file()else None,actual_loaded_compiler=admitted,binary_sha256=build['binary_sha256']))
 report=dict(schema=1,kind='geometry-owned-module-results',plan_sha256=file_sha(plan_path),build=str(Path(build_path)),build_sha256=file_sha(build_path),state_root=str(state),cases=cases,status='passed'if all(c['status']=='passed'for c in cases)else'partial',GPU_started=False,game_started=False,native_PSOs_ready=False)
 save(out/'result.json',report);return report

def freeze(*,project,plan_path,results_path,cohort,gpu_root,output,check=lambda:None):
 P=directory(Path(project));out=output_root(P,output);p=load(plan_path,64*1024*1024);require_plan(p);r=load(results_path,64*1024*1024);need(r['kind']=='geometry-owned-module-results'and r['plan_sha256']==file_sha(plan_path),'actual CPU result/plan binding')
 build=load(Path(r['build']));need(file_sha(r['build'])==r['build_sha256']and build.get('diagnostic_only')is False,'actual current CPU build');checked_bytes(Path(build['binary']),limit=64*1024*1024,expected=build['binary_sha256']);checked_bytes(Path(build['pinned_library']),limit=256*1024*1024,expected=build['pinned_library_sha256'])
 need(HEX.fullmatch(cohort),'verified actual PSO cohort required');gpu=directory(Path(gpu_root));need(gpu.is_relative_to(P/'state')and gpu!=P/'state','isolated actual GPU root');artifact=gpu/'cache/CUSA03173/metal';artifact.mkdir(parents=True,exist_ok=True);directory(artifact)
 need(file_sha(p['recipes_path'])==p['recipes_sha256'],'recipe plan changed');jobs={j['module_id']:j for j in p['modules']};ready={}
 seen_cases=set()
 for case in r['cases']:
  check();need(case['module_id']in jobs and case['module_id']not in seen_cases,'unique planned module result');seen_cases.add(case['module_id'])
  if case['status']!='passed':continue
  j=jobs[case['module_id']];need(case['exit']==0 and case['packet_sha256']==j['packet_sha256']and file_sha(j['packet'])==j['packet_sha256'],'module exact input/exit');packet=load(Path(j['packet']));receipt=Path(case['output'])/'result.json';need(file_sha(receipt)==case['receipt_sha256'],'actual receipt changed');proof=load(receipt);verify_receipt(packet,proof,j['packet_sha256']);need(case['binary_sha256']==build['binary_sha256']and case['actual_loaded_compiler']['library_sha256']==build['pinned_library_sha256']and case['actual_loaded_compiler']['owned_post_worker_sha_verified']is True,'actual CPU producer compatibility')
  blob,_=checked_bytes(Path(case['output'])/'module.spv',expected=packet['expected_spirv']['sha256']);need(len(blob)==packet['expected_spirv']['bytes'],'actual emitted SPV');ready[j['module_id']]=(j,packet,receipt,blob)
 packets=[];rejections=[]
 for row in records(p['recipes_path']):
  check();unit=row['unit'];assoc=row['plan'];missing=[s['module_id']for s in assoc['stages']if s['module_id']not in ready]
  if missing:rejections.append(dict(recipe=assoc['recipe'],reason='CPU module not ready',modules=missing));continue
  require_geometry(unit,p['layout']);need(len(unit['stages'])==len(assoc['stages']),'complete stage association');dest=out/f'pso-{len(packets):03d}';dest.mkdir();key,_=checked_bytes(Path(unit['key']['reference']['path']),expected=unit['key']['reference']['sha256']);profile,_=checked_bytes(Path(unit['profile']['reference']['path']),expected=unit['profile']['reference']['sha256']);stages=[]
  for i,(s,a)in enumerate(zip(unit['stages'],assoc['stages'])):
   need(stage_identity(unit,s)==a['stage_identity'],'exact recipe stage changed');j,m,receipt,spv=ready[a['module_id']];verify_stage_binding(unit,s,m)
   spec,_=checked_bytes(Path(m['spec']['path']),expected=m['spec']['sha256']);raw_receipt,_=checked_bytes(receipt,expected=file_sha(receipt));sr=new_bytes(dest,f'spec-{i}.bin',spec);sr.update(source_offset=24,native_spec_only_reader_allowed=True)
   item=dict(logical_stage=s['logical_stage'],hardware_stage=s['hardware_stage'],program_hash=s['program_hash'],permutation=s['cache_local_permutation'],permutation_hash=s['permutation_hash'],spec=sr,spirv=new_bytes(dest,f'spv-{i}.bin',spv),module_input_packet_sha256=j['packet_sha256'],retranslation_receipt=new_bytes(dest,f'receipt-{i}.json',raw_receipt),source_provenance=m['source_provenance'])
   if s['hardware_stage']=='geometry':
    rb=m['spec_rebinding'];at=rb['runtime_span_offset'];need(rb['bytes']==16 and rb['serialized_span_zeroed']is True and not any(spec[at:at+16])and len(spec[at:at+16])==16,'actual sanitized GS span');copy,_=checked_bytes(Path(m['gs_copy']['code']['path']),expected=m['gs_copy']['code']['sha256']);item.update(spec_rebinding=rb,gs_copy=dict(m['gs_copy'],code=new_bytes(dest,f'copy-{i}.bin',copy)))
   stages.append(item)
  packet=dict(packet_schema=2,manifest_schema=2,geometry_pso_schema=1,kind='owned-gcn-geometry-pso-input',candidate_only=True,runtime_cache_import_allowed=False,native_info_srt_allowed=False,walker_bytes_passed=0,cache_id=unit['namespace']['cache_id'],recipe_blob_identity=unit['recipe_blob_identity'],frozen_input_set_identity=unit['execution_policy']['candidate_preparation']['frozen_input_set_identity'],original_cache=unit['namespace']['cache'],current_cohort=cohort,artifact_cache_dir=str(artifact),allow_compile=True,key=new_bytes(dest,'key.bin',key),retranslation_profile=new_bytes(dest,'profile.bin',profile),layout=dict(bindings_size=p['layout']['Backend::Bindings.size'],runtime_size=p['layout']['RuntimeInfo.size'],graphics_key_size=p['layout']['GraphicsPipelineKey.size']),stages=stages)
  save(dest/'packet.json',packet);packets.append(dict(packet=str(dest/'packet.json'),packet_sha256=file_sha(dest/'packet.json'),recipe=assoc['recipe'],native_ready=False))
 report=dict(schema=1,status='frozen'if not rejections else'partial',packets=packets,rejections=rejections,plan_sha256=file_sha(plan_path),CPU_results_sha256=file_sha(results_path),GS_packets=len(packets),native_PSOs_ready=False,old_GS_gate_changed=False,GPU_started=False,game_started=False,formal_data_modified=False);save(out/'manifest.json',report);return report

def main():
 ap=argparse.ArgumentParser(description=__doc__);sub=ap.add_subparsers(dest='phase',required=True)
 p=sub.add_parser('plan')
 for name in('project','cache-list','gaps','layout','layout-source','catalog','static-index','output'):p.add_argument('--'+name,type=Path,required=True)
 for phase in('run-cpu','freeze'):
  p=sub.add_parser(phase)
  for name in('project','plan','output'):p.add_argument('--'+name,type=Path,required=True)
  if phase=='run-cpu':p.add_argument('--build',type=Path,required=True)
  else:
   for name in('results','gpu-root'):p.add_argument('--'+name,type=Path,required=True)
   p.add_argument('--cohort',required=True)
 a=ap.parse_args();from native_observer import NativeObserver
 from qa_memory_policy import MemoryPolicy
 from qa_build_guard import require_no_games
 require_no_games();observer=NativeObserver();before=observer.system();need(before['pressure']!=4 and not before['locked'],'safe CPU planning entry');policy=MemoryPolicy(before);start=time.monotonic()
 def check():
  why=policy.observe(time.monotonic()-start,observer.system())
  if why:raise RuntimeError(why)
 try:
  if a.phase=='plan':
   layout=load_canonical_layout(a.layout,a.layout_source,file_sha(a.layout));r=plan(project=a.project,cache_list=a.cache_list,gaps=a.gaps,layout=layout,catalog=a.catalog,static_index=a.static_index,output=a.output,check=check)
  elif a.phase=='run-cpu':r=run_cpu(project=a.project,plan_path=a.plan,build_path=a.build,output=a.output,check=check)
  else:r=freeze(project=a.project,plan_path=a.plan,results_path=a.results,cohort=a.cohort,gpu_root=a.gpu_root,output=a.output,check=check)
  check()
 finally:observer.close()
 print(json.dumps({k:v for k,v in r.items()if k in('status','counts','GS_packets','native_PSOs_ready','GPU_started')},indent=2))
if __name__=='__main__':main()
