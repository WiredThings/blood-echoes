"""Authored machine code, metadata and EH headers; no game content."""
import struct,hashlib
from pathlib import Path

def make(mode='valid'):
 image=bytearray(65536);names=['f7KBOafysXo#p#J','Fjc4-n1+y2g#p#J'];code=bytearray.fromhex('48 83 ec 08');fix=[];guards=[]
 if mode=='dynamic-align':
  import re
  runtime=(Path(__file__).resolve().parents[1]/'vendor/bb_runtime/src/import_names.inc').read_text();nid=next(n for n,name in re.findall(r'\{"([^"]+)","([^"]+)"\}',runtime)if name=='__tls_get_addr');names.append(nid);struct.pack_into('<QQ',image,0x4800,2,0)
 def e(h):code.extend(bytes.fromhex(h))
 def r(h,to):
  e(h);at=0x100+len(code);code.extend(bytes(4));fix.append((at,to))
 def g(cc='85'):e('0f '+cc);guards.append(len(code));code.extend(bytes(4))
 def q(value):e('48 3d');code.extend(struct.pack('<I',value));g()
 def query(address=0x8000,flags=2,output=0x4500):
  r('48 8d 3d',address);e('be');code.extend(struct.pack('<I',flags&0xffffffff));r('48 8d 15',output);r('ff 15',0x4400)
 def match(address):r('48 8d 3d',0x4500);r('48 8d 35',address);r('ff 15',0x4408)
 if mode=='bad-output':query(output=65528);e('3d 0e 00 02 80');g()
 elif mode in ['flags','negative-flags']:
  query(flags=3 if mode=='flags'else -1);e('3d 16 00 02 80');g();r('48 8b 05',0x4500);q(0)
 elif mode=='missing':
  e('bf 01 00 00 00 be 02 00 00 00');r('48 8d 15',0x4500);r('ff 15',0x4400);e('3d 03 00 02 80');g()
 elif mode=='bad-match':e('bf 34 12 00 00 31 f6');r('ff 15',0x4408)
 else:
  query();e('85 c0');g();r('48 8b 05',0x4500);q(424);r('8b 05',0x4500+264);e('3d 01 00 00 00');g()
  for at,value in [(0x8000,1),(0xbfff,1),(0xc000,0),(0x7fff,0)]:match(at);e('3d');code.extend(struct.pack('<I',value));g()
  if mode=='bad-count':r('48 8d 05',0x4500);e('c7 80 a0 01 00 00 05 00 00 00');match(0x8000);e('85 c0');g()
  if mode=='dynamic-align':
   r('48 8d 3d',0x4800);r('ff 15',0x4410);e('48 85 c0');g('84');e('48 83 38 00');g();e('a8 1f');g()
 e('31 c0 48 83 c4 08 c3');bad=len(code);e('b8 63 00 00 00 48 83 c4 08 c3')
 for pos in guards:struct.pack_into('<i',code,pos,bad-pos-4)
 image[0x100:0x100+len(code)]=code
 for at,to in fix:struct.pack_into('<i',image,at,to-at-4)
 image[0x8000:0x8003]=bytes.fromhex('31 c0 c3')
 image[0x8300:0x8314]=bytes([1,0x1b,3,0x3b])+struct.pack('<iI',-0x104,1)+bytes(8)
 segs=[(0,0x4000,5),(0x4000,0x4000,6),(0x8000,0x4000,5),(0xc000,0x4000,6)]
 boot=b'BBPROBE5'+struct.pack('<6Q',len(image),0x100,4,len(names),len(names),1)+struct.pack('<6Q',0x4000,0x4100,8,16,16,1)
 boot+=struct.pack('<7Q',0x8000,0x8000,0x8000,0xc100,32,16,2)+struct.pack('<Q',0)
 boot+=b''.join(struct.pack('<3Q',*s)for s in segs)+b''.join(n.encode().ljust(128,b'\0')for n in names)
 boot+=b''.join(struct.pack('<4Q',0x4400+8*i,1,i,0)for i in range(len(names)))+image
 def info(i):
  b=bytearray(424);struct.pack_into('<Q',b,0,424);name=b'eboot.qa'if i==0 else b'libc.qa';b[8:8+len(name)]=name
  struct.pack_into('<iI',b,264,i,i+1);struct.pack_into('<Q4I',b,272,0x4100 if i==0 else 0xc100,8 if i==0 else 16,16 if i==0 else 32,16 if i==0 else 0,32 if i and mode=='dynamic-align'else 16)
  struct.pack_into('<Q',b,296,0x100 if i==0 else 0x8000)
  if i:struct.pack_into('<QQII',b,328,0x8300,0x8200,20,256)
  for j,s in enumerate(segs[i*2:i*2+2]):struct.pack_into('<QIi',b,352+j*16,*s)
  struct.pack_into('<I',b,416,2);return bytes(b)
 metadata=b'BBMOD001'+hashlib.sha256(boot).digest()+struct.pack('<QQ',len(image),2)+info(0)+info(1)
 return boot,metadata
