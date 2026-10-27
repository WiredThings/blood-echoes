"""Typed full-companion preparation inside the original automatic module phase.

Native code alone decodes the companion, source receipt and typed specialization.
Python never guesses V#, reads old Info, or confuses a blob locator with perm_hash.
"""
from pathlib import Path
import collections,hashlib,json,re,secrets,stat,time
from complete_pipeline_inputs import directory,digest,file_sha
from game_module_inputs import need
from game_pso_owned_audit import checked_bytes,metadata_sections
from prepare_game_psos import new_bytes

FIELDS=('companion_key','input_integrity_sha256','program_hash','hardware_stage','logical_stage','permutation','permutation_hash','reference_origin','canonical_spec_sha256','code_sha256','observed_spirv_sha256','observed_spirv_bytes')
def association_key(f,profile_sha):
 return(f['program_hash'],f['hardware_stage'],f['logical_stage'],f['permutation_hash'],f.get('observed_spirv_sha256',f.get('spirv_sha256')),profile_sha)
def stage_key(unit,stage):
 return(stage['program_hash'],stage['hardware_stage'],stage['logical_stage'],stage['permutation_hash'],stage['spirv']['sha256'],unit['profile']['reference']['sha256'])

def target_product_key(f,target_specs):
 """An already-proved target product, NOT equivalence of captured sources.

 Keep the original full source UID and receipt. A different capture locator,
 origin or snapshot digest need not be compiled when a previously validated
 source already produces exactly this code/spec/SPIR-V for the current target.
 """
 return tuple(f[k]for k in ('program_hash','hardware_stage','logical_stage','permutation',
  'permutation_hash','canonical_spec_sha256','code_sha256','observed_spirv_sha256','observed_spirv_bytes'))+(tuple(sorted(target_specs)),)

def verified_entry(entry,*,profile_sha,component,nonce,cohort,game_root):
 raw,_=checked_bytes(Path(entry['packet']),limit=256*1024,expected=entry['packet_sha256']);packet=json.loads(raw)
 receipt=json.loads(checked_bytes(Path(entry['receipt']),limit=256*1024,expected=entry['receipt_sha256'])[0])
 need(packet['selected_backend_sha256']==component['library_sha256']and packet['selected_backend']==str(component['library']),'same actual typed compiler')
 need(packet['game_root']==str(game_root),'same typed game source')
 need(packet['profile']['sha256']==profile_sha,'same typed profile')
 checked_bytes(Path(packet['profile']['path']),expected=profile_sha)
 need(packet['expected']==entry['facts']and entry['source_input_identity']==entry['facts']['input_integrity_sha256'],'original complete typed source identity')
 need(packet['automatic_preparation']==dict(schema=1,nonce=nonce,cohort=cohort),'same typed automatic admission')
 need(entry['target_spec_sha256s']==[r['sha256']for r in packet['target_specs']],'indexed typed target set')
 for ref in packet['target_specs']:checked_bytes(Path(ref['path']),limit=16384,expected=ref['sha256'])
 need(Path(entry['receipt']).parent==Path(entry['spirv']).parent and Path(entry['spirv']).name=='module.spv','same typed output root')
 need(entry['spirv_sha256']==receipt['spirv_sha256'],'indexed typed SPIR-V identity')
 validate_call(0,receipt,Path(entry['spirv']).parent,packet)
 return entry

def validate_call(exit_code,result,output,packet):
 need(exit_code==0 and result.get('candidate_only')is True and result.get('companion_contract_schema')==1,'successful typed companion contract')
 need(all(result.get(k)is False for k in('runtime_cache_import_allowed','gpu_started','native_info_srt_deserialized','old_walker_executed','guest_memory_read','full_final_pso_ready')),'typed companion forbidden state')
 need(result.get('input_packet_sha256')==file_sha(Path(packet['profile']['path']).parent/'packet.json'),'typed packet exact receipt')
 need(result.get('source_compiler_sha256')==packet['selected_backend_sha256'],'typed companion actual selected compiler')
 if packet['kind']=='owned-fetch-companion-inventory':
  need(result.get('status')=='inventoried'and result.get('native_module_compiled')is False and type(result.get('accepted'))is int and 0<=result['accepted']<=8192 and type(result.get('rejected'))is int,'typed bounded inventory result')
  need({p.name for p in output.iterdir()}==({'result.json','inventory.jsonl'}if result['accepted']else{'result.json'}),'only typed inventory outputs')
 else:
  need(result.get('status')=='completed'and all(result.get(k)is True for k in('native_module_compiled','expected_spirv_equal','runtime_after_equal','canonical_specialization_equal','program_base_from_owned_snapshot','current_generated_srt_verified','target_canonical_specs_verified')),'full owned translation gates')
  need('unknown_program_base_rejected_before_use'not in result,'known captured base is not an unknown-base proof')
  facts=result['association'];need(all(facts[k]==packet['expected'][k]for k in FIELDS),'actual runtime stage/perm facts unchanged')
  need(result.get('target_spec_sha256s')==[r['sha256']for r in packet['target_specs']],'all actual target canonical specs proved')
  need(result.get('spirv_sha256')==facts['observed_spirv_sha256']and result.get('spirv_bytes')==facts['observed_spirv_bytes'],'actual generated SPIR-V identity')
  raw,_=checked_bytes(output/'module.spv',expected=facts['observed_spirv_sha256']);need(len(raw)==facts['observed_spirv_bytes'],'actual complete SPIR-V extent')
  raw,_=checked_bytes(output/'canonical-spec.bin',limit=16384,expected=facts['canonical_spec_sha256'])
  need({p.name for p in output.iterdir()}=={'result.json','module.spv','canonical-spec.bin'},'only explicit typed outputs')

def _packet(*,kind,case,profile_ref,capture_root,game_root,component,nonce,cohort):
 inp=case/'input';inp.mkdir(mode=0o700);profile,_=checked_bytes(Path(profile_ref['path']),expected=profile_ref['sha256'])
 return dict(packet_schema=1,kind=kind,candidate_only=True,runtime_cache_import_allowed=False,native_info_srt_allowed=False,companion_root=str(capture_root),game_root=str(game_root),selected_backend=str(component['library']),selected_backend_sha256=component['library_sha256'],profile=new_bytes(inp,'profile.bin',profile),automatic_preparation=dict(schema=1,nonce=nonce,cohort=cohort),constraints=dict(guest_dereference_allowed=False,old_walker_execution_allowed=False,final_runtime_import_allowed=False,require_association=True))

def index_product(index,uid,entry,profile_sha):
 key=digest(list(association_key(entry['facts'],profile_sha)))
 bucket=index.setdefault('companion_required_index',{}).setdefault(key,[])
 if not isinstance(bucket,list):bucket=[];index['companion_required_index'][key]=bucket
 if uid not in bucket:bucket.append(uid)

def required_products(*,context,gpu,root,component,cohort,nonce,profiles,targets,index,required_code,observed_modules,check):
 """Query products for CURRENT full targets, not historical capture sources.

 Lookup hints never grant admission: verify original native packet/output, the
 current static code identity, and actual profile/spec/SPIR-V bytes. A different
 readonly source view is provenance when all these witnessed inputs agree.
 """
 if type(index.get('companion_required_index_schema'))is not int or index['companion_required_index_schema']!=1 or not isinstance(index.get('companion_required_index'),dict):
  index['companion_required_index']={}
  for uid,entry in index.get('companions',{}).items():
   check()
   try:
    packet=json.loads(checked_bytes(Path(entry['packet']),limit=256*1024,expected=entry['packet_sha256'])[0])
    index_product(index,uid,entry,packet['profile']['sha256'])
   except(ValueError,OSError,KeyError,TypeError):continue
  index['companion_required_index_schema']=1
 ready={};source_base=gpu/'automatic-game-inputs/source-views'
 for key,target in targets.items():
  check()
  if key not in required_code or key not in observed_modules or not target:continue
  candidates=index['companion_required_index'].get(digest(list(key)),[])
  if not isinstance(candidates,list):continue
  for uid in candidates[:64]:
   try:
    entry=index['companions'][uid]
    if entry['facts']['code_sha256']!=required_code[key]or not set(target)<=set(entry['target_spec_sha256s']):continue
    raw,_=checked_bytes(Path(entry['packet']),limit=256*1024,expected=entry['packet_sha256']);packet=json.loads(raw)
    case=Path(entry['packet']).parent.parent
    need(case.parent==root and re.fullmatch(r'companion-module-[0-9a-f]{64}-[0-9a-f]{16}',case.name),'same registered typed product store')
    origin=directory(Path(packet['game_root']))
    current_source=directory(Path(context['game_root']))
    for source in(origin,current_source):
     need(source.parent.parent==source_base and source.name=='view'and stat.S_IMODE(source.stat().st_mode)==0o500,'registered readonly typed origin')
    verified_entry(entry,profile_sha=key[-1],component=component,nonce=nonce,cohort=cohort,game_root=origin)
    current_profile=profiles[key[-1]]
    for ref in [current_profile,observed_modules[key],*target.values()]:
     need(Path(ref['path']).is_relative_to(gpu/'cache'),'current target from selected runtime cache')
    previous_profile=checked_bytes(Path(packet['profile']['path']),expected=key[-1])[0]
    need(checked_bytes(Path(current_profile['path']),expected=current_profile['sha256'])[0]==previous_profile,'actual current profile equality')
    previous_spirv=checked_bytes(Path(entry['spirv']),expected=entry['spirv_sha256'])[0]
    observed=observed_modules[key]
    need(checked_bytes(Path(observed['path']),expected=observed['sha256'])[0]==previous_spirv,'actual current SPIR-V equality')
    old_specs={ref['sha256']:ref for ref in packet['target_specs']}
    for spec_sha,ref in target.items():
     raw,_=checked_bytes(Path(ref['path']),expected=ref['sha256']);_,sections=metadata_sections(raw,component['native_layout']);section=sections['specialization']
     actual=raw[section['offset']:section['offset']+section['bytes']]
     need(hashlib.sha256(actual).hexdigest()==spec_sha,'current complete target specialization')
     need(actual==checked_bytes(Path(old_specs[spec_sha]['path']),limit=16384,expected=spec_sha)[0],'actual current specialization equality')
    ready[key]=entry;break
   except(ValueError,OSError,KeyError,TypeError):continue
 return ready

def prepare(*,context,gpu,root,component,identity,catalog,cohort,nonce,profiles,targets,index,index_path,check,deadline,native_runner,
            required_only=False,required_code=None,observed_modules=None):
 from launcher_game_modules import atomic_json,NativeModuleRejected
 capture=context.get('capture_root')
 if capture is None:return dict(status='unavailable',reason='normal-path typed capture not configured',counts={}),{}
 capture=directory(Path(capture));need(capture.is_relative_to(gpu/'automatic-game-inputs')and capture!=gpu/'automatic-game-inputs','selected owned companion root')
 counts=collections.Counter();gaps=collections.Counter();ready={};index.setdefault('companions',{});seen=set()
 if required_only:
  ready=required_products(context=context,gpu=gpu,root=root,component=component,cohort=cohort,nonce=nonce,
      profiles=profiles,targets=targets,index=index,required_code=required_code or{},observed_modules=observed_modules or{},check=check)
  counts['required_target_keys']=len(targets);counts['verified_required_products']=len(ready)
  if len(ready)==len(targets):
   atomic_json(index_path,index)
   return dict(status='prepared',counts=dict(counts),gaps={},historical_capture_inventory_skipped=True,
       required_target_scope=True,actual_stage_and_permutation_separate_from_input_integrity=True,full_final_PSO_ready=False),ready
 products=collections.defaultdict(list);verified_products={}
 for entry in index['companions'].values():
  try:
   # This is only a lookup hint. Complete old packet/receipt/output validation
   # below is mandatory before a hint may satisfy a current target.
   if entry.get('native_typed_companion')is True and entry.get('target_spec_sha256s'):
    products[target_product_key(entry['facts'],entry['target_spec_sha256s'])].append(entry)
  except(KeyError,TypeError):pass
 for profile_sha,profile in profiles.items():
  check()
  if time.monotonic()>=deadline:gaps['total-budget-pending']+=1;break
  case=root/('companion-inventory-'+secrets.token_hex(12));case.mkdir(mode=0o700);p=_packet(kind='owned-fetch-companion-inventory',case=case,profile_ref=profile,capture_root=capture,game_root=context['game_root'],component=component,nonce=nonce,cohort=cohort);path=case/'input/packet.json';atomic_json(path,p)
  try:inventory=native_runner(component,path,case/'output',case,gpu,nonce,cohort,check,identity)
  except(ValueError,OSError,KeyError)as error:gaps[str(error)]+=1;continue
  counts['inventory_profiles']+=1;counts['source_rejections']+=inventory['rejected'];inventory_path=case/'output/inventory.jsonl'
  if not inventory['accepted']:continue
  with inventory_path.open()as stream:
   count=0
   for line in stream:
    check();count+=1;need(count<=8192 and len(line)<=16384,'bounded native typed inventory facts');facts=json.loads(line);need(set(facts)==set(FIELDS),'native typed inventory schema');counts['inventory_records']+=1
    key=association_key(facts,profile_sha);target=targets.get(key,{})
    if required_only and key not in targets:counts['unrequired_capture_records']+=1;continue
    if required_only and key in ready:counts['verified_target_reuses']+=1;continue
    product=target_product_key(facts,target)if target else None
    prepared=verified_products.get((profile_sha,product))if product is not None else None
    if prepared is None and product is not None:
     for old in products.get(product,()):
      check()
      try:prepared=verified_entry(old,profile_sha=profile_sha,component=component,nonce=nonce,cohort=cohort,game_root=context['game_root'])
      except(ValueError,OSError,KeyError,TypeError):continue
      verified_products[(profile_sha,product)]=prepared;counts['verified_target_products']+=1;break
    if prepared is not None:
     ready[key]=prepared;counts['verified_target_reuses']+=1;continue
    uid=digest(dict(kind='typed-owned-companion-v1',facts=facts,profile=profile_sha,compiler=component['module_consumer_sha256'],library=component['library_sha256'],cohort=cohort,target_specs=sorted(target)))
    if uid in seen:counts['exact_reuses']+=1;continue
    seen.add(uid);entry=index['companions'].get(uid)
    if uid in index.get('companion_rejected',{}):gaps['same-producer-rejected-input']+=1;continue
    if entry is not None:
     try:
      verified_entry(entry,profile_sha=profile_sha,component=component,nonce=nonce,cohort=cohort,game_root=context['game_root']);ready[key]=entry;counts['verified_existing']+=1
      if product is not None:verified_products[(profile_sha,product)]=entry
      continue
     except(ValueError,OSError,KeyError):gaps['invalid-indexed-typed-module']+=1;continue
    if time.monotonic()>=deadline:gaps['total-budget-pending']+=1;continue
    work=root/('companion-module-'+uid+'-'+secrets.token_hex(8));work.mkdir(mode=0o700);packet=_packet(kind='owned-fetch-companion-module-input',case=work,profile_ref=profile,capture_root=capture,game_root=context['game_root'],component=component,nonce=nonce,cohort=cohort);packet.update(companion_key=facts['companion_key'],expected=facts,target_specs=[])
    try:
     need(len(target)<=64,'bounded exact target specialization set')
     for n,(spec_sha,ref)in enumerate(sorted(target.items())):
      raw,_=checked_bytes(Path(ref['path']),expected=ref['sha256']);_,sections=metadata_sections(raw,component['native_layout']);spec=sections['specialization'];need(spec['sha256']==spec_sha,'exact target spec identity');packet['target_specs'].append(new_bytes(work/'input',f'target-spec-{n}.bin',raw[spec['offset']:spec['offset']+spec['bytes']]))
     path=work/'input/packet.json';atomic_json(path,packet);counts['native_attempts']+=1;result=native_runner(component,path,work/'output',work,gpu,nonce,cohort,check,identity)
     entry=dict(packet=str(path),packet_sha256=file_sha(path),receipt=str(work/'output/result.json'),receipt_sha256=file_sha(work/'output/result.json'),spirv=str(work/'output/module.spv'),spirv_sha256=result['spirv_sha256'],target_spec_sha256s=result['target_spec_sha256s'],facts=facts,source_input_identity=facts['input_integrity_sha256'],native_typed_companion=True)
     index['companions'][uid]=entry;index_product(index,uid,entry,profile_sha);atomic_json(index_path,index);ready[key]=entry;counts['native_completed']+=1
     if product is not None:verified_products[(profile_sha,product)]=entry
    except NativeModuleRejected as error:
     gaps[str(error)]+=1
     if error.deterministic:index.setdefault('companion_rejected',{})[uid]=dict(reason=str(error),consumer_sha256=component['module_consumer_sha256'],library_sha256=component['library_sha256']);atomic_json(index_path,index)
    except(ValueError,OSError,KeyError)as error:gaps[str(error)]+=1
   need(count==inventory['accepted'],'complete typed native inventory consumed')
 if required_only:
  missing=set(targets)-set(ready)
  if missing:gaps['required-targets-unresolved']+=len(missing)
 return dict(status='prepared'if not gaps else'partial',counts=dict(counts),gaps=dict(gaps),required_target_scope=required_only,actual_stage_and_permutation_separate_from_input_integrity=True,full_final_PSO_ready=False),ready
