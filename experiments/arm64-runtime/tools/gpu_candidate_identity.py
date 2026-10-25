"""Validate a private GPU child against its actual Mach-O load edge before launch."""
from pathlib import Path
import hashlib,os,stat,struct,subprocess

def sha(path):
 with Path(path).open('rb')as f:return hashlib.file_digest(f,'sha256').hexdigest()
def verify(meta,backend_root):
 root=Path(backend_root).absolute()
 if root!=root.resolve():raise ValueError('noncanonical private backend root')
 target=Path(meta['target']).absolute();library=Path(meta['library']).absolute()
 for path in(target,library):
  if path!=path.resolve()or not path.is_relative_to(root):raise ValueError('GPU candidate outside private backend')
  st=path.stat()
  if not stat.S_ISREG(st.st_mode)or st.st_uid!=os.getuid()or st.st_nlink!=1:raise ValueError('unowned/nonregular GPU candidate')
 if sha(library)!=meta['library_sha256']:raise ValueError('GPU candidate library identity changed')
 for path in(target,library):
  if subprocess.check_output(['/usr/bin/lipo','-archs',str(path)],text=True).strip()!='arm64':raise ValueError('GPU candidate must be native ARM64')
 with target.open('rb')as source:header=source.read(16)
 if len(header)!=16 or struct.unpack('<4I',header)[0]!=0xfeedfacf or struct.unpack('<4I',header)[3]!=2:raise ValueError('GPU child must be a thin native main executable')
 lines=subprocess.check_output(['/usr/bin/otool','-L',str(target)],text=True).splitlines()[1:]
 deps=[x.strip().split(' (compatibility')[0]for x in lines]
 gpu=[x for x in deps if Path(x).name.startswith('libbbgpu')]
 resolved=[]
 for edge in gpu:
  if edge.startswith('@loader_path/'):
   resolved.append(str((target.parent/edge[len('@loader_path/'):]).resolve()))
  elif edge.startswith('@executable_path/'):
   resolved.append(str((target.parent/edge[len('@executable_path/'):]).resolve()))
  elif edge.startswith('/'):
   resolved.append(str(Path(edge).resolve()))
  else:raise ValueError('unresolved GPU child load edge: '+edge)
 if resolved!=[str(library)]:raise ValueError('GPU child load edge does not match candidate library: '+repr(gpu))
 for path in(target,library):
  signature=subprocess.run(['/usr/bin/codesign','--verify','--strict',str(path)],capture_output=True,text=True)
  if signature.returncode:raise ValueError('GPU signature rejected: '+path.name+': '+(signature.stdout+signature.stderr).strip()[:1024])
 return dict(target=str(target),target_sha256=sha(target),library=str(library),library_sha256=sha(library),load_edge_verified=True,strict_signatures=True,architecture='arm64',declared_gpu_load_edges=gpu)


def select_for_isolated_run(manifest_path, *, engine, graphics, private_root):
 """Optional private selection, using the same verified Mach-O contract as fixtures."""
 if manifest_path is None:return None
 if engine!='arm64' or not graphics:raise ValueError('GPU candidate requires isolated ARM64 graphics')
 import json
 private_root=Path(private_root).resolve(strict=True)
 manifest=Path(manifest_path).absolute()
 if manifest!=manifest.resolve(strict=True)or not manifest.is_relative_to(private_root/'evidence'):
  raise ValueError('GPU candidate manifest must be a canonical private evidence file')
 record=json.loads(manifest.read_text())
 return verify(record,private_root/'backend')
