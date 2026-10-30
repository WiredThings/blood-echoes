"""Bounded metadata decode using compiler-derived canonical layouts.

No native cache deserializers: PersistentSrtInfo::Deserialize registers code.
This reader only consumes bytes and never constructs a GPU, invokes JIT or follows pointers.
"""
from pathlib import Path
import argparse,collections,hashlib,json,struct,time
from game_shader_catalog import CatalogError,require,span
HW=('fragment','vertex','geometry','export','hull','local','compute')
SW=('fragment','tessellation_control','tessellation_eval','vertex','geometry','compute')
class Reader:
 def __init__(self,data):require(len(data)<=1024*1024,'blob budget');self.data=data;self.offset=0;self.serial_capacity=0
 def take(self,n):
  b=span(self.data,self.offset,n)
  if self.offset+n>=self.serial_capacity:self.serial_capacity+=n
  self.offset+=n;return bytes(b)
 def reserve(self,n):require(0<=n<=1024*1024,'reserve budget');self.serial_capacity+=n
 def u32(self):return struct.unpack('<I',self.take(4))[0]
 def u64(self):return struct.unpack('<Q',self.take(8))[0]
 def vector(self,size,limit=128):
  count=self.u64();require(count<=limit and count<=((len(self.data)-self.offset)//size),'container span/count');return[self.take(size)for _ in range(count)]
 def end(self):
  require(self.serial_capacity==len(self.data),'canonical Writer allocation extent mismatch')
  require(not any(self.data[self.offset:]),'nonzero serialized tail')
def scalar(data,layout,type_name,field):
 at=layout[type_name+'.'+field];size=layout[type_name+'.'+field+'.bytes'];require(0<size<=8,'scalar layout width');return int.from_bytes(span(data,at,size),'little')
def fields(data,l,t,names):return{n:scalar(data,l,t,n)for n in names}
def semantic_profile(data,l):
 require(len(data)>=16 and struct.unpack('<4I',data[:16])==(0x42425043,10,7,5),'profile layout/version')
 body=data[16:];size=l['Profile.size'];feature=False;flags=0;prefix=16
 if len(body)>=4 and struct.unpack_from('<I',body)[0]==0x31504642:
  require(len(body)==20+size,'profile feature envelope extent')
  magic,version,width,flags,viewport=struct.unpack_from('<5I',body)
  require(version==1 and width==size and size==80 and flags&~3==0,'profile feature envelope version/bits')
  require(viewport<=65536 and struct.unpack_from('<I',body,20)[0]==0x31504642,'profile feature payload marker/viewport')
  feature=True;body=bytearray(body[20:]);struct.pack_into('<I',body,0,viewport);prefix+=20
  require('Profile.relaxed_vertex_float'in l and l['Profile.relaxed_vertex_float']==74 and l['Profile.two_level_bda']==73,'complete feature Profile layout')
 else:require(len(body)==size or(size==80 and len(body)==72),'legacy profile extent')
 # Legacy byte74 is not a feature: the old format never declared that field.
 # Zero-extend the two old omitted tail bools, not arbitrary omitted inputs.
 actual_size=len(body);raw=body+bytes(size-len(body));values={};covered=set()
 for field in l['profile_fields']:
  name=field['name'];legacy_feature=name=='relaxed_vertex_float'and not feature
  value=0 if legacy_feature else scalar(raw,l,'Profile',name)
  if field['type']=='bool':require(value in(0,1),'profile bool')
  values[name]=value;at=l['Profile.'+name]
  if not legacy_feature:covered.update(range(at,at+l['Profile.'+name+'.bytes']))
 if feature:require(flags==(values['two_level_bda']|(values['relaxed_vertex_float']<<1)),'profile feature/raw disagreement')
 encoded=json.dumps(values,sort_keys=True,separators=(',',':')).encode()
 return dict(fields=values,semantic_sha256=hashlib.sha256(encoded).hexdigest(),raw_sha256=hashlib.sha256(data).hexdigest(),ignored_padding_offsets=[prefix+i for i in range(actual_size)if i not in covered],qualification='Named fields follow the explicit current feature envelope or the legacy field set; not complete renderer/compiler/driver binary compatibility.')

def decode_meta(data,l):
 r=Reader(data);require((r.u32(),r.u32())==(7,10),'metadata versions');perm_hash=r.u64();index=r.u64();bindings=r.take(l['Backend::Bindings.size']);runtime=r.take(l['RuntimeInfo.size']);bit_count=r.u64();require(bit_count==128,'resource bitset shape');bits=r.take(bit_count);require(set(bits)<=set(b'01'),'invalid resource bitset')
 fetch_size=r.u64();require(fetch_size in(0,l['Gcn::FetchShaderData.size']),'fetch layout mismatch');fetch=None
 if fetch_size:
  count=struct.unpack('<Q',span(r.data,r.offset+6,8))[0];require(count<=128,'fetch attribute budget');r.reserve(6+count*l['Gcn::VertexAttribute.size'])
  size=r.u32();vertex_offset,instance_offset=struct.unpack('<bb',r.take(2));attributes=r.vector(l['Gcn::VertexAttribute.size']);fetch=dict(code_size=size,vertex_sgpr_offset=vertex_offset,instance_sgpr_offset=instance_offset,attributes=[dict(raw_hex=a.hex())for a in attributes])
 arrays={name:r.vector(l[t+'.size'])for name,t in[('vertex_attributes','VsAttribSpecialization'),('buffers','BufferSpecialization'),('images','ImageSpecialization'),('fmasks','FMaskSpecialization'),('samplers','SamplerSpecialization')]};spec_end=r.offset
 info=r.take(l['InfoPersistent.size']);flat=r.vector(4,16384);srt=r.take(l['PersistentSrtInfo.size']);walker_size=scalar(srt,l,'PersistentSrtInfo','walker_func_size');require(walker_size<=1024*1024,'walker byte budget');walker=r.take(walker_size);r.end();writer_tail=len(data)-r.offset
 hw=scalar(info,l,'InfoPersistent','hw_stage');sw=scalar(info,l,'InfoPersistent','sw_stage');require(hw<len(HW)and sw<len(SW),'stage enum');require((scalar(runtime,l,'RuntimeInfo','hw_stage'),scalar(runtime,l,'RuntimeInfo','sw_stage'))==(hw,sw),'runtime/info stage mismatch')
 base_hw=l['RuntimeInfo.hw'];base_sw=l['RuntimeInfo.sw'];motion=False;session_pointer=False;context={}
 if hw==1:
  value=scalar(runtime[base_hw:],l,'HwVertexRuntimeInfo','motion_vectors');require(value in(0,1),'vertex motion bool');motion=bool(value);context['step_rates']=fields(runtime[base_sw:],l,'SwVertexRuntimeInfo',['step_rate_0','step_rate_1'])
 if hw==0:
  fragment=fields(runtime[base_hw:],l,'HwFragmentRuntimeInfo',['motion_vectors','num_inputs','num_samples']);require(fragment['motion_vectors']in(0,1)and fragment['num_inputs']<=32,'fragment shape');context['fragment']=fragment
 if hw==2:
  at=base_hw+l['HwGeometryRuntimeInfo.vs_copy'];pointer,length=struct.unpack('<QQ',runtime[at:at+16]);session_pointer=bool(pointer);context['geometry_copy']=dict(serialized_pointer_nonzero=session_pointer,words=length,hash=f"{scalar(runtime[base_hw:],l,'HwGeometryRuntimeInfo','vs_copy_hash'):016x}",pointer_followed=False)
 if hw==6:
  at=base_hw+l['HwComputeRuntimeInfo.workgroup_size'];context['workgroup_size']=list(struct.unpack('<3I',runtime[at:at+12]))
 buffers=[dict(packed_word=struct.unpack('<I',b[:4])[0],**fields(b,l,'BufferSpecialization',['dst_select','num_conversion']),raw_hex=b.hex())for b in arrays['buffers']]
 images=[]
 for b in arrays['images']:
  image=fields(b,l,'ImageSpecialization',['type','is_integer','is_storage','is_cube','is_srgb','dst_select','num_conversion','num_bindings']);require(all(image[n]in(0,1)for n in['is_integer','is_storage','is_cube','is_srgb']),'image bool');images.append(dict(image,raw_hex=b.hex()))
 return dict(program_hash=f"{scalar(info,l,'InfoPersistent','pgm_hash'):016x}",permutation=index,permutation_hash=f'{perm_hash:016x}',stage=HW[hw],logical_stage=SW[sw],bindings=fields(bindings,l,'Backend::Bindings',['unified','buffer','user_data']),runtime_properties=fields(runtime[l['RuntimeInfo.props']:],l,'RuntimeInfo::Properties',['num_user_data','num_input_vgprs','num_allocated_vgprs']),runtime_raw_hex=runtime.hex(),resource_bitset=bits.decode(),fetch=fetch,vertex_attributes=[dict(raw_hex=b.hex(),**fields(b,l,'VsAttribSpecialization',['divisor','num_class','dst_select']))for b in arrays['vertex_attributes']],buffers=buffers,images=images,fmasks=[b.hex()for b in arrays['fmasks']],samplers=[b.hex()for b in arrays['samplers']],context=context,serialized_spec_sha256=hashlib.sha256(data[24:spec_end]).hexdigest(),motion_vertex_cache_load_rejected=motion,session_pointer_in_geometry_runtime=session_pointer,flattened_words=len(flat),srt_declared_flattened_words=scalar(srt,l,'PersistentSrtInfo','flattened_bufsize_dw'),walker_bytes=walker_size,walker_sha256=hashlib.sha256(walker).hexdigest(),walker_registered_or_executed=False,canonical_writer_zero_tail_bytes=writer_tail)
def decode_key(data,l):
 r=Reader(data);require(r.u32()==5,'pipeline key version');kind=r.u32();require(kind in(0,1),'pipeline key kind')
 if kind==1:
  key=r.u64();r.end();return dict(kind='compute',stage_permutation_hashes=[f'{key:016x}'])
 key=r.take(l['GraphicsPipelineKey.size']);hashes=struct.unpack('<'+'Q'*l['MaxShaderStages'],key[l['GraphicsPipelineKey.stage_hashes']:l['GraphicsPipelineKey.stage_hashes']+8*l['MaxShaderStages']]);count=r.u32();require(count<=l['MaxVertexBufferCount'],'static vertex attribute count');attributes=[dict(zip(('location','per_instance','divisor'),struct.unpack('<3I',r.take(12))))for _ in range(count)];require(all(a['per_instance']in(0,1)for a in attributes),'instance rate bool');r.end();colors=scalar(key,l,'GraphicsPipelineKey','num_color_attachments');require(colors<=l['MaxColorBuffers'],'color attachment count')
 formats=struct.unpack('<'+'I'*l['MaxVertexBufferCount'],key[l['GraphicsPipelineKey.vertex_buffer_formats']:l['GraphicsPipelineKey.vertex_buffer_formats']+4*l['MaxVertexBufferCount']]);return dict(kind='graphics',stage_permutation_hashes=[f'{h:016x}'for h in hashes if h],stages=dict(zip(SW,[f'{h:016x}'for h in hashes])),vertex_formats=list(formats),vertex_attributes=attributes,color_attachments=colors,motion_vectors=scalar(key,l,'GraphicsPipelineKey','motion_vectors'),patch_control_points=scalar(key,l,'GraphicsPipelineKey','patch_control_points'),num_samples=scalar(key,l,'GraphicsPipelineKey','num_samples'),depth_samples=scalar(key,l,'GraphicsPipelineKey','depth_samples'),raw_key_sha256=hashlib.sha256(key).hexdigest())

def decode_inventory(inventory,l):
 cache=Path(inventory['cache']);require(cache==cache.resolve()and cache.is_dir(),'canonical cache directory required');metas={};pipelines=[];errors=[]
 def blob(row):
  require(Path(row['name']).name==row['name'],'cache basename required');path=cache/row['name'];require(path.is_file()and not path.is_symlink()and path.stat().st_size<=1024*1024,'regular bounded cache input');data=path.read_bytes();require(hashlib.sha256(data).hexdigest()==row['sha256'],'inventory blob changed');return data
 profile=semantic_profile(blob(inventory['profile']),l)
 for pair in inventory['paired_specializations']:
  row=pair['meta']
  try:
   blob(pair['spirv']);meta=decode_meta(blob(row),l);require(meta['program_hash']==pair['program_hash']and meta['permutation']==pair['permutation'],'decoded program/index mismatch');meta['name']=row['name'];metas[meta['permutation_hash']]=meta
  except(ValueError,KeyError,struct.error)as error:errors.append(dict(name=row['name'],error=str(error)))
 for row in inventory['pipeline_keys']:
  try:
   key=decode_key(blob(row),l);key['name']=row['name'];key['missing_metadata']=[h for h in key['stage_permutation_hashes']if h not in metas];key['motion_vertex_cache_rejected_refs']=[h for h in key['stage_permutation_hashes']if h in metas and metas[h]['motion_vertex_cache_load_rejected']]
   expected=key['stages'].items()if key['kind']=='graphics'else[('compute',key['stage_permutation_hashes'][0])]
   key['stage_metadata_mismatches']=[dict(expected=stage,actual=metas[h]['logical_stage'],permutation_hash=h)for stage,h in expected if int(h,16)and h in metas and metas[h]['logical_stage']!=stage];pipelines.append(key)
  except(ValueError,KeyError,struct.error)as error:errors.append(dict(name=row['name'],error=str(error)))
 referenced={h for key in pipelines for h in key['stage_permutation_hashes']};return dict(schema=1,profile=profile,summary=dict(decoded_metadata=len(metas),metadata_by_stage=dict(collections.Counter(m['stage']for m in metas.values())),decoded_pipeline_keys=len(pipelines),decode_errors=len(errors),motion_vertex_cache_rejections=sum(m['motion_vertex_cache_load_rejected']for m in metas.values()),geometry_runtime_with_session_pointer=sum(m['session_pointer_in_geometry_runtime']for m in metas.values()),pipelines_missing_metadata=sum(bool(p['missing_metadata'])for p in pipelines),pipelines_with_stage_mismatch=sum(bool(p['stage_metadata_mismatches'])for p in pipelines),pipelines_with_motion_vertex_cache_rejection=sum(bool(p['motion_vertex_cache_rejected_refs'])for p in pipelines),metadata_not_referenced_by_existing_pipeline_keys=len(metas.keys()-referenced)),metadata=list(metas.values()),pipeline_keys=pipelines,errors=errors,qualification='Read-only compiler-layout-guided decode, not renderer/PSO creation or full-game coverage. Serialized pointers never followed; walker bytes hashed, never registered/executed. Raw serialized spec hashes are cache-local identities, not normalized cross-session semantic keys.')
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--inventory',type=Path,required=True);ap.add_argument('--layout',type=Path);a=ap.parse_args();p=Path(__file__).resolve().parents[1];from qa_build_guard import require_no_games;require_no_games();lpath=a.layout or Path(json.loads((p/'evidence/metadata-layout-current.json').read_text())['layout']);l=json.loads(lpath.read_text());require(l['schema']==1 and l['pointer_bytes']==8 and l['little_endian'],'unsupported native layout')
 for path,digest in l['source_sha256'].items():require(hashlib.sha256((p/'vendor/metal_api/shadps4'/path).read_bytes()).hexdigest()==digest,'layout source changed: '+path)
 inv=json.loads(a.inventory.read_text());require(Path(inv['cache']).resolve().is_relative_to(p/'state'),'private QA cache only');e=p/'evidence'/('decoded-shader-cache-'+str(time.time_ns()));e.mkdir();out=decode_inventory(inv,l);out['layout']=str(lpath);out['source_inventory']=str(a.inventory.resolve());(e/'decoded.json').write_text(json.dumps(out,indent=2)+'\n');print(e);print(json.dumps(out['summary'],indent=2));raise SystemExit(bool(out['errors'])or bool(out['summary']['pipelines_with_stage_mismatch']))
