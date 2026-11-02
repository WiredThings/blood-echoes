"""Static exact-code input construction, using the existing native/PSO queues.

Only FS/CS and one actual complete context per target/profile. Native records
are read lazily on first use; this phase does not enlarge a live object cache.
"""
from pathlib import Path
import copy,hashlib, json, os,re,secrets, struct, time
from game_module_inputs import need
from game_pso_owned_audit import checked_bytes, metadata_sections
from game_pso_candidates import cache_records
from inventory_shader_cache import combine
from prepare_game_psos import new_bytes
from static_variant_inputs import candidates, freeze, validate_derivation

WIRE=struct.Struct('<8sIIIIQQQIIII32s32s32s32s32s')
assert WIRE.size==224
MAX_RECORD=224+1536*1024


def record_bytes(packet,result,output,layout,compiler):
    output=Path(output); publication=result['static_variant_publication']
    metadata,_=checked_bytes(output/'module.meta',limit=1024*1024,expected=publication['metadata_sha256'])
    spirv,_=checked_bytes(output/'module.spv',limit=512*1024,expected=result['spirv_sha256'])
    meta,sections=metadata_sections(metadata,layout)
    need(meta['program_hash']==packet['program_hash'] and meta['stage']==packet['hardware_stage'] and
         meta['logical_stage']==packet['logical_stage'] and meta['permutation']==0 and
         meta['permutation_hash']==publication['permutation_hash']==f"{combine(int(packet['program_hash'],16),0):016x}",
         'regenerated native metadata identity')
    raw_spec,_=checked_bytes(Path(packet['spec']['path']),expected=packet['spec']['sha256'])
    region=sections['specialization']
    need(metadata[region['offset']:region['offset']+region['bytes']]==raw_spec,
         'regenerated complete serialized specialization')
    hw={'fragment':0,'compute':6}[packet['hardware_stage']]
    sw={'fragment':0,'compute':5}[packet['logical_stage']]
    header=WIRE.pack(b'BBSVAR01',1,hw,sw,0,int(packet['program_hash'],16),
        int(packet['static_derivation']['template_program_hash'],16),int(publication['permutation_hash'],16),
        packet['code']['bytes'],len(metadata),len(spirv),0,
        bytes.fromhex(packet['code']['sha256']),bytes.fromhex(packet['profile']['sha256']),
        bytes.fromhex(publication['metadata_sha256']),bytes.fromhex(result['spirv_sha256']),bytes.fromhex(compiler))
    return header+metadata+spirv


def verify_record(path,entry,target,profile,compiler):
    data,_=checked_bytes(path,limit=MAX_RECORD,expected=entry['record_sha256'])
    need(len(data)>=WIRE.size,'complete native record header')
    h=WIRE.unpack_from(data)
    need(h[0]==b'BBSVAR01' and h[1]==1 and h[2]==target['hardware_stage_value'] and
         h[3]=={'fragment':0,'compute':5}[target['hardware_stage']] and h[4]==0 and
         h[5]==int(target['program_hash'],16) and h[6]!=h[5] and h[7]==combine(h[5],0) and h[11]==0 and
         0<h[8]<=512*1024 and h[12].hex()==target['code_sha256'] and h[13].hex()==profile and h[16].hex()==compiler,
         'current exact native record identity')
    need(0<h[9]<=1024*1024 and 20<=h[10]<=512*1024 and h[10]%4==0 and
         len(data)==WIRE.size+h[9]+h[10] and hashlib.sha256(data[WIRE.size:WIRE.size+h[9]]).digest()==h[14] and
         hashlib.sha256(data[WIRE.size+h[9]:]).digest()==h[15],'complete native record payload')
    return data


def pipeline_input(candidate,packet,result,output,layout,destination,check):
    """Replace exactly one stage in an ACTUAL full context; use original readers.

    Slot zero belongs to this new prepared program. Runtime uses it only after
    a full match and successful adoption, never by assuming the game's order.
    """
    destination=Path(destination);destination.mkdir(mode=0o700)
    unit=candidate['unit']; stage=candidate['stage']; output=Path(output)
    def put(name,reference):
        check();data,_=checked_bytes(Path(reference['path']),expected=reference['sha256'])
        new_bytes(destination,name,data)
    put('profile.bin',unit['profile']['reference'])
    for old in unit['stages']:
        if old['logical_stage']==stage['logical_stage']:
            for name,path in ((f"0x{result['static_variant_publication']['permutation_hash']}.meta",output/'module.meta'),
                              (f"0x{packet['program_hash']}_0.spv",output/'module.spv')):
                new_bytes(destination,name,checked_bytes(path)[0])
        else:
            put(old['metadata']['name'],old['metadata']);put(old['spirv']['name'],old['spirv'])
    key=bytearray(checked_bytes(Path(unit['key']['reference']['path']),expected=unit['key']['reference']['sha256'])[0])
    if unit['key']['decoded']['kind']=='compute':
        need(stage['logical_stage']=='compute','compute stage replacement');at=8
    else:
        from decode_shader_cache import SW
        at=8+layout['GraphicsPipelineKey.stage_hashes']+8*SW.index(stage['logical_stage'])
    need(struct.unpack_from('<Q',key,at)[0]==int(stage['permutation_hash'],16),'original full key stage identity')
    struct.pack_into('<Q',key,at,int(result['static_variant_publication']['permutation_hash'],16))
    new_bytes(destination,'derived-'+hashlib.sha256(key).hexdigest()+'.key',key)
    units=[r for r in cache_records(destination,layout,check)if r['record_type']=='offline-pso-input']
    need(len(units)==1 and units[0]['execution_policy']['candidate_preparation']['policy_eligible'] is True,
         'one complete native derived pipeline input')
    return units[0]


def cached_pipeline_input(entry,*,root,uid,layout,check):
    """Reconstruct locations through the ORIGINAL full cache reader.

    Copied caches contain source-location provenance, not permission to read
    the previous root. Never follow those absolute paths or edit descriptors.
    The current local files must reconstruct the exact same complete input
    after removing only independently validated location-derived hashes.
    """
    root=Path(root);need(root==root.resolve(strict=True),'canonical current variant root')
    oldpath=Path(entry['pipeline_input']);name=oldpath.parent.name
    need(oldpath.name=='pipeline-input.json'and re.fullmatch('static-variant-'+re.escape(uid)+'-[0-9a-f]{12}',name),
         'exact indexed variant case')
    path=root/name/'pipeline-input.json'
    data,_=checked_bytes(path,limit=256*1024,expected=entry['pipeline_input_sha256'])
    old=json.loads(data);oldcache=oldpath.parent/'pipeline-cache'
    need(old['namespace']['cache']==str(oldcache),'indexed variant cache-local recipe')
    cache=path.parent/'pipeline-cache';need(cache==cache.resolve(strict=True),'canonical current variant cache')
    fresh=[r for r in cache_records(cache,layout,check)if r.get('record_type')=='offline-pso-input']
    need(len(fresh)==1,'one complete reconstructed variant recipe')
    from game_pso_progress_identity import input_identity
    from game_pso_candidates import digest
    def content(unit):
        input_identity(unit) # Validate BOTH original full scope/read-set hashes.
        need(unit['recipe_blob_identity']==digest(dict(profile=unit['profile']['semantic_sha256'],
             key=unit['key']['reference']['sha256'],stages=unit['stages'])),'original recipe content identity')
        result=copy.deepcopy(unit)
        result['namespace'].pop('cache');result['namespace'].pop('cache_id')
        refs=[result['profile']['reference'],result['key']['reference']]
        for stage in result['stages']:refs.extend((stage['metadata'],stage['spirv']))
        for ref in refs:ref.pop('path') # Name, size, digest and ALL decoded state remain.
        result.pop('recipe_blob_identity')
        result['execution_policy']['candidate_preparation'].pop('frozen_input_set_identity')
        return result
    need(content(old)==content(fresh[0]),'reconstructed variant input content changed')
    return fresh[0]


def prepare(*,records,units,layout,catalog,table,component,identity,root,gpu,nonce,cohort,
            check,native_runner,index,index_path,atomic_json,capabilities=None,seconds=120):
    if (capabilities or {}).get('static_variant_publication_schema')!=1:
        return dict(status='unavailable',reason='native static publication capability unavailable')
    root=Path(root);gpu=Path(gpu);start=time.monotonic()
    compiler=identity['library_sha256'];store=gpu/'automatic-game-inputs/static-native-variants'/compiler
    store.mkdir(parents=True,exist_ok=True,mode=0o700)
    need(store==store.resolve(strict=True),'canonical selected native variant directory')
    registry=index.setdefault('static_variants',{});refused=index.setdefault('static_variant_rejected',{})
    counts=dict(prepared=0,reused=0,rejected=0,deferred=0);recipes=[];gaps={}
    choices=candidates(units,records)
    for choice in choices:
        check();wanted=choice['target']['identity'];profile=choice['unit']['profile']['reference']['sha256']
        filename=f"{profile}.{wanted['hardware_stage_value']}.{wanted['program_hash']}/{wanted['code_sha256']}.bbsv"
        (store/filename).parent.mkdir(mode=0o700,exist_ok=True)
        need((store/filename).parent==(store/filename).parent.resolve(strict=True),'canonical target record group')
        uid=hashlib.sha256(json.dumps(dict(target=wanted,profile=profile,
            metadata=choice['stage']['metadata']['sha256'],spirv=choice['stage']['spirv']['sha256'],
            compiler=compiler),sort_keys=True,separators=(',',':')).encode()).hexdigest()
        entry=registry.get(filename)
        if entry is not None:
            try:
                verify_record(store/filename,entry,wanted,profile,compiler)
                recipes.append(cached_pipeline_input(entry,root=root,uid=uid,layout=layout,check=check));counts['reused']+=1;continue
            except(ValueError,OSError,KeyError,TypeError):
                # Preserve a damaged/new-format record; never replace silently.
                if os.path.lexists(store/filename):
                    (store/filename).rename(store/(filename+'.retired-'+secrets.token_hex(8)))
                registry.pop(filename,None)
        if uid in refused:
            counts['rejected']+=1;continue
        if time.monotonic()-start>=seconds:
            counts['deferred']+=1;continue
        case=root/('static-variant-'+uid+'-'+secrets.token_hex(6));case.mkdir(mode=0o700)
        try:
            packet=freeze(choice,layout=layout,catalog=catalog,output=case/'input')
            validate_derivation(packet,catalog,table)
            packet['automatic_preparation']=dict(schema=1,nonce=nonce,cohort=cohort)
            path=case/'input/packet.json';atomic_json(path,packet)
            result=native_runner(component,path,case/'output',case,gpu,nonce,cohort,check,identity)
            need(result.get('input_packet_sha256')==hashlib.sha256(path.read_bytes()).hexdigest() and
                 result.get('input_code_sha256')==packet['code']['sha256'] and
                 result.get('input_spec_sha256')==packet['spec']['sha256'] and
                 result.get('input_profile_sha256')==packet['profile']['sha256'] and
                 result.get('source_provenance')==packet['source_provenance'],
                 'exact current native input receipt')
            record=record_bytes(packet,result,case/'output',layout,compiler)
            recipe=pipeline_input(choice,packet,result,case/'output',layout,case/'pipeline-cache',check)
            recipe_path=case/'pipeline-input.json';atomic_json(recipe_path,recipe)
            if os.path.lexists(store/filename):
                raise ValueError('unindexed native record preserved; explicit repair required')
            temporary=store/(filename+'.new-'+secrets.token_hex(8));new_bytes(temporary.parent,temporary.name,record)
            try:os.link(temporary,store/filename,follow_symlinks=False)
            finally:temporary.unlink()
            registry[filename]=dict(input_identity=uid,record_sha256=hashlib.sha256(record).hexdigest(),
                packet=str(path),packet_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                pipeline_input=str(recipe_path),pipeline_input_sha256=hashlib.sha256(recipe_path.read_bytes()).hexdigest(),
                target_code_sha256=wanted['code_sha256'],compiler_library_sha256=compiler)
            recipes.append(recipe);counts['prepared']+=1;atomic_json(index_path,index)
        except(ValueError,OSError,KeyError,TypeError)as error:
            reason=str(error);counts['rejected']+=1;gaps[reason]=gaps.get(reason,0)+1
            if getattr(error,'deterministic',False):
                refused[uid]=dict(reason=reason,compiler_library_sha256=compiler)
            atomic_json(index_path,index)
    manifest=root/('static-variant-recipes-'+secrets.token_hex(8)+'.jsonl')
    with manifest.open('x')as stream:
        for recipe in recipes:stream.write(json.dumps(recipe,sort_keys=True,separators=(',',':'))+'\n')
    atomic_json(index_path,index)
    return dict(schema=1,status='prepared'if not counts['deferred']and not counts['rejected']else'partial',
        counts=counts,candidates=len(choices),gaps=gaps,recipes=len(recipes),
        manifest=dict(path=str(manifest),sha256=hashlib.sha256(manifest.read_bytes()).hexdigest()),
        elapsed_seconds=time.monotonic()-start,bulk_runtime_preload=False,
        static_only_complete_input_claim=False,descriptors_synthesized=False)


def append_manifest(source,extra,destination,check):
    raw,_=checked_bytes(Path(extra['path']),limit=256*1024*1024,expected=extra['sha256'])
    need(len(raw.splitlines())<=100000,'static recipe count budget')
    from prepare_game_psos import rows
    count=0
    with Path(destination).open('xb')as stream:
        for path in (source,Path(extra['path'])):
            for row in rows(path):
                check();stream.write(json.dumps(row,sort_keys=True,separators=(',',':')).encode()+b'\n')
                if path!=source:count+=1
    return count
