"""Temporary host-local selector for the existing bounded texture-binding capture.

No preferences, saves, caches or input endpoints. No capture until the operator
creates the native trigger. Only this fixed Shared development root is admitted.
"""
from pathlib import Path
import json,os,re,stat,time

ROOT=Path('/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/state')
REQUEST='texture-binding-request.json'
MAX_AGE=8*3600

def resolve(*,root=ROOT,now=None):
 now=time.time()if now is None else now
 if root!=root.resolve(strict=True):raise ValueError('noncanonical diagnostic root')
 path=root/REQUEST
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
 with os.fdopen(fd,'rb')as stream:
  before=os.fstat(stream.fileno())
  if not stat.S_ISREG(before.st_mode)or before.st_uid!=os.getuid()or before.st_nlink!=1 or before.st_mode&0o077 or not 0<before.st_size<=4096:raise ValueError('unowned or unbounded diagnostic request')
  raw=stream.read(4097);after=os.fstat(stream.fileno())
  stamp=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
  if len(raw)!=before.st_size or stamp(before)!=stamp(after):raise ValueError('diagnostic request changed')
 value=json.loads(raw)
 if not isinstance(value,dict)or set(value)!={'schema','id','created','expires'}or type(value['schema'])is not int or value['schema']!=1:raise ValueError('diagnostic request schema')
 tag=value['id'];created=value['created'];expires=value['expires']
 if not isinstance(tag,str)or not re.fullmatch('[0-9a-f]{24}',tag):raise ValueError('diagnostic request id')
 if not all(type(x)in(int,float)for x in(created,expires))or not created<=now<expires<=created+MAX_AGE:raise ValueError('expired diagnostic request')
 directory=root/('texture-binding-'+tag)
 if directory!=directory.resolve(strict=True):raise ValueError('linked diagnostic directory')
 info=directory.stat()
 if not stat.S_ISDIR(info.st_mode)or info.st_uid!=os.getuid()or info.st_mode&0o077:raise ValueError('unowned diagnostic directory')
 return directory,tag

def apply(environment,*,root=ROOT,now=None):
 """Invalid or absent optional request disables diagnosis, never the game."""
 env=dict(environment)
 try:directory,tag=resolve(root=root,now=now)
 except(OSError,ValueError,KeyError,TypeError):return env,None
 env.update(BB_TEXTURE_BINDING_CAPTURE_DIR=str(directory),BB_CAPTURE_TEXTURE_BINDINGS='1',BB_CAPTURE_CONSECUTIVE_COUNT='3')
 return env,dict(id=tag,trigger_required=True,frames_per_request=3,texture_contents_read=False)
