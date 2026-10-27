"""Inspect bounded, GPU-completed partial texture history. Never infer whole-image correctness."""
from pathlib import Path
import argparse,hashlib,json,os,re,stat

def regular(path,limit):
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
 with os.fdopen(fd,'rb')as f:
  s=os.fstat(f.fileno())
  if not stat.S_ISREG(s.st_mode)or s.st_uid!=os.getuid()or s.st_nlink!=1 or s.st_size>limit:raise ValueError('unowned or oversized lineage input')
  value=f.read(limit+1)
  after=os.fstat(f.fileno());stamp=lambda x:(x.st_dev,x.st_ino,x.st_mode,x.st_uid,x.st_nlink,x.st_size,x.st_mtime_ns,x.st_ctime_ns)
  if len(value)!=s.st_size or len(value)>limit or stamp(s)!=stamp(after):raise ValueError('lineage input changed')
  return value

def analyze(path):
 path=Path(path).absolute()
 if path!=path.resolve(strict=True):raise ValueError('noncanonical lineage path')
 rows=[];encoded={};saved={};decisions={};warnings=[];scope=[]
 for line in regular(path,8*1024*1024).splitlines():
  try:r=json.loads(line)
  except(ValueError,UnicodeError):warnings.append('malformed or partial lineage row');continue
  if not isinstance(r,dict):warnings.append('invalid lineage row shape');continue
  rows.append(r)
 for r in rows:
  op=r.get('op')
  if op=='lineage_scope':scope.append(r)
  if op=='lineage_gap':warnings.append('explicit history gap')
  if op=='lineage_decision':
   if r['event']in decisions:warnings.append('duplicate decision identity')
   decisions[r['event']]=r
  if op in('lineage_encoded','lineage_saved'):
   target=encoded if op=='lineage_encoded'else saved;event=r['event']
   if event in target:warnings.append('duplicate event identity')
   target[event]=r
 if len(scope)!=1:warnings.append('missing or ambiguous process epoch')
 result=[]
 if(path.parent/'lineage-incomplete').exists():warnings.append('diagnostic failure marker present')
 for event,r in sorted(encoded.items()):
  d=r['detail'];s=saved.get(event);row=dict(event=event,tick=r['tick'],phase=d['phase'],texture=d.get('texture'),predecessor=d.get('predecessor'),context=d.get('context'),partial=True,completed=False)
  if not isinstance(d.get('texture'),dict)or not d['texture'].get('uid'):warnings.append('missing physical object identity')
  if not s or not s.get('gpu_complete')or not s.get('written'):warnings.append('snapshot not GPU-complete and saved');result.append(row);continue
  name=s['file']
  if name!=r['file']or not re.fullmatch(r'lineage_\d+_\d+_\d+\.raw',name):raise ValueError('invalid lineage payload name')
  raw=regular(path.parent/name,6144)
  if len(raw)!=s['bytes']:raise ValueError('lineage payload size mismatch')
  patches=[]
  if'regions'in d:
   for z in d['regions']:
    row_bytes=((z['width']+z['block']-1)//z['block'])*z['block_bytes'];offset=z['offset'];pitch=z['row'];count=z['rows']
    if not 0<row_bytes<=pitch or offset<0 or count<1 or offset+(count-1)*pitch+row_bytes>len(raw):raise ValueError('lineage region outside saved buffer')
    tight=b''.join(raw[offset+y*pitch:offset+y*pitch+row_bytes]for y in range(count))
    patches.append(dict(x=z['x'],y=z['y'],sha256=hashlib.sha256(tight).hexdigest(),first16=tight[:16].hex(),bytes=len(tight)))
  else:
   n=d['logical_bytes']
   if not 0<n<=len(raw):raise ValueError('lineage input range')
   patches.append(dict(sha256=hashlib.sha256(raw[:n]).hexdigest(),first16=raw[:16].hex(),bytes=n))
  row.update(completed=True,patches=patches,file=name);result.append(row)
 if set(saved)-set(encoded):warnings.append('saved event lacks encoded provenance')
 if set(decisions)&set(encoded):warnings.append('ambiguous decision/snapshot identity')
 result.extend(dict(r,completed=False,partial=True)for r in decisions.values())
 result.sort(key=lambda x:x['event'])
 return dict(scope=scope,observations=result,warnings=sorted(set(warnings)),whole_texture_correctness_proven=False,root_cause_proven=False,note='Sort by event within one process epoch. Use physical uid/birth plus allocation generation; never join only by pointer. Three level-zero patches cannot establish all-texel or all-mip correctness. Call entry/return is not proof a requested copy encoded successfully. Render and raw-buffer writes are not exhaustively snapshotted.')

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('trace',type=Path);p.add_argument('--output',required=True,type=Path);a=p.parse_args();value=analyze(a.trace)
 with a.output.open('x')as f:json.dump(value,f,indent=2);f.write('\n')
