"""Executable contract proposal, NOT installed runtime code or a host-success stub.
All backend methods must perform real locking or raise OSError. Tests use an
explicit accounting backend to inject failures. Real microcontrol is separate.
External mapping lease must surround lock/unlock/committed_removal. Lock ordering
is VMA lifetime lease -> this mutex -> syscall; never acquire VMA under mutex.
"""
import errno,threading
PAGE=16384;GUEST_LIMIT=1<<40
class BoundedMlock:
 def __init__(self,backend,*,max_call=16<<20,max_total=64<<20,max_entries=4096):
  if min(max_call,max_total,max_entries)<=0 or max_call%PAGE or max_total%PAGE:raise ValueError('invalid finite policy')
  if max_call>16<<20 or max_total>64<<20 or max_entries>4096:raise ValueError('absolute candidate ceiling')
  self.backend=backend;self.max_call=max_call;self.max_total=max_total;self.max_entries=max_entries
  self.pages=set();self.gate=threading.Lock();self.poisoned=False;self.high_water=0
 def span(self,address,size):
  if address<0 or size<0 or address>=GUEST_LIMIT or size>GUEST_LIMIT-address:raise OSError(errno.EINVAL,'guest range overflow')
  begin=address&-PAGE;end=(address+size+PAGE-1)&-PAGE
  if end>GUEST_LIMIT:raise OSError(errno.EINVAL,'rounded overflow')
  return range(begin,end,PAGE)
 def check(self):
  if self.poisoned:raise RuntimeError('unrecoverable host rollback: refuse guest continuation')
 def lock(self,address,size):
  span=self.span(address,size)
  if len(span)*PAGE>self.max_call:raise OSError(errno.ENOMEM,'per-call ceiling')
  with self.gate:
   self.check();self.backend.require_owned_span(span.start,span.stop)
   new=[p for p in span if p not in self.pages]
   if (len(self.pages)+len(new))*PAGE>self.max_total or len(self.pages)+len(new)>self.max_entries:raise OSError(errno.ENOMEM,'aggregate ceiling')
   installed=[]
   try:
    for p in new:
     self.backend.lock(p,PAGE);self.pages.add(p);installed.append(p)
     self.high_water=max(self.high_water,len(self.pages)*PAGE)
   except OSError:
    for p in reversed(installed):
     try:self.backend.unlock(p,PAGE);self.pages.remove(p)
     except OSError:self.poisoned=True # still accounted; never hide pinned pages
    if self.poisoned:raise RuntimeError('lock rollback failed; retained pages accounted')
    raise
 def unlock(self,address,size):
  span=self.span(address,size)
  if len(span)*PAGE>self.max_call:raise OSError(errno.ENOMEM,'per-call ceiling')
  with self.gate:
   self.check();self.backend.require_owned_span(span.start,span.stop)
   old=[p for p in span if p in self.pages];removed=[]
   try:
    for p in old:
     self.backend.unlock(p,PAGE);self.pages.remove(p);removed.append(p)
   except OSError:
    for p in removed:
     try:self.backend.lock(p,PAGE);self.pages.add(p)
     except OSError:self.poisoned=True # actual unlocked pages remain absent
    if self.poisoned:raise RuntimeError('unlock rollback failed; actual state accounted')
    raise
 def committed_removal(self,address,size):
  """Only AFTER real munmap/MAP_FIXED success, under exclusive VMA lifetime lease.
  Removal implicitly unwires that mapping; calling munlock here would touch the
  replacement mapping or another owner. Failure must not invoke this method.
  """
  if address%PAGE or size%PAGE:raise ValueError('committed removal must be page aligned')
  span=self.span(address,size)
  with self.gate:self.pages.difference_update(span)
 @property
 def wired_bytes(self):return len(self.pages)*PAGE
