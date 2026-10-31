"""Hash complete ar members; normalize only container date/uid/gid fields."""
import hashlib

def digests(stream,limit):
 raw=hashlib.sha256();content=hashlib.sha256();count=0
 def take(n):
  nonlocal count
  value=stream.read(n);count+=len(value)
  if count>limit:raise ValueError('archive identity input exceeded bound')
  raw.update(value);return value
 magic=take(8)
 if magic==b'!<thin>\n':raise ValueError('thin archive references are not self-contained identity inputs')
 if magic!=b'!<arch>\n':
  while take(65536):pass
  return raw.hexdigest(),raw.hexdigest(),count
 content.update(b'BE-complete-ar-members-v1\0'+magic)
 while True:
  header=take(60)
  if not header:break
  if len(header)!=60 or header[58:]!=b'`\n':raise ValueError('invalid archive member header')
  text=header[48:58].strip()
  if not text or not text.isdigit():raise ValueError('invalid archive member extent')
  size=int(text)
  if size>limit-count:raise ValueError('archive member exceeds input bound')
  # Preserve name, mode, extent, symbol/string tables, all member bytes and pad.
  # Date, uid and gid never enter emitted native instructions or relocation ABI.
  content.update(header[:16]+b'0'.ljust(12)+b'0'.ljust(6)+b'0'.ljust(6)+header[40:])
  remaining=size
  while remaining:
   block=take(min(65536,remaining))
   if not block:raise ValueError('truncated archive member')
   content.update(block);remaining-=len(block)
  if size%2:
   pad=take(1)
   if pad!=b'\n':raise ValueError('invalid archive alignment')
   content.update(pad)
 return raw.hexdigest(),content.hexdigest(),count
