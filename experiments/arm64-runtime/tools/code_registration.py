"""Existing digest/journal receipts applied to an explicit application closure.

Receipts register checked code, never GPU compilation compatibility or game data.
No file contents, descriptors or background watchers survive registration.
"""
from pathlib import Path
import hashlib,os,re,stat
from incremental_file_hashes import Hashes,stat_stamp,digest


def code_stamp(path):
 p=Path(path).absolute()
 if p!=p.resolve(strict=True):raise ValueError('registered code must be canonical')
 s=p.lstat()
 if not stat.S_ISREG(s.st_mode)or s.st_uid not in(0,os.getuid())or s.st_nlink!=1 or s.st_mode&0o022:raise ValueError('registered code owner/mode/type')
 return stat_stamp(s)

def system_pin(path):
 # Apple's command shims legitimately have multiple root-owned hard links.
 # This policy is restricted to these literal system roles, never bundled
 # executables, libraries, receipts or data files.
 p=Path(path)
 if str(p)not in('/usr/bin/otool','/usr/bin/lipo','/usr/bin/codesign','/System/Library/CoreServices/SystemVersion.plist')or p!=p.resolve(strict=True):raise ValueError('system validation role')
 s=p.lstat()
 if not stat.S_ISREG(s.st_mode)or s.st_uid!=0 or s.st_nlink<1 or s.st_mode&0o022:raise ValueError('system validation file shape')
 return stat_stamp(s)


class Closure:
 def __init__(self,app,registration_root,binding,hasher):
  from launcher_runtime_profile import owned_dir
  self.app=Path(app);self.root=owned_dir(registration_root,create=True)
  if self.root.is_relative_to(self.app):raise ValueError('registration must stay outside code')
  self.binding=dict(schema=1,policy='complete-code-closure-v1',app=str(self.app),manifests=binding)
  self.hashes=Hashes(self.root/(digest(self.binding)+'.json'),self.binding,hasher=hasher,
    watch_roots=[self.app],source_stamp=code_stamp,code_roots=True)
  # Without continuous history perform the original code reads. Metadata by
  # itself must not turn a cross-launch code receipt into admission authority.
  if not self.hashes.journal_valid:self.hashes.previous={}
 def check(self,root,name,want,limit):
  rel=Path(name)
  if rel.is_absolute()or '..'in rel.parts or str(rel)!=name or not isinstance(want,str)or re.fullmatch('[0-9a-f]{64}',want)is None:raise ValueError('registered code role')
  p=Path(root)/rel
  if not p.is_relative_to(self.app):raise ValueError('registered role escaped app')
  got,size=self.hashes.get(p);row=self.hashes.seen[str(p)];s=row['stamp']
  if got!=want:raise ValueError('bundled code identity changed')
  if not 0<size<=limit or not stat.S_ISREG(s[2])or s[3]not in(0,os.getuid())or s[5]!=1 or s[2]&0o022:raise ValueError('bounded registered code role')
  return dict(path=str(p),sha256=got,bytes=size)
 def finish(self):
  # The caller enumerated the exact manifest closure, including every script.
  # A successful hint is published only after that complete pass and recheck.
  if self.hashes.journal_valid:self.hashes.verify()
  else:self.hashes.publish()
  # Share the admitted digests with actual role checks in this interpreter.
  # Their current metadata/ownership checks remain in code_bytes().
  import launcher_runtime_profile as p
  with p._code_memo_lock:
   for name,row in self.hashes.seen.items():
    p._code_memo[name]=(tuple(row['stamp']),row['sha256']);p._code_memo.move_to_end(name)
   while len(p._code_memo)>p._CODE_MEMO_MAX:p._code_memo.popitem(last=False)
  return self.hashes.report()


def native_gpu_identity(target,backend,profile,*,resolver=None):
 """Persist actual Mach-O/signature checks; always recheck the load-edge scope.

 A checksum is not authority by itself: both actual frozen roles, OS/tool pins,
 complete result shape, and continuous app history must also still match.
 """
 import json
 import startup_change_journal as journal
 from game_pso_owned_audit import checked_bytes
 from launcher_game_catalog import write_json
 from launcher_runtime_profile import code_bytes,owned_dir
 from gpu_candidate_identity import resolve_gpu_edge
 if resolver is None:
  from warm_archive_manifest import resolve_gpu_identity
  resolver=resolve_gpu_identity
 target=profile.code(Path(target).resolve(strict=True));backend=profile.code(backend)
 if backend!=profile.bundle.stage:raise ValueError('registered GPU backend mismatch')
 roles=profile.bundle.native['frozen_runtime'];role=str(target.relative_to(backend))
 if role not in roles:raise ValueError('registered target role missing')
 target_id=code_bytes(target,limit=512*1024*1024,expected=roles[role],content=False)[1]
 tools={str(p):system_pin(p)for p in map(Path,('/usr/bin/otool','/usr/bin/lipo','/usr/bin/codesign','/System/Library/CoreServices/SystemVersion.plist'))}
 policy=code_bytes(Path(__file__).with_name('gpu_candidate_identity.py'),content=False)[1]['sha256']
 binding=dict(schema=1,role='actual-native-gpu-check',target=target_id,backend=str(backend),tools=tools,policy=policy)
 root=owned_dir(profile.work_root/'native-code-checks',create=True);path=root/(digest(binding)+'.json')
 def validate(result):
  fields={'target','target_sha256','library','library_sha256','load_edge_verified','strict_signatures','architecture','declared_gpu_load_edges','declared_rpaths'}
  if set(result)!=fields or result['target']!=str(target)or result['target_sha256']!=target_id['sha256']or result['load_edge_verified']is not True or result['strict_signatures']is not True or result['architecture']!='arm64':raise ValueError('complete native verification result')
  library=profile.code(result['library']);library_role=str(library.relative_to(backend))
  if library_role not in roles or library.suffix!='.dylib' or roles[library_role]!=result['library_sha256']:raise ValueError('registered GPU library role')
  code_bytes(library,limit=512*1024*1024,expected=roles[library_role],content=False)
  edges=result['declared_gpu_load_edges'];rpaths=result['declared_rpaths']
  if not isinstance(edges,list)or len(edges)!=1 or not isinstance(rpaths,list)or len(rpaths)>64 or any(not isinstance(p,str)or len(p)>4096 for p in rpaths):raise ValueError('registered load-edge envelope')
  if [str(resolve_gpu_edge(target,edge,rpaths=rpaths))for edge in edges]!=[str(library)]:raise ValueError('registered load edge changed')
 try:
  saved_shape=path.lstat()
  if saved_shape.st_nlink!=1 or stat.S_IMODE(saved_shape.st_mode)!=0o600:raise ValueError('private native verification registration')
  doc=json.loads(checked_bytes(path,limit=65536)[0]);body=doc['body']
  if set(doc)!={'body','sha256'}or digest(body)!=doc['sha256']or set(body)!={'binding','result','journal'}or body['binding']!=binding:raise ValueError('native verification registration identity')
  if not journal.unchanged(body['journal'],[profile.bundle.app],code_roots=True):raise ValueError('native code history changed')
  validate(body['result'])
  if not journal.unchanged(body['journal'],[profile.bundle.app],code_roots=True):raise ValueError('native code changed during reuse')
  return body['result']
 except(OSError,ValueError,KeyError,TypeError):pass
 token=journal.query([profile.bundle.app],code_roots=True)
 result=resolver(target,backend,code_owners=(0,os.getuid()));validate(result)
 if token is not None and journal.unchanged(token,[profile.bundle.app],code_roots=True):
  body=dict(binding=binding,result=result,journal=token);write_json(path,dict(body=body,sha256=digest(body)))
 return result
