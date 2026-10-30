"""Reuse the original loader to describe this exact private boot; never edit it."""
from pathlib import Path
import argparse,hashlib,struct,sys,os,tempfile,mmap,json,stat,plistlib,importlib.util
PRIVATE_ROOT=Path('/Users/Shared/BloodEchoesDevelopment')
PROJECT=PRIVATE_ROOT/'arm64-runtime-prototype'
SUPPORT=('vendor/blood_echoes/loader/prepare.py','vendor/blood_echoes/loader/game_check.py')
SIZE=424;PAGE=16384

def require(ok,why):
 if not ok:raise ValueError(why)
def owned_file(path,limit,*,code_owners=None):
 path=Path(path).absolute();info=path.lstat()
 require(path==path.resolve(strict=True)and stat.S_ISREG(info.st_mode)and info.st_uid in((os.getuid(),)if code_owners is None else code_owners)and info.st_nlink==1 and 0<info.st_size<=limit,'module helper code must be canonical owned regular file')
 return path
def digest(path):
 with path.open('rb')as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def formal_code_root(root):
 info=root.parent.parent.parent/'Contents/Info.plist'
 if not info.is_file():return False
 return plistlib.loads(owned_file(info,1024**2,code_owners=(0,os.getuid())).read_bytes()).get('CFBundleIdentifier')=='io.github.bbport.optimized'
def declared_code_root(executable):
 """Original CPU roles and the explicitly declared independent QA CPU role."""
 executable=Path(executable).absolute()
 layouts=('bin/bb-probe-metal','bin/Bloodborne Game.app/Contents/MacOS/bb-probe-metal','bin/Bloodborne bbcpu Game.app/Contents/MacOS/bb-probe-metal')
 for relative in layouts:
  parts=Path(relative).parts
  if executable.parts[-len(parts):]!=parts:continue
  root=executable.parents[len(parts)-1]
  app=root.parent.parent.parent
  require(root.name=='bbport'and root.parent.name=='Resources'and root.parent.parent.name=='Contents'and app.suffix=='.app','module helper bundle layout is not declared')
  if formal_code_root(root):
   owned_file(executable,256*1024**2,code_owners=(0,os.getuid()))
  else:
   require(app.parent==PROJECT/'apps','module helper bundle is not a declared private QA application')
   owned_file(executable,256*1024**2)
  require(root==root.resolve(strict=True),'linked module helper code root')
  return root,relative
 raise ValueError('module helper executable role is not declared')
def packaged_loader(code_root,executable,*,script=None):
 """Bind code to frozen bundle manifests; never fall back after any refusal."""
 root,role=declared_code_root(executable)
 formal=formal_code_root(root)
 code_args={'code_owners':(0,os.getuid())}if formal else{}
 require(Path(code_root).absolute()==root,'module helper code root differs from actual executable')
 helper=owned_file(root/'native-runtime-tools/prepare_module_metadata.py',2*1024**2,**code_args)
 require(Path(script if script is not None else __file__).absolute()==helper,'module helper is not running from its frozen bundle role')
 manifest=json.loads(owned_file(root/'ARM64-LOCAL-PREPARATION.json',1024**2,**code_args).read_bytes())
 require(manifest['schema']==1 and ((manifest.get('production')is True)if formal else(manifest['host_local_only']is True and manifest['runtime_project']==str(PROJECT))),'module helper requires declared prepared component')
 require(manifest['runtime_tools']['prepare_module_metadata.py']==digest(helper),'module helper identity changed')
 support=manifest['runtime_support'];require(set(support)==set(SUPPORT),'module helper original parser closure differs')
 for relative in SUPPORT:
  path=owned_file(root/relative,2*1024**2,**code_args)
  require(support[relative]==digest(path),'module helper original parser identity changed: '+relative)
 native=json.loads(owned_file(root/'NATIVE-METAL.json',1024**2,**code_args).read_bytes())
 cpu_profile=(native.get('game_runtime')=='fex-arm64' or
              (not formal and native.get('game_runtime')=='arm64'and native.get('cpu_backends')==['fex','bbcpu']))
 require(native['schema']==1 and cpu_profile and native['production']is formal,'module helper requires declared native runtime profile')
 require(native['frozen_runtime'][role]==digest(Path(executable)),'module helper actual executable identity changed')
 if role=='bin/Bloodborne bbcpu Game.app/Contents/MacOS/bb-probe-metal':
  require(not formal and native.get('game_runtime')=='arm64'and native.get('cpu_backends')==['fex','bbcpu'],'independent CPU metadata role requires explicit QA capabilities')
  cpu=json.loads(owned_file(root/'BBCPU-COMPONENT.json',1024**2).read_bytes())
  require(cpu.get('schema')==1 and cpu.get('development_only')is True and cpu.get('backend')=='bbcpu'and cpu.get('cpu')==dict(path=role,sha256=digest(Path(executable))),'module helper independent CPU role differs from frozen component')
 return root/'vendor/blood_echoes/loader'
def formal_inputs(code_root,executable,boot,game,out,environment):
 root,role=declared_code_root(executable)
 require(Path(code_root).absolute()==root,'formal metadata code root mismatch')
 if not formal_code_root(root):return None
 # Verify the sibling before executing it under isolated Python (-I).
 manifest=json.loads(owned_file(root/'ARM64-LOCAL-PREPARATION.json',1024**2,code_owners=(0,os.getuid())).read_bytes())
 path=owned_file(root/'native-runtime-tools/launcher_runtime_profile.py',2*1024**2,code_owners=(0,os.getuid()))
 require(digest(path)==manifest['runtime_tools']['launcher_runtime_profile.py'],'formal metadata profile tool identity')
 name='_blood_echoes_metadata_profile_'+digest(path);spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
 # This helper runs under Python -I, outside the launcher's import scope.
 # Keep its original self-contained full code gate until the native/child
 # registration handoff exists; do not add an unverified sys.path fallback.
 admitted=module.bundle(root);require(admitted is not None,'formal metadata outer bundle admission')
 return module.metadata_inputs(admitted,boot,game,out,environment)
def select_loader(*,code_root=None,executable=None,loader=None,script=None):
 script=Path(script if script is not None else __file__).absolute()
 if code_root is not None or executable is not None:
  require(code_root is not None and executable is not None and loader is None,'bundle module helper requires exact root/runtime and no loader override')
  return packaged_loader(code_root,executable,script=script)
 # A bundle invocation missing its role arguments is an error, not permission
 # to silently use old tools from P. Only the old unbundled developer role remains.
 require(not any(part.endswith('.app')for part in script.parts),'bundle module helper role arguments missing')
 require(script==owned_file(PROJECT/'tools/prepare_module_metadata.py',2*1024**2),'unbundled module helper must be the fixed development tool')
 return Path(loader)if loader is not None else PROJECT/'vendor/blood_echoes/loader'
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

def prepare(boot,game,out,loader,*,formal_inputs=None):
 boot=boot.resolve(strict=True);game=game.resolve(strict=True);out=out.absolute();loader=loader.resolve(strict=True)
 if formal_inputs is None:
  for path in [boot,game,out.parent.resolve(strict=True),loader]:require(path.is_relative_to(PRIVATE_ROOT),'inputs/output must stay in private Shared tree')
 else:require((boot,game,out,loader)==(formal_inputs.boot,formal_inputs.game,formal_inputs.out,formal_inputs.loader),'formal module inputs differ from admitted roles')
 require(not out.is_symlink(),'linked metadata destination')
 sys.path.insert(0,str(loader));from prepare import parse_self,unpack
 require(Path(sys.modules['prepare'].__file__).resolve(strict=True)==loader/'prepare.py','module parser imported outside selected original loader')
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
 sys.dont_write_bytecode=True
 ap=argparse.ArgumentParser();ap.add_argument('--boot',type=Path,required=True);ap.add_argument('--app0',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);ap.add_argument('--loader-scripts',type=Path);ap.add_argument('--code-root',type=Path);ap.add_argument('--runtime-executable',type=Path);a=ap.parse_args()
 loader=select_loader(code_root=a.code_root,executable=a.runtime_executable,loader=a.loader_scripts)
 formal=formal_inputs(a.code_root,a.runtime_executable,a.boot,a.app0,a.out,os.environ)if a.code_root is not None else None
 prepare(a.boot,a.app0,a.out,loader,formal_inputs=formal)
