"""Pure audit of already-owned bytes; no file reopen or native deserialization.

The existing decoder is authoritative. Section arithmetic only locates its
already-decoded fields, and is checked against its existing spec hash.
"""
import hashlib,struct
from pathlib import Path
import json,os,stat
from decode_shader_cache import decode_meta,decode_key,semantic_profile
class Refused(ValueError):pass
def need(value,message):
    if not value:raise Refused(message)
def checked_bytes(path, *, limit=1024*1024, expected=None):
    path=Path(path);need(path.is_absolute() and path==path.resolve(strict=True),'canonical nonlinked audit source')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        before=os.fstat(fd);need(stat.S_ISREG(before.st_mode) and before.st_uid==os.getuid() and 0<before.st_size<=limit,'owned bounded audit source')
        data=bytearray()
        while len(data)<before.st_size:
            part=os.read(fd,before.st_size-len(data));need(bool(part),'short audit source');data.extend(part)
        after=os.fstat(fd);identity=lambda x:(x.st_dev,x.st_ino,x.st_size,x.st_mtime_ns)
        need(identity(before)==identity(after),'audit source changed during read');data=bytes(data)
        sha=hashlib.sha256(data).hexdigest()
        if expected is not None:need(sha==expected,'canonical layout SHA mismatch')
        return data,dict(path=str(path),bytes=len(data),sha256=sha)
    finally:os.close(fd)


def verify_owned(blob, reference):
    need(isinstance(blob,bytes),'owned immutable bytes required')
    need(0<len(blob)<=1024*1024 and len(blob)==reference['bytes'],'owned input byte bound/size')
    need(hashlib.sha256(blob).hexdigest()==reference['sha256'],'owned input SHA changed')

def metadata_sections(blob, layout):
    meta=decode_meta(blob,layout)
    at=24+layout['Backend::Bindings.size']+layout['RuntimeInfo.size']+8+128+8
    if meta['fetch'] is not None:
        at+=4+2+8+len(meta['fetch']['attributes'])*layout['Gcn::VertexAttribute.size']
    for name,typename in (('vertex_attributes','VsAttribSpecialization'),('buffers','BufferSpecialization'),
                           ('images','ImageSpecialization'),('fmasks','FMaskSpecialization'),('samplers','SamplerSpecialization')):
        at+=8+len(meta[name])*layout[typename+'.size']
    need(24<=at<=len(blob),'specialization range')
    need(hashlib.sha256(blob[24:at]).hexdigest()==meta['serialized_spec_sha256'],'derived specialization range disagrees with canonical decoder')
    info_at=at
    flat_at=info_at+layout['InfoPersistent.size']
    srt_at=flat_at+8+4*meta['flattened_words']
    walker_at=srt_at+layout['PersistentSrtInfo.size']
    end=walker_at+meta['walker_bytes']
    need(end+meta['canonical_writer_zero_tail_bytes']==len(blob),'metadata section extent')
    return meta,dict(
      metadata_header=dict(offset=0,bytes=24),
      specialization=dict(offset=24,bytes=at-24,sha256=meta['serialized_spec_sha256'],native_spec_only_reader_allowed=True),
      info_persistent_audit_only=dict(offset=info_at,bytes=layout['InfoPersistent.size'],native_deserialize_allowed=False),
      flattened_table_audit_only=dict(offset=flat_at,bytes=8+4*meta['flattened_words'],native_deserialize_allowed=False),
      srt_opaque_forbidden=dict(offset=srt_at,bytes=layout['PersistentSrtInfo.size'],native_deserialize_allowed=False),
      walker_forbidden=dict(offset=walker_at,bytes=meta['walker_bytes'],sha256=meta['walker_sha256'],register_or_execute_allowed=False),
      canonical_zero_tail=dict(offset=end,bytes=meta['canonical_writer_zero_tail_bytes']))

def audit_owned_stage(stage, metadata, spirv, layout):
    verify_owned(metadata,stage['metadata']);verify_owned(spirv,stage['spirv'])
    meta,sections=metadata_sections(metadata,layout)
    need((meta['program_hash'],meta['permutation'],meta['permutation_hash'],meta['logical_stage'],meta['stage'])==
         (stage['program_hash'],stage['cache_local_permutation'],stage['permutation_hash'],stage['logical_stage'],stage['hardware_stage']),
         'owned metadata stage identity')
    need(meta['serialized_spec_sha256']==stage['serialized_spec_sha256'],'owned serialized spec identity')
    need(not meta['motion_vertex_cache_load_rejected'] and not meta['session_pointer_in_geometry_runtime'],
         'existing motion/session metadata exclusion')
    need(meta['logical_stage']!='geometry','geometry variant not admitted')
    need(len(spirv)>=20 and len(spirv)%4==0 and struct.unpack_from('<I',spirv)[0]==0x07230203,'owned SPIR-V header')
    return dict(program_hash=meta['program_hash'],permutation=meta['permutation'],permutation_hash=meta['permutation_hash'],
                logical_stage=meta['logical_stage'],hardware_stage=meta['stage'],sections=sections,
                no_native_info_srt_deserialize=True,walker_registered_or_executed=False)

def audit_owned_key_profile(unit,key,profile,layout):
    verify_owned(key,unit['key']['reference']);verify_owned(profile,unit['profile']['reference'])
    decoded=decode_key(key,layout);p=semantic_profile(profile,layout)
    need(p['semantic_sha256']==unit['profile']['semantic_sha256'],'owned Profile semantic identity')
    need(decoded=={k:v for k,v in unit['key']['decoded'].items() if k in decoded},'owned key decoded identity')
    expected=decoded['stages'] if decoded['kind']=='graphics' else {'compute':decoded['stage_permutation_hashes'][0]}
    expected={k:v for k,v in expected.items() if int(v,16)}
    need(expected=={s['logical_stage']:s['permutation_hash'] for s in unit['stages']} and len(expected)==len(unit['stages']),
         'owned complete key/stage graph')
    return dict(key_kind=decoded['kind'],stage_references=expected,profile_semantic_sha256=p['semantic_sha256'],
                key_native_non_srt_reader_allowed=True,native_info_srt_deserialize_allowed=False)

def load_canonical_layout(path, source, expected_sha256):
    data, ref = checked_bytes(Path(path).resolve(), limit=4 * 1024 * 1024, expected=expected_sha256)
    layout = json.loads(data)
    need(layout['schema'] == 1 and layout['pointer_bytes'] == 8 and layout['little_endian'], 'layout domain')
    receipts = []
    # The canonical serialization source predates the reviewed clear-selection
    # fix; normalize only this exact non-Writer delta, never a layout change.
    extra = '''    const auto clear_selection = [&] {
        sel.infos.fill(nullptr);
        sel.modules.fill({});
        sel.fetch_shader.reset();
    };
    clear_selection();
    SCOPE_EXIT { clear_selection(); };
'''
    for relative, want in layout['source_sha256'].items():
        raw, current = checked_bytes(Path(source).resolve() / relative, limit=4 * 1024 * 1024)
        normalized = False
        if current['sha256'] != want and relative == 'video_core/pipeline_cache_core_serialization.cpp':
            text = raw.decode()
            need(text.count(extra) == 2, 'unreviewed serialization delta')
            raw = text.replace('#include "common/scope_exit.h"\n', '').replace(extra, '').encode()
            normalized = True
        need(hashlib.sha256(raw).hexdigest() == want, 'canonical layout source changed: ' + relative)
        receipts.append(dict(current, relative=relative, canonical_sha256=want, normalized=normalized))
    return layout
