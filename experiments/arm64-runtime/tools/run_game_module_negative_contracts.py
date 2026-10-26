"""Bounded native packet rejection tests in new owned CPU-only fixture roots."""
from pathlib import Path
import argparse,collections,copy,hashlib,json,struct,time
from game_module_inputs import need
from game_pso_owned_audit import checked_bytes
from complete_pipeline_inputs import directory,file_sha
from run_game_module_contracts import canonical_packet,run_child,validate_call

def vectors(raw,layout):
 at=layout['Backend::Bindings.size']+layout['RuntimeInfo.size'];need(struct.unpack_from('<Q',raw,at)[0]==128,'source bitset');at+=8+128;need(struct.unpack_from('<Q',raw,at)[0]==0,'no source fetch');at+=8;out={}
 for k in('VsAttribSpecialization','BufferSpecialization','ImageSpecialization','FMaskSpecialization','SamplerSpecialization'):
  n=struct.unpack_from('<Q',raw,at)[0];need(n<=128,'source vector bound');out[k]=(at+8,n);at+=8+n*layout[k+'.size']
 need(at==len(raw),'source exact spec interval');return out

def mutate_blob(p,root,key,change):
 r=p[key];path=Path(r['path']);raw=bytearray(path.read_bytes());change(raw);path.write_bytes(raw);r.update(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
def setword(raw,offset,value):struct.pack_into('<I',raw,offset,value)
def setbyte(raw,offset,value):raw[offset]=value
def drop_vector(raw,layout,kind):
 at,n=vectors(raw,layout)[kind];need(n>0,'nonempty source vector');struct.pack_into('<Q',raw,at-8,n-1);del raw[at:at+layout[kind+'.size']]

def cases(packets,layout):
 cs=next(p for p in packets if p['hardware_stage']=='compute');fs=next(p for p in packets if p['hardware_stage']=='fragment');vs=next(p for p in packets if p['hardware_stage']=='vertex');r=layout['Backend::Bindings.size'];h=r+layout['RuntimeInfo.hw'];props=r+layout['RuntimeInfo.props'];bit=r+layout['RuntimeInfo.size']
 # Mutation closures update SHA/length so the intended semantic boundary is
 # tested, rather than being stopped by the outer digest guard first.
 rows=[]
 def packet(label,base,error,fn):rows.append((label,base,error,fn))
 def blob(label,base,error,key,fn):packet(label,base,error,lambda p,root:mutate_blob(p,root,key,fn))
 packet('policy-runtime-import',cs,'module packet policy',lambda p,r:p.update(runtime_cache_import_allowed=True))
 packet('unknown-program-base-not-gated',cs,'explicit unknown program base',lambda p,r:p['program_base'].update(known=True))
 packet('wrong-layout',cs,'actual native module layout',lambda p,r:p['layout'].update(runtime_size=1))
 packet('wrong-packet-stage',cs,'complete plain stage pair',lambda p,r:p.update(hardware_stage='fragment'))
 packet('false-source-identity',cs,'module exact code provenance',lambda p,r:p['source_provenance'].update(title_id='CUSA00000'))
 packet('false-code-provenance-sha',cs,'module exact code provenance',lambda p,r:p['source_provenance'].update(code_sha256='0'*64))
 packet('missing-input-sha',cs,'input SHA mismatch',lambda p,r:p['flat'].update(sha256='0'*64))
 packet('empty-input-sha',cs,'required file SHA256 identity',lambda p,r:p['flat'].update(sha256=''))
 blob('profile-raw-bool2',cs,'profile bool representation','profile',lambda b:setbyte(b,16+layout['Profile.support_int8'],2))
 blob('profile-limit',cs,'profile limits','profile',lambda b:setword(b,16+layout['Profile.sparse_page_shift'],64))
 blob('runtime-hw-enum',cs,'runtime plain stage discriminators','spec',lambda b:setword(b,r+layout['RuntimeInfo.hw_stage'],99))
 blob('runtime-sw-enum',cs,'runtime plain stage discriminators','spec',lambda b:setword(b,r+layout['RuntimeInfo.sw_stage'],99))
 # The canonical Properties prefix is four u32 FP enums followed by counts.
 # Assert the extracted count anchor before using that stable prefix.
 need(layout['RuntimeInfo::Properties.num_user_data']==16,'canonical FP prefix anchor')
 blob('runtime-FP-enum',cs,'runtime FP mode enum','spec',lambda b:setword(b,props,4))
 blob('runtime-UD-count',cs,'runtime register count','spec',lambda b:setword(b,props+layout['RuntimeInfo::Properties.num_user_data'],17))
 blob('runtime-VGPR-count',cs,'runtime register count','spec',lambda b:setword(b,props+layout['RuntimeInfo::Properties.num_allocated_vgprs'],257))
 blob('runtime-CS-zero-dimension',cs,'runtime CS dimension','spec',lambda b:setword(b,h+layout['HwComputeRuntimeInfo.workgroup_size'],0))
 # HwComputeRuntimeInfo has array<u32,3> immediately followed by array<bool,3>;
 # actual preceding-field extent comes from the canonical extracted ABI.
 blob('runtime-CS-tgid-raw-bool2',cs,'runtime raw boolean','spec',lambda b:setbyte(b,h+layout['HwComputeRuntimeInfo.workgroup_size']+layout['HwComputeRuntimeInfo.workgroup_size.bytes'],2))
 blob('runtime-FS-array33',fs,'runtime FS input count','spec',lambda b:setword(b,h+layout['HwFragmentRuntimeInfo.num_inputs'],33))
 blob('runtime-FS-samples3',fs,'runtime sample count','spec',lambda b:setbyte(b,h+layout['HwFragmentRuntimeInfo.num_samples'],3))
 blob('runtime-FS-motion-raw-bool2',fs,'runtime raw boolean','spec',lambda b:setbyte(b,h+layout['HwFragmentRuntimeInfo.motion_vectors'],2))
 blob('runtime-VS-motion-raw-bool2',vs,'runtime raw boolean','spec',lambda b:setbyte(b,h+layout['HwVertexRuntimeInfo.motion_vectors'],2))
 vs_sw=r+layout['RuntimeInfo.sw'];need(layout['SwVertexRuntimeInfo.step_rate_1']==4,'canonical SwVertex u32 prefix anchor')
 blob('invalid-vertex-SGPR-range',vs,'runtime vertex indirect SGPR range','spec',lambda b:struct.pack_into('<H',b,vs_sw+8,65535))
 blob('bitset-size-bomb',cs,'spec bitset shape','spec',lambda b:struct.pack_into('<Q',b,bit,2**63))
 blob('bitset-character',cs,'spec bitset value','spec',lambda b:setbyte(b,bit+8,ord('2')))
 blob('uncaptured-fetch',cs,'offline module fetch','spec',lambda b:struct.pack_into('<Q',b,bit+8+128,1))
 blob('vector-count129',cs,'specialization vector allocation bound','spec',lambda b:struct.pack_into('<Q',b,bit+8+128+8,129))
 blob('forbidden-spec-tail',cs,'exact spec-only interval','spec',lambda b:b.extend(bytes(8)))
 blob('flat-misalignment',cs,'complete owned flat/UD extent','flat',lambda b:b.pop())
 blob('different-observed-SPIRV',cs,'retranslated SPIR-V differs','expected_spirv',lambda b:b.extend(bytes(4)))
 for base in packets:
  v=vectors(Path(base['spec']['path']).read_bytes(),layout);at,n=v['ImageSpecialization']
  if n:
   blob('image-raw-bool2',base,'image raw boolean','spec',lambda b,at=at:setbyte(b,at+layout['ImageSpecialization.is_storage'],2))
   blob('image-type-enum',base,'image type enum','spec',lambda b,at=at:struct.pack_into('<Q',b,at+layout['ImageSpecialization.type'],1))
   blob('image-conversion-enum',base,'image conversion/binding bound','spec',lambda b,at=at:setword(b,at+layout['ImageSpecialization.num_conversion'],8));break
 for kind in('BufferSpecialization','ImageSpecialization','SamplerSpecialization'):
  for base in packets:
   if base['hardware_stage']=='export':continue
   if vectors(Path(base['spec']['path']).read_bytes(),layout)[kind][1]:
    blob('short-'+kind,base,'specialization vector shape','spec',lambda b,kind=kind:drop_vector(b,layout,kind));break
 return rows

def clone_packet(p,root):
 root.mkdir();p=copy.deepcopy(p)
 for key in('code','profile','spec','flat','walker','expected_spirv'):
  ref=p[key]
  if ref is not None:
   data,_=checked_bytes(Path(ref['path']),expected=ref['sha256']);dest=root/ref['name'];dest.write_bytes(data);ref['path']=str(dest)
 return p

def main():
 ap=argparse.ArgumentParser(description=__doc__)
 for key in('project','build','batch','catalog','layout','output'):ap.add_argument('--'+key,type=Path,required=True)
 ap.add_argument('--case',action='append',dest='selected_cases')
 a=ap.parse_args();P=directory(a.project);need(a.output.is_relative_to(P/'evidence')and not a.output.exists(),'fresh negative evidence');a.output.mkdir(mode=0o700);directory(a.output)
 build=json.loads(a.build.read_text());binary=Path(build['binary']);checked_bytes(binary,limit=64*1024*1024,expected=build['binary_sha256']);checked_bytes(Path(build['pinned_library']),limit=256*1024*1024,expected=build['pinned_library_sha256']);catalog=json.loads(a.catalog.read_text());batch=json.loads(a.batch.read_text());layout=json.loads(a.layout.read_text());packets=[canonical_packet(x['packet'],catalog)for x in batch['packets']]
 from qa_build_guard import require_no_games
 from native_observer import NativeObserver
 from qa_memory_policy import MemoryPolicy
 require_no_games();observer=NativeObserver();before=observer.system();need(before['pressure']!=4 and not before['locked'],'safe CPU entry');policy=MemoryPolicy(before);start=time.monotonic();state=P/'state'/('game-module-negatives-'+str(time.time_ns()));state.mkdir(mode=0o700);results=[]
 def check():
  reason=policy.observe(time.monotonic()-start,observer.system())
  if reason:raise RuntimeError(reason)
 try:
  for name,base,error,change in cases(packets,layout):
   if a.selected_cases and name not in a.selected_cases:continue
   check();case=a.output/name;case.mkdir();s=state/name;s.mkdir();p=clone_packet(base,case/'input');change(p,case/'input');path=case/'input/packet.json';path.write_text(json.dumps(p));exit_code,result,seconds=run_child(binary,path,case/'output',case,s,check);diagnostic=base['expected_spirv']if name=='different-observed-SPIRV'and build.get('diagnostic_only')else None;validate_call(exit_code,result,case/'output',p,True,rejected_diagnostic=diagnostic);need(error in result['error'],'intended native gate not reached: '+name)
   results.append(dict(name=name,exit=exit_code,error=result['error'],seconds=seconds));print(name,'rejected',result['error'],flush=True)
  summary=dict(schema=1,status='passed',cases=results,count=len(results),diagnostic_consumer=bool(build.get('diagnostic_only')),runtime_preparer_eligible=False,build_sha256=file_sha(a.build),before=before,after=observer.system(),GPU_started=False,game_started=False,formal_data_modified=False,runtime_cache_import_allowed=False)
  (a.output/'result.json').write_text(json.dumps(summary,sort_keys=True,indent=2)+'\n')
 finally:observer.close()
 print(json.dumps(dict(status=summary['status'],count=len(results),result=str(a.output/'result.json'))))
if __name__=='__main__':main()
