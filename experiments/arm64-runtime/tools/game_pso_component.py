"""Freeze/verify a host-local preparation worker; no game cache or game data."""
from pathlib import Path
import hashlib,json,os,re,stat,shutil
from game_pso_owned_audit import checked_bytes,load_canonical_layout
HEX=re.compile('[0-9a-f]{64}')
def need(v,m):
 if not v:raise ValueError(m)
def sha(p):
 p=Path(p);need(p.is_absolute()and p==p.resolve(strict=True),'canonical component input')
 fd=os.open(p,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
 try:
  a=os.fstat(fd);need(stat.S_ISREG(a.st_mode)and a.st_uid==os.getuid()and a.st_size<=512*1024*1024,'owned bounded component')
  with os.fdopen(fd,'rb',closefd=False)as f:h=hashlib.file_digest(f,'sha256').hexdigest()
  b=os.fstat(fd);need((a.st_dev,a.st_ino,a.st_size,a.st_mtime_ns)==(b.st_dev,b.st_ino,b.st_size,b.st_mtime_ns),'component changed')
  return h
 finally:os.close(fd)
def relative(root,rel):
 p=Path(rel);need(not p.is_absolute()and '..'not in p.parts and str(p)==rel,'component relative path');return root/p

def freeze_inputs(stage,component,library,*,rewrite,admission_root):
 """Call AFTER dependency relocation/signing; bind final copied GPU and worker."""
 stage=Path(stage).resolve(strict=True);library=Path(library).resolve(strict=True)
 raw,_=checked_bytes(Path(component),limit=1024*1024);m=json.loads(raw)
 need(m['schema']==1 and m['kind']=='blood-echoes-game-pso-component'and m['production']is False,'admitted private component required')
 source=Path(m['worker']);need(sha(source)==m['worker_sha256'],'worker source identity')
 from warm_archive_manifest import resolve_gpu_identity
 actual=resolve_gpu_identity(source,Path(admission_root).resolve(strict=True))
 need(actual['library']==str(Path(m['library']).resolve(strict=True))and
      actual['library_sha256']==m['library_sha256'],'worker actual GPU edge/source library identity mismatch')
 # The input DSO identity is the admitted coherent pre-packaging library. The
 # caller verifies it against its GPU build before rewriting copied load edges.
 layout=Path(m['layout']);source_root=Path(m['source_root'])
 l=load_canonical_layout(layout,source_root,m['layout_sha256'])
 root=stage/'native-runtime-inputs';root.mkdir(mode=0o700)
 dst=root/'layout.json';shutil.copy2(layout,dst);need(sha(dst)==m['layout_sha256'],'copied native layout changed')
 headers=root/'layout-source';headers.mkdir()
 for name,want in l['source_sha256'].items():
  src=relative(source_root,name);data,_=checked_bytes(src,limit=4*1024*1024,expected=want)
  out=relative(headers,name);out.parent.mkdir(parents=True,exist_ok=True);out.write_bytes(data)
 worker=stage/'bin/game-pso-preparer';shutil.copy2(source,worker)
 need(sha(worker)==m['worker_sha256'],'copied worker changed before relocation')
 rewrite(worker,Path(m['library']),library)
 # No development-source paths survive in the installed runtime manifest.
 frozen=dict(schema=1,enabled=True,kind=m['kind'],production=False,
  worker=str(worker.relative_to(stage)),worker_sha256=sha(worker),
  library=str(library.relative_to(stage)),library_sha256=sha(library),
  layout=str(dst.relative_to(stage)),layout_sha256=sha(dst),
  source_root=str(headers.relative_to(stage)))
 (stage/'GAME-PSO-COMPONENT.json').write_text(json.dumps(frozen,indent=2)+'\n')
 return frozen

def load(stage):
 stage=Path(stage).resolve(strict=True);path=stage/'GAME-PSO-COMPONENT.json'
 if not path.exists():return None
 raw,_=checked_bytes(path);m=json.loads(raw)
 need(m['schema']==1 and m['kind']=='blood-echoes-game-pso-component'and m['enabled']is True and m['production']is False,'invalid preparation component')
 result=dict(m)
 for field in('worker','library','layout','source_root'):
  p=relative(stage,m[field]);need(p==p.resolve(strict=True)and p.is_relative_to(stage),'linked/outside component')
  result[field]=p
  if field!='source_root':need(HEX.fullmatch(m[field+'_sha256'])and sha(p)==m[field+'_sha256'],'packaged component identity changed: '+field)
 from warm_archive_manifest import resolve_gpu_identity
 actual=resolve_gpu_identity(result['worker'],stage)
 need(actual['library']==str(result['library'])and actual['library_sha256']==result['library_sha256'],
      'packaged worker actual GPU edge/library mismatch')
 result['native_layout']=load_canonical_layout(result['layout'],result['source_root'],result['layout_sha256'])
 return result
