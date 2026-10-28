"""Tool environment registration, separate from compiled artifact identity.

Owned receipts plus code-root change history permit reusing a prior *real*
identify. Uncertainty falls back to NativeToolchain's original probes. Actual
compiler invocations retain the full before/after environment checks.
"""
from pathlib import Path
import hashlib,json,os,plistlib,stat
from incremental_file_hashes import stat_stamp
import startup_change_journal as journal

MAX_RECEIPT=256*1024

def readonly_mount(path):
 """A read-only mounted tool image has no FSEvents journal on this host.

 Pin its live mount and actual files instead. This is never a substitute for
 history on mutable directories. The mutable mount-selection parent is watched.
 """
 p=Path(path);v=os.statvfs(p)
 if not v.f_flag&os.ST_RDONLY:return None
 mount=p
 while not os.path.ismount(mount):mount=mount.parent
 return dict(path=str(mount),pin=pin(mount),fsid=v.f_fsid,flags=v.f_flag)

def scopes(chain,selected):
 roots={str(Path(x['path']).parent)for x in chain.pins.values()}|{str(Path(chain.sdk_selection['canonical']).parent)}
 immutable={};mutable=set()
 for name in roots:
  mounted=readonly_mount(name)
  if mounted is None:mutable.add(name)
  else:
   immutable[name]=mounted
   if mounted['path']!='/':mutable.add(str(Path(mounted['path']).parent))
 # SDK updates and selected Xcode/toolchain installations are independent of
 # OS updates. Watch their mutable selection/install roots, not just tools.
 if 'developer_canonical'in selected:
  mutable.add(selected['developer_canonical'])
  system=Path('/Library/Developer')
  if system.is_dir():mutable.add(str(system.resolve(strict=True)))
 return sorted(mutable),immutable

def pin(path):
 p=Path(path).absolute()
 if p.parent!=p.parent.resolve(strict=True)or '..'in p.parts:raise ValueError('tool registration canonical parent')
 s=p.lstat()
 link=stat.S_ISLNK(s.st_mode);directory=stat.S_ISDIR(s.st_mode)
 if not(link or directory or stat.S_ISREG(s.st_mode))or s.st_uid not in(0,os.getuid())or(not directory and s.st_nlink!=1)or(not link and s.st_mode&0o022):raise ValueError('tool registration plain code')
 return stat_stamp(s)+[str(p.resolve(strict=True))]

def context(runner,guard):
 # FixedRunner deliberately ignores DEVELOPER_DIR/TOOLCHAINS. Observe the
 # actual system selection using one small selector query, not xcrun probes.
 selected=runner.run(['/usr/bin/xcode-select','-p'],10,guard).decode().strip()
 if not selected.startswith('/')or '\n'in selected:raise ValueError('developer selection')
 developer=Path(selected).resolve(strict=True);s=developer.stat()
 if not stat.S_ISDIR(s.st_mode)or s.st_uid not in(0,os.getuid())or s.st_mode&0o022:raise ValueError('developer selection ownership')
 version=Path('/System/Library/CoreServices/SystemVersion.plist');before=pin(version)
 if not 0<before[6]<=65536:raise ValueError('bounded system version')
 raw=version.read_bytes()
 if pin(version)!=before:raise ValueError('system changed during selection')
 build=plistlib.loads(raw).get('ProductBuildVersion')
 if not isinstance(build,str)or not 0<len(build)<=256:raise ValueError('system build')
 return dict(developer_selected=selected,developer_canonical=str(developer),
             developer_stamp=stat_stamp(s),os_build=build,machine=os.uname().machine)

class Registration:
 def __init__(self,root,*,quick_context=context):
  from local_metal_builder import child_directory,digest,canonical
  self.root=child_directory(root,'.tool-environments-v1',create=True)
  self.path=self.root/'current.json';self.active=None;self.quick_context=quick_context
  self.stats=dict(reused=0,full_probes=0,selection_checks=0,metadata_checks=0)
  sources=[Path(__file__).resolve(),Path(__file__).with_name('local_metal_builder.py').resolve()]
  # Rule code changes re-register the environment, never change BuildId by
  # themselves. Unrelated UI/logging files are not part of this binding.
  from launcher_runtime_profile import code_bytes
  self.binding=dict(schema=1,policy='fixed-native-tools-registration-v1',rules={str(p):code_bytes(p,content=False)[1]['sha256']for p in sources})
 def _context(self,chain,guard):
  self.stats['selection_checks']+=1;return self.quick_context(chain.runner,guard)
 def files_unchanged(self):
  if self.active is None:return False
  try:
   self.stats['metadata_checks']+=1
   return all(pin(name)==value for name,value in self.active['file_pins'].items())and all(readonly_mount(name)==value for name,value in self.active['readonly'].items())
  except(OSError,ValueError,TypeError):return False
 def current(self,chain,guard):
  if not self.files_unchanged():return False
  try:return self._context(chain,guard)==self.active['context']and journal.unchanged(self.active['journal'],self.active['roots'],code_roots=True)
  except(OSError,ValueError,TypeError,KeyError)as error:
   from local_metal_builder import Stopped
   if isinstance(error,Stopped):raise
   return False
 def load(self,chain,guard):
  from local_metal_builder import canonical,digest,regular_bytes,Stopped
  try:
   s=self.path.lstat()
   if s.st_mode&0o077:raise ValueError('private tool registration')
   doc=json.loads(regular_bytes(self.path,MAX_RECEIPT));body=doc['body']
   if set(doc)!={'body','sha256'}or digest(canonical(body))!=doc['sha256']or body['binding']!=self.binding:raise ValueError('tool registration identity')
   if set(body)!={'binding','context','file_pins','roots','readonly','journal','toolchain'}or not 1<=len(body['file_pins'])<=32 or not 1<=len(body['roots'])<=16:raise ValueError('bounded tool registration')
   value=body['toolchain'];manifest=value['build_manifest']
   if set(value)!={'pins','sdk_selection','build_manifest','build_id','environment'}or not isinstance(manifest,dict)or manifest.get('schema')!=2 or not manifest.get('sdk')or digest(canonical(manifest))!=value['build_id']:raise ValueError('complete registered compiler identity')
   if set(value['pins'])!={'metal','metallib','xcrun'}or set(manifest['tools'])!={'metal','metallib','xcrun'}:raise ValueError('complete registered tools')
   if any(v['sha256']!=manifest['tools'][k]['sha256']for k,v in value['pins'].items()):raise ValueError('tool digest/manifest mismatch')
   names={v['path']for v in value['pins'].values()}|set(value['sdk_selection']['pins'])|set(value['environment']['pins'])
   from types import SimpleNamespace
   roots,immutable=scopes(SimpleNamespace(**value),body['context'])
   if set(body['file_pins'])!=names or body['roots']!=roots or body['readonly']!=immutable:raise ValueError('complete registered tool/SDK pin set')
   if body['context']['os_build']!=manifest['os_build']:raise ValueError('registered system identity')
   self.active=body
   if not self.current(chain,guard):raise ValueError('tool registration changed')
   self.stats['reused']+=1
   for name in ('pins','sdk_selection','build_manifest','build_id'):setattr(chain,name,value[name])
   return value['build_id'],manifest
  except(OSError,ValueError,TypeError,KeyError,AttributeError,RecursionError)as error:
   if isinstance(error,Stopped):raise
   self.active=None;return None
 def save(self,chain,guard):
  from local_metal_builder import canonical,digest,atomic_json,write_new,Refused
  self.stats['full_probes']+=1
  try:
   first=self._context(chain,guard)
   roots,immutable=scopes(chain,first)
   token=journal.query(roots,code_roots=True)
   if token is None:return False
   # Anchor first, then actually recheck. Never advance history past changes
   # that the complete original tool/SDK validation did not observe.
   chain._unchanged_full(guard);environment=chain._preparation_environment_full(guard)
   if self._context(chain,guard)!=first or first['os_build']!=chain.build_manifest['os_build']:raise Refused('tool environment changed during registration')
   names={x['path']for x in chain.pins.values()}|set(chain.sdk_selection['pins'])|set(environment['pins'])
   files={name:pin(name)for name in names}
   if not journal.unchanged(token,roots,code_roots=True):return False
   value={name:getattr(chain,name)for name in('pins','sdk_selection','build_manifest','build_id')};value['environment']=environment
   if scopes(chain,first)!=(roots,immutable):raise Refused('tool mounts changed during registration')
   body=dict(binding=self.binding,context=first,file_pins=files,roots=roots,readonly=immutable,journal=token,toolchain=value)
   if len(canonical(body))>MAX_RECEIPT//2:return False
   doc=dict(body=body,sha256=digest(canonical(body)));raw=canonical(doc)+b'\n';history=self.root/(doc['sha256']+'.json')
   if not history.exists():write_new(history,raw)
   atomic_json(self.path,doc);self.active=body;return True
  except(OSError,ValueError,TypeError,KeyError)as error:
   self.active=None
   if isinstance(error,Refused):raise
   return False
