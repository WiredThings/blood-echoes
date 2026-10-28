"""Validate a private GPU child against its actual Mach-O load edge before launch."""
from pathlib import Path
import hashlib,os,re,stat,struct,subprocess

_verification_work=dict(hash_reads=0,hash_bytes=0,tools={})
def verification_work():return dict(_verification_work,tools=dict(_verification_work['tools']))
def output(command):
 name=Path(command[0]).name;_verification_work['tools'][name]=_verification_work['tools'].get(name,0)+1
 return subprocess.check_output(command,text=True)

def sha(path):
 value=hashlib.sha256();_verification_work['hash_reads']+=1
 with Path(path).open('rb')as f:
  for block in iter(lambda:f.read(1024*1024),b''):
   value.update(block);_verification_work['hash_bytes']+=len(block)
 return value.hexdigest()
def load_rpaths(target):
 """Only this main executable's declared paths; no environment/default search."""
 text=output(['/usr/bin/otool','-l',str(target)])
 paths=[];pending=False
 for raw in text.splitlines():
  line=raw.strip()
  if line.startswith('cmd '):
   if pending:raise ValueError('incomplete native RPATH command')
   pending=line=='cmd LC_RPATH'
  elif pending and line.startswith('path '):
   m=re.fullmatch(r'path (.+) \(offset [0-9]+\)',line)
   if not m or len(m[1])>4096:raise ValueError('invalid native RPATH')
   paths.append(m[1]);pending=False
 if pending or len(paths)>64:raise ValueError('invalid native RPATH count/extent')
 return paths

def resolve_gpu_edge(target,edge,*,rpaths=None):
 """Resolve one declared edge, refusing ambiguous or implicit RPATH lookup.

 The caller still verifies the resolved library's private root, owner, ARM64,
 signature and SHA. RPATH support does not grant a new filesystem read root.
 """
 target=Path(target)
 if edge.startswith(('@loader_path/','@executable_path/')):
  return(target.parent/edge.split('/',1)[1]).resolve(strict=True)
 if edge.startswith('/'):return Path(edge).resolve(strict=True)
 if not edge.startswith('@rpath/'):raise ValueError('unresolved GPU child load edge: '+edge)
 suffix=Path(edge[len('@rpath/'):])
 if suffix.is_absolute()or '..'in suffix.parts or not suffix.parts:raise ValueError('invalid GPU RPATH suffix')
 matches=set()
 for raw in load_rpaths(target)if rpaths is None else rpaths:
  if raw.startswith(('@loader_path/','@executable_path/')):base=target.parent/raw.split('/',1)[1]
  elif raw in('@loader_path','@executable_path'):base=target.parent
  elif raw.startswith('/'):base=Path(raw)
  else:raise ValueError('unresolved native RPATH: '+raw)
  candidate=base/suffix
  if os.path.lexists(candidate):matches.add(candidate.resolve(strict=True))
 if len(matches)!=1:raise ValueError('GPU RPATH must resolve to one unambiguous declared library')
 return matches.pop()
def verify(meta,backend_root,*,code_owners=None):
 root=Path(backend_root).absolute()
 if root!=root.resolve():raise ValueError('noncanonical private backend root')
 target=Path(meta['target']).absolute();library=Path(meta['library']).absolute()
 for path in(target,library):
  if path!=path.resolve()or not path.is_relative_to(root):raise ValueError('GPU candidate outside private backend')
  st=path.stat()
  if not stat.S_ISREG(st.st_mode)or st.st_uid not in((os.getuid(),)if code_owners is None else code_owners)or st.st_nlink!=1:raise ValueError('unowned/nonregular GPU candidate')
  if code_owners is not None and st.st_mode&0o022:raise ValueError('writable bundled GPU code')
 if sha(library)!=meta['library_sha256']:raise ValueError('GPU candidate library identity changed')
 for path in(target,library):
  if output(['/usr/bin/lipo','-archs',str(path)]).strip()!='arm64':raise ValueError('GPU candidate must be native ARM64')
 with target.open('rb')as source:header=source.read(16)
 if len(header)!=16 or struct.unpack('<4I',header)[0]!=0xfeedfacf or struct.unpack('<4I',header)[3]!=2:raise ValueError('GPU child must be a thin native main executable')
 lines=output(['/usr/bin/otool','-L',str(target)]).splitlines()[1:]
 deps=[x.strip().split(' (compatibility')[0]for x in lines]
 gpu=[x for x in deps if Path(x).name.startswith('libbbgpu')]
 resolved=[]
 rpaths=load_rpaths(target)if any(edge.startswith('@rpath/')for edge in gpu)else[]
 for edge in gpu:
  resolved.append(str(resolve_gpu_edge(target,edge,rpaths=rpaths)))
 if resolved!=[str(library)]:raise ValueError('GPU child load edge does not match candidate library: '+repr(gpu))
 for path in(target,library):
  _verification_work['tools']['codesign']=_verification_work['tools'].get('codesign',0)+1
  signature=subprocess.run(['/usr/bin/codesign','--verify','--strict',str(path)],capture_output=True,text=True)
  if signature.returncode:raise ValueError('GPU signature rejected: '+path.name+': '+(signature.stdout+signature.stderr).strip()[:1024])
 return dict(target=str(target),target_sha256=sha(target),library=str(library),library_sha256=sha(library),load_edge_verified=True,strict_signatures=True,architecture='arm64',declared_gpu_load_edges=gpu,declared_rpaths=rpaths)


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
