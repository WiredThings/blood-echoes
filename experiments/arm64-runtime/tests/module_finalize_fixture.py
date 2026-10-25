"""Authored x86/code/data/metadata only; production game files are not read."""
from pathlib import Path
import hashlib,importlib.util,re,struct
P=next(p for p in Path(__file__).resolve().parents if(p/'tools/qa_build_guard.py').is_file())
def make(mode='valid'):
 spec=importlib.util.spec_from_file_location('existing_module_fixture',P/'tests/module_info_fixture.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
 original,metadata=mod.make();data=bytearray(original[-65536:]);data[:0x4000]=bytes(0x4000);data[0x8000:0x8100]=bytes(0x100);data[0x8000:0x8003]=bytes.fromhex('31 c0 c3')
 text=(P/'vendor/bb_runtime/src/import_names.inc').read_text();nids={symbol:nid for nid,symbol in re.findall(r'\{"([^\"]+)\",\"([^\"]+)\"\}',text)}
 nids.update(scePthreadCreate='6UgtwV+0zb4#p#J',scePthreadJoin='onNY9Byn-W8#p#J',__cxa_atexit='tsvEmnenz48#q#q',__cxa_finalize='H2e8t5ScQGc#q#q')
 names=['sceKernelGetModuleInfoFromAddr','__pthread_cxa_finalize','scePthreadKeyCreate','scePthreadKeyDelete','scePthreadSetspecific','scePthreadGetspecific','scePthreadCreate','scePthreadJoin','__cxa_atexit','__cxa_finalize']
 fix=[]
 class Function:
  def __init__(self,at):self.at=at;self.code=bytearray.fromhex('48 83 ec 08');self.fail=[]
  def e(self,s):self.code+=bytes.fromhex(s)
  def r(self,s,to,tail=0):self.e(s);patch=self.at+len(self.code);self.code+=bytes(4);fix.append((patch,to-tail))
  def c(self,name):self.r('ff 15',0x4800+8*names.index(name))
  def check(self,cc='85'):self.e('0f '+cc);self.fail.append(len(self.code));self.code+=bytes(4)
  def zero(self):self.e('85 c0');self.check()
  def eq(self,value):self.e('48 3d');self.code+=struct.pack('<I',value);self.check()
  def query(self,address):self.r('48 8d 3d',address);self.e('be 02 00 00 00');self.r('48 8d 15',0x4600);self.c('sceKernelGetModuleInfoFromAddr');self.zero()
  def finalize(self,address):self.query(address);self.r('48 8d 3d',0x4600);self.c('__pthread_cxa_finalize')
  def count(self,at,value):self.r('8b 05',at);self.eq(value)
  def create(self,key,callback):
   self.r('48 8d 3d',key)
   if callback is None:self.e('31 f6')
   else:self.r('48 8d 35',callback)
   self.c('scePthreadKeyCreate');self.zero()
  def get(self,key,value):self.r('8b 3d',key);self.c('scePthreadGetspecific');self.eq(value)
  def set(self,key,value):self.r('8b 3d',key);self.e('be');self.code+=struct.pack('<I',value);self.c('scePthreadSetspecific');self.zero()
  def worker(self):
   self.r('48 8d 3d',0x4508);self.e('31 f6');self.r('48 8d 15',0x1400);self.e('31 c9 45 31 c0');self.c('scePthreadCreate');self.zero()
   self.r('48 8b 3d',0x4508);self.r('48 8d 35',0x4510);self.c('scePthreadJoin');self.zero();self.r('48 8b 05',0x4510);self.eq(0)
  def atexit(self,callback,arg,dso,raw=False):
   self.r('48 8d 3d',callback);self.e('be');self.code+=struct.pack('<I',arg)
   if raw:self.e('ba');self.code+=struct.pack('<I',dso)
   else:self.r('48 8d 15',dso)
   self.c('__cxa_atexit');self.zero()
  def end(self):
   self.e('31 c0 48 83 c4 08 c3');bad=len(self.code);self.e('b8 63 00 00 00 48 83 c4 08 c3')
   for pos in self.fail:struct.pack_into('<i',self.code,pos,bad-pos-4)
   assert len(self.code)<(0xf00 if self.at==0x100 else 0x200),(hex(self.at),len(self.code))
   data[self.at:self.at+len(self.code)]=self.code
 f=Function(0x100)
 if mode in ['bad-info-span','bad-info-identity']:
  if mode=='bad-info-span':f.r('48 8d 3d',65528)
  else:
   f.query(0x8000);f.r('c7 05',0x4600+416,4);f.e('05 00 00 00');f.r('48 8d 3d',0x4600)
  f.c('__pthread_cxa_finalize')
 elif mode in ['non-guest-callback','data-callback','veneer-callback','bad-atexit-callback']:
  f.r('48 8d 3d',0x4500)
  if mode=='non-guest-callback':f.e('be 01 00 00 00')
  elif mode=='data-callback':f.r('48 8d 35',0x4500)
  elif mode=='veneer-callback':f.r('48 8b 35',0x4800+8*names.index('__cxa_finalize'))
  else:
   f.e('bf 01 00 00 00 31 f6 31 d2');f.c('__cxa_atexit')
  if mode!='bad-atexit-callback':f.c('scePthreadKeyCreate');f.e('3d 16 00 02 80');f.check();f.count(0x4500,0)
 else:
  f.create(0x4500,None if mode=='preserve-nondtor'else 0x1000);f.create(0x4504,0x8100);f.set(0x4500,0x42);f.set(0x4504,0x43)
  if mode=='preserve-nondtor':f.worker();f.count(0x4524,1);f.get(0x4500,0x42)
  elif mode=='reentrant-finalize':f.worker()
  elif mode=='global-null':
   f.atexit(0x1200,0x33,0x4008);f.atexit(0x8200,0x44,0xc008)
   f.e('31 ff');f.c('__cxa_finalize');f.count(0x4528,1);f.count(0x452c,1);f.worker();f.count(0x4520,1);f.count(0x4524,1)
   f.e('31 ff');f.c('__cxa_finalize');f.count(0x4528,1);f.count(0x452c,1)
  elif mode=='unknown-dso':
   f.atexit(0x1200,0x33,1,raw=True);f.e('bf 01 00 00 00');f.c('__cxa_finalize');f.count(0x4528,1);f.worker();f.count(0x4520,1);f.count(0x4524,1)
  else:
   f.finalize(0x8100);f.finalize(0x8100);f.get(0x4500,0x42);f.get(0x4504,0x43);f.count(0x4520,0);f.count(0x4524,0)
   f.worker();f.count(0x4520,1);f.count(0x4524,0)
   f.atexit(0x1200,0x33,0x4008);f.r('48 8d 3d',0x4008);f.c('__cxa_finalize');f.count(0x4528,1);f.count(0x4520,2)
   f.r('48 8d 3d',0x4008);f.c('__cxa_finalize');f.count(0x4528,1);f.worker();f.count(0x4520,2);f.count(0x4524,0)
   f.r('8b 3d',0x4500);f.c('scePthreadKeyDelete');f.zero();f.r('48 8d 3d',0x4500);f.e('31 f6');f.c('scePthreadKeyCreate');f.zero();f.get(0x4500,0)
 f.end()
 f=Function(0x1000);f.e('48 83 ff 11');f.check()
 if mode=='reentrant-finalize':f.finalize(0x1000)
 f.r('ff 05',0x4520);f.end()
 f=Function(0x8100);f.e('48 83 ff 22');f.check()
 if mode=='preserve-nondtor':f.get(0x4500,0x11)
 f.r('ff 05',0x4524);f.end()
 f=Function(0x1400);f.set(0x4500,0x11);f.set(0x4504,0x22);f.end()
 f=Function(0x1200);f.e('48 83 ff 33');f.check();f.r('ff 05',0x4528)
 if mode=='valid':f.worker();f.count(0x4520,2)
 f.end()
 f=Function(0x8200);f.e('48 83 ff 44');f.check();f.r('ff 05',0x452c);f.end()
 for at,to in fix:struct.pack_into('<i',data,at,to-at-4)
 segs=[(0,0x4000,5),(0x4000,0x4000,6),(0x8000,0x4000,5),(0xc000,0x4000,6)]
 boot=b'BBPROBE5'+struct.pack('<6Q',len(data),0x100,4,len(names),len(names),1)+struct.pack('<6Q',0x4000,0x4100,8,16,16,1)
 boot+=struct.pack('<7Q',0x8000,0x8000,0x8000,0xc100,32,16,2)+struct.pack('<Q',0)
 boot+=b''.join(struct.pack('<3Q',*s)for s in segs)+b''.join(nids[n].encode().ljust(128,b'\0')for n in names)
 boot+=b''.join(struct.pack('<4Q',0x4800+8*i,1,i,0)for i in range(len(names)))+data
 metadata=metadata[:8]+hashlib.sha256(boot).digest()+metadata[40:]
 return boot,metadata
