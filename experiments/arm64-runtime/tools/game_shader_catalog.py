"""Read-only metadata inventory for the fixed Bloodborne DFLT/BND4 profile.

Format references (independent parser, not a SoulsFormats port):
https://github.com/soulsmods/SoulsFormats/tree/master/SoulsFormats/Formats/Binder
OrbShdr layout: vendored shadPS4 video_core/amdgpu/regs_shader.h.
Never extract entry payloads or modify the input game/cache.
"""
from pathlib import Path
import argparse,collections,hashlib,json,mmap,struct,time,zlib

LIMIT=128*1024*1024
class CatalogError(ValueError):pass
def require(ok,message):
 if not ok:raise CatalogError(message)
def span(data,start,size):
 require(type(start)is int and type(size)is int and 0<=start<=len(data)and 0<=size<=len(data)-start,'out-of-bounds span')
 return memoryview(data)[start:start+size]
def unpack(fmt,data,offset):
 return struct.unpack(fmt,span(data,offset,struct.calcsize(fmt)))
def bounded_zlib(data,expected):
 require(0<=expected<=LIMIT,'decompression budget')
 stream=zlib.decompressobj()
 try:out=stream.decompress(data,expected+1)
 except zlib.error as error:raise CatalogError('bad zlib stream')from error
 require(len(out)==expected and stream.eof and not stream.unused_data and not stream.unconsumed_tail,'decompression size or stream mismatch')
 return out
def dcx(data):
 require(len(data)>=76 and data[:4]==b'DCX\0','not DCX')
 require(unpack('>5I',data,4)==(0x10000,0x18,0x24,0x44,0x4c),'unsupported DCX profile')
 require(data[24:28]==b'DCS\0'and data[36:44]==b'DCP\0DFLT'and data[68:72]==b'DCA\0','unsupported DCX compression')
 require(unpack('>I',data,44)[0]==0x20 and unpack('>I',data,72)[0]==8,'bad DFLT header')
 size,compressed=unpack('>II',data,28)
 require(len(data)==76+compressed,'DCX compressed span mismatch')
 return bounded_zlib(span(data,76,compressed),size)
def reverse(byte):return int(f'{byte:08b}'[::-1],2)
def bnd4(data):
 require(len(data)>=64 and data[:4]==b'BND4','not BND4')
 require(data[9]==0 and data[10]in(0,1),'only little-endian BND4 supported')
 require(data[48]==1,'only UTF-16 BND4 supported')
 count=unpack('<i',data,12)[0];header=unpack('<Q',data,16)[0];entry_size,headers_end=unpack('<QQ',data,32)
 require(header==64 and 0<=count<=100000,'bad BND4 header/count')
 raw=data[49];bit_big=not bool(data[10]);fmt=raw if bit_big or(raw&1 and not raw&128)else reverse(raw)
 require(not fmt&1 and fmt&0xc and fmt!=4,'unsupported BND4 format')
 expected=16+(8 if fmt&16 else 4)+(8 if fmt&32 else 0)+(4 if fmt&2 else 0)+4
 require(entry_size==expected and 64+count*entry_size<=headers_end<=len(data),'BND4 header extent')
 entries=[]
 for index in range(count):
  at=64+index*entry_size;end=at+entry_size;flags=data[at]if bit_big else reverse(data[at]);require(not any(span(data,at+1,3))and unpack('<i',data,at+4)[0]==-1,'bad entry prefix')
  compressed=unpack('<q',data,at+8)[0];pos=at+16
  uncompressed=unpack('<q',data,pos)[0]if fmt&32 else compressed;pos+=8 if fmt&32 else 0
  offset=unpack('<Q'if fmt&16 else'<I',data,pos)[0];pos+=8 if fmt&16 else 4
  ident=unpack('<i',data,pos)[0]if fmt&2 else -1;pos+=4 if fmt&2 else 0
  name_at=unpack('<I',data,pos)[0];require(pos+4==end,'entry shape mismatch')
  require(64+count*entry_size<=name_at<headers_end and name_at%2==0,'filename pointer')
  cursor=name_at
  while cursor+2<=headers_end and cursor-name_at<8192 and data[cursor:cursor+2]!=b'\0\0':cursor+=2
  require(cursor+2<=headers_end and data[cursor:cursor+2]==b'\0\0','unterminated filename')
  try:name=data[name_at:cursor].decode('utf-16-le',errors='strict')
  except UnicodeError as error:raise CatalogError('invalid filename')from error
  require(offset>=headers_end and 0<=compressed<=LIMIT and 0<=uncompressed<=LIMIT,'entry size/data offset')
  span(data,offset,compressed)
  require(flags&1 or compressed==uncompressed,'raw entry size mismatch')
  entries.append(dict(index=index,id=ident,name=name,offset=offset,stored_bytes=compressed,bytes=uncompressed,compressed=bool(flags&1)))
 return dict(count=count,format=fmt,entry_header_size=entry_size,version=data[24:32].rstrip(b'\0').decode('ascii',errors='strict'),entries=entries)
def entry_data(data,entry):
 view=span(data,entry['offset'],entry['stored_bytes'])
 if not entry['compressed']:return view
 content=dcx(bytes(view))if bytes(view[:4])==b'DCX\0'else bounded_zlib(view,entry['bytes'])
 require(len(content)==entry['bytes'],'entry decompression mismatch');return memoryview(content)
def orb_metadata(data):
 raw=data if isinstance(data,(bytes,mmap.mmap))else bytes(data)
 # Verify the same fast-path prefix -> BinaryInfo relation used by GetParams,
 # rather than guess code start by subtracting code length from metadata.
 prefix_map=collections.defaultdict(list);at=-1
 while True:
  at=raw.find(b'\xff\x03\xeb\xbe',at+1)
  if at<0:break
  if at+8>len(raw):continue
  info_at=at+(unpack('<I',raw,at+4)[0]+1)*8
  if info_at+28<=len(raw)and raw[info_at:info_at+7]==b'OrbShdr':prefix_map[info_at].append(at)
 records=[];rejected=[];start=0
 while True:
  pos=raw.find(b'OrbShdr',start)
  if pos<0:break
  start=pos+7
  if pos+28>len(raw):rejected.append(dict(offset=pos,reason='truncated BinaryInfo'));continue
  packed=unpack('<I',raw,pos+8)[0];length=packed>>8
  if not length or length%4 or length>pos:rejected.append(dict(offset=pos,reason='invalid code length'));continue
  candidates=[at for at in prefix_map[pos]if at+length<=pos]
  record=dict(binary_info_offset=pos,version=raw[pos+7],binary_type_raw=(packed>>2)&15,code_bytes=length,program_hash=f"{unpack('<Q',raw,pos+16)[0]:016x}",crc32=f"{unpack('<I',raw,pos+24)[0]:08x}",code_prefix_verified=len(candidates)==1)
  if len(candidates)==1:
   code_at=candidates[0];record.update(code_offset=code_at,code_sha256=hashlib.sha256(span(raw,code_at,length)).hexdigest())
  records.append(record)
 return dict(records=records,rejected=rejected)
def game_identity(data):
 magic,version,keys,values,count=unpack('<4sIIII',data,0)
 require(magic==b'\0PSF'and count<=1024 and 20+count*16<=keys<=values<=len(data),'invalid SFO header')
 result={}
 for i in range(count):
  key,fmt,size,capacity,off=unpack('<HHIII',data,20+i*16)
  key_at=keys+key;require(keys<=key_at<values and size<=capacity,'invalid SFO entry')
  end=data.find(b'\0',key_at,values);require(end>=key_at,'unterminated SFO key')
  name=data[key_at:end].decode('utf-8',errors='strict')
  payload=span(data,values+off,size)
  if name in('TITLE_ID','APP_VER'):
   require(fmt==0x204 and name not in result,'invalid identity SFO entry')
   result[name]=bytes(payload).rstrip(b'\0').decode('utf-8',errors='strict')
 require(result.get('TITLE_ID','').startswith('CUSA')and result.get('APP_VER')=='01.09','fixed 1.09 identity required')
 return dict(result,sfo_sha256=hashlib.sha256(data).hexdigest())

def read_package(root,path,embedded=False):
 """Original package reader; metadata caching never supplies synthetic inputs."""
 if embedded:return read_embedded(root,path)
 require(path.stat().st_size<=LIMIT,'compressed input budget')
 compressed=path.read_bytes();data=dcx(compressed);binder=bnd4(data);before=hashlib.sha256(compressed).hexdigest();debug='shaderbnddebug'in path.name;is_shader=path.parent==root/'dvdroot_ps4/shader'
 package=dict(path=str(path.relative_to(root)),sha256=before,compressed_bytes=len(compressed),uncompressed_bytes=len(data),entry_count=binder['count'],version=binder['version'],debug=debug,kind='shader'if is_shader else'material',suffixes=dict(collections.Counter(Path(x['name']).suffix for x in binder['entries'])),entries=[])
 for entry in binder['entries']:
  payload=entry_data(data,entry);meta=dict(entry,sha256=hashlib.sha256(payload).hexdigest());meta.pop('offset')
  if is_shader:
   scanned=orb_metadata(payload);meta['binary_info']=scanned['records'];meta['rejected_signature_candidates']=scanned['rejected']
  package['entries'].append(meta)
  del payload  # Do not keep the previous large decompressed binder alive.
 require(hashlib.sha256(path.read_bytes()).hexdigest()==before,'input changed during read')
 return package

def read_embedded(root,path):
 require(0<path.stat().st_size<=LIMIT,'embedded input budget')
 with path.open('rb')as stream,mmap.mmap(stream.fileno(),0,access=mmap.ACCESS_READ)as data:
  magic=data[:4];require(magic in(b'\x7fELF',b'O\x15=\x1d'),'unsupported executable/module container')
  before=hashlib.sha256(data).hexdigest();scanned=orb_metadata(data)
  package=dict(path=str(path.relative_to(root)),sha256=before,kind='embedded',debug=False,stored_bytes=len(data),entry_count=0,entries=[],binary_info=scanned['records'],rejected_signature_candidates=scanned['rejected'],container='ELF'if magic==b'\x7fELF'else'SELF',compressed_segments_not_decoded=True)
  require(hashlib.sha256(data).hexdigest()==before,'embedded input changed during read')
 return package

def inventory(root,*,products=None,source_files=None):
 root=Path(root);require(root.is_absolute()and root==root.resolve()and root.is_dir(),'absolute non-symlink game root required')
 sfo=root/'sce_sys/param.sfo';require(sfo.is_file()and not sfo.is_symlink()and sfo.stat().st_size<=65536,'SFO identity required');identity=game_identity(sfo.read_bytes())
 shader=root/'dvdroot_ps4/shader';material=root/'dvdroot_ps4/mtd/allmaterialbnd.mtdbnd.dcx'
 paths=sorted(shader.glob('*.shaderbnd*.dcx'))+[material];require(len(paths)>1,'shader bundles missing')
 selected={row['path']:row for row in source_files}if source_files is not None else{}
 packages=[];programs=[]
 for path in paths+[root/'eboot.bin',*sorted((root/'sce_module').glob('*.prx'))]:
  relative=str(path.relative_to(root));registered=selected.get(relative);embedded=path not in paths
  require(path.is_file()and not path.is_symlink()and path.resolve().is_relative_to(root),'unsafe/missing package')
  require(0<path.stat().st_size<=LIMIT,'package byte budget')
  source=registered or dict(path=relative,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),bytes=path.stat().st_size)
  proof=dict(kind='package-metadata-v1',source=source,embedded=embedded)
  package=products.get(proof)if products is not None else None
  if package is None:
   package=read_package(root,path,embedded)
   require(package['sha256']==source['sha256'],'registered package content changed')
   if products is not None:products.put(proof,package)
  packages.append(package)
  for entry in package['entries']if package['kind']=='shader'else[package]if embedded else[]:
   for record in entry.get('binary_info',[]):
    extra={'entry_sha256':entry['sha256']}if not embedded else{}
    programs.append(dict(package=relative,entry=entry.get('name'),debug=package['debug'],**extra,**record))
 nondebug=[x for x in programs if not x['debug']];groups=collections.defaultdict(list)
 for x in nondebug:groups[x['program_hash']].append(x)
 return dict(schema=1,game_root=str(root),game_identity=identity,packages=packages,programs=programs,summary=dict(packages=len(packages),shader_packages=sum(x['kind']=='shader'for x in packages),material_packages=sum(x['kind']=='material'for x in packages),shader_entries=sum(x['entry_count']for x in packages if x['kind']=='shader'and not x['debug']),debug_entries=sum(x['entry_count']for x in packages if x['debug']),material_entries=sum(x['entry_count']for x in packages if x['kind']=='material'),nondebug_binary_info_records=len(nondebug),unique_program_hashes=len(groups),multiple_entries_per_hash=sum(len(v)>1 for v in groups.values()),hashes_with_distinct_verified_code=sum(len({x.get('code_sha256')for x in v if x['code_prefix_verified']})>1 for v in groups.values()),verified_code_records=sum(x['code_prefix_verified']for x in nondebug),unique_verified_code_sha256=len({x['code_sha256']for x in nondebug if x['code_prefix_verified']}),unverified_binary_info_records=sum(not x['code_prefix_verified']for x in nondebug),entries_without_binary_info=sum(not x.get('binary_info')for p in packages if p['kind']=='shader'and not p['debug']for x in p['entries'])),qualification='Local metadata only. Verified prefixes/code digests identify stored code, not complete runtime specialization or pipeline compatibility. Executable SELF compressed segments are not decoded; no whole-game completeness claim. Debug packages separate; no payloads extracted. No compilation or game/cache modifications.')

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--game-root',type=Path,required=True);a=ap.parse_args();from qa_build_guard import require_no_games;require_no_games();p=Path(__file__).resolve().parents[1];e=p/'evidence'/('fixed-game-shader-catalog-'+str(time.time_ns()));e.mkdir();result=inventory(a.game_root);(e/'catalog.json').write_text(json.dumps(result,indent=2)+'\n');print(e);print(json.dumps(result['summary'],indent=2))
if __name__=='__main__':main()
