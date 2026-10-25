"""Reuse the original loader to describe this exact private boot; never edit it."""
from pathlib import Path
import argparse,hashlib,struct,sys,os,tempfile,mmap
P=Path(__file__).resolve().parents[1]
SIZE=424;PAGE=16384

def require(ok,why):
 if not ok:raise ValueError(why)
def boot_header(path):
 with path.open('rb')as f:
  def read(n):
   b=f.read(n);require(len(b)==n,'truncated boot metadata');return b
  require(read(8)==b'BBPROBE5','requires original BBPROBE5')
  size,entry,ns,nr,ni,caps=struct.unpack('<6Q',read(48));require(0<size<=512*1024**2 and 0<ns<=64 and nr<=1000000 and ni<=100000 and caps==1,'boot limits')
  proc=struct.unpack('<Q',read(8))[0];tls=struct.unpack('<4Q',read(32));nm=struct.unpack('<Q',read(8))[0];require(nm==2,'this fixed-title module set requires libc and Fios2')
  mods=[struct.unpack('<7Q',read(56))for _ in range(nm)];nb=struct.unpack('<Q',read(8))[0];require(nb<=ni,'binding count');read(nb*24)
  segs=[struct.unpack('<3Q',read(24))for _ in range(ns)]
  require(path.stat().st_size==f.tell()+ni*128+nr*32+size,'boot length')
 return dict(size=size,tls=tls,mods=mods,segs=segs)

def record(m,name,base,ident,tls_index,main=False):
 loads=[p for p in m['ph']if p['type']in(1,0x61000010) and p['memsz']]
 encoded=name.encode('ascii');require(0<len(loads)<=4 and 0<len(encoded)<256 and b'\0'not in encoded,'module name/segments')
 b=bytearray(SIZE);struct.pack_into('<Q',b,0,SIZE);b[8:8+len(encoded)]=encoded;struct.pack_into('<iI',b,264,ident,tls_index)
 tls=next((p for p in m['ph']if p['type']==7),None)
 if tls:
  align=tls['align']or 16;require(align<=4096 and align&(align-1)==0 and tls['filesz']<=tls['memsz']<=1048576,'TLS layout')
  offset=(tls['memsz']+align-1)&-align if main else 0
  struct.pack_into('<Q4I',b,272,base+tls['vaddr'],tls['filesz'],tls['memsz'],offset,align)
 for key,at in [(12,296),(13,304)]:
  if key in m['tags']:struct.pack_into('<Q',b,at,base+m['tags'][key])
 eh=next((p for p in m['ph']if p['type']==0x6474e550),None)
 if eh:
  from prepare import span
  hdr=span(m['elf'],eh['offset'],12);require(hdr[:4]==bytes([1,0x1b,3,0x3b]),'unsupported fixed-title EH encoding')
  frame=eh['vaddr']+4+struct.unpack_from('<i',hdr,4)[0];count=struct.unpack_from('<I',hdr,8)[0]
  require(0<=frame<eh['vaddr'] and 12+count*8<=eh['filesz']==eh['memsz'],'invalid EH frame range/table')
  require(any(p['vaddr']<=frame and eh['vaddr']+eh['memsz']<=p['vaddr']+p['filesz']for p in loads),'EH data outside file-backed load')
  struct.pack_into('<QQII',b,328,base+eh['vaddr'],base+frame,eh['memsz'],eh['vaddr']-frame)
 for i,p in enumerate(loads):
  size=(p['memsz']+PAGE-1)&-PAGE;require(0<size<2**32 and 0<=p['flags']<=7,'segment limits')
  struct.pack_into('<QIi',b,352+i*16,base+p['vaddr'],size,p['flags'])
 struct.pack_into('<I',b,416,len(loads));return bytes(b),[(base+p['vaddr'],p['memsz'],p['flags'])for p in loads]

def prepare(boot,game,out,loader):
 boot=boot.resolve(strict=True);game=game.resolve(strict=True);out=out.absolute();loader=loader.resolve(strict=True)
 for path in [boot,game,out.parent.resolve(strict=True),loader]:require(path.is_relative_to(Path('/Users/Shared/BloodEchoesDevelopment')),'inputs/output must stay in private Shared tree')
 require(not out.is_symlink(),'linked metadata destination')
 sys.path.insert(0,str(loader));from prepare import parse_self,unpack
 def module(path):
  require(path.is_file() and 0<path.stat().st_size<=512*1024**2,'source module size')
  # Reuse the original SELF decoder, not its symbol/relocation inventory. mmap
  # avoids another full source-file copy; this child exits before FEX starts.
  with path.open('rb')as source,mmap.mmap(source.fileno(),0,access=mmap.ACCESS_READ)as mapped:
   with memoryview(mapped)as view:
    elf,header,ph,_,missing=parse_self(view,path.name);sha=hashlib.sha256(view).hexdigest()
  dp=next(p for p in ph if p['type']==2);require(dp['filesz']<=65536 and dp['filesz']%16==0,'dynamic table bounds')
  tags={}
  for at in range(dp['offset'],dp['offset']+dp['filesz'],16):
   key,value=unpack('<QQ',elf,at)
   if not key:break
   tags[key]=value
  else:raise ValueError('dynamic table lacks terminator')
  return dict(elf=elf,header=header,ph=ph,tags=tags,sha256=sha)
 header=boot_header(boot);records=[];segments=[]
 names=['eboot.bin','sce_module/libc.prx','sce_module/libSceFios2.prx']
 source_hashes=[]
 for i,name in enumerate(names):
  m=module(game/name);source_hashes.append(m['sha256']);base=0 if i==0 else header['mods'][i-1][0]
  tls=next((p for p in m['ph']if p['type']==7),None);index=(1 if tls else 0)if i==0 else header['mods'][i-1][6]
  if i==0:require((tls['vaddr'],tls['filesz'],tls['memsz'],tls['align'])==header['tls'],'main TLS differs from boot')
  else:
   old=header['mods'][i-1];require(base+m['tags'].get(12,0)==old[2] and (base+tls['vaddr'],tls['memsz'],tls['filesz'])==old[3:6],'module init/TLS differs from boot')
  row,segs=record(m,Path(name).name,base,i,index,i==0);records.append(row);segments.extend(segs)
  del m
 require(segments==header['segs'],'source PT_LOAD layout differs from boot')
 with boot.open('rb')as f:digest=hashlib.file_digest(f,'sha256').digest() if hasattr(hashlib,'file_digest')else None
 if digest is None:
  h=hashlib.sha256()
  with boot.open('rb')as f:
   for b in iter(lambda:f.read(65536),b''):h.update(b)
  digest=h.digest()
 data=b'BBMOD001'+digest+struct.pack('<QQ',header['size'],len(records))+b''.join(records)
 fd,tmp=tempfile.mkstemp(prefix='.module-info-',dir=out.parent)
 try:
  with os.fdopen(fd,'wb')as f:f.write(data);f.flush();os.fsync(f.fileno())
  os.replace(tmp,out)
 finally:
  if os.path.exists(tmp):os.unlink(tmp)
 print('ARM MODULE metadata records=%d bytes=%d boot=%s'%(len(records),len(data),digest.hex()),flush=True)
 return dict(records=len(records),bytes=len(data),boot_sha256=digest.hex(),source_sha256=source_hashes)
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--boot',type=Path,required=True);ap.add_argument('--app0',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);ap.add_argument('--loader-scripts',type=Path,default=P/'vendor/blood_echoes/loader');a=ap.parse_args();prepare(a.boot,a.app0,a.out,a.loader_scripts)
