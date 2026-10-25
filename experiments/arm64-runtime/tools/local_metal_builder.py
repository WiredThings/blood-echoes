#!/usr/bin/env python3
"""Explicit, local-only MSL -> AIR -> metallib builder. OFF performs no IO.

Only the CLI's --enable branch constructs native observation/toolchain services. Internal
runner/fingerprint injection exists solely for authored tests; no compiler/flag override is
accepted from CLI or request JSON. Nothing uploads source, libraries, receipts or caches.
"""
from pathlib import Path
import argparse,fcntl,hashlib,json,os,re,signal,stat,subprocess,tempfile,time

PROJECT=Path(__file__).resolve().parents[1]
RECIPE='bb-metal-local-aot-v1'
LIBRARY_RECIPE='bb-metal-local-library-v2'
HEX=re.compile(r'^[0-9a-f]{64}$')
SOURCE_MAX=1024*1024
LIBRARY_MAX=32*1024*1024
AIR_MAX=64*1024*1024
JSON_MAX=16*1024
REQUEST_MAX=4096
LOG_MAX=1024*1024
STAGE_TIMEOUT=60.0
TOTAL_TIMEOUT=1800.0
REQUEST_FIELDS={'schema','key','cohort','source_sha256','entry','language','math_mode','math_fp32','recipe'}
LIBRARY_REQUEST_FIELDS=REQUEST_FIELDS-{'entry'}

class Refused(RuntimeError):pass
class Stopped(Refused):pass

def digest(data):return hashlib.sha256(data).hexdigest()
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')
def derive_key(request):
    fields=[request[k]for k in (('recipe','cohort','source_sha256','language','math_mode','math_fp32') if request.get('schema')==2 else ('recipe','cohort','source_sha256','entry','language','math_mode','math_fp32'))]
    encoded=b''.join(str(len(value.encode('utf-8'))).encode()+b':'+value.encode('utf-8')for value in fields)
    return digest(encoded)
def _identity(st):return st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns

def regular_bytes(path,maximum,*,owned=True,request_pin=None):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        before=os.fstat(fd)
        links=1 if request_pin is None else request_pin[2]
        if not stat.S_ISREG(before.st_mode)or before.st_nlink!=links or(owned and before.st_uid!=os.getuid()):raise Refused('unowned, linked or nonregular file')
        if request_pin is not None and(before.st_dev,before.st_ino)!=request_pin[:2]:raise Refused('request inode changed after inventory')
        if not 0<=before.st_size<=maximum:raise Refused('file exceeds bounded size')
        chunks=[];remaining=maximum+1
        while remaining:
            part=os.read(fd,min(remaining,1024*1024))
            if not part:break
            chunks.append(part);remaining-=len(part)
        data=b''.join(chunks)
        end=os.fstat(fd)
        if len(data)>maximum or _identity(before)!=_identity(end)or before.st_nlink!=end.st_nlink:raise Refused('input changed or exceeds bounded size')
        return data
    finally:os.close(fd)

def _pairs(pairs):
    out={}
    for key,value in pairs:
        if key in out:raise Refused('duplicate JSON field')
        out[key]=value
    return out

def read_json(path,*,request_pin=None):
    try:return json.loads(regular_bytes(path,JSON_MAX,request_pin=request_pin).decode('utf-8'),object_pairs_hook=_pairs,parse_constant=lambda value:(_ for _ in ()).throw(Refused('nonfinite JSON')))
    except(UnicodeError,json.JSONDecodeError)as error:raise Refused('invalid UTF-8/JSON')from error

def check_directory(path):
    st=path.lstat()
    if not stat.S_ISDIR(st.st_mode)or st.st_uid!=os.getuid():raise Refused('unowned or linked directory')
    return path

def child_directory(parent,name,*,create=False):
    if Path(name).name!=name:raise Refused('invalid directory basename')
    path=parent/name
    if create:
        try:path.mkdir(mode=0o700)
        except FileExistsError:pass
    return check_directory(path)

def checked_root(root):
    # Deliberately only this prepared private host; no HOME/cwd/installation fallback.
    state=PROJECT/'state';root=Path(root)
    if not root.is_absolute()or root!=root.resolve(strict=True):raise Refused('noncanonical or symlink root')
    try:relative=root.relative_to(state)
    except ValueError as error:raise Refused('root must stay inside private state')from error
    if len(relative.parts)<2:raise Refused('AOT root must be nested below a private GPU directory')
    current=check_directory(state)
    for part in relative.parts:current=check_directory(current/part)
    gpu=os.environ.get('BB_GPU_USER_DIR')
    if gpu:
        gpu=Path(gpu)
        if not gpu.is_absolute()or gpu!=gpu.resolve(strict=True)or not gpu.is_relative_to(state):raise Refused('invalid actual GPU state root')
        if root==gpu or not root.is_relative_to(gpu):raise Refused('AOT root must be below actual GPU state root')
    return root

def request_keys(folder,*,recovery=None,pins=None):
    # Read-only recovery: accept only complete request pairs; preserve orphan finals
    # and known publication temporaries. Never unlink/rename/"repair" request files.
    final=re.compile(r'^([0-9a-f]{64})\.(metal|json)$')
    temporary=re.compile(r'^([0-9a-f]{64})\.(metal|json)\.tmp-([0-9]+)-([0-9]+)$')
    seen={};groups={};count=0;ignored=0
    with os.scandir(folder)as entries:
        for entry in entries:
            count+=1
            # 2 finals plus bounded legacy source/JSON temporaries per request.
            if count>4*REQUEST_MAX:raise Refused('request/recovery entry limit exceeded')
            match=final.fullmatch(entry.name);temp=False
            if match is None:match=temporary.fullmatch(entry.name);temp=True
            if match is None:raise Refused('unexpected request entry')
            key,extension=match.group(1),match.group(2)
            row=entry.stat(follow_symlinks=False)
            limit=SOURCE_MAX if extension=='metal'else JSON_MAX
            if not stat.S_ISREG(row.st_mode)or row.st_uid!=os.getuid()or not 0<=row.st_size<=limit:raise Refused('unowned/nonregular/oversized request entry')
            group=groups.setdefault((row.st_dev,row.st_ino),[])
            group.append((entry.name,key,extension,row.st_nlink,temp))
            if temp:ignored+=1
            else:seen.setdefault(key,set()).add(extension)
    if len(seen)>REQUEST_MAX:raise Refused('request count limit exceeded')
    legacy=0
    for(dev,inode),group in groups.items():
        # A legacy link+unlink crash may leave final+temp names for ONE content-key
        # family. Every link must be accounted for inside this exact requests folder.
        # External/unknown links and cross-key/extension links remain forbidden.
        if any(row[3]!=len(group)for row in group)or len({(row[1],row[2])for row in group})!=1:raise Refused('request has unaccounted or cross-family hardlinks')
        if len(group)>1:legacy+=1
        if pins is not None:
            for name,key,extension,links,temp in group:
                if not temp:pins[name]=(dev,inode,links)
    complete=sorted(key for key,value in seen.items()if value=={'metal','json'})
    deferred=sorted(key for key,value in seen.items()if value!={'metal','json'})
    if recovery is not None:recovery.update(ignored_temporaries=ignored,deferred_unpaired=deferred,legacy_internal_link_groups=legacy,requests_modified=False)
    return complete

def load_request(folder,key,cohort,*,pins=None):
    pin=lambda suffix:None if pins is None else pins[key+suffix]
    request=read_json(folder/(key+'.json'),request_pin=pin('.json'))
    if type(request)is not dict or type(request.get('schema'))is not int or request['schema']not in(1,2):raise Refused('unsupported request schema')
    fields=REQUEST_FIELDS if request['schema']==1 else LIBRARY_REQUEST_FIELDS
    if set(request)!=fields:raise Refused('request fields do not match schema')
    if any(type(request[k])is not str for k in fields-{'schema'}):raise Refused('request field type mismatch')
    if request['key']!=key or request['cohort']!=cohort or not HEX.fullmatch(request['source_sha256']):raise Refused('request key/cohort/source identity mismatch')
    if request['schema']==1:
        if request['recipe']!=RECIPE or request['language']!='metal3.1' or request['math_mode']not in('safe','relaxed')or request['math_fp32']!='fast':raise Refused('unsupported fixed compile recipe')
        entry=request['entry']
        if not entry or len(entry.encode('utf-8'))>256 or '\0'in entry:raise Refused('invalid entry name')
    else:
        if request['recipe']!=LIBRARY_RECIPE or request['language']not in {'metal1.0','metal1.1','metal1.2','metal2.0','metal2.1','metal2.2','metal2.3','metal2.4','metal3.0','metal3.1','metal3.2','metal4.0','metal4.1'} or request['math_mode']not in('safe','relaxed','fast')or request['math_fp32']not in('fast','precise'):raise Refused('unsupported complete library recipe')
    if derive_key(request)!=key:raise Refused('recomputed request key mismatch')
    source=regular_bytes(folder/(key+'.metal'),SOURCE_MAX,request_pin=pin('.metal'))
    try:source.decode('utf-8')
    except UnicodeError as error:raise Refused('source is not UTF-8')from error
    if b'\0'in source or digest(source)!=request['source_sha256']:raise Refused('source digest/content mismatch')
    return request,source

def write_new(path,data):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,'wb',closefd=False)as stream:stream.write(data);stream.flush();os.fsync(fd)
    finally:os.close(fd)

def atomic_json(path,value):
    # Caller already owns and has validated the containing private directory.
    fd,name=tempfile.mkstemp(prefix='.'+path.name+'-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb')as stream:stream.write(canonical(value)+b'\n');stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
    finally:
        try:os.unlink(name)
        except FileNotFoundError:pass

def expected_receipt(request,build_id,library):
    return dict(request,build_id=build_id,library_sha256=digest(library),library_bytes=len(library))

def already_succeeded(folder,request,build_id):
    library=folder/(request['key']+'.metallib');receipt=folder/(request['key']+'.json')
    has_lib=os.path.lexists(library);has_receipt=os.path.lexists(receipt)
    if has_receipt:
        if not has_lib:raise Refused('receipt exists without its library')
        blob=regular_bytes(library,LIBRARY_MAX)
        if not blob:raise Refused('empty existing library')
        if canonical(read_json(receipt))!=canonical(expected_receipt(request,build_id,blob)):raise Refused('existing receipt/library identity mismatch')
        return True
    if has_lib:
        # An interrupted library-first publish is not a success and cannot be skipped.
        regular_bytes(library,LIBRARY_MAX)
    return False

class Safety:
    def __init__(self,observer,require_no_games,policy_factory,clock=time.monotonic):
        self.observer=observer;self.no_games=require_no_games;self.clock=clock
        self.started=clock();baseline=observer.system()
        if baseline['locked']or baseline['pressure']==4:raise Stopped('locked or severe pressure before build')
        self.policy=policy_factory(baseline);self.no_games()
    def check(self,*,preflight=False):
        now=self.clock()-self.started;sample=self.observer.system()
        reason=self.policy.observe(now,sample)
        if reason or now>TOTAL_TIMEOUT:raise Stopped(reason or 'total build timeout')
        if preflight:
            self.no_games()
            if sample['pressure']==4:raise Stopped('severe pressure before new compiler stage')

class FixedRunner:
    def __init__(self,private_tmp):self.tmp=private_tmp
    def run(self,argv,timeout,guard,*,cwd=None,outputs=()):
        guard.check(preflight=True)
        # Deliberately do not inherit DEVELOPER_DIR, TOOLCHAINS, DYLD_*, or shell options.
        env={'PATH':'/usr/bin:/bin:/usr/sbin:/sbin','HOME':str(self.tmp),'TMPDIR':str(self.tmp)+'/'}
        log=tempfile.TemporaryFile(dir=self.tmp);proc=None;started=time.monotonic()
        try:
            proc=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
                                  cwd=cwd,env=env,shell=False,start_new_session=True)
            while proc.poll()is None:
                guard.check()
                if time.monotonic()-started>timeout:raise Stopped('bounded compiler/tool query timeout')
                if os.fstat(log.fileno()).st_size>LOG_MAX:raise Stopped('tool output limit exceeded')
                for path,limit in outputs:
                    if os.path.lexists(path):
                        row=Path(path).lstat()
                        if not stat.S_ISREG(row.st_mode)or row.st_uid!=os.getuid()or row.st_nlink!=1 or not 0<=row.st_size<=limit:raise Stopped('compiler output size/type limit exceeded')
                time.sleep(.1)
            guard.check()
            if proc.returncode:
                # Compiler diagnostics can quote private MSL. Retain only a bounded
                # private tail below artifacts; summary/stdout contain its path only.
                size=os.fstat(log.fileno()).st_size;log.seek(max(0,size-2048))
                tail=log.read(2048)or b'<empty tool output>\n'
                fd,name=tempfile.mkstemp(prefix='failed-tool-',suffix='.log',dir=self.tmp)
                try:
                    with os.fdopen(fd,'wb')as diagnostic:diagnostic.write(tail);diagnostic.flush();os.fsync(diagnostic.fileno())
                except BaseException:
                    try:os.unlink(name)
                    except OSError:pass
                    raise
                raise Refused('fixed tool command failed: '+str(proc.returncode)+'; private diagnostic tail: '+name)
            if os.fstat(log.fileno()).st_size>LOG_MAX:raise Refused('tool output limit exceeded')
            log.seek(0);return log.read(LOG_MAX+1)
        finally:
            if proc is not None and proc.poll()is None:
                os.killpg(proc.pid,signal.SIGTERM)
                # The unchanged launcher escalates an unresponsive game after
                # three seconds. Reap compiler children within that interval.
                try:proc.wait(timeout=.75)
                except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait(timeout=.75)
            log.close()

def tool_fingerprint(path):
    path=Path(path).resolve(strict=True)
    before=path.stat()
    if not stat.S_ISREG(before.st_mode)or before.st_mode&0o022:raise Refused('tool binary must be regular and not group/world writable')
    with path.open('rb')as stream:sha=hashlib.file_digest(stream,'sha256').hexdigest()
    if _identity(before)!=_identity(path.stat()):raise Refused('tool binary changed while hashing')
    return dict(path=str(path),sha256=sha,stat=list(_identity(before)))

class NativeToolchain:
    def __init__(self,runner,*,fingerprint=tool_fingerprint):self.runner=runner;self.fingerprint=fingerprint
    def identify(self,guard):
        tools={};versions={}
        for name in('metal','metallib'):
            found=self.runner.run(['/usr/bin/xcrun','--find',name],10,guard).decode().strip()
            if not found.startswith('/')or '\n'in found:raise Refused('invalid resolved tool path')
            tools[name]=self.fingerprint(found)
            versions[name]=self.runner.run(['/usr/bin/xcrun',name,'--version'],10,guard).decode('utf-8').strip()
            if not versions[name]or len(versions[name].encode())>4096:raise Refused('empty or oversized tool version')
        build=self.runner.run(['/usr/bin/sw_vers','-buildVersion'],10,guard).decode().strip()
        if not build or len(build.encode())>256:raise Refused('empty or oversized OS build identity')
        tools['xcrun']=self.fingerprint('/usr/bin/xcrun')
        versions['xcrun']=self.runner.run(['/usr/bin/xcrun','--version'],10,guard).decode().strip()
        if not versions['xcrun']or len(versions['xcrun'].encode())>1024:raise Refused('empty or oversized xcrun version')
        # Stat pins detect mid-build replacement; only actual binary SHA/version/OS define ID.
        manifest=dict(schema=1,recipe=RECIPE,tools={name:{'sha256':row['sha256'],'version':versions[name]}for name,row in tools.items()},os_build=build)
        self.pins=tools;self.build_manifest=manifest;self.build_id=digest(canonical(manifest))
        return self.build_id,manifest
    def unchanged(self,guard):
        guard.check(preflight=True)
        for name in('metal','metallib'):
            found=self.runner.run(['/usr/bin/xcrun','--find',name],10,guard).decode().strip()
            if str(Path(found).resolve(strict=True))!=self.pins[name]['path']:raise Refused('selected toolchain changed during build')
        for row in self.pins.values():
            if list(_identity(Path(row['path']).stat()))!=row['stat']:raise Refused('tool binary changed during build')
    def compile(self,source,air,library,request,guard):
        self.unchanged(guard)
        command=['/usr/bin/xcrun','metal','-std='+request['language'],
                 '-fmetal-math-mode='+request['math_mode'],'-fmetal-math-fp32-functions='+request['math_fp32'],
                 '-c',str(source),'-o',str(air)]
        self.runner.run(command,STAGE_TIMEOUT,guard,cwd=source.parent,outputs=((air,AIR_MAX),))
        row=air.lstat()
        if not stat.S_ISREG(row.st_mode)or row.st_uid!=os.getuid()or row.st_nlink!=1 or not 0<row.st_size<=AIR_MAX:raise Refused('invalid/empty AIR output')
        self.runner.run(['/usr/bin/xcrun','metallib',str(air),'-o',str(library)],STAGE_TIMEOUT,guard,
                        cwd=source.parent,outputs=((library,LIBRARY_MAX),))
        self.unchanged(guard)

def real_services(private_tmp):
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parent))
    from native_observer import NativeObserver
    from qa_memory_policy import MemoryPolicy
    from qa_build_guard import require_no_games
    observer=NativeObserver()
    return observer,Safety(observer,require_no_games,MemoryPolicy),NativeToolchain(FixedRunner(private_tmp))

def build(root=None,cohort=None,*,enabled=False,guard=None,toolchain=None):
    # Do not move path resolution, imports, enumeration, hashes or services above this gate.
    if not enabled:return dict(status='off',build_id=None,built=0,skipped=0)
    summary=dict(status='failed',build_id=None,built=0,skipped=0,requests=0,results=[])
    observer=None;lock_fd=None;summary_path=None
    try:
        if type(cohort)is not str or not HEX.fullmatch(cohort):raise Refused('cohort must be lowercase 64-hex')
        root=checked_root(root);folder=child_directory(root,cohort);requests=child_directory(folder,'requests')
        artifact_root=child_directory(folder,'artifacts',create=True)
        private_tmp=child_directory(artifact_root,'.builder-tmp',create=True)
        lock_fd=os.open(artifact_root/'.builder.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        st=os.fstat(lock_fd)
        if not stat.S_ISREG(st.st_mode)or st.st_uid!=os.getuid()or st.st_nlink!=1:raise Refused('invalid builder lock')
        try:fcntl.flock(lock_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as error:raise Refused('another builder owns this cohort')from error
        summary_path=artifact_root/'builder-summary.json'
        if(guard is None)!=(toolchain is None):raise Refused('internal test services must be supplied together')
        if guard is None:observer,guard,toolchain=real_services(private_tmp)
        guard.check(preflight=True)
        recovery={};pins={}
        keys=request_keys(requests,recovery=recovery,pins=pins);summary['requests']=len(keys);summary['request_recovery']=recovery
        build_id,manifest=toolchain.identify(guard)
        if not HEX.fullmatch(build_id)or digest(canonical(manifest))!=build_id:raise Refused('invalid toolchain build identity')
        summary['build_id']=build_id
        artifacts=child_directory(artifact_root,build_id,create=True)
        build_record=artifacts/'build.json'
        if os.path.lexists(build_record):
            if canonical(read_json(build_record))!=canonical(manifest):raise Refused('existing build manifest identity mismatch')
        else:atomic_json(build_record,manifest)
        for key in keys:
            guard.check(preflight=True)
            request,source=load_request(requests,key,cohort,pins=pins)
            if already_succeeded(artifacts,request,build_id):
                summary['skipped']+=1;summary['results'].append(dict(key=key,status='skipped-verified'));continue
            with tempfile.TemporaryDirectory(prefix=key+'-',dir=private_tmp)as scratch:
                scratch=Path(scratch);snapshot=scratch/'source.metal';air=scratch/'output.air';library=scratch/'output.metallib'
                write_new(snapshot,source);del source
                toolchain.compile(snapshot,air,library,request,guard)
                blob=regular_bytes(library,LIBRARY_MAX)
                if not blob:raise Refused('empty metallib output')
                receipt=expected_receipt(request,build_id,blob);del blob
                staged_receipt=scratch/'receipt.json';write_new(staged_receipt,canonical(receipt)+b'\n')
                # No successful pair is ever overwritten: verified pairs already skipped;
                # mismatched pairs refused. A crash between these renames leaves only an
                # untrusted orphan library, never a receipt authorizing unknown bytes.
                if already_succeeded(artifacts,request,build_id):raise Refused('artifact appeared during serial build')
                guard.check(preflight=True)
                os.replace(library,artifacts/(key+'.metallib'))
                os.replace(staged_receipt,artifacts/(key+'.json'))
                fd=os.open(artifacts,os.O_RDONLY|os.O_DIRECTORY)
                try:os.fsync(fd)
                finally:os.close(fd)
            summary['built']+=1;summary['results'].append(dict(key=key,status='built'))
        summary['status']='partial'if recovery['deferred_unpaired']else'passed'
    except KeyboardInterrupt:
        summary['status']='stopped';summary['error']='interrupted'
    except Exception as error:
        summary['status']='stopped'if isinstance(error,Stopped)else'refused'if isinstance(error,(Refused,OSError,ValueError,TypeError))else'failed'
        summary['error']=str(error)[:1024]
    finally:
        if summary_path is not None:
            try:atomic_json(summary_path,summary)
            except Exception as error:
                # Preserve a lock/memory stop even when its diagnostic cannot be
                # written; auto fallback must not launch after a safety stop.
                if summary['status']!='stopped':summary['status']='failed'
                summary['summary_write_error']=str(error)[:512]
        if lock_fd is not None:os.close(lock_fd)
        if observer is not None:observer.close()
    return summary

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--enable',action='store_true');parser.add_argument('--root');parser.add_argument('--cohort')
    args=parser.parse_args();summary=build(args.root,args.cohort,enabled=args.enable)
    print(json.dumps(summary,indent=2,ensure_ascii=False));return 0 if summary['status']in('off','passed')else 1
if __name__=='__main__':raise SystemExit(main())
