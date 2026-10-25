"""Materialize and verify reviewed old GPU sources for old-ABI diagnostic libraries.
Never use current Info headers against a library built before the Info change.
The private source tree contains no game/caches; it is verified against Git blobs.
"""
from pathlib import Path
import hashlib,json,subprocess,tarfile,time
P=Path(__file__).resolve().parents[1]
REV='f349bd44ed37bc2658ef1327f9e2a1ecba6ef5ef'
PREFIX='experiments/arm64-runtime/'
REPO=Path('/Users/k.d./Documents/Codex/2026-10-16/https-github-com-loreanxavier-pt-pc/work/blood-echoes-arm64-audit')
def verified_headers():
 out=subprocess.check_output(['git','ls-tree','-r','-z',REV,'--',PREFIX+'src',PREFIX+'vendor/metal_api'],cwd=REPO)
 expected={}
 for entry in out.split(b'\0'):
  if not entry:continue
  head,path=entry.split(b'\t',1);mode,kind,digest=head.decode().split();assert mode in ('100644','100755')and kind=='blob'
  name=path.decode();assert name.startswith(PREFIX);relative=name[len(PREFIX):];assert not Path(relative).is_absolute()and '..'not in Path(relative).parts;expected[relative]=digest
 assert expected and 'src/bb_tiler_bounds.h'in expected
 attrs_data=subprocess.check_output(['git','check-attr','--source',REV,'-z','--stdin','text','eol'],cwd=REPO,input=b''.join((PREFIX+x).encode()+b'\0'for x in expected)).split(b'\0');attrs={}
 for n in range(0,len(attrs_data)-1,3):attrs.setdefault(attrs_data[n].decode()[len(PREFIX):],{})[attrs_data[n+1].decode()]=attrs_data[n+2].decode()
 created=False
 current=P/'evidence/frozen-gpu-headers-current.json'
 if current.exists():
  meta=json.loads(current.read_text());assert meta['revision']==REV;root=Path(meta['path']);assert root.is_relative_to(P/'state')
 else:
  root=P/'state'/('frozen-gpu-headers-'+str(time.time_ns()));root.mkdir();created=True
  proc=subprocess.Popen(['git','archive',REV,PREFIX+'src',PREFIX+'vendor/metal_api'],cwd=REPO,stdout=subprocess.PIPE)
  with tarfile.open(fileobj=proc.stdout,mode='r|')as archive:
   seen=set()
   for member in archive:
    if member.isdir():continue
    assert member.isfile()and member.name.startswith(PREFIX)
    name=member.name[len(PREFIX):];assert name in expected and name not in seen;seen.add(name)
    target=root/name;target.parent.mkdir(parents=True,exist_ok=True)
    with archive.extractfile(member)as source,target.open('xb')as dest:
     while data:=source.read(1<<16):dest.write(data)
  assert proc.wait(timeout=10)==0 and seen==set(expected)
 for name,digest in expected.items():
  f=root/name;assert f.is_file()and not f.is_symlink();size=f.stat().st_size;h=hashlib.sha1(b'blob '+str(size).encode()+b'\0')
  with f.open('rb')as stream:
   while data:=stream.read(1<<16):h.update(data)
  if created and h.hexdigest()!=digest and (attrs[name].get('text')=='set' or attrs[name].get('eol')in('lf','crlf')):
   # git archive honors declared working-tree EOL; preserve canonical Git blob bytes.
   data=f.read_bytes().replace(b'\r\n',b'\n');h=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data)
   assert h.hexdigest()==digest,name;f.write_bytes(data)
  assert h.hexdigest()==digest,name
 current.write_text(json.dumps(dict(path=str(root),revision=REV,files=len(expected)),indent=2)+'\n');return root
