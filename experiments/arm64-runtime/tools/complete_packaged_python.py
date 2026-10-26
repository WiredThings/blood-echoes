"""Complete admitted SQLite/hashlib extensions in a NEW copied package Python."""
from pathlib import Path
import hashlib,json,os,shutil,stat,struct,subprocess
SAFE_ENV={'PATH':'/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin','PYTHONDONTWRITEBYTECODE':'1'}
def need(v,m):
 if not v:raise ValueError(m)
def sha(p):
 with Path(p).open('rb')as f:return hashlib.file_digest(f,'sha256').hexdigest()
def run(args):return subprocess.check_output(list(map(str,args)),text=True,env=SAFE_ENV,timeout=20)
EXTENSIONS={
 '_sqlite3':dict(key='sqlite',kind='sqlite',receipt='SQLITE-CLOSURE.json'),
 '_hashlib':dict(key='hashlib',kind='hashlib',receipt='HASHLIB-CLOSURE.json'),
}
def extension_spec(module):
 need(module in EXTENSIONS,'unsupported packaged extension')
 return EXTENSIONS[module]
def probe_code(module):
 extension_spec(module)
 if module=='_sqlite3':
  return "import sqlite3,_sqlite3,json,tempfile,sys;from pathlib import Path;tmp=tempfile.TemporaryDirectory(dir=sys.argv[1],prefix='.sqlite-probe-');p=Path(tmp.name)/'check.db';db=sqlite3.connect(p);db.execute('create table t(v)');db.execute('insert into t values(11)');db.commit();db.close();db=sqlite3.connect(p);assert db.execute('select v from t').fetchone()==(11,);db.close();tmp.cleanup();print(json.dumps(dict(module=_sqlite3.__file__,sqlite=sqlite3.sqlite_version)))"
 return "import hashlib,_hashlib,json;assert hashlib.sha256 is _hashlib.openssl_sha256;assert hashlib.sha256(b'').hexdigest()=='e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855';h=hashlib.sha256(b'a');g=h.copy();h.update(b'bc');g.update(memoryview(b'bc'));assert h.hexdigest()==g.hexdigest()=='ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad';print(json.dumps(dict(module=_hashlib.__file__,implementation='_hashlib.openssl_sha256')))"
def info(p):
 code="import sys,sysconfig,platform,importlib.util,json;print(json.dumps(dict(version=sys.version,minor='%s.%s'%sys.version_info[:2],arch=platform.machine(),soabi=sysconfig.get_config_var('SOABI'),suffix=sysconfig.get_config_var('EXT_SUFFIX'),prefix=sys.prefix,sqlite=importlib.util.find_spec('_sqlite3').origin if importlib.util.find_spec('_sqlite3') else None,hashlib=importlib.util.find_spec('_hashlib').origin if importlib.util.find_spec('_hashlib') else None)))"
 return json.loads(run([p,'-I','-B','-c',code]))
def edges(p):return [s.strip().split(' (compatibility')[0]for s in run(['/usr/bin/otool','-L',p]).splitlines()[1:]]
def system(s):return s.startswith(('/usr/lib/','/System/Library/'))
def native(p):
 p=Path(p).resolve(strict=True);s=p.stat();need(stat.S_ISREG(s.st_mode)and s.st_uid==os.getuid(),'owned native provider required')
 need(run(['/usr/bin/lipo','-archs',p]).strip()=='arm64','native Python closure must be arm64')
 subprocess.run(['/usr/bin/codesign','--verify','--strict',str(p)],check=True,env=SAFE_ENV,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
 return p

def existing_closure(root,target,target_info,*,module_name='_sqlite3'):
    spec=extension_spec(module_name)
    # Inspect before importing sqlite3. Do not let dyld search outside the copy.
    module=Path(target_info[spec['key']]).resolve(strict=True)
    need(module.is_relative_to(root)and module.name==module_name+target_info['suffix'],
         'existing extension outside copied Python / wrong ABI suffix')
    seen={};visiting=set()
    def visit(path):
        path=Path(path).resolve(strict=True)
        need(path.is_relative_to(root),'existing extension dependency outside copied Python')
        if path in seen:return
        need(path not in visiting,'existing native dependency cycle');visiting.add(path)
        native(path);links=edges(path)
        with path.open('rb')as f:head=f.read(16)
        # Framework binaries may have no .dylib suffix. MH_DYLIB owns an ID row.
        is_library=len(head)==16 and struct.unpack('<4I',head)[0]==0xfeedfacf and struct.unpack('<4I',head)[3]==6
        if is_library:links=links[1:]
        seen[path]=sha(path)
        for edge in links:
            if system(edge):continue
            if edge.startswith('@loader_path/'):dep=path.parent/edge[len('@loader_path/'):]
            elif edge.startswith('@executable_path/'):dep=target.parent/edge[len('@executable_path/'):]
            elif edge.startswith('/'):dep=Path(edge)
            else:raise ValueError('unresolved existing extension dependency: '+edge)
            visit(dep)
        visiting.remove(path)
    visit(module)
    # Probe only after every native load edge is confined to the copied tree.
    result=json.loads(run([target,'-I','-B','-c',probe_code(module_name),root]))
    need(Path(result['module']).resolve(strict=True)==module,'existing extension import identity changed')
    for path,want in seen.items():need(sha(path)==want,'existing native bytes changed')
    receipt=dict(schema=1,kind='packaged-python-'+spec['kind'],status='existing',python_version=target_info['version'],
        architecture='arm64',soabi=target_info['soabi'],probe=result,
        files=[dict(path=str(p.relative_to(root)),sha256=h)for p,h in seen.items()],
        source_modified=False,native_bytes_modified=False,provider_consulted=False)
    if module_name=='_sqlite3':receipt['sqlite']=result['sqlite']
    previous=root/spec['receipt']
    if previous.exists():receipt['previous_receipt_sha256']=sha(previous)
    previous.write_text(json.dumps(receipt,indent=2)+'\n');return receipt

def complete(python_root,*,writable_stage,provider=None,module_name='_sqlite3'):
 spec=extension_spec(module_name)
 root=Path(python_root).absolute();stage=Path(writable_stage).resolve(strict=True)
 need(root==stage/'python'and root==root.resolve(strict=True)and root.is_dir(),'new copied package Python required')
 target=root/'bin/python3';target_info=info(target);need(target_info['arch']=='arm64','target Python must be arm64')
 if target_info[spec['key']]is not None:return existing_closure(root,target,target_info,module_name=module_name)
 provider=Path(provider)if provider else Path('/opt/homebrew/opt/python@'+target_info['minor']+'/bin/python'+target_info['minor'])
 provider_info=info(provider)
 for name in('version','arch','soabi','suffix'):need(target_info[name]==provider_info[name],'provider/target Python mismatch: '+name)
 need(provider_info[spec['key']],'matching provider lacks '+module_name)
 src=native(provider_info[spec['key']]);dest=root/'lib'/('python'+target_info['minor'])/'lib-dynload'/src.name
 need(src.name==module_name+target_info['suffix']and dest.parent.is_dir(),'matching extension and packaged stdlib required')
 need(not dest.exists()and target_info[spec['key']]is None,'preserve existing package extension; fresh missing-module repair only')
 mapping={src:dest};sources={};visiting=set()
 def collect(path):
  path=native(path);need(path not in visiting,'native dependency cycle');visiting.add(path)
  links=edges(path);links=links[1:]if path.suffix=='.dylib'else links;sources[path]=links
  for link in links:
   if system(link):continue
   need(link.startswith('/'),'unresolved source dependency: '+link);dep=native(link)
   if dep not in mapping:
    out=root/'lib'/dep.name;need(not out.exists()and out not in mapping.values(),'preserve existing dependency / basename collision')
    mapping[dep]=out;collect(dep)
  visiting.remove(path)
 collect(src);original={str(p):sha(p)for p in mapping}
 for old,new in mapping.items():new.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(old,new);need(sha(new)==original[str(old)],'source changed while copying')
 # Rewrite only new copies. Source .so and source dylib signatures remain intact.
 for old,new in mapping.items():
  for link in sources[old]:
   if system(link):continue
   dep=Path(link).resolve(strict=True);relative='@loader_path/'+os.path.relpath(mapping[dep],new.parent)
   subprocess.run(['/usr/bin/install_name_tool','-change',link,relative,str(new)],check=True,env=SAFE_ENV)
  if new.suffix=='.dylib':subprocess.run(['/usr/bin/install_name_tool','-id','@loader_path/'+new.name,str(new)],check=True,env=SAFE_ENV)
  subprocess.run(['/usr/bin/codesign','--force','--sign','-',str(new)],check=True,env=SAFE_ENV,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
 for old,new in mapping.items():
  native(new)
  links=edges(new);links=links[1:]if new.suffix=='.dylib'else links
  for link in links:
   if system(link):continue
   need(link.startswith('@loader_path/'),'external dependency survived relocation')
   resolved=(new.parent/link[len('@loader_path/'):]).resolve(strict=True);need(resolved.is_relative_to(root)and resolved in mapping.values(),'dependency outside copied Python closure')
  need(sha(old)==original[str(old)],'provider changed during completion')
 result=json.loads(run([target,'-I','-B','-c',probe_code(module_name),root]));need(Path(result['module']).resolve(strict=True)==dest.resolve(strict=True),'extension imported outside copied runtime')
 receipt=dict(schema=1,kind='packaged-python-'+spec['kind'],status='repaired',python_version=target_info['version'],architecture='arm64',soabi=target_info['soabi'],provider=str(provider.resolve()),probe=result,files=[dict(path=str(new.relative_to(root)),sha256=sha(new),source=str(old),source_sha256=original[str(old)])for old,new in mapping.items()],source_modified=False)
 if module_name=='_sqlite3':receipt['sqlite']=result['sqlite']
 (root/spec['receipt']).write_text(json.dumps(receipt,indent=2)+'\n');return receipt
