"""Authored FEX contracts. Reuse authored module/TLS bootstrap, replace main.
Every positive case pins at most one 16 KiB page, then explicitly unlocks/unmaps.
No game bytes, game saves, or graphics. Not executed by the authoring subagent.
"""
from pathlib import Path
import re,struct
from game_fixture import make as bootstrap
P=Path(__file__).resolve().parents[1]
def make(mode):
 b=bootstrap('msync-flex');size,entry,ns,nr,ni,caps=struct.unpack_from('<6Q',b,8)
 off=96;nm=struct.unpack_from('<Q',b,off)[0];off+=8+56*nm;nb=struct.unpack_from('<Q',b,off)[0];off+=8+24*nb+24*ns
 names=[b[off+128*i:off+128*(i+1)].split(b'\0')[0].decode()for i in range(ni)];prefix=b[:off];off+=128*ni
 relocs=[struct.unpack_from('<4Q',b,off+32*i)for i in range(nr)];off+=32*nr;data=bytearray(b[off:]);assert len(data)==size
 relocs=[r for r in relocs if not 0x400<=r[0]<0x800]
 nid={name:nid for nid,name in re.findall(r'\{"([^"]+)","([^"]+)"\}',(P/'vendor/bb_runtime/src/import_names.inc').read_text())};assert nid['sceKernelMlock']=='3k6kx-zOOSQ#p#J' and nid['munlock']=='OG4RsDwLguo#p#J'
 code=bytearray();guards=[]
 def emit(h):code.extend(bytes.fromhex(h))
 def ref(h,target):emit(h);patch=0x400+len(code);code.extend(struct.pack('<i',target-patch-4))
 def call(name):
  key=nid[name]
  if key not in names:
   names.append(key);relocs.append((0x4800+8*(len(names)-1),1,len(names)-1,0))
  else:
   slot=0x4800+8*names.index(key)
   if not any(r[0]==slot for r in relocs):relocs.append((slot,1,names.index(key),0))
  ref('ff 15',0x4800+8*names.index(key))
 def guard():emit('0f 85');guards.append(len(code));emit('00 00 00 00')
 def eq(value):emit('3d');code.extend(struct.pack('<I',value&0xffffffff));guard()
 def zero():emit('85 c0');guard()
 def address():ref('48 8b 3d',0x4370)
 def size(n):emit('be');code.extend(struct.pack('<I',n))
 def map_(n=16384):ref('48 8d 3d',0x4370);size(n);emit('ba 03 00 00 00 31 c9');call('sceKernelMapFlexibleMemory');zero()
 def protect(p):address();size(16384);emit('ba');code.extend(struct.pack('<I',p));call('sceKernelMprotect');zero()
 def lock(n=8192,name='sceKernelMlock'):address();size(n);call(name)
 def unlock(name='munlock'):address();size(8192);call(name);zero()
 def unmap(n=16384):address();size(n);call('sceKernelMunmap');zero()
 def failed_posix(error):eq(0xffffffff);call('__error');emit('81 38');code.extend(struct.pack('<I',error));guard()
 emit('48 83 ec 08')
 if mode=='flex-set':
  map_();lock();zero();lock();zero()
  address();size(8192);emit('ba 02 00 00 00');call('msync');failed_posix(16)
  unlock('sceKernelMunlock');lock();zero();unmap()
  ref('48 8d 3d',0x4370);size(16384);emit('ba 03 00 00 00 b9 10 00 00 00');call('sceKernelMapFlexibleMemory');zero();lock();zero();unlock();unmap()
 elif mode in ('readonly','none-lock','none-unlock'):
  map_()
  if mode=='none-unlock':lock();zero()
  protect(1 if mode=='readonly'else 0)
  if mode=='none-lock':lock();eq(0x8002000c);lock(name='mlock');failed_posix(12);protect(3);lock();zero();unlock()
  elif mode=='none-unlock':unlock();protect(3)
  else:lock();zero();unlock()
  unmap()
 elif mode in ('image','stack'):
  def pointer():
   if mode=='image':ref('48 8d 3d',0x5000)
   else:emit('48 89 e7 48 81 ef 00 40 00 00 48 81 e7 00 c0 ff ff')
  pointer();size(8192);call('sceKernelMlock');zero();pointer();size(8192);call('munlock');zero()
 elif mode in ('unpublished','overflow'):
  def pointer():emit('48 bf');code.extend(struct.pack('<Q',0x100000000 if mode=='unpublished'else (1<<64)-16))
  pointer();size(8192 if mode=='unpublished'else 32);call('sceKernelMlock');eq(0x8002000c if mode=='unpublished'else 0x80020016)
  pointer();size(8192 if mode=='unpublished'else 32);call('mlock');failed_posix(12 if mode=='unpublished'else 22)
 elif mode=='budget':map_(17<<20);lock(17<<20);eq(0x8002000c);unmap(17<<20)
 elif mode=='freed':map_();lock();zero();unmap();lock();eq(0x8002000c)
 else:raise ValueError(mode)
 emit('31 ff');call('exit');emit('0f 0b');emit('31 c0 48 83 c4 08 c3');bad=len(code);emit('b8 5d 00 00 00 48 83 c4 08 c3')
 for g in guards:struct.pack_into('<i',code,g,bad-g-4)
 assert len(code)<1024;data[0x400:0x800]=bytes(code)+b'\xcc'*(1024-len(code))
 prefix=bytearray(prefix);struct.pack_into('<Q',prefix,32,len(relocs));struct.pack_into('<Q',prefix,40,len(names))
 return bytes(prefix)+b''.join(n.encode().ljust(128,b'\0')for n in names)+b''.join(struct.pack('<4Q',*r)for r in relocs)+bytes(data)
