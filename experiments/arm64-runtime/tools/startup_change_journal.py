"""Bounded Darwin change-journal hints. Uncertainty always requests a full check.

No daemon, background watcher, or disk cache authority. FullHistory overlap is
remembered explicitly; only identical already-seen events may be deduplicated.
The native query lives in a timeout-bounded child and releases its CF objects.
"""
from pathlib import Path
import json,os,stat,subprocess,sys
MAX_ROOTS=16
MAX_EVENTS=4096
MAX_MESSAGE=1024*1024


def query(roots,previous=None):
    try:
        request=json.dumps(dict(roots=[str(Path(p).absolute())for p in roots],previous=previous),separators=(',',':')).encode()
        if len(request)>MAX_MESSAGE:return None
        env={k:v for k,v in os.environ.items()if not k.startswith(('BB_','DYLD_'))}
        result=subprocess.run([sys.executable,'-B',str(Path(__file__).resolve()),'--native-query'],
            input=request,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=5,env=env)
        if result.returncode or len(result.stdout)>MAX_MESSAGE:return None
        value=json.loads(result.stdout)
        return value if type(value.get('schema'))is int and value['schema']==1 and value.get('complete')is True else None
    except(OSError,ValueError,TypeError,subprocess.TimeoutExpired):return None


def unchanged(token,roots):
    if not isinstance(token,dict):return False
    value=query(roots,token)
    return value is not None and value.get('unchanged')is True


def _native(roots,previous):
    import ctypes as C,time
    if sys.platform!='darwin' or not isinstance(roots,list)or not 0<len(roots)<=MAX_ROOTS:raise ValueError('journal roots')
    cf=C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    fs=C.CDLL('/System/Library/Frameworks/CoreServices.framework/Frameworks/FSEvents.framework/FSEvents')
    callback_type=C.CFUNCTYPE(None,C.c_void_p,C.c_void_p,C.c_size_t,C.c_void_p,C.POINTER(C.c_uint32),C.POINTER(C.c_uint64))
    definitions=[(cf,'CFStringCreateWithCString',[C.c_void_p,C.c_char_p,C.c_uint32],C.c_void_p),
      (cf,'CFStringGetCString',[C.c_void_p,C.c_char_p,C.c_long,C.c_uint32],C.c_bool),
      (cf,'CFUUIDCreateString',[C.c_void_p,C.c_void_p],C.c_void_p),
      (cf,'CFArrayCreate',[C.c_void_p,C.POINTER(C.c_void_p),C.c_long,C.c_void_p],C.c_void_p),
      (cf,'CFRelease',[C.c_void_p],None),(cf,'CFRunLoopGetCurrent',[],C.c_void_p),
      (cf,'CFRunLoopRunInMode',[C.c_void_p,C.c_double,C.c_bool],C.c_int),
      (fs,'FSEventsGetCurrentEventId',[],C.c_uint64),
      (fs,'FSEventsCopyUUIDForDevice',[C.c_int],C.c_void_p),
      (fs,'FSEventStreamCreateRelativeToDevice',[C.c_void_p,callback_type,C.c_void_p,C.c_int,C.c_void_p,C.c_uint64,C.c_double,C.c_uint32],C.c_void_p),
      (fs,'FSEventStreamScheduleWithRunLoop',[C.c_void_p,C.c_void_p,C.c_void_p],None),
      (fs,'FSEventStreamStart',[C.c_void_p],C.c_bool),
      (fs,'FSEventStreamFlushSync',[C.c_void_p],None),
      (fs,'FSEventStreamStop',[C.c_void_p],None),
      (fs,'FSEventStreamInvalidate',[C.c_void_p],None),
      (fs,'FSEventStreamRelease',[C.c_void_p],None)]
    for lib,name,args,result in definitions:
        func=getattr(lib,name);func.argtypes=args;func.restype=result
    groups={};pins={}
    for name in sorted(set(roots)):
        p=Path(name).absolute();s=p.lstat()
        if p!=p.resolve(strict=True)or not stat.S_ISDIR(s.st_mode)or s.st_uid!=os.getuid():raise ValueError('journal canonical owned root')
        mount=p
        while not os.path.ismount(mount):mount=mount.parent
        uuid=fs.FSEventsCopyUUIDForDevice(s.st_dev)
        if not uuid:raise ValueError('journal volume UUID unavailable')
        text=None
        try:
            text=cf.CFUUIDCreateString(None,uuid);buf=C.create_string_buffer(128)
            if not text or not cf.CFStringGetCString(text,buf,len(buf),0x08000100):raise ValueError('journal UUID')
            volume=buf.value.decode('ascii')
        finally:
            if text:cf.CFRelease(text)
            cf.CFRelease(uuid)
        pins[name]=[s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,volume,str(mount)]
        groups.setdefault((s.st_dev,name),[]).append('/'+str(p.relative_to(mount)))
    if previous is not None:
        if type(previous.get('schema'))is not int or previous['schema']!=1 or previous.get('pins')!=pins:raise ValueError('journal roots/volume changed')
        cursor=previous['cursor'];known=previous['events']
        if type(cursor)is not int or not 0<cursor<2**64-1 or not isinstance(known,list)or len(known)>MAX_EVENTS:raise ValueError('journal checkpoint')
    else:cursor=int(fs.FSEventsGetCurrentEventId());known=[]
    if cursor<=0 or int(fs.FSEventsGetCurrentEventId())<cursor:raise ValueError('journal cursor reset')
    events=[];unsafe=False;event_bytes=0
    mode=cf.CFStringCreateWithCString(None,b'kCFRunLoopDefaultMode',0x08000100)
    if not mode:raise ValueError('journal runloop mode')
    try:
        for (device,_root),names in groups.items():
            history=False;strings=[];array=None;stream=None;started=False
            def callback(_stream,_context,count,paths,flags,ids):
                nonlocal history,unsafe,event_bytes
                try:
                    if count>MAX_EVENTS:raise ValueError('journal event batch budget')
                    names_=C.cast(paths,C.POINTER(C.c_char_p))
                    for i in range(count):
                        flag=int(flags[i])
                        if flag&0x10:history=True;continue
                        # Includes coalesced/dropped/wrapped/root/mount/unmount.
                        if flag&0xef:unsafe=True
                        raw=names_[i]
                        if raw is None or len(raw)>16384:raise ValueError('journal path budget')
                        event_bytes+=len(raw)
                        if event_bytes>MAX_MESSAGE//2:raise ValueError('journal total path budget')
                        if len(events)>=MAX_EVENTS:raise ValueError('journal event count budget')
                        events.append([int(device),int(ids[i]),flag,os.fsdecode(raw)])
                except BaseException:unsafe=True
            cb=callback_type(callback)
            try:
                for name in names:
                    value=cf.CFStringCreateWithCString(None,os.fsencode(name),0x08000100)
                    if not value:raise ValueError('journal path CFString')
                    strings.append(value)
                values=(C.c_void_p*len(strings))(*strings);array=cf.CFArrayCreate(None,values,len(strings),None)
                if not array:raise ValueError('journal path array')
                # FileEvents | WatchRoot | NoDefer | FullHistory. Never IgnoreSelf.
                stream=fs.FSEventStreamCreateRelativeToDevice(None,cb,None,device,array,cursor,.01,0x96)
                if not stream:raise ValueError('journal stream unavailable')
                fs.FSEventStreamScheduleWithRunLoop(stream,cf.CFRunLoopGetCurrent(),mode)
                started=bool(fs.FSEventStreamStart(stream))
                if not started:raise ValueError('journal stream refused')
                deadline=time.monotonic()+2
                while not history and time.monotonic()<deadline:cf.CFRunLoopRunInMode(mode,.02,True)
                if not history:raise ValueError('journal history incomplete')
                fs.FSEventStreamFlushSync(stream)
                if unsafe:raise ValueError('journal continuity unavailable')
            finally:
                if stream:
                    if started:fs.FSEventStreamStop(stream)
                    fs.FSEventStreamInvalidate(stream);fs.FSEventStreamRelease(stream)
                if array:cf.CFRelease(array)
                for value in strings:cf.CFRelease(value)
    finally:cf.CFRelease(mode)
    # Exact overlap events only. New/changed events at older IDs are NOT ignored.
    old={json.dumps(row,separators=(',',':'))for row in known}
    clean=previous is not None and all(json.dumps(row,separators=(',',':'))in old for row in events)
    return dict(schema=1,complete=True,pins=pins,cursor=cursor,events=events,unchanged=clean)


if __name__=='__main__':
    try:
        if sys.argv[1:]!=['--native-query']:raise ValueError('native-only query')
        raw=sys.stdin.buffer.read(MAX_MESSAGE+1)
        if len(raw)>MAX_MESSAGE:raise ValueError('query budget')
        request=json.loads(raw);result=_native(request['roots'],request['previous'])
        raw=json.dumps(result,separators=(',',':')).encode()
        if len(raw)>MAX_MESSAGE:raise ValueError('response budget')
        sys.stdout.buffer.write(raw)
    except Exception:raise SystemExit(65)
