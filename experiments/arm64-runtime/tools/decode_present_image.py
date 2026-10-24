"""Decode an owned, bounded completed-game-buffer dump; never desktop capture.

Raw output remains intact. The RGB PNG is a diagnostic preview, not a drawable
colorimetry or performance comparison; presentation ignores the unused alpha.
Requires Pillow; packed RGB10A2 previews also use NumPy.
"""
from pathlib import Path
import hashlib,os,re,stat
from PIL import Image
MAX_BYTES=32*1024*1024
FORMATS={70:'rgba',71:'rgba',80:'bgra',81:'bgra',90:'rgba',94:'rgba'}
PATTERN=re.compile(r'f(\d+)_present_(\d+)x(\d+)_(rgba|bgra)\.raw')
def decode(path,pixel_format,flip_vertical=False):
 path=Path(path).absolute();m=PATTERN.fullmatch(path.name)
 if not m or path!=path.resolve()or pixel_format not in FORMATS:raise ValueError('unsupported canonical raw display dump')
 _,w,h,layout=m.groups();w=int(w);h=int(h)
 if not w or not h or w*h>MAX_BYTES//4 or FORMATS[pixel_format]!=layout:raise ValueError('display dimensions/format mismatch')
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
 with os.fdopen(fd,'rb')as source:
  s=os.fstat(source.fileno())
  if not stat.S_ISREG(s.st_mode)or s.st_uid!=os.getuid()or s.st_nlink!=1 or s.st_size!=w*h*4:raise ValueError('incomplete or unowned raw display file')
  raw=source.read(MAX_BYTES+1)
 if len(raw)!=w*h*4:raise ValueError('display file changed during read')
 if pixel_format in (70,71,80,81):image=Image.frombytes('RGBA',(w,h),raw,'raw',layout.upper()).convert('RGB')
 else:
  import numpy as np
  words=np.frombuffer(raw,dtype='<u4').reshape(h,w);rgb=np.empty((h,w,3),dtype=np.uint8)
  for channel,shift in enumerate((0,10,20)if pixel_format==90 else(20,10,0)):
   rgb[:,:,channel]=((((words>>shift)&1023)*255+511)//1023).astype(np.uint8)
  image=Image.fromarray(rgb)
 if flip_vertical:
  oriented=image.transpose(Image.Transpose.FLIP_TOP_BOTTOM);image.close();image=oriented
 output=path.with_suffix('.png');fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
 try:
  with os.fdopen(fd,'wb')as target:image.save(target,format='PNG',compress_level=2)
 finally:image.close()
 return dict(raw=str(path),png=str(output),width=w,height=h,metal_format=pixel_format,flip_vertical=bool(flip_vertical),raw_sha256=hashlib.sha256(raw).hexdigest(),preview='RGB, original tight raw retained; no desktop pixels or alpha composition')
def proof(path,text):
 formats=re.findall(r'Metal: display buffer dumped to '+re.escape(str(path))+r' metal_format=(\d+) vertical_flip=([01])(?:\s|$)',text)
 if len(formats)!=1:raise ValueError('missing or duplicate native pixel-format proof: '+Path(path).name)
 return int(formats[0][0]),formats[0][1]=='1'
def from_log(directory,text,reuse_verified=False):
 directory=Path(directory).absolute();assert directory==directory.resolve()and directory.is_dir()
 rows=[]
 for path in sorted(directory.glob('*.raw')):
  pixel_format,flipped=proof(path,text)
  sidecar=path.with_suffix('.preview.json')
  if reuse_verified and sidecar.is_file()and not sidecar.is_symlink():
   import json
   row=json.loads(sidecar.read_text());png=Path(row['png'])
   if row['raw']!=str(path)or png!=path.with_suffix('.png')or png.is_symlink()or row['metal_format']!=pixel_format or row['flip_vertical']!=flipped or not png.is_file():raise ValueError('preview identity mismatch')
   if hashlib.file_digest(path.open('rb'),'sha256').hexdigest()!=row['raw_sha256']or hashlib.file_digest(png.open('rb'),'sha256').hexdigest()!=row['png_sha256']:raise ValueError('preview bytes changed')
   rows.append(row)
  else:rows.append(decode(path,pixel_format,flipped))
 return rows
