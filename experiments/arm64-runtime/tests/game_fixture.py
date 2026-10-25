#!/usr/bin/env python3
"""Authored BBPROBE5: real module init/static+dynamic TLS/callback/thread-exit/clock HLE.
No game content. All machine code below is explicitly authored for this test.
"""
from pathlib import Path
import struct,re
P=Path(__file__).resolve().parents[1]
def make(mode=None):
 data=bytearray(786432 if mode=="heap-bridge" else 49152 if mode=="long-string" else 32768);relocs=[];names=[];fix=[]
 text=(P/'vendor/bb_runtime/src/import_names.inc').read_text()
 nids={symbol:nid for nid,symbol in re.findall(r'\{"([^"]+)","([^"]+)"\}',text)}
 # Prefer the libkernel direct-thread exports, already used by the real game.
 nids['bb.prototype.missing']='bb.prototype.missing'
 nids['bb.prototype.travel_check']='bb.prototype.travel_check'
 nids['bb.prototype.extra_heap_check']='bb.prototype.extra_heap_check'
 nids['bb.prototype.extra_heap_during']='bb.prototype.extra_heap_during'
 nids['bb.prototype.gpu_hooks']='bb.prototype.gpu_hooks'
 nids['bb.prototype.gpu_clock']='bb.prototype.gpu_clock'
 nids['bb.prototype.gpu_clock_wrong_role']='bb.prototype.gpu_clock_wrong_role'
 nids['bb.prototype.heap_install']='bb.prototype.heap_install'
 nids['bb.prototype.heap_live']='bb.prototype.heap_live'
 nids['bb.prototype.net_workers']='bb.prototype.net_workers'
 nids['bb.prototype.net_nested']='bb.prototype.net_nested'
 nids['__strlen_qa']='j4ViWNHEgww#q#q'
 nids.update(scePthreadCreate='6UgtwV+0zb4#p#J',scePthreadJoin='onNY9Byn-W8#p#J',scePthreadExit='3kg7rT0NQIs#p#J')
 def function(at,error):
  code=bytearray.fromhex('48 83 ec 08');guards=[]
  def emit(h):code.extend(bytes.fromhex(h))
  def ref(h,target):
   emit(h);patch=at+len(code);code.extend(b'\0'*4);fix.append((patch,target-4 if h=="c7 05" else target))
  def call(name):
   if name not in names:names.append(name)
   ref('ff 15',0x4800+8*names.index(name))
  def guard(cc='85'):emit('0f '+cc);guards.append(len(code));code.extend(b'\0'*4)
  def zero():emit('85 c0');guard()
  def eq(h,imm):emit(h);code.extend(struct.pack('<I',imm));guard()
  def end():
   emit('31 c0 48 83 c4 08 c3');bad=len(code);emit('b8');code.extend(struct.pack('<I',error));emit('48 83 c4 08 c3')
   for g in guards:struct.pack_into('<i',code,g,bad-g-4)
   assert len(code)<1024;data[at:at+len(code)]=code
  emit.here=lambda: at+len(code)
  return emit,ref,call,guard,zero,eq,end
 for at,module,expected,value in [(0,2,0x12,1),(0x200,3,0x34,2)]:
  e,r,c,g,z,q,end=function(at,10+module)
  r('8b 05',0x4300);q('3d',value-1)
  # Both FS and GS address the same per-thread TCB, static TLS uses variant-II negative offset.
  for seg in ['64','65']:
   e(seg+' 48 8b 04 25 f0 ff ff ff');q('48 3d',0x11223344)
  r('48 8d 3d',0x4340+(module-2)*16);c('__tls_get_addr');e('48 85 c0');g('84');q('48 81 38',expected)
  e('48 c7 00');e(f'{0x50+module:02x} 00 00 00')
  r('c7 05',0x4300);e(f'{value:02x} 00 00 00');end()
 e,r,c,g,z,q,end=function(0x400,20)
 r('8b 05',0x4300);q('3d',2)
 # Main-thread writes must not leak into new guest threads.
 e('65 48 c7 04 25 f0 ff ff ff 99 88 77 00')
 for module in [2,3]:
  r('48 8d 3d',0x4340+(module-2)*16);c('__tls_get_addr');q('48 81 38',0x50+module)
 for i in range(2):r('48 8d 3d',0x4310);r('48 8d 35',0x800);c('scePthreadOnce');z()
 r('8b 05',0x4318);q('3d',1)
 for repeat in range(3):
  r('48 8d 3d',0x4320);e('31 f6');r('48 8d 15',0xa00);e('b9 42 00 00 00 45 31 c0');c('scePthreadCreate');z()
  r('48 8b 3d',0x4320);r('48 8d 35',0x4328);c('scePthreadJoin');z()
  r('48 8b 05',0x4328);q('48 3d',0x42)
 e('65 48 8b 04 25 f0 ff ff ff');q('48 3d',0x778899)
 c('sceKernelReadTsc');r('48 89 05',0x4360);e('0f 31 48 c1 e2 20 48 09 d0');r('48 2b 05',0x4360);r('48 89 05',0x4368)
 r('48 8b 05',0x4368);e('48 3d 00 ca 9a 3b');g('83')
 # Flexible memory must return zeroed storage after unmap/remap at the same VA.
 r('48 8d 3d',0x4370);e('be 00 40 00 00 ba 03 00 00 00 31 c9');c('sceKernelMapFlexibleMemory');z()
 r('48 8b 3d',0x4370);e('48 c7 07 78 56 34 12 be 00 40 00 00');c('sceKernelMunmap');z()
 r('48 8d 3d',0x4370);e('be 00 40 00 00 ba 03 00 00 00 b9 10 00 00 00');c('sceKernelMapFlexibleMemory');z()
 r('48 8b 3d',0x4370);e('48 83 3f 00');g();e('be 00 40 00 00');c('sceKernelMunmap');z()
 # Guest fixed-map/protect/unmap must refuse native-owned image pages.
 r('48 8d 05',0x4000);r('48 89 05',0x4370)
 r('48 8d 3d',0x4370);e('be 00 40 00 00 ba 03 00 00 00 b9 10 00 00 00');c('sceKernelMapFlexibleMemory');e('85 c0');g('84')
 r('48 8d 3d',0x4000);e('be 00 40 00 00 ba 03 00 00 00');c('sceKernelMprotect');e('85 c0');g('84')
 r('48 8d 3d',0x4000);e('be 00 40 00 00');c('sceKernelMunmap');e('85 c0');g('84');end()
 # once callback increments the data cell and returns through nested FEX CallGuest.
 data[0x800:0x808]=bytes.fromhex('ff 05 00 00 00 00 c3 90');fix.append((0x802,0x4318))
 e,r,c,g,z,q,end=function(0xa00,30)
 e('48 83 ff 42');g()
 e('65 48 8b 04 25 f0 ff ff ff');q('48 3d',0x11223344)
 for module,expected in [(2,0x12),(3,0x34)]:
  r('48 8d 3d',0x4340+(module-2)*16);c('__tls_get_addr');q('48 81 38',expected)
 e('bf 42 00 00 00');c('scePthreadExit');e('0f 0b');end()
 if mode:
  fix[:]=[(patch,target) for patch,target in fix if not 0x400<=patch<0x800]
  e,r,c,g,z,q,end=function(0x400,40)
  if mode and mode.startswith('libc-span:'):
   _,operation,which=mode.split(':')
   destination=0x7ff0 if which=='destination' else 0x2000 if which=='readonly' else 0x4504 if which=='overlap' else 0x4400
   source=0x7ff0 if which=='source' else 0x4500
   r('48 8d 3d',destination)
   if operation=='memset':e('be 5a 00 00 00')
   else:r('48 8d 35',source)
   if which=='huge':e('48 ba ff ff ff ff ff ff ff ff')
   else:e('ba 20 00 00 00')
   c(operation)
   if which in ['valid','overlap']:
    if operation=='memcmp':z()
    else:r('8b 05',destination);q('3d',0x5a5a5a5a)
   data[0x4400:0x4420]=b'Z'*32;data[0x4500:0x4520]=b'Z'*32
  elif mode in ['progname','progname-rewrite','progname-function']:
   if '__progname' not in names:names.append('__progname')
   if mode=='progname-function':c('__progname')
   else:
    # Relocation points to a char* slot, not directly to the string.
    r('48 8b 05',0x4800+8*names.index('__progname'));e('48 8b 38')
    e('81 3f 65 62 6f 6f');g();e('81 7f 04 74 2e 62 69');g();e('66 81 7f 08 6e 00');g()
    c('__strlen_qa');q('48 3d',9)
    if mode=='progname-rewrite':
     r('48 8b 05',0x4800+8*names.index('__progname'));r('48 8d 3d',0x4500)
     e('c7 07 51 41 00 00 48 89 38');c('__strlen_qa');q('48 3d',2)
     r('48 8b 05',0x4800+8*names.index('__progname'));e('48 8b 38');c('__strlen_qa');q('48 3d',2)
  elif mode=='dialogs':
   for family in ['sceMsgDialog','sceSaveDataDialog','sceNpProfileDialog','sceNpCommerceDialog']:
    c(family+'Initialize');z()
    c(family+'UpdateStatus');q('3d',1)
    e('31 ff');c(family+'Open');z()
    c(family+'UpdateStatus');q('3d',3)
    c(family+'Terminate');z()
  elif mode=='missing-import':c('bb.prototype.missing')
  elif mode and mode.startswith('guest-exit:'):
   raw=int(mode.split(':',1)[1]);e('bf');e(struct.pack('<I',raw&0xffffffff).hex(' '));c('exit')
  elif mode and mode.startswith('guest-hard-exit:'):
   raw=int(mode.split(':',1)[1]);e('bf');e(struct.pack('<I',raw&0xffffffff).hex(' '));c('_exit')
  elif mode=='extra-heaps':
   c('sceKernelGetDirectMemorySize');e('48 89 c6 31 ff ba 00 40 00 00 b9 00 40 00 00 45 31 c0');r('4c 8d 0d',0x4388);c('sceKernelAllocateDirectMemory');z()
   r('48 8d 3d',0x4380);e('be 00 40 00 00 ba 33 00 00 00 31 c9');r('4c 8b 05',0x4388);e('41 b9 00 40 00 00');c('sceKernelMapDirectMemory');z()
   r('48 8b 05',0x4380);r('48 89 05',0x4400)
   def allocation(at,alignment=64):
    r('48 8d 3d',0x4400);e('be 80 00 00 00 ba');e(f'{alignment:02x} 00 00 00');r('e8',at);r('48 3b 05',0x4400);g()
   def release(at,check_vector=False):
    r('48 8d 3d',0x4400);r('48 8b 35',0x4400);r('e8',at);q('48 3d',0x77)
    if check_vector:
     r('c5 7e 7f 3d',0x4640)
     for i in range(4):r('48 8b 05',0x4640+8*i);e('48 83 f8 ff');g()
   allocation(0x3000);release(0x3100,True)
   allocation(0x3200);release(0x3400,True)
   allocation(0x3300,16);release(0x3400)
   allocation(0x3300,64);release(0x3400)
   r('48 8d 05',0x5000);r('48 89 05',0x4400);allocation(0x3000);release(0x3100)
   r('c7 05',0x4400);e('00 00 00 00');r('c7 05',0x4404);e('00 00 00 00');allocation(0x3000)
   r('48 8b 05',0x4380);r('48 89 05',0x4400);r('c7 05',0x4420);e('01 00 00 00');allocation(0x3000);release(0x3100)
   c('bb.prototype.extra_heap_check');z()
   if 'bb.prototype.extra_heap_during'not in names:names.append('bb.prototype.extra_heap_during')
  elif mode=='travel-entry':
   r('c5 7e 6f 3d',0x4600) # vmovdqu ymm15, [rip+data]
   for h in ['bf 01 00 00 00','be 02 00 00 00','ba 03 00 00 00','b9 04 00 00 00','41 b8 05 00 00 00','41 b9 06 00 00 00']:e(h)
   e('48 83 ec 10 48 c7 04 24 07 00 00 00');r('e8',0x2800);e('48 83 c4 10');z()
   r('c5 7e 7f 3d',0x4640)
   for i in range(4):r('48 8b 05',0x4640+8*i);e('48 83 f8 ff');g()
   r('e8',0x2e00);q('48 3d',20)
   r('e8',0x2f00);q('48 3d',20)
   c('bb.prototype.travel_check');z()
  elif mode in ['gpu-clock','gpu-clock-wrong-role']:
   c('sceKernelReadTsc');r('48 89 05',0x4360)
   c('bb.prototype.gpu_clock_wrong_role' if mode=='gpu-clock-wrong-role' else 'bb.prototype.gpu_clock')
   r('48 2b 05',0x4360);e('48 3d 00 ca 9a 3b');g('83')
   c('bb.prototype.gpu_hooks')
  elif mode in ['opaque-callbacks','bad-netctl-callback']:
   if mode=='bad-netctl-callback':e('bf 34 12 00 00')
   else:r('48 8d 3d',0x800)
   e('be 01 00 00 00');r('48 8d 15',0x4380);c('sceNetCtlRegisterCallback');z()
   r('8b 05',0x4380);e('85 c0');g('84')
   for symbol in ['sceNpRegisterStateCallback','sceNpRegisterPlusEventCallback','sceNpRegisterGamePresenceCallback']:
    r('48 8d 3d',0x800);e('be 01 00 00 00');c(symbol)
  elif mode in ['ajm-builder','bad-ajm-builder']:
   e('48 83 ec 10');e('48 c7 04 24 08 00 00 00');r('48 8d 05',0x800);e('48 89 44 24 08')
   r('48 8d 3d',0x7ff0 if mode=='bad-ajm-builder' else 0x5000);e('be 01 00 00 00 31 d2');r('48 8d 0d',0x800);e('41 b8 08 00 00 00');r('4c 8d 0d',0x5200)
   c('sceAjmBatchJobControlBufferRa');e('48 83 c4 10');r('48 8d 15',0x5040);e('48 39 d0');g()
   r('48 8b 05',0x5010);r('48 8d 15',0x800);e('48 39 d0');g()
  elif mode=='heap-bridge':
   c('bb.prototype.heap_install')
   for k in range(6):
    if k==0:e('bf 20 00 00 00')
    elif k==1:r('48 8d 3d',0x4400)
    elif k==2:e('bf 02 00 00 00 be 10 00 00 00')
    elif k==3:r('48 8d 3d',0x4400);e('be 40 00 00 00')
    elif k==4:e('bf 40 00 00 00 be 80 00 00 00')
    else:r('48 8d 3d',0x4380);e('be 40 00 00 00 ba 80 00 00 00')
    r('ff 15',0xba190+8*k)
    if k in [0,2,3,4]:r('48 8d 15',0x4400);e('48 39 d0');g()
    elif k==5:z();r('48 8b 05',0x4380);r('48 8d 15',0x4400);e('48 39 d0');g()
   c('bb.prototype.heap_live');q('48 3d',3)
  elif mode=='net-thd-push-alias':
   e('31 ff 31 f6 ba 78 56 34 12 b9 01 00 00 00');c('sceNpWebApiRegisterPushEventCallback');e('85 c0');g('8e')
  elif mode in ['net-thd-read-valid','net-thd-read-bad','net-thd-recv-bad','net-thd-npid-bad','net-thd-path-bad']:
   e('bf ff ff ff ff');r('48 8d 35',0x7ff8);e('ba 08 00 00 00' if mode=='net-thd-read-valid' else 'ba 10 00 00 00');e('31 c9')
   if mode=='net-thd-path-bad':
    e('31 f6');r('48 8d 15',0x7ff8);e('45 31 c0');r('4c 8d 0d',0x5400);c('sceNpWebApiCreateRequest')
   else:c('sceNpGetNpId' if mode=='net-thd-npid-bad' else 'sceNetRecv' if mode=='net-thd-recv-bad' else 'sceNpWebApiReadData')
  elif mode in ['net-http-span-valid','net-http-span-bad','net-http-span-overflow']:
   e('bf ff ff ff ff');r('48 8d 35',0x7ff8)
   if mode=='net-http-span-overflow':e('48 c7 c2 ff ff ff ff')
   else:e('ba 08 00 00 00' if mode=='net-http-span-valid' else 'ba 10 00 00 00')
   c('sceHttpReadData') # invalid reqId is fine; bad spans must be stopped before HLE.
  elif mode=='net-http-roundtrip':
   e('bf 00 00 10 00');c('sceSslInit');e('85 c0');g('8e');r('89 05',0x5400)
   e('31 ff 89 c6 ba 00 00 10 00');c('sceHttpInit');e('85 c0');g('8e');r('89 05',0x5404)
   e('89 c7');r('48 8d 35',0x5600);e('ba 02 00 00 00 31 c9');c('sceHttpCreateTemplate');e('85 c0');g('8e');r('89 05',0x5408)
   e('89 c7');r('48 8d 35',0x5680);e('31 d2');c('sceHttpCreateConnectionWithURL');e('85 c0');g('8e');r('89 05',0x540c)
   e('89 c7 31 f6');r('48 8d 15',0x5680);e('31 c9');c('sceHttpCreateRequestWithURL');e('85 c0');g('8e');r('89 05',0x5410)
   for timeout in ['sceHttpSetConnectTimeOut','sceHttpSetRecvTimeOut','sceHttpSetSendTimeOut']:
    if timeout not in nids:continue
    r('8b 3d',0x5410);e('be 40 4b 4c 00');c(timeout);z()
   r('8b 3d',0x5410);e('31 f6 31 d2');c('sceHttpSendRequest');z()
   r('8b 3d',0x5410);r('48 8d 35',0x5414);c('sceHttpGetStatusCode');z();r('8b 05',0x5414);q('3d',200)
   r('8b 3d',0x5410);r('48 8d 35',0x5420);r('48 8d 15',0x5428);c('sceHttpGetAllResponseHeaders');z()
   r('48 8b 3d',0x5420);c('__strlen_qa');e('48 85 c0');g('84');r('48 3b 05',0x5428);g();r('48 8b 05',0x5420);e('48 c1 e8 28 48 85 c0');g()
   r('c7 05',0x5418);e('80 00 00 00');loop=e.here()
   r('8b 3d',0x5410);r('48 8d 35',0x6000);e('ba 00 10 00 00');c('sceHttpReadData');e('85 c0');g('88')
   e('0f 84');done_patch=e.here();e('00 00 00 00');r('01 05',0x541c);r('ff 0d',0x5418);r('0f 85',loop);e('b8 01 00 00 00');z();done=e.here();fix.append((done_patch,done))
   r('8b 05',0x541c);e('85 c0');g('84')
   for name,cell in [('sceHttpDeleteRequest',0x5410),('sceHttpDeleteConnection',0x540c),('sceHttpDeleteTemplate',0x5408),('sceHttpTerm',0x5404)]:
    r('8b 3d',cell);c(name);z()
   c('sceSslTerm');z()
  elif mode=='net-http-handle':
   e('31 ff 31 f6 ba 00 00 10 00');c('sceHttpInit');q('3d',1)
   e('bf 01 00 00 00');r('48 8d 35',0x5400);c('sceHttpCreateEpoll');z()
   r('48 8b 3d',0x5400);r('48 8d 35',0x6000);e('ba 01 00 00 00 31 c9');c('sceHttpWaitRequest');z()
   e('bf 01 00 00 00');r('48 8b 35',0x5400);c('sceHttpDestroyEpoll');z()
  elif mode in ['net-world-info','net-bad-callback','net-bad-output']:
   e('31 ff');c('sceNpMatching2Initialize');z()
   e('31 ff')
   if mode=='net-bad-output':e('be 34 12 00 00')
   else:r('48 8d 35',0x5400)
   c('sceNpMatching2CreateContext');z()
   r('48 8d 3d',0x2000);e('be 55 00 00 00');c('sceNpMatching2RegisterContextCallback');z()
   # RequestOptParam: fn, opaque arg, timeout, appReqId, padding.
   if mode=='net-bad-callback':e('48 c7 c0 34 12 00 00')
   else:r('48 8d 05',0x2000)
   r('48 89 05',0x5500);e('b8 55 00 00 00');r('48 89 05',0x5508)
   e('bf 01 00 00 00 31 f6');r('48 8d 15',0x5500);r('48 8d 0d',0x5404);c('sceNpMatching2GetWorldInfoList');z()
   r('c7 05',0x540c);e('d0 07 00 00');loop=e.here()
   e('bf e8 03 00 00');c('sceKernelUsleep');r('8b 05',0x5408);e('85 c0');e('0f 85');donepatch=e.here();e('00 00 00 00')
   r('ff 0d',0x540c);r('0f 85',loop);e('b8 01 00 00 00');z();done=e.here();fix.append((donepatch,done))
   r('8b 05',0x5408);q('3d',1)
  elif mode=='net-workers':
   r('48 8d 3d',0x1000);c('bb.prototype.net_workers');z()
  elif mode=='thread-churn':
   r('c7 05',0x4390);e('40 00 00 00');loop=e.here()
   r('e8',0x1000);z();r('ff 0d',0x4390);r('0f 85',loop)
  elif mode in ['vma-valid','vma-readonly','vma-freed']:
   r('48 8d 3d',0x4370);e('be 00 40 00 00 ba 03 00 00 00 31 c9');c('sceKernelMapFlexibleMemory');z()
   if mode=='vma-readonly':
    r('48 8b 3d',0x4370);e('be 00 40 00 00 ba 01 00 00 00');c('sceKernelMprotect');z()
   elif mode=='vma-freed':
    r('48 8b 3d',0x4370);e('be 00 40 00 00');c('sceKernelMunmap');z()
   e('31 ff');r('48 8b 35',0x4370);c('sceKernelClockGettime');z()
   if mode=='vma-valid':r('48 8b 3d',0x4370);e('be 00 40 00 00');c('sceKernelMunmap');z()
  elif mode=='long-string':r('48 8d 3d',0x7000);c('__strlen_qa');q('48 3d',5000)
  elif mode=='bad-timespec':e('31 ff');r('48 8d 35',0x7ff8);c('sceKernelClockGettime')
  elif mode=='bad-file-buffer':e('bf ff ff ff ff');r('48 8d 35',0x7ff8);e('ba 10 00 00 00');c('sceKernelRead')
  elif mode=='bad-callback':r('48 8d 3d',0x4310);e('be 34 12 00 00');c('scePthreadOnce')
  else:raise ValueError(mode)
  end()
 struct.pack_into('<Q',data,0x4100,0x11223344)
 for i,v in enumerate([0x12,0x34]):struct.pack_into('<Q',data,0x4200+i*16,v);struct.pack_into('<QQ',data,0x4340+i*16,2+i,0)
 if mode=='thread-churn':
  e,r,c,g,z,q,finish=function(0x1000,50)
  for i in range(8):
   r('48 8d 3d',0x5000+8*i);e('31 f6');r('48 8d 15',0xa00);e('b9 42 00 00 00 45 31 c0');c('scePthreadCreate');z()
  for i in range(8):
   r('48 8b 3d',0x5000+8*i);r('48 8d 35',0x5100+8*i);c('scePthreadJoin');z();r('48 8b 05',0x5100+8*i);q('48 3d',0x42)
  finish()
 if mode in ['net-world-info','net-bad-callback','net-bad-output']:
  e,r,c,g,z,q,finish=function(0x2000,90)
  q('81 ff',1);q('81 fa',2);e('85 c9');g();q('49 81 f9',0x55)
  e('4c 89 c0 48 c1 e8 28 48 85 c0');g() # response below 1 TiB
  e('41 83 78 08 01');g();e('49 8b 00 48 89 c2 48 c1 ea 28 48 85 d2');g()
  e('83 78 08 01');g();e('65 48 8b 04 25 f0 ff ff ff');q('48 3d',0x11223344)
  r('c7 05',0x5408);e('01 00 00 00');finish()
 if mode=='net-workers':
  for at,base in [(0x1000,1),(0x1800,11)]:
   e,r,c,g,z,q,finish=function(at,60+base)
   for reg,opcode in enumerate(['48 81 ff','48 81 fe','48 81 fa','48 81 f9','49 81 f8','49 81 f9']):q(opcode,base+reg)
   e('48 8b 44 24 10');q('48 3d',base+6)
   e('65 48 8b 04 25 f0 ff ff ff');q('48 3d',0x11223344)
   r('48 8d 3d',0x4340);c('__tls_get_addr');q('48 81 38',0x12)
   if at==0x1000:r('48 8d 3d',0x1800);c('bb.prototype.net_nested');z()
   finish()
 if mode=='heap-bridge':
  for k in range(6):
   at=0x1000+k*0x80
   relocs.append((0xba190+8*k,0,at,0))
   # Actual x86 guest bodies, called through nested FEX from native wrappers.
   if k==1:code=bytearray.fromhex('31 c0 c3')
   elif k==5:
    code=bytearray.fromhex('48 8d 05 00 00 00 00 48 89 07 31 c0 c3');fix.append((at+3,0x4400))
   else:code=bytearray.fromhex('48 8d 05 00 00 00 00 c3');fix.append((at+3,0x4400))
   data[at:at+len(code)]=code
 if mode=='travel-entry':
  # Original FlipperUpdate prefix with a balanced, seven-argument test body.
  at=0x2800;code=bytearray.fromhex('55 48 89 e5 41 57 41 56 41 55 41 54 53 48 83 ec 38');guards=[]
  for opcode,value in [('48 83 ff',1),('48 83 fe',2),('48 83 fa',3),('48 83 f9',4),('49 83 f8',5),('49 83 f9',6),('48 83 7d 10',7)]:
   code.extend(bytes.fromhex(opcode)+bytes([value])+bytes.fromhex('0f 85'));guards.append(len(code));code.extend(bytes(4))
  code.extend(bytes.fromhex('31 c0 eb 05'));bad=len(code);code.extend(bytes.fromhex('b8 57 00 00 00'))
  code.extend(bytes.fromhex('48 83 c4 38 5b 41 5c 41 5d 41 5e 41 5f 5d c3'))
  for g in guards:struct.pack_into('<i',code,g,bad-g-4)
  data[at:at+len(code)]=code
  at=0x2c00;code=bytearray.fromhex('55 48 89 e5 41 57 41 56 53 48 83 ec 38 89 3d 00 00 00 00 48 83 c4 38 5b 41 5e 41 5f 5d c3');fix.append((at+15,0x4500));data[at:at+len(code)]=code
  data[0x4600:0x4620]=bytes([255])*32
  code=bytearray.fromhex('41 54 41 55 49 c7 c4 16 00 00 00 49 c7 c5 21 00 00 00 48 c7 c0 0b 00 00 00');code.extend(bytes([0x90])*(32-len(code)));code.extend(bytes.fromhex('ba 09 00 00 00 48 85 c0 48 01 d0 41 5d 41 5c c3'));data[0x2e00:0x2e00+len(code)]=code
  code=bytearray.fromhex('53 41 55 48 c7 c3 21 00 00 00 49 c7 c5 16 00 00 00 48 c7 c0 0b 00 00 00');code.extend(bytes([0x90])*(32-len(code)));code.extend(bytes.fromhex('48 89 c2 b9 09 00 00 00 48 85 d2 48 01 c8 41 5d 5b c3'));data[0x2f00:0x2f00+len(code)]=code
 if mode=='extra-heaps':
  prefix=bytes.fromhex('55 48 89 e5 41 57')
  for at in [0x3000,0x3200,0x3300]:
   code=bytearray(prefix)
   if at==0x3300:
    # Same semantic small-alignment tail as the real guarded profile.
    code.extend(bytes.fromhex('48 83 fa 10 77 08 41 5f 5d e9'));patch=at+len(code);code.extend(bytes(4));fix.append((patch,0x3200))
   code.extend(bytes.fromhex('48 ff 47 08 48 8b 07 41 5f 5d c3'));data[at:at+len(code)]=code
  for at in [0x3100,0x3400]:
   code=bytearray(prefix+bytes.fromhex('48 ff 47 10 48 83 ec 08 ff 15'));patch=at+len(code);code.extend(bytes(4));fix.append((patch,0x4800+8*names.index('bb.prototype.extra_heap_during')))
   code.extend(bytes.fromhex('48 83 c4 08 c5 7e 6f 3d'));patch=at+len(code);code.extend(bytes(4));fix.append((patch,0x4600));code.extend(bytes.fromhex('b8 77 00 00 00 41 5f 5d c3'));data[at:at+len(code)]=code
  data[0x4600:0x4620]=bytes([255])*32
 if mode=='net-http-roundtrip':
  for at,text in [(0x5600,b'BloodEchoes-ARM64-Isolated-QA/1.0\0'),(0x5680,b'https://thehuntersdream.com/about\0')]:data[at:at+len(text)]=text
 if mode=='net-thd-path-bad':data[0x7ff8:0x8000]=b'x'*8
 if mode=='long-string':data[0x7000:0x7000+5001]=b'x'*5000+b'\0'
 for patch,target in fix:struct.pack_into('<i',data,patch,target-patch-4)
 for i,name in enumerate(names):relocs.append((0x4800+8*i,2 if name=='__progname' and mode!='progname-function' else 1,i,0))
 modules=[(0,512,0,0x4200,16,8,2),(0x200,512,0x200,0x4210,16,8,3)]
 header=b'BBPROBE5'+struct.pack('<6Q',len(data),0x400,2,len(relocs),len(names),1)
 header+=struct.pack('<6Q',0x4000,0x4100,8,16,16,2)
 header+=b''.join(struct.pack('<7Q',*m) for m in modules)+struct.pack('<Q',0)
 header+=struct.pack('<6Q',0,16384,5,16384,len(data)-16384,6)
 header+=b''.join(nids[n].encode().ljust(128,b'\0') for n in names)
 return header+b''.join(struct.pack('<4Q',*r) for r in relocs)+data
if __name__=='__main__':
 out=P/'fixtures/modules-tls.bbprobe';out.write_bytes(make());print(out)
