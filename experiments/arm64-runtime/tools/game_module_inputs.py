"""Freeze a locally owned GCN module with an observed complete specialization.

This is a CPU compiler input contract, not a native-cache import or a PSO.
No legacy Info/SRT reader, walker registration, guest pointer read or GPU call.
"""
from pathlib import Path
import hashlib,json,os,struct
from game_shader_catalog import dcx,bnd4,entry_data,orb_metadata,game_identity,LIMIT
from game_pso_owned_audit import checked_bytes,metadata_sections,audit_owned_stage
from game_pso_progress_identity import layout_abi_identity
from prepare_game_psos import new_bytes
from decode_shader_cache import semantic_profile

MAX_CODE=512*1024

def need(value,message):
    if not value:raise ValueError(message)

def source_code(game_root,catalog,identity,evidence):
    root=Path(game_root).absolute();need(root==root.resolve(strict=True)and root.is_dir(),'canonical fixed game directory required')
    sfo,_=checked_bytes(root/'sce_sys/param.sfo',limit=65536)
    current=game_identity(sfo)
    need(current==catalog['game_identity'],'fixed game identity changed')
    package=next((p for p in catalog['packages']if p['path']==evidence['package']),None)
    need(package is not None and package['kind']in('shader','embedded')and not package['debug'],'nondebug shader/module required')
    rel=Path(package['path']);need(not rel.is_absolute()and '..'not in rel.parts,'relative package path')
    raw,reference=checked_bytes(root/rel,limit=LIMIT,expected=package['sha256'])
    if package['kind']=='embedded':
        from gnm_shader_metadata import checked_elf
        entry,*_=checked_elf(raw,str(rel));entry_sha=hashlib.sha256(entry).hexdigest()
        need(evidence.get('entry')is None,'embedded offset domain')
    else:
        payload=dcx(raw);binder=bnd4(payload)
        entries=[e for e in binder['entries']if e['name']==evidence['entry']]
        need(len(entries)==1,'one exact shader entry required')
        entry=entry_data(payload,entries[0]);entry_sha=hashlib.sha256(entry).hexdigest()
    need(entry_sha==evidence.get('entry_sha256',evidence.get('source_sha256')),'static entry changed')
    records=[r for r in orb_metadata(entry)['records']if r.get('code_prefix_verified')and
             r['program_hash']==identity['program_hash']and r.get('code_sha256')==identity['code_sha256']and
             r['code_offset']==evidence['code_offset']]
    need(len(records)==1,'unique independent BinaryInfo code identity')
    record=records[0];need(0<record['code_bytes']<=MAX_CODE,'owned GCN byte budget')
    code=bytes(entry[record['code_offset']:record['code_offset']+record['code_bytes']])
    need(len(code)%4==0 and struct.unpack_from('<I',code)[0]==0xBEEB03FF and
         hashlib.sha256(code).hexdigest()==identity['code_sha256'],'owned GCN prefix/SHA')
    return code,dict(title_id=current['TITLE_ID'],app_ver=current['APP_VER'],sfo_sha256=current['sfo_sha256'],
        container_path=package['path'],container_sha256=reference['sha256'],entry_name=evidence['entry'],
        entry_sha256=entry_sha,code_offset=record['code_offset'],code_bytes=len(code),
        code_sha256=identity['code_sha256'],code_prefix_verified=True)

def module_constraints(meta):
    reasons=[]
    if meta['logical_stage']not in('compute','fragment','vertex'):reasons.append('tessellation-runtime-constants-not-captured')
    if meta['stage']=='geometry'or meta['session_pointer_in_geometry_runtime']:reasons.append('geometry-session-copy-not-owned')
    if meta['fetch']is not None:reasons.append('fetch-register-reads-not-captured')
    if meta['motion_vertex_cache_load_rejected']or meta.get('context',{}).get('fragment',{}).get('motion_vectors'):reasons.append('session-motion-addresses-not-captured')
    if meta['flattened_words']<16 or meta['srt_declared_flattened_words']!=meta['flattened_words']:reasons.append('flat-SRT-length-mismatch')
    if meta['walker_bytes']>256*1024 or meta['walker_bytes']%12:reasons.append('not-current-portable-walker-data')
    return reasons

def freeze_module(*,stage,profile_reference,layout,game_root,catalog,static_identity,evidence,output):
    output=Path(output).absolute();need(not output.exists(),'new private module input directory required');output.mkdir(mode=0o700)
    need(output==output.resolve(strict=True),'canonical module output')
    metadata,_=checked_bytes(Path(stage['metadata']['path']),expected=stage['metadata']['sha256'])
    spirv,_=checked_bytes(Path(stage['spirv']['path']),expected=stage['spirv']['sha256'])
    audit_owned_stage(stage,metadata,spirv,layout)
    meta,sections=metadata_sections(metadata,layout)
    need(not module_constraints(meta),'module dependencies not captured: '+','.join(module_constraints(meta)))
    need((static_identity['program_hash'],static_identity['hardware_stage'])==(stage['program_hash'],stage['hardware_stage']),'static/observed stage identity')
    code,provenance=source_code(game_root,catalog,static_identity,evidence)
    profile,_=checked_bytes(Path(profile_reference['path']),expected=profile_reference['sha256']);semantic_profile(profile,layout)
    spec=sections['specialization'];flat=sections['flattened_table_audit_only'];walk=sections['walker_forbidden']
    need(flat['bytes']==8+4*meta['flattened_words'],'flat vector extent')
    flat_data=metadata[flat['offset']+8:flat['offset']+flat['bytes']]
    walker_data=metadata[walk['offset']:walk['offset']+walk['bytes']]
    packet=dict(packet_schema=1,kind='owned-gcn-module-input',candidate_only=True,runtime_cache_import_allowed=False,
        native_info_srt_allowed=False,program_hash=stage['program_hash'],hardware_stage=stage['hardware_stage'],logical_stage=stage['logical_stage'],
        profile=new_bytes(output,'profile.bin',profile),code=new_bytes(output,'code.bin',code),
        spec=new_bytes(output,'spec.bin',metadata[spec['offset']:spec['offset']+spec['bytes']]),
        flat=new_bytes(output,'flat.bin',flat_data),user_data_words=16,flat_dwords=meta['flattened_words'],
        walker=new_bytes(output,'walker.bin',walker_data)if walker_data else None,walker_bytes=len(walker_data),
        program_base=dict(known=False,value=None,required_gate='reject-offset-by-program-base'),source_provenance=provenance,
        expected_spirv=new_bytes(output,'expected-spirv.bin',spirv),
        layout=dict(bindings_size=layout['Backend::Bindings.size'],runtime_size=layout['RuntimeInfo.size'],profile_size=layout['Profile.size'],serialization_abi_sha256=layout_abi_identity(layout)),
        constraints=dict(old_walker_execution_allowed=False,guest_dereference_allowed=False,final_runtime_import_allowed=False,
                         native_consumer_required=True,requires_regenerated_srt_equality=True,requires_current_profile_match=True),
        source_metadata=dict(sha256=stage['metadata']['sha256'],sections=sections,opaque_Info_bytes_forwarded=0))
    encoded=json.dumps(packet,sort_keys=True,separators=(',',':')).encode()+b'\n';need(len(encoded)<=256*1024,'module packet budget')
    new_bytes(output,'packet.json',encoded)
    return packet
