"""Preparation-only dispatch budget; no game runtime or cache eviction policy."""
import concurrent.futures as futures
import ctypes as C
import os
import time

MIB=1024**2
CPU_LIMIT=max(1,os.cpu_count()or 1)

def maximum_jobs(value='auto',*,cpu_count=CPU_LIMIT):
    if type(cpu_count)is not int or cpu_count<1:raise ValueError('invalid logical CPU count')
    if value=='auto':return min(2,cpu_count)
    if not isinstance(value,str)or not value.isascii()or not value.isdecimal()or str(int(value))!=value or not 1<=int(value)<=cpu_count:
        raise ValueError('compiler job limit must be auto or 1..'+str(cpu_count))
    return int(value)

def capacity(sample,maximum=2,*,worker_mib=768,reserve_mib=768):
    if type(maximum)is not int or not 1<=maximum<=CPU_LIMIT:raise ValueError('compiler limit exceeds logical CPU count')
    pressure=sample['pressure'];headroom=sample['headroom_mib']
    if pressure not in(1,2,4)or headroom<0:raise ValueError('invalid compiler memory observation')
    if pressure==4:return 0
    count=max(0,min(maximum,int((headroom-reserve_mib)//worker_mib)))
    return min(count,1)if pressure==2 else count

class Memory:
    """Reuse the bounded VM observer. No lock-screen or QA swap-stop policy."""
    def __init__(self):
        from native_vm_observer import NativeVMObserver
        self.vm=NativeVMObserver()
        self.vm.lib.sysctlbyname.argtypes=[C.c_char_p,C.c_void_p,C.POINTER(C.c_size_t),C.c_void_p,C.c_size_t]
        self.vm.lib.sysctlbyname.restype=C.c_int
    def close(self):self.vm.close()
    def __call__(self):
        pressure=C.c_int();size=C.c_size_t(C.sizeof(pressure))
        if self.vm.lib.sysctlbyname(b'kern.memorystatus_vm_pressure_level',C.byref(pressure),C.byref(size),None,0)or size.value!=C.sizeof(pressure):raise OSError('compiler pressure observation failed')
        vm=self.vm.system()
        # Only half of inactive pages count as reclaimable budget. Never count
        # compressed memory or free disk/swap space as available RAM.
        # mach/vm_statistics.h explicitly includes speculative_count in free_count.
        pages=vm['free_count']+vm['inactive_count']//2
        return dict(pressure=pressure.value,headroom_mib=pages*vm['page_bytes']/MIB)

def run(items,work,*,maximum=None,observe,progress=lambda **event:None,
        clock=time.monotonic,poll_seconds=.25,stall_seconds=300):
    """Submit only current capacity; finish running work when capacity falls.

    Existing objects are owned by the caller. A pause or failure never removes
    them. Output order is stable and independent of task completion order.
    """
    maximum=maximum_jobs()if maximum is None else maximum
    if type(maximum)is not int or not 1<=maximum<=CPU_LIMIT:raise ValueError('compiler limit exceeds logical CPU count')
    items=list(items);output=[None]*len(items);next_item=completed=0
    pending={};last_progress=clock();last_event=None;last_emit=-float('inf')
    with futures.ThreadPoolExecutor(max_workers=maximum)as pool:
        while next_item<len(items)or pending:
            now=clock();sample=observe();limit=capacity(sample,maximum)
            while next_item<len(items)and len(pending)<limit:
                index=next_item;next_item+=1;pending[pool.submit(work,items[index])]=index
            event=(completed,len(pending),limit,sample['pressure'])
            if event!=last_event or now-last_emit>=5:
                progress(done=completed,total=len(items),active=len(pending),capacity=limit,
                         pressure=sample['pressure'],headroom_mib=round(sample['headroom_mib'],1))
                last_event=event;last_emit=now
            if not pending:
                if now-last_progress>=stall_seconds:raise RuntimeError('compiler dispatch paused for memory; completed objects retained')
                time.sleep(poll_seconds);continue
            done,_=futures.wait(pending,timeout=poll_seconds,return_when=futures.FIRST_COMPLETED)
            for future in done:
                output[pending.pop(future)]=future.result();completed+=1;last_progress=clock()
        progress(done=completed,total=len(items),active=0,capacity=0,finished=True)
    return output
