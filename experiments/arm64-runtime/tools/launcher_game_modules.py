"""Bounded automatic module phase for the existing launcher preparation flow.

This controller consumes only the selected launch cache and the immutable static
catalogue. It never treats a module result as a final PSO or as permission for a
runtime-cache import. Unknown inputs remain explicit gaps for normal capture.
"""
from pathlib import Path
import collections,hashlib,json,os,secrets,signal,stat,struct,subprocess,time
from complete_pipeline_inputs import digest,directory,file_sha
from game_module_inputs import need,freeze_module,module_constraints
from game_pso_candidates import cache_records
from game_pso_owned_audit import checked_bytes,metadata_sections
from game_pso_progress_identity import layout_abi_identity
from static_program_links import rows,lookup_table
from fetch_projection_inputs import project,freeze_projection,FIELDS
from owned_geometry_inputs import freeze_geometry
from run_game_module_contracts import canonical_packet,validate_call,result_record
from prepare_game_psos import dyld_gpu_image,verify_loaded_gpu

ROOT_POLICY='selected-owned-gpu/automatic-game-inputs-v1'
MAX_RECORDS=100000
class NativeModuleRejected(ValueError):
 """A clean refusal; only immutable translation differences are persistent."""
 @property
 def deterministic(self):
  return str(self)in('owned companion runtime-after differs','owned companion complete canonical specialization differs','owned companion regenerated SPIR-V differs','actual cache target complete canonical specialization differs','retranslated specialization differs from captured state','retranslated SPIR-V differs from observed module')

def _ref_identity(ref):return dict(sha256=ref['sha256'],bytes=ref['bytes'])
def module_input_identity(unit,stage,static_identity,layout,game_identity,projection_proof=None):
 """Entire observed source identity; deliberately different from runtime perm_hash."""
 return digest(dict(schema=1,recipe='automatic-owned-module-v1',game=game_identity,
  static_identity=static_identity,profile=_ref_identity(unit['profile']['reference']),
  metadata=_ref_identity(stage['metadata']),observed_spirv=_ref_identity(stage['spirv']),
  fetch_key_proof=projection_proof,logical_stage=stage['logical_stage'],
  permutation_hash=stage['permutation_hash'],abi=layout_abi_identity(layout)))

def stage_input_identity(unit,stage,static_identity,layout,game_identity):
 raw,_=checked_bytes(Path(stage['metadata']['path']),expected=stage['metadata']['sha256']);meta,_=metadata_sections(raw,layout)
 proof=None
 if meta['fetch']is not None:
  row=projection_row(unit,stage,meta,static_identity);proof=dict(key=_ref_identity(unit['key']['reference']),projection=project(row))
 return module_input_identity(unit,stage,static_identity,layout,game_identity,proof)

def projection_row(unit,stage,meta,static_identity):
 """Reconstitute facts directly from canonical current bytes, not a CLI sidecar.

 One exact original PSO key is a sufficient conversion proof for that input.
 Other keys produce separate full input identities; none is guessed or merged.
 """
 fetch=meta['fetch'];need(fetch is not None and stage['logical_stage']=='vertex','vertex fetch only')
 need(len(fetch['attributes'])==len(meta['vertex_attributes']),'fetch/spec shape')
 attrs=[]
 for a,v in zip(fetch['attributes'],meta['vertex_attributes']):
  raw=bytes.fromhex(a['raw_hex']);need(len(raw)==9,'canonical nine fetch bytes')
  attrs.append(dict(fields=dict(zip(FIELDS,raw)),vertex_specialization=v))
 key=unit['key'];decoded=key['decoded'];need(decoded['kind']=='graphics'and decoded['stages']['vertex']==stage['permutation_hash'],'actual key vertex permutation')
 return dict(schema=1,static_identity=static_identity,observed=dict(metadata=stage['metadata'],permutation=stage['cache_local_permutation'],permutation_hash=stage['permutation_hash'],serialized_spec_sha256=stage['serialized_spec_sha256']),fetch=dict(code_size=fetch['code_size'],vertex_sgpr_offset=fetch['vertex_sgpr_offset'],instance_sgpr_offset=fetch['instance_sgpr_offset'],attributes=attrs),step_rates=meta.get('context',{}).get('step_rates'),pipeline_key_vertex_inputs=[dict(source_reference=key['reference'],raw_key_sha256=decoded['raw_key_sha256'],vertex_formats=decoded['vertex_formats'],vertex_attributes=decoded['vertex_attributes'])])

def freeze_stage(unit,stage,*,layout,catalog,table,output):
 matches=table.get((stage['program_hash'],stage['hardware_stage']),[])
 need(len(matches)==1,'unique native-stage static code source required');static=matches[0]
 raw,_=checked_bytes(Path(stage['metadata']['path']),expected=stage['metadata']['sha256']);meta,_=metadata_sections(raw,layout)
 if meta['stage']=='geometry':
  return freeze_geometry(metadata_ref=stage['metadata'],spirv_ref=stage['spirv'],profile_ref=unit['profile']['reference'],layout=layout,game_root=catalog['game_root'],catalog=catalog,table=table,output=output)
 if meta['fetch']is not None:
  row=projection_row(unit,stage,meta,static['identity']);project(row)
  return freeze_projection(row,layout=layout,catalog=catalog,table=table,output=output)
 reasons=module_constraints(meta);need(not reasons,';'.join(reasons))
 refs=[r for variant in static['variants']for r in variant['evidence_refs']];need(refs,'independent static source reference')
 return freeze_module(stage=stage,profile_reference=unit['profile']['reference'],layout=layout,game_root=catalog['game_root'],catalog=catalog,static_identity=static['identity'],evidence=refs[0],output=output)

def atomic_json(path,value):
 temp=path.with_name(path.name+'.'+secrets.token_hex(8)+'.tmp')
 with temp.open('x')as stream:
  json.dump(value,stream,sort_keys=True,separators=(',',':'));stream.write('\n');stream.flush();os.fsync(stream.fileno())
 os.replace(temp,path)

def _automatic_packet(packet,path,nonce,cohort):
 packet['automatic_preparation']=dict(schema=1,nonce=nonce,cohort=cohort)
 atomic_json(path,packet)

def _capability(component,gpu,*,runner=None,require_companion=False):
 from game_pso_component import _run_bounded_capability
 env={k:v for k,v in os.environ.items()if not k.startswith(('BB_','DYLD_'))};env['BB_GPU_USER_DIR']=str(gpu)
 result=(runner or _run_bounded_capability)([str(component['module_consumer']),'--capability'],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=10,env=env)
 need(result.returncode==0,'module capability query failed')
 lines=result.stdout.decode().splitlines();need(len(lines)==1 and lines[0].startswith('GAME_MODULE_CAPABILITY '),'actual module capability envelope')
 cap=json.loads(lines[0].split(' ',1)[1]);need(cap.get('schema')==1 and cap.get('cpu_only')is True and cap.get('automatic_input_root')==ROOT_POLICY and cap.get('requires_nonce_and_cohort')is True and cap.get('diagnostic_only')is False and cap.get('packet_schema')==1,'actual automatic module capability required')
 if require_companion:need(cap.get('companion_contract_schema')==1,'actual typed companion consumer capability required')
 return cap

def coherent_component(component,*,gpu,selected_renderer,backend_root,resolver=None,capability_runner=None):
 """Verify actual signed ARM load edges and bytes, not an asserted ready flag."""
 need(component.get('coherent_renderer')is True and component.get('diagnostic_only')is False,'coherent non-diagnostic module component required')
 from warm_archive_manifest import resolve_gpu_identity
 resolver=resolver or resolve_gpu_identity
 consumer=Path(component['module_consumer']);raw,ref=checked_bytes(consumer,limit=64*1024*1024,expected=component['module_consumer_sha256'])
 original=resolver(Path(selected_renderer),Path(backend_root));actual=resolver(consumer,Path(backend_root))
 need(actual['library_sha256']==original['library_sha256']==component['library_sha256']and actual['library']==original['library'],'consumer and selected renderer require identical actual DSO')
 _capability(component,gpu,runner=capability_runner,require_companion=True)
 return actual

def run_module(component,packet,output,case,gpu,nonce,cohort,check,identity):
 """One CPU child; selected GPU root and PID-bound actual DSO are verified."""
 output.mkdir(mode=0o700);temp=case/'tmp';temp.mkdir(mode=0o700)
 env={k:v for k,v in os.environ.items()if not k.startswith(('BB_','DYLD_'))};env.update(BB_GPU_USER_DIR=str(gpu),BB_MODULE_PREPARATION_NONCE=nonce,BB_MODULE_PREPARATION_COHORT=cohort,TMPDIR=str(temp),DYLD_PRINT_LIBRARIES='1')
 out=case/'stdout.log';err=case/'stderr.log';start=time.monotonic()
 with out.open('x')as stdout,err.open('x')as stderr:
  proc=subprocess.Popen([str(component['module_consumer']),str(packet),str(output)],stdout=stdout,stderr=stderr,env=env,start_new_session=True)
  try:
   while proc.poll()is None:
    check();need(time.monotonic()-start<=30,'module compiler30s budget');need(out.stat().st_size+err.stat().st_size<=8*1024*1024,'module output budget');time.sleep(.05)
  finally:
   if proc.poll()is None:
    os.killpg(proc.pid,signal.SIGTERM)
    try:proc.wait(timeout=3)
    except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
 loaded=set()
 with err.open()as lines:
  for line in lines:
   need(len(line)<=65536,'bounded native load record');image=dyld_gpu_image(line,proc.pid)
   if image is not None:loaded.add(image)
 need(out.stat().st_size+err.stat().st_size<=8*1024*1024,'module output budget')
 verify_loaded_gpu(loaded,identity)
 result=result_record(out.read_text());p=json.loads(checked_bytes(packet,limit=256*1024)[0])
 if proc.returncode==65 and result.get('status')=='rejected':
  need(result.get('candidate_only')is True and all(result.get(k)is False for k in('runtime_cache_import_allowed','gpu_started','native_info_srt_deserialized','old_walker_executed','guest_memory_read'))and not list(output.iterdir()),'clean native refusal without partial outputs')
  raise NativeModuleRejected(result.get('error','native input rejected'))
 if p['kind'].startswith('owned-fetch-companion-'):
  from game_companion_modules import validate_call as validate_typed_companion
  validate_typed_companion(proc.returncode,result,output,p)
 else:validate_call(proc.returncode,result,output,p)
 checked_bytes(Path(component['module_consumer']),limit=64*1024*1024,expected=component['module_consumer_sha256'])
 return result

def selected_units(caches,*,gpu,state_root,title,layout,check,candidate_manifest=None):
 """Reuse the original candidate builder's snapshot when one is supplied."""
 allowed=set()
 for rawcache in caches:
  cache=directory(Path(rawcache));need(cache.is_relative_to(gpu/'cache')and cache.name.startswith(title+'-metal-'),'only selected original launch cache');allowed.add(str(cache))
 if candidate_manifest is None:
  for cache in sorted(allowed):yield from cache_records(Path(cache),layout,check)
  return
 reference=candidate_manifest if isinstance(candidate_manifest,dict)else dict(path=str(candidate_manifest))
 path=Path(reference['path']);need(path.is_absolute()and path==path.resolve(strict=True)and path.is_relative_to(state_root),'owned original preparation manifest')
 fd=os.open(path,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
 with os.fdopen(fd,'rb')as stream:
  before=os.fstat(stream.fileno());need(stat.S_ISREG(before.st_mode)and before.st_uid==os.getuid()and before.st_nlink==1 and before.st_size<=256*1024*1024,'bounded owned original candidate snapshot');sha=hashlib.sha256();n=0
  # Authenticate the complete original manifest before publishing any child
  # result. This is a streaming JSONL byte pass, not another cache inventory.
  initial=hashlib.sha256();total=0
  while block:=stream.read(1024*1024):
   check();total+=len(block);need(total<=256*1024*1024,'candidate snapshot byte bound');initial.update(block)
  if 'sha256'in reference:need(initial.hexdigest()==reference['sha256'],'original candidate snapshot SHA')
  stream.seek(0)
  while True:
   check();raw=stream.readline(256*1024+1)
   if not raw:break
   n+=1;need(n<=MAX_RECORDS and len(raw)<=256*1024 and raw.endswith(b'\n'),'bounded original candidate rows');sha.update(raw);unit=json.loads(raw);need(unit.get('namespace',{}).get('cache')in allowed,'candidate from selected original cache only');yield unit
  after=os.fstat(stream.fileno());need((before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)==(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns),'original candidate snapshot changed')
  if 'sha256'in reference:need(sha.hexdigest()==reference['sha256'],'original candidate snapshot SHA')

def prepare(static_context,*,project,caches,component,cohort,check=lambda:None,environment=None,
            selected_renderer=None,backend_root=None,page_size=16,seconds=600,
            native_runner=run_module,identity_resolver=None,capability_runner=None,candidate_manifest=None):
 """Original-launcher module preparation entry. No alternate frontend/CLI batch.

 All selected records are visited, exact states are indexed, and native misses
 are processed in consecutive bounded pages in this launch until the total
 budget/guard stops work. The same stable index resumes on the next launch.
 Return GS resolver feeds the existing original PSO queue, never a second queue.
 """
 if not component:return dict(status='unavailable',reason='coherent CPU module component unavailable',geometry_resolver=None)
 if not component.get('coherent_renderer')or component.get('diagnostic_only')is not False:return dict(status='unavailable',reason='targeted or diagnostic consumer cannot prepare runtime inputs',geometry_resolver=None)
 environment=environment or {};gpu=directory(Path(environment.get('BB_GPU_USER_DIR')or static_context['gpu_root']));project=directory(Path(project));need(gpu.is_relative_to(project/'state')and gpu!=project/'state','selected isolated GPU root')
 need(0<page_size<=128 and 0<seconds<=600,'bounded original startup preparation page')
 selected_renderer=selected_renderer or environment.get('BB_GPU_PROCESS');backend_root=backend_root or component.get('backend_root')
 if not selected_renderer or not backend_root:return dict(status='unavailable',reason='selected renderer/packaged load-edge root unavailable',geometry_resolver=None)
 identity=coherent_component(component,gpu=gpu,selected_renderer=selected_renderer,backend_root=backend_root,resolver=identity_resolver,capability_runner=capability_runner)
 need(len(cohort)==64 and all(x in'0123456789abcdef'for x in cohort),'compiler cohort identity')
 layout=component['native_layout'];catalog=json.loads(checked_bytes(Path(static_context['catalog']),limit=32*1024*1024)[0]);need(Path(catalog['game_root'])==Path(static_context['game_root']),'actual final game source root')
 table=lookup_table(list(rows(Path(static_context['index']))));need(len(caches)<=128,'selected cache count budget')
 compiler=digest(dict(consumer=component['module_consumer_sha256'],library=identity['library_sha256'],layout=layout_abi_identity(layout),cohort=cohort,catalog=static_context['identity']))
 auto=gpu/'automatic-game-inputs';auto.mkdir(mode=0o700,exist_ok=True);directory(auto);root=auto/compiler;root.mkdir(mode=0o700,exist_ok=True);directory(root)
 nonce_path=root/'nonce';
 if not nonce_path.exists():nonce_path.write_text(secrets.token_hex(32))
 nonce=checked_bytes(nonce_path,limit=64)[0].decode();need(len(nonce)==64 and all(x in'0123456789abcdef'for x in nonce),'owned preparation nonce')
 index_path=root/'index.json';index=json.loads(checked_bytes(index_path,limit=32*1024*1024)[0])if index_path.exists()else dict(schema=1,compiler_identity=compiler,modules={},geometry={},rejected={})
 need(index.get('schema')==1 and index.get('compiler_identity')==compiler,'same complete producer identity')
 counts=collections.Counter();gaps=collections.Counter();start=time.monotonic();admitted={};visited=set();observed_static=set()
 typed_ready={};typed_report=dict(status='unavailable',reason='normal-path companion capture not configured')
 if static_context.get('capture_root')is not None:
  from game_companion_modules import prepare as prepare_companions,stage_key
  profiles={};targets=collections.defaultdict(dict)
  for cache in caches:
   path=Path(cache)/'profile.bin';_,ref=checked_bytes(path);profiles[ref['sha256']]=ref
  for unit in selected_units(caches,gpu=gpu,state_root=project/'state',title=catalog['game_identity']['TITLE_ID'],layout=layout,check=check,candidate_manifest=candidate_manifest):
   if unit['record_type']!='offline-pso-input':continue
   for stage in unit['stages']:
    if stage['logical_stage']=='vertex'and stage['hardware_stage']in('vertex','export','local'):targets[stage_key(unit,stage)][stage['serialized_spec_sha256']]=stage['metadata']
  typed_report,typed_ready=prepare_companions(context=static_context,gpu=gpu,root=root,component={**component,'library':identity['library']},identity=identity,catalog=catalog,cohort=cohort,nonce=nonce,profiles=profiles,targets=targets,index=index,index_path=index_path,check=check,deadline=start+seconds,native_runner=native_runner)
 links_path=root/('stage-links-'+str(time.time_ns())+'.jsonl');recipes_path=root/('recipe-links-'+str(time.time_ns())+'.jsonl');link_bytes=0
 def emit(stream,value):
  nonlocal link_bytes
  raw=json.dumps(value,sort_keys=True,separators=(',',':'))+'\n';link_bytes+=len(raw.encode());need(link_bytes<=64*1024*1024,'bounded automatic association ledger');stream.write(raw)
 stages_stream=links_path.open('x');recipes_stream=recipes_path.open('x')
 try:
  for unit in selected_units(caches,gpu=gpu,state_root=project/'state',title=catalog['game_identity']['TITLE_ID'],layout=layout,check=check,candidate_manifest=candidate_manifest):
   check();counts['records']+=1;need(counts['records']<=MAX_RECORDS,'automatic module inventory bound')
   if unit['record_type']!='offline-pso-input':gaps[unit['reason']]+=1;continue
   stage_results={};input_ids=[]
   geometry_unit=any(s['logical_stage']=='geometry'for s in unit['stages'])
   for stage in unit['stages']:
    check();matches=table.get((stage['program_hash'],stage['hardware_stage']),[])
    if len(matches)!=1:gaps['missing-or-ambiguous-static-source']+=1;continue
    if typed_ready and not geometry_unit:
     from game_companion_modules import stage_key
     typed=typed_ready.get(stage_key(unit,stage))
     if typed is not None and stage['serialized_spec_sha256']in typed['target_spec_sha256s']and typed['facts']['code_sha256']==matches[0]['identity']['code_sha256']:
      uid=digest(dict(typed_input=typed['source_input_identity'],target_spec=stage['serialized_spec_sha256'],compiler=compiler));input_ids.append(uid);counts['typed_companion_stage_links']+=1;observed_static.add((stage['program_hash'],stage['hardware_stage']));stage_results[stage['logical_stage']]=typed
      emit(stages_stream,dict(input_id=uid,input_integrity_sha256=typed['source_input_identity'],permutation_hash=stage['permutation_hash'],program_hash=stage['program_hash'],hardware_stage=stage['hardware_stage'],logical_stage=stage['logical_stage'],recipe_blob_identity=unit['recipe_blob_identity'],typed_receipt=typed['receipt'],typed_companion_verified=True));continue
    try:uid=stage_input_identity(unit,stage,matches[0]['identity'],layout,catalog['game_identity'])
    except(ValueError,KeyError)as error:gaps[str(error)]+=1;continue
    input_ids.append(uid)
    link=dict(input_id=uid,program_hash=stage['program_hash'],hardware_stage=stage['hardware_stage'],logical_stage=stage['logical_stage'],permutation_hash=stage['permutation_hash'],metadata_sha256=stage['metadata']['sha256'],profile_sha256=unit['profile']['reference']['sha256'],recipe_blob_identity=unit['recipe_blob_identity']);emit(stages_stream,link);observed_static.add((stage['program_hash'],stage['hardware_stage']))
    if uid in visited:
     counts['exact_source_reuses']+=1
     if uid in admitted:stage_results[stage['logical_stage']]=admitted[uid]
     continue
    visited.add(uid)
    existing=index['modules'].get(uid)
    if existing is not None:
     try:
      from complete_geometry_psos import verify_receipt
      reader=canonical_packet
      if stage['hardware_stage']=='geometry':
       from run_owned_geometry_contracts import canonical_geometry_packet
       reader=canonical_geometry_packet
      p=reader(Path(existing['packet']),catalog);receipt=json.loads(checked_bytes(Path(existing['receipt']),limit=256*1024,expected=existing['receipt_sha256'])[0]);need(file_sha(existing['packet'])==existing['packet_sha256'],'indexed exact packet SHA');validate_call(0,receipt,Path(existing['spirv']).parent,p);verify_receipt(p,receipt,existing['packet_sha256']);need(p.get('automatic_preparation')==dict(schema=1,nonce=nonce,cohort=cohort),'indexed automatic admission');stage_results[stage['logical_stage']]=admitted[uid]=existing;counts['verified_existing']+=1;continue
     except(ValueError,OSError,KeyError):gaps['invalid-indexed-module']+=1;continue
    if uid in index.get('rejected',{}):gaps['same-producer-rejected-input']+=1;continue
    if time.monotonic()-start>=seconds:gaps['bounded-total-budget-pending']+=1;continue
    if counts['native_attempts']%page_size==0:
     check();atomic_json(index_path,index);counts['pages']+=1
    # Interrupted/unpublished cases are preserved. A new owned attempt never
    # overwrites them and never turns their mere existence into readiness.
    case=root/('module-'+uid+'-'+secrets.token_hex(8));case.mkdir(mode=0o700)
    try:
     packet=freeze_stage(unit,stage,layout=layout,catalog=catalog,table=table,output=case/'input');path=case/'input/packet.json';_automatic_packet(packet,path,nonce,cohort)
     counts['native_attempts']+=1;result=native_runner(component,path,case/'output',case,gpu,nonce,cohort,check,identity)
     from complete_geometry_psos import verify_receipt
     verify_receipt(packet,result,file_sha(path))
     receipt=case/'output/result.json';spirv=case/'output/module.spv';item=dict(link,packet=str(path),packet_sha256=file_sha(path),receipt=str(receipt),receipt_sha256=file_sha(receipt),spirv=str(spirv),spirv_sha256=file_sha(spirv))
     admitted[uid]=item;index['modules'][uid]=item;stage_results[stage['logical_stage']]=item;counts['native_completed']+=1;atomic_json(index_path,index)
    except(ValueError,OSError,KeyError)as error:
     gaps[str(error)]+=1;index.setdefault('rejected',{})[uid]=dict(reason=str(error),compiler_identity=compiler);atomic_json(index_path,index)
   emit(recipes_stream,dict(recipe_blob_identity=unit['recipe_blob_identity'],module_input_ids=input_ids,all_stages_verified=len(stage_results)==len(unit['stages'])))
   if any(s['logical_stage']=='geometry'for s in unit['stages'])and len(stage_results)==len(unit['stages']):
    from game_pso_geometry_adapter import freeze_recipe
    rid=digest(dict(recipe=unit['recipe_blob_identity'],inputs=input_ids,compiler=compiler));destination=root/('recipe-'+rid)
    if rid not in index['geometry']:
     artifact=gpu/'cache'/catalog['game_identity']['TITLE_ID']/'metal'
     artifact.mkdir(parents=True,exist_ok=True);directory(artifact)
     index['geometry'][rid]=freeze_recipe(unit,layout,stage_results,destination,cohort,artifact,check);atomic_json(index_path,index)
    counts['geometry_recipes_ready']+=1
 finally:
  stages_stream.close();recipes_stream.close()
 def geometry_resolver(unit,current_layout,check):
  check();need(layout_abi_identity(current_layout)==layout_abi_identity(layout),'current recipe ABI')
  ids=[]
  for stage in unit['stages']:
   matches=table.get((stage['program_hash'],stage['hardware_stage']),[])
   if len(matches)!=1:return None
   try:ids.append(stage_input_identity(unit,stage,matches[0]['identity'],layout,catalog['game_identity']))
   except(ValueError,KeyError):return None
  return index['geometry'].get(digest(dict(recipe=unit['recipe_blob_identity'],inputs=ids,compiler=compiler)))
 report=dict(schema=1,status='prepared'if not gaps else'partial',counts=dict(counts),gaps=dict(gaps),typed_companions=typed_report,compiler_identity=compiler,module_index=str(index_path),stage_links=dict(path=str(links_path),sha256=file_sha(links_path)),recipe_links=dict(path=str(recipes_path),sha256=file_sha(recipes_path)),static_identities=sum(map(len,table.values())),observed_static_identities=len(observed_static),static_without_observed_inputs=sum(map(len,table.values()))-len(observed_static),full_final_PSO_ready=False,runtime_cache_import_allowed=False,game_started=False,GPU_started=False)
 atomic_json(index_path,index)
 atomic_json(root/'report.json',report)
 return dict(report,geometry_resolver=geometry_resolver,cache_root=root,report_path=str(root/'report.json'))
