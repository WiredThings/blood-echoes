"""Bounded, metadata-only fixed-title resource census. No extraction or GPU init.
Independent FLVER2/BHF4 metadata reader; format references in REFERENCES.md.
Imports the existing verified DCX/BND4/OrbShdr readers and original SELF loader.
"""
from pathlib import Path
import argparse,collections,hashlib,json,os,struct,sys,time,zlib,resource,importlib.util
LIMIT=128*1024*1024
class ScanError(ValueError):pass
class MetadataBudgetError(ScanError):
 def __init__(self,field,used,requested,limit):
  self.details=dict(kind='metadata-budget-exceeded',field=field,used=used,requested=requested,limit=limit)
  super().__init__(f'metadata budget exceeded: {field}: {used}+{requested}>{limit}')
MODEL_LIMITS=dict(records=32768,layout_members=16384,mesh_vertex_refs=8192,mesh_face_refs=8192,expanded_members=65536,string_bytes=1024*1024,estimated_bytes=32*1024*1024)
BHF_LIMITS=dict(records=100000,string_bytes=16*1024*1024,estimated_bytes=64*1024*1024)
class MetadataBudget:
 def __init__(self,limits):
  need(all(type(n)is int and n>=0 for n in limits.values()),'invalid metadata budget')
  self.limits=dict(limits);self.used=collections.Counter()
 def charge(self,key,count):
  need(type(count)is int and count>=0 and key in self.limits,'invalid metadata accounting')
  before=self.used[key]
  if count>self.limits[key]-before:raise MetadataBudgetError(key,before,count,self.limits[key])
  self.used[key]+=count
 def snapshot(self):return dict(used=dict(self.used),limits=self.limits)
def need(v,m):
 if not v:raise ScanError(m)
class StringPool:
 def __init__(self,data,budget):self.data=data;self.budget=budget;self.cache={}
 def get(self,offset,utf=True):
  key=(offset,utf)
  if key not in self.cache:
   value=text(self.data,offset,utf);self.cache[key]=(value,len(value.encode('utf8')))
  value,size=self.cache[key]
  # Charge each logical reference even when the physical string is shared.
  self.budget.charge('string_bytes',size);self.budget.charge('estimated_bytes',size*2+64)
  return value
def project_root(explicit=None):
 if explicit is not None:candidates=[Path(explicit).resolve(strict=True)]
 else:candidates=[Path(__file__).resolve().parents[1],*Path(__file__).resolve().parents]
 for root in candidates:
  if(root/'tools/game_shader_catalog.py').is_file()and(root/'vendor/blood_echoes/loader/prepare.py').is_file():return root
 raise ScanError('vendored tools/game_shader_catalog.py and vendor/blood_echoes/loader/prepare.py required; select --prototype')
def readat(d,o,n):
 need(isinstance(o,int) and isinstance(n,int) and 0<=o<=len(d) and 0<=n<=len(d)-o,'span out of bounds')
 return memoryview(d)[o:o+n]
def u(fmt,d,o):return struct.unpack(fmt,readat(d,o,struct.calcsize(fmt)))
def sha(d):return hashlib.sha256(d).hexdigest()
def text(d,o,utf=True):
 need(0<=o<len(d),'string offset');n=2 if utf else 1;end=o
 while end+n<=len(d) and end-o<=8192 and bytes(d[end:end+n])!=b'\0'*n:end+=n
 need(end+n<=len(d) and end-o<=8192,'string termination')
 return bytes(d[o:end]).decode('utf-16-le'if utf else'shift_jis','strict')
def digest_path(p):
 h=hashlib.sha256()
 with p.open('rb')as s:
  for b in iter(lambda:s.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def stable_stat(s):return(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns)
def ids(d,o,n,maxn):
 need(0<=n<=maxn,'index count');return list(u('<'+'i'*n,d,o))if n else[]
TYPE_SIZE={0:4,1:8,2:12,3:16,16:4,17:4,18:4,19:4,20:4,21:4,22:8,23:4,24:8,26:8,45:4,46:8,47:4,240:1}
def flver_metadata(d,budget=None):
 budget=MetadataBudget(MODEL_LIMITS)if budget is None else budget;need(isinstance(budget,MetadataBudget),'metadata budget object required');strings_pool=StringPool(d,budget)
 need(len(d)>=128 and bytes(d[:8])==b'FLVER\0L\0','FLVER2 little endian required')
 version,off,length,nd,nm,nb,nn,nv=u('<8I',d,8)
 need(version in(0x20013,0x20014),'FLVER fixed-title version')
 need(128<=off<=len(d) and length<=len(d)-off,'FLVER data section')
 for n in(nd,nm,nb,nn,nv):need(n<=100000,'FLVER table count')
 index_size,unicode=d[72],d[73];need(index_size in(0,8,16,32)and unicode in(0,1),'FLVER flags')
 nf,nl,nt=u('<3I',d,80)
 for n in(nf,nl,nt):need(n<=100000,'FLVER extra table count')
 mats_at=128+nd*64;meshes_at=mats_at+nm*32+nb*128;faces_at=meshes_at+nn*48;vbs_at=faces_at+nf*32;layouts_at=vbs_at+nv*32
 need(layouts_at+nl*16+nt*32<=off,'FLVER tables overlap data')
 budget.charge('records',nm+nn+nv+nf+nl);budget.charge('estimated_bytes',(nm+nn+nv+nf+nl)*384)
 mats=[]
 for i in range(nm):
  name,mtd,ntex,texindex,strings,gx,idx,zero=u('<8i',d,mats_at+32*i)
  need(zero==0 and 0<=ntex<=nt and 0<=texindex<=nt and ntex<=nt-texindex,'material texture bounds')
  need(gx==0 or 128<=gx<off,'material GX offset')
  mats.append(dict(name=strings_pool.get(name,bool(unicode)),mtd=strings_pool.get(mtd,bool(unicode)),texture_count=ntex,texture_index=texindex,gx_offset=gx,index=idx))
 layouts=[];layout_cache={}
 for i in range(nl):
  n,z1,z2,at=u('<4i',d,layouts_at+i*16);need(0<=n<=128 and not(z1 or z2),'layout header')
  budget.charge('layout_members',n);budget.charge('estimated_bytes',n*160)
  key=(at,n)
  if key in layout_cache:
   layouts.append(layout_cache[key]);continue
  members=[];width=0
  for j in range(n):
   stream,byteoff,kind,semantic,index=u('<5I',d,at+j*20)
   need(kind in TYPE_SIZE and byteoff==width,'layout member size/offset')
   members.append(dict(stream=stream,offset=byteoff,type=kind,semantic=semantic,index=index));width+=TYPE_SIZE[kind]
  item=dict(stride=width,members=members);layout_cache[key]=item;layouts.append(item)
 vbs=[]
 for i in range(nv):
  stream,layout,stride,count,z1,z2,size,start=u('<8I',d,vbs_at+i*32)
  need(layout<nl and not(z1 or z2)and stride==layouts[layout]['stride'],'vertex buffer layout')
  need(start<=length and size<=length-start,'vertex buffer extent')
  vbs.append(dict(stream=stream,layout=layout,stride=stride,vertex_count=count))
 faces=[]
 for i in range(nf):
  at=faces_at+i*32;flags,strip,cull,unknown,count,start,size,z1,bits,z2=u('<IBBh6I',d,at)
  need(strip in(0,1)and cull in(0,1)and not(z1 or z2)and bits in(0,16,32),'faceset flags')
  need(start<=length and size<=length-start,'faceset extent')
  faces.append(dict(flags=flags,triangle_strip=bool(strip),cull_backfaces=bool(cull),index_bits=bits or index_size,index_count=count))
 meshes=[]
 for i in range(nn):
  values=u('<12i',d,meshes_at+i*48);dynamic,mat,z1,z2,node,bones,bbox,boneoff,nfs,fsoff,nvb,vboff=values
  need(dynamic in(0,1)and mat>=0 and mat<nm and not(z1 or z2),'mesh material')
  need(nfs>=0 and nvb>=0,'negative mesh reference count')
  budget.charge('mesh_face_refs',nfs);budget.charge('mesh_vertex_refs',nvb);budget.charge('estimated_bytes',nfs*160+nvb*96)
  fs=ids(d,fsoff,nfs,nf);vb=ids(d,vboff,nvb,nv);need(all(0<=x<nf for x in fs)and all(0<=x<nv for x in vb),'mesh table links')
  layouts_for_mesh=[]
  for x in vb:
   layout=layouts[vbs[x]['layout']];budget.charge('expanded_members',len(layout['members']));budget.charge('estimated_bytes',len(layout['members'])*160)
   layouts_for_mesh.append(dict(stream=vbs[x]['stream'],stride=vbs[x]['stride'],layout=layout))
  meshes.append(dict(material_index=mat,dynamic=bool(dynamic),layouts=layouts_for_mesh,faces=[faces[x]for x in fs]))
 return dict(version=version,materials=mats,meshes=meshes,layout_count=nl,vertex_buffer_count=nv,face_set_count=nf,metadata_usage=budget.snapshot())
def split_bhf(header,body_size,budget=None):
 budget=MetadataBudget(BHF_LIMITS)if budget is None else budget;need(isinstance(budget,MetadataBudget),'metadata budget object required');strings_pool=StringPool(header,budget)
 need(64<=len(header)<=LIMIT and bytes(header[:4])==b'BHF4','BHF4 header')
 need(header[9]==0 and header[10]in(0,1)and header[48]==1,'BHF profile')
 count=u('<i',header,12)[0];start=u('<Q',header,16)[0];step=u('<Q',header,32)[0]
 need(start==64 and 0<=count<=100000,'BHF table')
 bit_big=not bool(header[10]);raw=header[49];fmt=raw if bit_big or(raw&1 and not raw&128)else int(f'{raw:08b}'[::-1],2)
 need(not fmt&1 and fmt&0xc and fmt!=4,'BHF format')
 expected=16+(8 if fmt&16 else 4)+(8 if fmt&32 else 0)+(4 if fmt&2 else 0)+4
 need(step==expected and 64+count*step<=len(header),'BHF entry size')
 budget.charge('records',count);budget.charge('estimated_bytes',count*512)
 rows=[]
 for i in range(count):
  at=64+i*step;flag=header[at]if bit_big else int(f'{header[at]:08b}'[::-1],2)
  need(not any(readat(header,at+1,3))and u('<i',header,at+4)[0]==-1,'BHF prefix')
  stored=u('<q',header,at+8)[0];pos=at+16;size=u('<q',header,pos)[0]if fmt&32 else stored;pos+=8 if fmt&32 else 0
  where=u('<Q'if fmt&16 else'<I',header,pos)[0];pos+=8 if fmt&16 else 4
  ident=u('<i',header,pos)[0]if fmt&2 else-1;pos+=4 if fmt&2 else 0;name=u('<I',header,pos)[0]
  need(64+count*step<=name<len(header)and name%2==0,'BHF name')
  need(0<=stored<=LIMIT and 0<=size<=LIMIT and 48<=where<=body_size and stored<=body_size-where,'BHF body extent')
  need(flag&1 or stored==size,'BHF raw size')
  rows.append(dict(index=i,id=ident,name=strings_pool.get(name),offset=where,stored_bytes=stored,bytes=size,compressed=bool(flag&1)))
 return rows
def dcx_bounded(d):
 need(len(d)>=76 and bytes(d[:4])==b'DCX\0','DCX header')
 profile=u('>5I',d,4)
 need(profile in((0x10000,0x18,0x24,0x44,0x4c),(0x10000,0x18,0x24,0x24,0x2c)),'unsupported DCX profile')
 need(bytes(d[24:28])==b'DCS\0'and bytes(d[36:44])==b'DCP\0DFLT'and bytes(d[68:72])==b'DCA\0','DCX DFLT tags')
 need(u('>I',d,44)[0]==0x20 and u('>I',d,72)[0]==8,'DCX header sizes')
 expected,stored=u('>II',d,28);need(expected<=LIMIT and len(d)==76+stored,'DCX declared extent/budget')
 stream=zlib.decompressobj()
 try:out=stream.decompress(readat(d,76,stored),expected+1)
 except zlib.error as e:raise ScanError('bad DCX zlib stream')from e
 need(len(out)==expected and stream.eof and not stream.unused_data and not stream.unconsumed_tail,'DCX exact decompressed extent')
 return out

def split_header_name(name):return name.endswith(('tpfbhd','hkxbhd','bhd.patch'))
def dispatch_payload(scanner,path,where):
 if split_header_name(path.name):return scanner.split(path,where)
 return scanner.payload(where,path.read_bytes())

class Scanner:
 def __init__(self,root,prior,out,p=None):
  p=project_root(p)
  need(out.is_dir()and not any(out.iterdir()),'fresh empty output directory required')
  sys.path.insert(0,str(p/'tools'));import game_shader_catalog as g
  loader=p/'vendor/blood_echoes/loader/prepare.py';sys.path.insert(0,str(loader.parent));spec=importlib.util.spec_from_file_location('_catalog_self_loader_'+hashlib.sha256(str(loader).encode()).hexdigest()[:12],loader);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
  self.g=g;self.parse_self=module.parse_self;self.root=root;self.out=out;self.p=p
  self.prior=json.loads(prior.read_text());need(Path(self.prior['game_root'])==root,'catalog root mismatch')
  self.known={x['path']:x for x in self.prior['packages']};self.count=collections.Counter();self.stages=collections.Counter();self.programs=set();self.codes=set();self.errors=[];self.gaps=[];self.layouts={};self.materials=set();self.partials={};self.raw_signatures=0
  self.streams={x:(out/(x+'.jsonl')).open('w')for x in ['files','resources','programs','models','partial-pipelines','errors','gaps']}
  self.exts=collections.Counter();self.bytes=0;self.reuse=0
 def emit(self,kind,row):
  # Iterencode avoids constructing an extra expanded model-sized JSON string.
  encoder=json.JSONEncoder(ensure_ascii=False,separators=(',',':'))
  for piece in encoder.iterencode(row):self.streams[kind].write(piece)
  self.streams[kind].write('\n')
 def error(self,path,error):
  row=dict(path=path,error=str(error))
  if isinstance(error,MetadataBudgetError):row.update(coverage_status='budget-limited',budget=error.details);self.count['metadata_budget_exceeded']+=1;self.gap(path,error.details)
  self.errors.append(row);self.emit('errors',row)
 def gap(self,path,why):
  row=dict(path=path,reason=why);self.gaps.append(row);self.emit('gaps',row)
 def records(self,where,meta,debug=False):
  for s in meta['records']:
   self.count['binary_info_records']+=1;self.stages[str(s['binary_type_raw'])]+=1
   if not debug:self.programs.add(s['program_hash'])
   if s.get('code_prefix_verified')and not debug:self.codes.add(s['code_sha256'])
   self.emit('programs',dict(resource=where,debug=debug,**s))
  for r in meta['rejected']:self.gap(where,dict(kind='rejected_OrbShdr_signature',**r))
 def model(self,where,d):
  m=flver_metadata(d);self.count['flver_parsed']+=1;self.count['mesh_records']+=len(m['meshes']);self.count['material_references']+=len(m['materials']);self.emit('models',dict(resource=where,sha256=sha(d),**m))
  for mesh in m['meshes']:
   material=m['materials'][mesh['material_index']];self.materials.add(material['mtd'].replace('\\','/').rsplit('/',1)[-1].casefold())
   # Observed file association, not a cartesian product of every layout/material.
   partial=dict(material=material['mtd'].replace('\\','/').rsplit('/',1)[-1].casefold(),dynamic=mesh['dynamic'],vertex_streams=mesh['layouts'],faces=mesh['faces'])
   ident=sha(json.dumps(partial,sort_keys=True,separators=(',',':')).encode());self.count['partial_pipeline_occurrences']+=1
   if ident not in self.partials:self.partials[ident]=0;self.emit('partial-pipelines',dict(id=ident,example_resource=where,known=partial,unresolved_fields=['shader-family-variant/pass','draw-adjusted-runtime-info-and-fetch-input-VGPR','fetch-shader-GPU-format','attachments-formats-count','blend-write-mask-depth-stencil','samples','resource-descriptor-specializations','GS-copy-program-identity']))
   self.partials[ident]+=1
 def payload(self,where,d,depth=0):
  need(depth<=6,'container nesting budget');need(len(d)<=LIMIT,'payload byte budget')
  raw=bytes(d)if isinstance(d,memoryview)else d
  if raw[:4]==b'DCX\0':
   decoded=dcx_bounded(raw);self.count['dcx_decoded']+=1;return self.payload(where+'::DCX',decoded,depth+1)
  if raw[:4]not in(b'BND4',b'\x7fELF',b'O\x15=\x1d'):
   self.records(where,self.g.orb_metadata(raw),'shaderbnddebug'in where)
  self.count['decoded_payloads']+=1
  if raw[:4]==b'BND4':
   binder=self.g.bnd4(raw);self.count['binders_parsed']+=1;self.count['binder_entries']+=len(binder['entries']);self.emit('resources',dict(path=where,kind='binder-header',bytes=len(raw),sha256=sha(raw),entries=len(binder['entries'])))
   for e in binder['entries']:
    payload=self.g.entry_data(raw,e);child=where+'::'+e['name'];self.emit('resources',dict(path=child,bytes=len(payload),sha256=sha(payload),kind='binder-entry',compressed=e['compressed']))
    try:self.payload(child,payload,depth+1)
    except (ValueError,UnicodeError,struct.error)as error:self.error(child,error)
    del payload
  elif raw[:6]==b'FLVER\0':
   self.model(where,raw)
  elif raw[:4]==b'BND3':self.gap(where,'BND3 nested container not structurally decoded')
  elif raw[:4]in(b'\x7fELF',b'O\x15=\x1d'):
   elf,h,ph,segs,missing=self.parse_self(raw,where);need(len(elf)<=LIMIT,'ELF reconstruction budget');self.count['elf_decoded']+=1;self.records(where+'::ELF',self.g.orb_metadata(elf));self.emit('resources',dict(path=where+'::ELF',kind='SELF-reconstructed',bytes=len(elf),sha256=sha(elf),segments=len(segs),missing_program_headers=missing));
   if missing:self.gap(where,dict(kind='ELF unavailable optional segments',indices=missing))
   del elf
 def raw_scan(self,path,where):
  # Raw signatures are counted, but uncompressed data without a container parser
  # is not silently promoted to a verified shader code record.
  tail=b'';count=0
  with path.open('rb')as s:
   for piece in iter(lambda:s.read(1024*1024),b''):
    data=tail+piece;count+=data.count(b'OrbShdr');tail=data[-6:]
  if count:self.gap(where,dict(kind='opaque_large_file_OrbShdr_signatures',count=count));self.raw_signatures+=count
 def split(self,path,where):
  body=path.with_name(path.name.replace('bhd','bdt'));need(body.is_file()and not body.is_symlink(),'missing BXF body')
  body_before=body.stat();header_size=path.stat().st_size
  if header_size>LIMIT:raise MetadataBudgetError('bhf_header_bytes',0,header_size,LIMIT)
  header=path.read_bytes();metadata_budget=MetadataBudget(BHF_LIMITS);rows=split_bhf(header,body_before.st_size,metadata_budget);self.count['split_headers_parsed']+=1
  self.emit('resources',dict(path=where,body=str(body.relative_to(self.root)),bytes=len(header),sha256=sha(header),kind='split-header',entries=len(rows),metadata_usage=metadata_budget.snapshot()))
  with body.open('rb')as s:
   need(s.read(4)==b'BDF4','BXF BDF4 signature')
   for e in rows:
    s.seek(e['offset']);raw=s.read(e['stored_bytes']);need(len(raw)==e['stored_bytes'],'BDF entry short read')
    d=dcx_bounded(raw)if raw[:4]==b'DCX\0'else self.g.bounded_zlib(raw,e['bytes'])if e['compressed']else raw
    # DCX may be an entry payload even if the binder compression flag is clear.
    if e['compressed']:need(len(d)==e['bytes'],'BDF uncompressed size')
    child=where+'::'+e['name'];self.emit('resources',dict(path=child,body=str(body.relative_to(self.root)),bytes=len(d),sha256=sha(d),kind='split-entry'))
    try:self.payload(child,d,1)
    except (ValueError,UnicodeError,struct.error)as error:self.error(child,error)
    self.count['split_entries']+=1;del raw,d
  need(stable_stat(body_before)==stable_stat(body.stat()),'split body changed during scan')
 def run(self):
  start=time.monotonic();files=[]
  for base,dirs,names in os.walk(self.root,followlinks=False):
   for n in names:files.append(Path(base)/n)
  for i,path in enumerate(sorted(files)):
   where=str(path.relative_to(self.root));before=path.stat(follow_symlinks=False)
   if path.is_symlink():self.error(where,'symlink input refused');continue
   suffix=''.join(path.suffixes[-2:])or'<none>';self.exts[suffix]+=1;self.bytes+=before.st_size
   digest=digest_path(path);mode='';known=self.known.get(where)
   try:
    if known and known['sha256']==digest and known['kind']in('shader','material'):
     self.reuse+=1;mode='verified-prior-binder-metadata'
     self.count['reused_shader_entries']+=known['entry_count']if known['kind']=='shader'else 0
     for e in known['entries']:
      if 'binary_info'in e:self.records(where+'::'+e['name'],dict(records=e['binary_info'],rejected=e.get('rejected_signature_candidates',[])),known['debug'])
    elif split_header_name(path.name):
     mode='split-BXF';self.split(path,where)
    elif path.name.endswith(('tpfbdt','hkxbdt','bdt.patch')):mode='split-body-covered-via-header'
    elif before.st_size<=LIMIT:
     mode='bounded-payload-scan';d=path.read_bytes();self.payload(where,d);del d
    else:
     mode='streaming-raw-signature-scan';self.raw_scan(path,where)
   except (ValueError,UnicodeError,struct.error)as error:self.error(where,error)
   after=path.stat(follow_symlinks=False);need(stable_stat(before)==stable_stat(after),'file changed during scan: '+where)
   self.emit('files',dict(path=where,bytes=before.st_size,sha256=digest,scan=mode))
   if i%1000==0:
    print(json.dumps(dict(progress=i,total=len(files),seconds=round(time.monotonic()-start,1),errors=len(self.errors),models=self.count['flver_parsed'])),flush=True)
  summary=dict(full_pso_recipes_from_static_scan=0,metadata_budget_policy=dict(flver=MODEL_LIMITS,bhf=BHF_LIMITS),files=len(files),bytes=self.bytes,suffix_counts=dict(self.exts),counts=dict(self.count),reused_prior_packages=self.reuse,unique_nondebug_program_hashes=len(self.programs),unique_nondebug_code_sha256=len(self.codes),binary_info_type_counts=dict(self.stages),distinct_referenced_materials=len(self.materials),material_names=sorted(self.materials),distinct_partial_pipeline_signatures=len(self.partials),partial_pipeline_occurrences=sum(self.partials.values()),errors=len(self.errors),gaps=len(self.gaps),elapsed_seconds=time.monotonic()-start,peak_rss_native_units=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
  for s in self.streams.values():s.close()
  (self.out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n');print(json.dumps(summary,ensure_ascii=False,indent=2))
  return summary

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--prototype',type=Path,help='source root; defaults to this checkout with vendored loader');ap.add_argument('--prior-catalog',required=True,type=Path);ap.add_argument('--game-root',required=True,type=Path);ap.add_argument('--out',required=True,type=Path);a=ap.parse_args()
 root=a.game_root.resolve(strict=True);need(root==a.game_root and root.is_dir(),'canonical absolute root');need(a.out.is_absolute()and not a.out.exists(),'new absolute output directory required');need(not a.out.resolve().is_relative_to(root),'metadata output must be outside game root');a.out.mkdir(parents=True)
 scanner=Scanner(root,a.prior_catalog,a.out,a.prototype);scanner.run()
if __name__=='__main__':main()
