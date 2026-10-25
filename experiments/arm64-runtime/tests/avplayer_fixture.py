"""Self-authored x86 callbacks and 16x16 movie only; no game code/data."""
from pathlib import Path
import re,struct
P=Path(__file__).resolve().parents[1]
def make(media,mode='valid'):
 assert 0<len(media)<4096
 data=bytearray(32768);relocs=[];fix=[];names=[]
 nids={name:nid for nid,name in re.findall(r'\{"([^"]+)","([^"]+)"\}',(P/'vendor/bb_runtime/src/import_names.inc').read_text())};nids['bb.prototype.avplayer_boundary']='bb.prototype.avplayer_boundary'
 class Body:
  def __init__(self,at):self.at=at;self.code=bytearray();self.labels={};self.guards=[]
  def emit(self,h):self.code.extend(bytes.fromhex(h))
  def ref(self,h,target):self.emit(h);fix.append((self.at+len(self.code),target,self));self.code.extend(bytes(4))
  def label(self,name):self.labels[name]=self.at+len(self.code)
  def guard(self,cc='85'):self.ref('0f '+cc,'bad')
  def eq(self,op,n):self.emit(op);self.code.extend(struct.pack('<I',n));self.guard()
  def call(self,n):
   if n not in names:names.append(n)
   self.ref('ff 15',0x4800+8*names.index(n))
  def zero(self):self.emit('85 c0');self.guard()
  def cookie(self):self.eq('48 81 ff',0x11223344);self.emit('65 4c 8b 14 25 f0 ff ff ff');self.eq('49 81 fa',0x11223344)
  def inc(self,target):self.ref('f0 ff 05',target)
  def commit(self,good,bad):self.emit(good);self.label('bad');self.emit(bad);assert len(self.code)<1024;data[self.at:self.at+len(self.code)]=self.code
 data[0:3]=data[0x200:0x203]=bytes.fromhex('31 c0 c3')
 b=Body(0x1000);b.cookie();b.emit('81 fa 00 08 00 00');b.guard('87');b.emit('b8 01 00 00 00');b.ref('f0 0f c1 05',0x4480);b.emit('83 f8 02');b.guard('83');b.emit('48 c1 e0 0b');b.ref('4c 8d 15',0x6000);b.emit('4c 01 d0')
 if mode=='bad-allocated-span':b.ref('48 8d 05',0x7ff0)
 b.commit('c3','31 c0 c3')
 b=Body(0x1100);b.cookie();b.inc(0x4484);b.commit('31 c0 c3','0f 0b')
 b=Body(0x1200);b.cookie();b.eq('81 3e',0x68747561);b.inc(0x4488);b.commit('b8 07 00 00 00 c3','b8 ff ff ff ff c3')
 b=Body(0x1300);b.cookie();b.inc(0x448c);b.commit('31 c0 c3','0f 0b')
 b=Body(0x1400);b.cookie();b.inc(0x4490)
 if mode=='bad-read-count':b.emit('48 8d 41 01 c3')
 else:
  b.emit('b8');b.code.extend(struct.pack('<I',len(media)));b.emit('48 39 c2');b.ref('0f 83','empty');b.emit('48 29 d0 48 39 c8 48 0f 42 c8 49 89 c8');b.ref('48 8d 05',0x7000);b.emit('48 01 d0 48 89 f7 48 89 c6 f3 a4 4c 89 c0 c3');b.label('empty');b.emit('31 c0 c3')
 b.commit('','b8 ff ff ff ff c3')
 b=Body(0x1500);b.cookie();b.emit('b8');b.code.extend(struct.pack('<I',len(media)));b.commit('c3','31 c0 c3')
 b=Body(0x1600);b.cookie();b.inc(0x4494);b.commit('31 c0 c3','0f 0b')
 b=Body(0x400);b.emit('48 83 ec 08 31 ff');b.call('bb.prototype.avplayer_boundary');b.zero()
 b.ref('48 8d 3d',0x7ff8 if mode=='bad-init-span'else 0x5200);b.call('sceAvPlayerInit');b.emit('48 85 c0');b.guard('84');b.ref('48 89 05',0x44b0)
 b.ref('48 8b 3d',0x44b0);b.ref('48 8d 35',0x5300);b.call('sceAvPlayerPostInit');b.zero()
 b.ref('48 8b 3d',0x44b0);b.ref('48 8d 35',0x5100);b.call('sceAvPlayerAddSource');b.zero()
 b.ref('c7 05',0x44b8-4);b.emit('f4 01 00 00') # RIP for C7 includes its immediate
 b.label('poll');b.ref('48 8b 3d',0x44b0);b.ref('48 8d 35',0x7ff0 if mode=='bad-frame-span'else 0x5400);b.call('sceAvPlayerGetVideoDataEx');b.emit('85 c0');b.ref('0f 85','loaded');b.emit('bf d0 07 00 00');b.call('sceKernelUsleep');b.zero();b.ref('ff 0d',0x44b8);b.ref('0f 85','poll');b.ref('e9','bad')
 b.label('loaded');b.ref('8b 05',0x5418);b.eq('3d',16);b.ref('8b 05',0x541c);b.eq('3d',16)
 b.ref('48 8b 3d',0x44b0);b.call('sceAvPlayerStreamCount');b.emit('83 f8 01');b.guard('8c')
 b.ref('48 8b 3d',0x44b0);b.emit('31 f6');b.ref('48 8d 15',0x5500);b.call('sceAvPlayerGetStreamInfo');b.zero()
 b.ref('48 8b 3d',0x44b0);b.emit('31 f6');b.call('sceAvPlayerSetLooping');b.zero()
 for name in ['sceAvPlayerCurrentTime','sceAvPlayerPause','sceAvPlayerResume','sceAvPlayerStop']:
  b.ref('48 8b 3d',0x44b0);b.call(name)
 b.ref('48 8b 3d',0x44b0);b.call('sceAvPlayerClose');b.zero();b.ref('48 8b 3d',0x44b0);b.call('sceAvPlayerIsActive');b.zero()
 for at,n in [(0x4480,2),(0x4484,2),(0x4488,1),(0x448c,1)]:b.ref('8b 05',at);b.eq('3d',n)
 for at in [0x4490,0x4494]:b.ref('8b 05',at);b.emit('85 c0');b.guard('84')
 b.emit('bf 01 00 00 00');b.call('bb.prototype.avplayer_boundary');b.zero();b.commit('31 c0 48 83 c4 08 c3','b8 28 00 00 00 48 83 c4 08 c3')
 for name in ['sceAvPlayerStart','sceAvPlayerEnableStream','sceAvPlayerGetAudioData']:
  if name not in names:names.append(name)
 data[0x5100:0x510d]=b'authored.mp4\0';data[0x5120:0x5124]=b'eng\0';data[0x7000:0x7000+len(media)]=media
 for offset in [0,40,80]:struct.pack_into('<Q',data,0x5200+offset,0x11223344)
 for offset,target in [(8,0x5000 if mode=='bad-callback'else 0x1000),(16,0x1100),(24,0x1000),(32,0x1100),(48,0x1200),(56,0x1300),(64,0x1400),(72,0x1500),(88,0x1600),(112,0x5120)]:relocs.append((0x5200+offset,0,target,0))
 struct.pack_into('<iB',data,0x5268,2,1);struct.pack_into('<Q',data,0x4100,0x11223344)
 for i,v in enumerate([0x12,0x34]):struct.pack_into('<Q',data,0x4200+i*16,v)
 for at,target,body in fix:struct.pack_into('<i',data,at,(body.labels[target]if isinstance(target,str)else target)-at-4)
 for i,n in enumerate(names):relocs.append((0x4800+8*i,1,i,0))
 modules=[(0,512,0,0x4200,16,8,2),(0x200,512,0x200,0x4210,16,8,3)]
 header=b'BBPROBE5'+struct.pack('<6Q',len(data),0x400,2,len(relocs),len(names),1)+struct.pack('<6Q',0x4000,0x4100,8,16,16,2)
 header+=b''.join(struct.pack('<7Q',*m)for m in modules)+struct.pack('<Q',0)+struct.pack('<6Q',0,16384,5,16384,len(data)-16384,6)
 header+=b''.join(nids[n].encode().ljust(128,b'\0')for n in names)
 return header+b''.join(struct.pack('<4Q',*r)for r in relocs)+data
