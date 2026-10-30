#!/usr/bin/env python3
"""Isolated local bbcpu producer: effective ELF -> upstream tools -> immutable ARM64 products.

No game selection, saves, UI, FEX execution or renderer implementation lives
here. The existing launcher supplies the effective input and selected tools.
"""
from pathlib import Path
import argparse, fcntl, hashlib, importlib.util, json, os
import shlex, shutil, stat, struct, subprocess, tempfile, threading, time, uuid
from local_metal_builder import canonical, child_directory, regular_bytes, tool_fingerprint
from native_package_assets import Relocator, commands, loads, _system
from bbcpu_compile_schedule import Memory as CompileMemory,run as compile_schedule

SOURCE=Path(__file__).resolve().parents[1]
VENDOR=SOURCE/'vendor/bbcpu'
spec=importlib.util.spec_from_file_location('_bbcpu_original_library',VENDOR/'tools/recomp/build_library.py')
original=importlib.util.module_from_spec(spec);spec.loader.exec_module(original)

def need(ok,why):
 if not ok:raise ValueError(why)
def key(value):return hashlib.sha256(canonical(value)).hexdigest()
def digest(path):
 with Path(path).open('rb')as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def snapshot(path,directory=False):
 path=Path(path).absolute();s=path.lstat()
 # Declared include directories (Homebrew commonly uses admin-writable dirs)
 # are change evidence, not a file admission. Opened input/tool files still
 # require the existing non-group/world-writable rule.
 need(path==path.resolve(strict=True)and(stat.S_ISDIR(s.st_mode)if directory else stat.S_ISREG(s.st_mode))and s.st_uid in(0,os.getuid())and(directory or not s.st_mode&0o022),'canonical trusted input required: '+str(path))
 return dict(path=str(path),directory=directory,stat=[s.st_dev,s.st_ino,s.st_uid,s.st_mode,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns])
def unchanged(pins):
 try:return bool(pins)and all(snapshot(row['path'],row.get('directory',False))==row for row in pins)
 except(OSError,ValueError):return False
def read_json(path):return json.loads(regular_bytes(path,4*1024*1024))
def write_json(path,value):
 fd,name=tempfile.mkstemp(prefix=path.name+'.',suffix='.new',dir=path.parent)
 with os.fdopen(fd,'wb')as stream:stream.write(canonical(value)+b'\n');stream.flush();os.fsync(stream.fileno())
 os.replace(name,path)
def emit(event,**fields):print(json.dumps(dict(event=event,**fields)),flush=True)
def safe_env():
 return {k:v for k,v in os.environ.items()if not k.startswith(('BB_','BBCPU_','DYLD_'))}
def work_root(path):
 root=path.resolve(strict=True)
 need(path.absolute()==root and root.stat().st_uid==os.getuid()and not root.stat().st_mode&0o022,'owned canonical isolated CPU work root required')
 need(any(root.is_relative_to(p)and root!=p for p in(Path('/private/tmp'),Path('/Users/Shared/BloodEchoesDevelopment'))),'this development producer requires isolated scratch')
 marker=root/'.bbcpu-local-v1'
 if not marker.exists():
  need(not any(root.iterdir()),'new CPU work root must be empty; existing caches are not adopted')
  fd=os.open(marker,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
  with os.fdopen(fd,'wb')as stream:stream.write(b'BloodEchoes.bbcpu.local.v1\n')
 need(regular_bytes(marker,128)==b'BloodEchoes.bbcpu.local.v1\n','CPU work marker mismatch')
 return root
def content_closure(rows,entry):
 return dict(executable=rows[str(entry.resolve())],dependencies=sorted(value for path,value in rows.items()if path!=str(entry.resolve())))
def compile_flags(resource,includes):
 return [*original.CFLAGS,'-DBBCPU_NATIVE_TSC=1','-isystem',str(resource),'-isystem',str(VENDOR/'tools/recomp/sdkless'),*[value for path in includes for value in('-I',str(path))]]

class Producer:
 def __init__(self,args):
  self.a=args;self.count=dict(generator_processes=0,preprocessor_processes=0,object_compiles=0,object_reuses=0,link_processes=0)
  self.env=safe_env();self.pins={};self.root=args.work;self.mutex=threading.Lock()
  self.logs=child_directory(self.root,'logs',create=True)
  self.session=uuid.uuid4().hex;self.log_sequence=0
 def pin(self,path):
  row=snapshot(path)
  with self.mutex:self.pins[row['path']]=row
  return row
 def increment(self,name):
  with self.mutex:self.count[name]+=1
 def run(self,argv,name,*,env=None,timeout=900):
  with self.mutex:self.log_sequence+=1;sequence=self.log_sequence
  result=subprocess.run(list(map(str,argv)),env=env or self.env,capture_output=True,text=True,timeout=timeout)
  (self.logs/f'{self.session}-{sequence:04}-{name}.log').write_text(result.stdout+result.stderr)
  need(result.returncode==0,f'{name} failed ({result.returncode}): '+result.stderr[-1600:]);return result.stdout
 def closure(self,binary,roots):
  # Reuse the package resolver's exact load-command/runpath rules read-only;
  # never copy/relocate a tool, guess a missing dylib, or search PATH.
  resolver=Relocator(self.root,search_dirs=roots);rows={};seen=set()
  def visit(path,executable,inherited):
   path=Path(path).resolve(strict=True)
   if path in seen:return
   need(len(seen)<128,'CPU tool dependency budget exceeded');seen.add(path)
   before=self.pin(path);rows[str(path)]=tool_fingerprint(path)['sha256']
   # System absolute dependencies remain the supported macOS ABI, as in the
   # existing package resolver. Do not mistake /usr/lib/swift's shared-cache
   # runpath for an external, copied compiler dependency.
   metadata=[row for row in commands(path)if not(row[0]=='LC_RPATH'and _system(row[1]))]
   try:runpaths=resolver._runpaths(path,executable,inherited,metadata)
   except ValueError as error:raise ValueError(f'{path}: {error}; declared rpaths={metadata}')from error
   for ref in loads(path):
    if not _system(ref):
     try:dep=resolver._resolve(ref,path,executable,runpaths)
     except ValueError as error:raise ValueError(f'{path}: {ref}: {error}')from error
     visit(dep,executable,runpaths)
   need(snapshot(path)==before,'CPU tool changed during dependency registration')
  visit(binary,binary,());return rows
 def object(self,source,flags,salt,objects,scratch):
  # Compile the very preprocessed bytes used for the key, not a second read of
  # possibly changed headers. Comments/names and include-directory locations
  # cannot masquerade as machine-code dependencies.
  tag=source.stem;preprocessed=scratch/(tag+'.i');deps=scratch/(tag+'.d')
  self.run([self.a.compiler,*flags,'-E','-P','-MD','-MF',deps,'-MT','bbcpu',source,'-o',preprocessed],'preprocess-'+tag)
  self.increment('preprocessor_processes')
  deptext=deps.read_text().replace('\\\n','');need(deptext.startswith('bbcpu:'),'unexpected CPU dependency output')
  for name in shlex.split(deptext[len('bbcpu:'):]):
   path=Path(name).resolve(strict=True);self.pin(path)
   if not path.is_relative_to(self.root):
    with self.mutex:self.pins[str(path.parent)]=snapshot(path.parent,True)
  fingerprint=key(dict(schema=1,compiler=salt,options=original.CFLAGS,clock='native-tsc-abi5',preprocessed_sha256=digest(preprocessed)))
  index=objects/(fingerprint+'.json')
  if index.exists():
   try:old=read_json(index)
   except(Exception):old={}
   file=objects/old.get('file','')
   if file.parent==objects and file.is_file()and old.get('key')==fingerprint:
    before=snapshot(file)
    if digest(file)==old.get('sha256')and snapshot(file)==before:
     self.increment('object_reuses');return file,fingerprint
   shutil.copyfile(index,objects/(fingerprint+'-old-index-'+uuid.uuid4().hex+'.json'))
  output=objects/(fingerprint+'-'+uuid.uuid4().hex+'.o')
  self.run([self.a.compiler,*flags,'-x','c','-c',preprocessed,'-o',output],'compile-'+tag)
  self.increment('object_compiles')
  write_json(index,dict(schema=1,key=fingerprint,file=output.name,sha256=digest(output)))
  return output,fingerprint
 def prepare(self):
  started=time.monotonic();a=self.a
  effective=a.effective.resolve(strict=True);need(a.effective.absolute()==effective,'effective input is linked')
  descriptor=effective/'effective-input.json';doc=read_json(descriptor)
  need(doc.get('schema')==1 and len(doc.get('effective_identity',''))==64,'effective input descriptor required')
  abi=read_json(a.abi);need(abi.get('abi')==5 and abi.get('architecture')=='arm64'and abi.get('native_tsc')is True,'actual native-clock CPU ABI required')
  basic=[descriptor,effective/'effective.elf',effective/'pointers.txt',a.abi,a.tools/'bbcpu-scan',a.tools/'bbrecomp',a.compiler,a.linker,(a.sdk/'usr/lib/libSystem.tbd').resolve(strict=True),Path(__file__).resolve(),VENDOR/'annotations/functions.tsv',Path(original.__file__).resolve()]
  for path in basic:self.pin(path)
  selected_inputs=list(self.pins.values())
  request=dict(schema=1,effective_identity=doc['effective_identity'],cpu_contract=abi['header_contract_sha256'],compiler=str(a.compiler),linker=str(a.linker),sdk=str(a.sdk),tools=str(a.tools),include=[str(p.resolve(strict=True))for p in a.include],dependency_roots=sorted(str(p.resolve(strict=True))for p in a.dependency_root),options=original.CFLAGS,allowed_undefined=original.ALLOWED_UNDEFINED,clock='native-tsc-abi5')
  index=self.root/('ready-'+key(request)+'.json')
  # A final library does not depend on retaining its intermediate .o/C files.
  # The entry records real input/closure pins and the final immutable products.
  reusable_compiled=None;saved={}
  if index.exists():
   try:saved=read_json(index)
   except(Exception):saved={}
   inputs_match=saved.get('schema')==1 and saved.get('request')==request and saved.get('selected_inputs')==selected_inputs and unchanged(saved.get('pins',[]))
   if inputs_match:
    artifact=Path(saved.get('result',{}).get('artifact','/'))
    product_names={Path(p['path']).name for p in saved.get('products',[])}
    products_match=artifact.is_relative_to(self.root/'generations')and product_names=={'effective.elf','librecomp.dylib','bbcpu.aot-receipt'}and len(saved.get('products',[]))==3 and all(Path(p['path']).parent==artifact for p in saved['products'])
    if products_match and unchanged(saved.get('products',[])):
     emit('done',**saved['result'],reused_ready=True,seconds=time.monotonic()-started,counters=self.count);return saved['result']
    # A broken final library/receipt needs a relink, not another whole-game
    # scan/preprocess pass, when its actual immutable objects still verify.
    candidate=saved.get('compiled',[])
    if candidate and saved.get('linker'):
     valid=True
     for row in candidate:
      file=Path(row['file'])
      try:valid=valid and file.parent==self.root/'objects'and snapshot(file)['stat'][2]==os.getuid()and digest(file)==row['sha256']
      except(OSError,ValueError):valid=False
     if valid:reusable_compiled=[(Path(row['file']),row['fingerprint'])for row in candidate]
  if reusable_compiled is None:
   need(digest(effective/'effective.elf')==doc['effective_elf_sha256'],'effective ELF digest mismatch')
   for name,expected in abi['headers'].items():
    path=VENDOR/name;self.pin(path);need(digest(path)==expected,'CPU build/header contract changed; rebuild selected tools')
   need(bool(abi.get('dependency_headers')),'selected CPU decoder ABI header contract absent')
   for name,expected in abi['dependency_headers'].items():
    rel=Path(name);need(not rel.is_absolute()and '..'not in rel.parts,'invalid CPU dependency header role')
    found=next((root/rel for root in a.include if(root/rel).is_file()),None)
    need(found is not None,'selected CPU dependency header missing: '+name)
    path=found.resolve(strict=True);self.pin(path);need(digest(path)==expected,'CPU dependency ABI header differs from compiled host: '+name)
   need(self.run([a.tools/'bbrecomp','--api'],'api',timeout=10).strip()=='5','selected generator ABI differs from CPU')
   need(self.run([a.tools/'bbrecomp','--abi-identity'],'compiled-cpu-contract',timeout=10).strip()==abi['header_contract_sha256'],'selected generator compiled CPU contract differs from descriptor')
   roots=tuple(p.resolve(strict=True)for p in a.dependency_root)
   tools={name:content_closure(self.closure(a.tools/name,roots),a.tools/name)for name in('bbcpu-scan','bbrecomp')}
   compiler=content_closure(self.closure(a.compiler,roots),a.compiler)
   linker=content_closure(self.closure(a.linker,roots),a.linker)
   resource=Path(self.run([a.compiler,'-print-resource-dir'],'resource',timeout=15).strip()).resolve(strict=True)/'include'
   need(resource.is_dir(),'selected compiler resource headers absent')
   include=[VENDOR/'src',*a.include];sdkless=VENDOR/'tools/recomp/sdkless'
   flags=compile_flags(resource,include)
   compiler_key=key(compiler)
   for directory in [resource,sdkless,*include]:
    directory=directory.resolve(strict=True);self.pins[str(directory)]=snapshot(directory,True)
   generation_key=key(dict(effective=doc['effective_identity'],tools=tools,annotations=digest(VENDOR/'annotations/functions.tsv'),generator_options='upstream-defaults-native-tsc-v1'))
   attempts=child_directory(self.root,'generations',create=True);objects=child_directory(self.root,'objects',create=True)
   stage=attempts/(generation_key+'-'+self.session);stage.mkdir(mode=0o700);scan=stage/'scan';scan.mkdir();gen=stage/'gen';gen.mkdir();scratch=stage/'preprocessed';scratch.mkdir()
   emit('step',step='scan',effective_identity=doc['effective_identity'])
   self.run([a.tools/'bbcpu-scan',effective/'effective.elf',scan,effective/'pointers.txt'],'scan');self.count['generator_processes']+=1
   functions=scan/'functions.tsv';offsets=[];exclusions=doc.get('excluded_code_slots',[])
   for line in functions.read_text().splitlines()[1:]:
    if not line.strip():continue
    fields=line.split('\t');offset=int(fields[0],0);size=int(fields[1],0)
    if not any(offset<start+length and start<offset+size for start,length in exclusions):offsets.append(fields[0])
   need(offsets,'no statically admissible functions found');listing=scan/'all.txt';listing.write_text('\n'.join(offsets)+'\n')
   env={**self.env,'BB_JIT':'0','BB_RECOMP_ANNOTATIONS':str(VENDOR/'annotations/functions.tsv')}
   self.run([a.tools/'bbrecomp',effective/'effective.elf',functions,gen/'recomp.c','@'+str(listing)],'generate',env=env);self.count['generator_processes']+=1
   sources=sorted(gen.glob('recomp*.c'));need(sources and len(sources)<=8192,'invalid generated source count')
   # Limit concurrent compilers; no game/GPU runs alongside this producer.
   memory=CompileMemory()
   try:
    compiled=compile_schedule(sources,lambda source:self.object(source,flags,compiler_key,objects,scratch),
      maximum=2 if a.jobs=='auto'else int(a.jobs),observe=memory,
      progress=lambda **row:emit('compile-progress',**row))
   finally:memory.close()
   function_count=len(offsets);source_count=len(sources)
  else:
   compiled=reusable_compiled;linker=saved['linker'];self.count['object_reuses']=len(compiled)
   self.pins.update({p['path']:p for p in saved['pins']})
   attempts=child_directory(self.root,'generations',create=True);stage=attempts/('repair-'+self.session);stage.mkdir(mode=0o700)
   function_count=saved['result']['functions'];source_count=saved['result']['generated_units']
   emit('step',step='relink-verified-objects',objects=len(compiled))
  artifact_key=key(dict(cpu_contract=abi['header_contract_sha256'],effective=doc['effective_identity'],objects=[fp for _,fp in compiled],linker=linker,link_options='apple-arm64-macos15-nostdlib-v1'))
  artifact=stage/'artifact';artifact.mkdir(mode=0o700)
  shutil.copyfile(effective/'effective.elf',artifact/'effective.elf');library=artifact/'librecomp.dylib'
  allowed=['-Wl,-U,'+symbol for symbol in original.ALLOWED_UNDEFINED]
  # Use the selected clang's Darwin linker. The produced library has no FEX,
  # renderer, Zydis or SDK runtime dependency; allowed undefineds are libSystem.
  dependency_file=stage/'link.dependencies'
  self.run([a.compiler,'--target=arm64-apple-macos15','-dynamiclib','-fuse-ld='+str(a.linker),'-isysroot',a.sdk,'-nostdlib','-Wl,-undefined,error','-Wl,-dependency_info,'+str(dependency_file),*allowed,'-install_name','@rpath/librecomp.dylib',*[obj for obj,_ in compiled],'-lSystem','-o',library],'link');self.count['link_processes']+=1
  # Apple's ld records the actual stubs it consumed. Register them rather than
  # pretending that a compiler version string identifies the entire link.
  raw=dependency_file.read_bytes();at=0;link_inputs={}
  while at<len(raw):
   opcode=raw[at];at+=1;end=raw.find(b'\0',at);need(end>=at,'invalid linker dependency record')
   value=raw[at:end].decode();at=end+1
   if opcode==0x10:
    path=Path(value).resolve(strict=True)
    if not path.is_relative_to(self.root):self.pin(path);link_inputs[str(path.relative_to(a.sdk))if path.is_relative_to(a.sdk)else path.name]=digest(path)
  artifact_key=key(dict(product=artifact_key,link_inputs=link_inputs))
  self.run(['/usr/bin/codesign','--force','--sign','-',library],'sign')
  library_sha=digest(library)
  (artifact/'bbcpu.aot-receipt').write_bytes(struct.pack('<8sQQ32s32s32s32s',b'BBAOT005',5,0x0100000c,bytes.fromhex(abi['header_contract_sha256']),bytes.fromhex(doc['effective_identity']),bytes.fromhex(doc['effective_elf_sha256']),bytes.fromhex(library_sha)))
  # Intermediate generated C paths are output provenance, not future warm-start
  # inputs. Actual shared headers/tool closures remain registered.
  pins=[row for path,row in self.pins.items()if not Path(path).is_relative_to(stage)]
  need(unchanged(pins),'CPU preparation inputs changed before publication')
  result=dict(artifact=str(artifact),artifact_key=artifact_key,functions=function_count,generated_units=source_count,library_bytes=library.stat().st_size,library_sha256=library_sha,effective_identity=doc['effective_identity'],header_contract_sha256=abi['header_contract_sha256'])
  products=[snapshot(artifact/name)for name in('effective.elf','librecomp.dylib','bbcpu.aot-receipt')]
  if index.exists():shutil.copyfile(index,index.with_name(index.stem+'-previous-'+self.session+'.json'))
  write_json(index,dict(schema=1,request=request,selected_inputs=selected_inputs,pins=pins,products=products,result=result,linker=linker,compiled=[dict(file=str(file),fingerprint=fp,sha256=digest(file))for file,fp in compiled]))
  emit('done',**result,reused_ready=False,seconds=time.monotonic()-started,counters=self.count);return result

def main():
 p=argparse.ArgumentParser(description=__doc__)
 for name in('effective','work','tools','abi','compiler','linker','sdk'):p.add_argument('--'+name,type=Path,required=True)
 p.add_argument('--dependency-root',type=Path,action='append',required=True);p.add_argument('--include',type=Path,action='append',default=[])
 p.add_argument('--jobs',choices=('auto','1','2'),default='auto');a=p.parse_args()
 a.compiler=a.compiler.resolve(strict=True);a.linker=a.linker.resolve(strict=True);a.sdk=a.sdk.resolve(strict=True);a.tools=a.tools.resolve(strict=True);a.abi=a.abi.resolve(strict=True);a.work=work_root(a.work)
 lock=os.open(a.work/'.bbcpu-writer.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
 try:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);Producer(a).prepare()
 finally:os.close(lock)
if __name__=='__main__':
 try:main()
 except(Exception)as error:emit('error',message=str(error));raise SystemExit(1)
