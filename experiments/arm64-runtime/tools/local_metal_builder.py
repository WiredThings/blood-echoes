#!/usr/bin/env python3
"""Explicit, local-only MSL -> AIR -> metallib builder. OFF performs no IO.

Only the CLI's --enable branch constructs native observation/toolchain services. Internal
runner/fingerprint injection exists solely for authored tests; no compiler/flag override is
accepted from CLI or request JSON. Nothing uploads source, libraries, receipts or caches.
"""
from pathlib import Path
import argparse,fcntl,hashlib,json,os,re,signal,sqlite3,stat,subprocess,sys,tempfile,time

PROJECT=Path(__file__).resolve().parents[1]
RECIPE='bb-metal-local-aot-v1'
INVARIANT_RECIPE='bb-metal-local-aot-invariant-v1'
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
INVARIANT_REQUEST_FIELDS=REQUEST_FIELDS|{'preserve_invariance'}

class Refused(RuntimeError):pass
class Stopped(Refused):pass

def macos_xcrun(*args):return ['/usr/bin/xcrun','--sdk','macosx',*args]

def digest(data):return hashlib.sha256(data).hexdigest()
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')
def derive_key(request):
    fields=[request[k]for k in (('recipe','cohort','source_sha256','language','math_mode','math_fp32') if request.get('schema')==2 else ('recipe','cohort','source_sha256','entry','language','math_mode','math_fp32'))]
    if request.get('schema')==3:fields.append('preserve-invariance=1')
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

def checked_root(root,runtime_profile=None):
    # Deliberately only this prepared private host; no HOME/cwd/installation fallback.
    if runtime_profile is not None:
        gpu=runtime_profile.gpu(runtime_profile.gpu_root);root=Path(root)
        if root!=gpu/'local-metal':raise Refused('formal AOT root must be selected namespace/local-metal')
        return check_directory(root)
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
    if type(request)is not dict or type(request.get('schema'))is not int or request['schema']not in(1,2,3):raise Refused('unsupported request schema')
    fields={1:REQUEST_FIELDS,2:LIBRARY_REQUEST_FIELDS,3:INVARIANT_REQUEST_FIELDS}[request['schema']]
    if set(request)!=fields:raise Refused('request fields do not match schema')
    if any(type(request[k])is not str for k in fields-{'schema','preserve_invariance'}):raise Refused('request field type mismatch')
    if request['schema']==3 and (type(request['preserve_invariance'])is not bool or not request['preserve_invariance']):raise Refused('invariant recipe requires explicit true')
    if request['key']!=key or request['cohort']!=cohort or not HEX.fullmatch(request['source_sha256']):raise Refused('request key/cohort/source identity mismatch')
    if request['schema']in(1,3):
        recipe=INVARIANT_RECIPE if request['schema']==3 else RECIPE
        if request['recipe']!=recipe or request['language']!='metal3.1' or request['math_mode']not in('safe','relaxed')or request['math_fp32']!='fast':raise Refused('unsupported fixed compile recipe')
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

def verified_library_bytes(folder,request,build_id):
    library=folder/(request['key']+'.metallib');receipt=folder/(request['key']+'.json')
    has_lib=os.path.lexists(library);has_receipt=os.path.lexists(receipt)
    if has_receipt:
        if not has_lib:raise Refused('receipt exists without its library')
        blob=regular_bytes(library,LIBRARY_MAX)
        if not blob:raise Refused('empty existing library')
        if canonical(read_json(receipt))!=canonical(expected_receipt(request,build_id,blob)):raise Refused('existing receipt/library identity mismatch')
        return blob
    if has_lib:
        # An interrupted library-first publish is not a success and cannot be skipped.
        regular_bytes(library,LIBRARY_MAX)
    return None

def already_succeeded(folder,request,build_id):
    return verified_library_bytes(folder,request,build_id)is not None

_METAL_VERSION=re.compile(r'(Apple metal version [^\n]+\nTarget: [^\n]+\nThread model: [^\n]+\nInstalledDir: )((?:/private)?/var/run/com\.apple\.security\.cryptexd/mnt/com\.apple\.MobileAsset\.MetalToolchain-v([0-9]+(?:\.[0-9]+)+)\.[A-Za-z0-9]{6,64})(/Metal\.xctoolchain/usr/metal/current/bin)')

def stable_metal_version(version,*,selected=None):
    """Only the evidenced cryptex random mount suffix is location, not code.

    All other version/target/asset-version text stays in compatibility. Unknown
    formats stay byte-exact. Live selection is still pinned by the registration;
    historical manifests are compared only AFTER their original digest is checked.
    """
    match=_METAL_VERSION.fullmatch(version)
    if match is None:return version
    if selected is not None:
        mount=Path(match[2]).resolve(strict=True)
        if not Path(selected).resolve(strict=True).is_relative_to(mount/'Metal.xctoolchain'):
            raise Refused('Metal InstalledDir differs from actual selected toolchain')
        directory=Path(match[2]+match[4]).resolve(strict=True)
        if not directory.is_relative_to(mount/'Metal.xctoolchain')or not directory.is_dir():
            raise Refused('Metal InstalledDir is outside selected toolchain')
    return match[1]+'<MetalToolchain-v'+match[3]+'>/Metal.xctoolchain/usr/metal/current/bin'

def stable_build_manifest(manifest):
    # Round-trip, retaining EVERY unknown field. No compiler/SDK/options fields
    # are dropped; only the single recognized metal version location is changed.
    result=json.loads(canonical(manifest))
    if result.get('schema')==2 and isinstance(result.get('tools'),dict):
        metal=result['tools'].get('metal')
        if isinstance(metal,dict)and isinstance(metal.get('version'),str):
            metal['version']=stable_metal_version(metal['version'])
    return result

def equivalent_builds(artifact_root,build_id,manifest,guard):
    """Bounded one-time discovery in this exact cohort; never walk all caches."""
    matches=[]
    with os.scandir(artifact_root)as stream:
        for n,item in enumerate(stream):
            guard.check(preflight=True)
            if n>=128:break # Preserve generations beyond the lookup budget.
            if item.name==build_id or not HEX.fullmatch(item.name):continue
            try:
                folder=check_directory(Path(item.path));old=read_json(folder/'build.json')
                if digest(canonical(old))!=item.name or stable_build_manifest(old)!=manifest:continue
                matches.append((folder,item.name,old))
            except(OSError,ValueError,TypeError,KeyError,Refused)as error:
                if isinstance(error,Stopped):raise
    return matches

def retained_mount_product(candidates,request,guard):
    for folder,old_id,old_manifest in candidates:
        guard.check(preflight=True)
        try:
            # Re-read through adoption: directory discovery is not admission.
            if read_json(folder/'build.json')!=old_manifest:continue
            blob=verified_library_bytes(folder,request,old_id)
            if blob is not None:
                return blob,dict(schema=1,kind='verified-metal-mount-relocation-adoption',
                    original_request=request,original_build_id=old_id,
                    original_build_manifest=old_manifest,original_library_sha256=digest(blob),
                    current_request=request,original_native_key_preserved=True)
        except(OSError,ValueError,TypeError,KeyError,Refused)as error:
            if isinstance(error,Stopped):raise
    return None

def retained_product(root,request,source,build_id,manifest,guard):
    """One content-addressed lookup; retain all native/origin identities.

    A library compile consumes exact MSL/entry/options and SDK/toolchain, not
    the launch cohort string. Validate BOTH full native request keys, actual
    source equality, original build and receipt/bytes before adopting. Unknown
    legacy SDK identities cannot enter this migration.
    """
    if manifest.get('schema')!=2 or not manifest.get('sdk'):return None
    from compilation_domains import library_product_key
    product=library_product_key(request,build_id)
    try:
        index=child_directory(root,'.products-v1');group=child_directory(index,product)
        with os.scandir(group)as stream:
            for n,item in enumerate(stream):
                guard.check(preflight=True)
                if n>=32:return None # lookup budget; never delete/shrink stored products
                if not re.fullmatch('[0-9a-f]{64}\\.json',item.name):continue
                try:
                    row=read_json(Path(item.path))
                    if set(row)!={'schema','product','build_id','request'}or type(row['schema'])is not int or row['schema']!=1:continue
                    if digest(canonical(row))+'.json'!=item.name or row['product']!=product or row['build_id']!=build_id:continue
                    old=row['request'];oldcohort=old['cohort'];oldkey=old['key']
                    if not HEX.fullmatch(oldcohort)or not HEX.fullmatch(oldkey)or library_product_key(old,build_id)!=product:continue
                    previous=child_directory(root,oldcohort);requests=child_directory(previous,'requests')
                    originals=[]
                    for folder in(requests,requests/oldkey[:2]):
                        if not folder.exists():continue
                        check_directory(folder)
                        if not os.path.lexists(folder/(oldkey+'.json'))or not os.path.lexists(folder/(oldkey+'.metal')):continue
                        actual,original=load_request(folder,oldkey,oldcohort)
                        if actual!=old or original!=source:raise Refused('retained product original input mismatch')
                        originals.append(True)
                    if not originals:continue
                    artifacts=child_directory(child_directory(previous,'artifacts'),build_id)
                    if read_json(artifacts/'build.json')!=manifest:continue
                    blob=verified_library_bytes(artifacts,old,build_id)
                    if blob is not None:
                        return blob,dict(schema=1,kind='verified-identical-compile-input-adoption',product=product,
                            original_request=old,original_build_id=build_id,original_library_sha256=digest(blob),
                            current_request=request,original_native_key_preserved=True)
                except(OSError,ValueError,TypeError,KeyError,Refused)as error:
                    if isinstance(error,Stopped):raise
                    continue
    except(OSError,ValueError,TypeError,KeyError,Refused)as error:
        if isinstance(error,Stopped):raise
    return None

def remember_product(root,request,build_id,manifest):
    if manifest.get('schema')!=2 or not manifest.get('sdk'):return False
    from compilation_domains import library_product_key
    product=library_product_key(request,build_id)
    index=child_directory(root,'.products-v1',create=True);group=child_directory(index,product,create=True)
    row=dict(schema=1,product=product,build_id=build_id,request=request)
    path=group/(digest(canonical(row))+'.json')
    if os.path.lexists(path):return read_json(path)==row
    write_new(path,canonical(row)+b'\n');return True

def adoption_provenance(root,request,build_id,proof):
    # Provenance is kept separately from inputs read by the native factory and
    # its startup proofs. It never claims that an entry/PSO has been admitted.
    parent=child_directory(root,'.product-adoptions-v1',create=True)
    for name in(request['cohort'],build_id):parent=child_directory(parent,name,create=True)
    proof=dict(proof,native_entry_admitted=False,pipeline_ready=False)
    path=parent/(request['key']+'-'+digest(canonical(proof))+'.json')
    if os.path.lexists(path):
        if read_json(path)!=proof:raise Refused('existing adoption provenance differs')
    else:write_new(path,canonical(proof)+b'\n')
    return path

def publish_new_file(source,target):
    """Complete candidate, atomic NO-REPLACE publication on macOS."""
    if sys.platform=='darwin':
        import ctypes
        api=ctypes.CDLL('/usr/lib/libSystem.B.dylib',use_errno=True).renamex_np
        api.argtypes=(ctypes.c_char_p,ctypes.c_char_p,ctypes.c_uint);api.restype=ctypes.c_int
        # SDK sys/stdio.h: RENAME_EXCL, available since macOS 10.12.
        if api(os.fsencode(source),os.fsencode(target),0x00000004):
            code=ctypes.get_errno();raise OSError(code,os.strerror(code),str(target))
    else:
        # Author-only non-Metal platforms. Receipt publication follows unlink;
        # the real native reader never accepts an unaccounted hardlinked file.
        os.link(source,target,follow_symlinks=False);os.unlink(source)

def pair_pins(folder,key):
    result={}
    for suffix,limit in(('.metallib',LIBRARY_MAX),('.json',JSON_MAX),('.adoption.json',JSON_MAX)):
        path=folder/(key+suffix)
        if not os.path.lexists(path):continue
        info=path.lstat()
        if not stat.S_ISREG(info.st_mode)or info.st_uid!=os.getuid()or info.st_nlink!=1 or info.st_mode&0o022 or not 0<=info.st_size<=limit:
            raise Refused('unsafe current artifact node cannot be moved for repair')
        result[suffix]=_session_stat(info)
    return result

def retain_invalid_pair(root,cohort,build_id,key,folder,pins,guard):
    """Only after a valid replacement exists; keep every previous byte.

    History is outside active product/import proofs, so unrelated retained
    generations cannot invalidate warm startup or enlarge native residency.
    """
    guard.check(preflight=True)
    if pair_pins(folder,key)!=pins:raise Refused('current artifact changed during repair')
    parent=child_directory(root,'.retained-invalid-v1',create=True)
    for name in(cohort,build_id,key):parent=child_directory(parent,name,create=True)
    history=Path(tempfile.mkdtemp(prefix='repair-',dir=parent));files=[]
    for suffix in pins:
        path=folder/(key+suffix);limit=LIBRARY_MAX if suffix=='.metallib'else JSON_MAX
        blob=regular_bytes(path,limit);files.append(dict(name=path.name,sha256=digest(blob),bytes=len(blob),stamp=pins[suffix]));del blob
    write_new(history/'MANIFEST.json',canonical(dict(schema=1,kind='retained-invalid-native-library-pair',cohort=cohort,build_id=build_id,key=key,files=files))+b'\n')
    for row in files:
        guard.check(preflight=True);path=folder/row['name']
        if _session_stat(path.lstat())!=row['stamp']:raise Refused('current artifact changed before retention')
        publish_new_file(path,history/path.name)
        limit=LIBRARY_MAX if path.suffix=='.metallib'else JSON_MAX
        blob=regular_bytes(history/path.name,limit)
        if digest(blob)!=row['sha256']or len(blob)!=row['bytes']:raise Refused('retained artifact changed')
        del blob
    return history

class Safety:
    def __init__(self,observer,require_no_games,policy_factory,clock=time.monotonic):
        self.observer=observer;self.no_games=require_no_games;self.clock=clock
        self.started=clock();self.policy=None
        if policy_factory is not None:
            baseline=observer.system()
            if baseline['locked']or baseline['pressure']==4:raise Stopped('locked or severe pressure before build')
            self.policy=policy_factory(baseline)
        self.no_games()
    def check(self,*,preflight=False):
        now=self.clock()-self.started;sample=self.observer.system()if self.policy is not None else None
        reason=self.policy.observe(now,sample)if self.policy is not None else None
        if reason or now>TOTAL_TIMEOUT:raise Stopped(reason or 'total build timeout')
        if preflight:
            self.no_games()
            if sample is not None and sample['pressure']==4:raise Stopped('severe pressure before new compiler stage')

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
                # Wait wakes immediately on exit; timeout preserves the existing
                # at-most-0.1s supervision cadence for a still-running child.
                try:proc.wait(timeout=.1)
                except subprocess.TimeoutExpired:pass
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
            # A short-lived command can exit between periodic samples. Check its
            # final outputs too, before any successful return is observable.
            for path,limit in outputs:
                if os.path.lexists(path):
                    row=Path(path).lstat()
                    if not stat.S_ISREG(row.st_mode)or row.st_uid!=os.getuid()or row.st_nlink!=1 or not 0<=row.st_size<=limit:raise Stopped('compiler output size/type limit exceeded')
            log.seek(0);return log.read(LOG_MAX+1)
        finally:
            if proc is not None and proc.poll()is None:
                os.killpg(proc.pid,signal.SIGTERM)
                # The unchanged launcher escalates an unresponsive game after
                # three seconds. Reap compiler children within that interval.
                try:proc.wait(timeout=.75)
                except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait(timeout=.75)
            log.close()

def _session_stat(st):
    # atime may change during legitimate reads; every mutation-sensitive field stays.
    return [st.st_dev,st.st_ino,st.st_mode,st.st_uid,st.st_gid,st.st_nlink,st.st_size,st.st_mtime_ns,st.st_ctime_ns]

def tool_fingerprint(path):
    path=Path(path).resolve(strict=True)
    before=path.stat()
    if not stat.S_ISREG(before.st_mode)or before.st_mode&0o022:raise Refused('tool binary must be regular and not group/world writable')
    with path.open('rb')as stream:sha=hashlib.file_digest(stream,'sha256').hexdigest()
    if _session_stat(before)!=_session_stat(path.stat()):raise Refused('tool binary changed while hashing')
    return dict(path=str(path),sha256=sha,stat=list(_identity(before)),session_stat=_session_stat(before))

class NativeToolchain:
    def __init__(self,runner,*,fingerprint=tool_fingerprint):self.runner=runner;self.fingerprint=fingerprint;self.registration=None
    def configure_registration(self,root):
        if isinstance(self.runner,FixedRunner):
            from tool_environment_registration import Registration
            self.registration=Registration(root)
    def registration_work(self):
        return dict(self.registration.stats)if self.registration is not None else {}
    def sdk_identity(self,guard):
        guard.check(preflight=True)
        raw=self.runner.run(macos_xcrun('--show-sdk-path'),10,guard).decode().strip()
        if not raw.startswith('/')or '\n'in raw:raise Refused('invalid selected SDK path')
        selected=Path(raw);sdk=selected.resolve(strict=True)
        if not sdk.is_dir():raise Refused('selected SDK is not a directory')
        pins={str(selected):_session_stat(selected.lstat()),str(sdk):_session_stat(sdk.stat())};files={}
        for name in ('SDKSettings.json','SDKSettings.plist'):
            p=sdk/name
            if not p.exists():continue
            before=p.lstat()
            if before.st_uid not in(0,os.getuid())or before.st_mode&0o022:raise Refused('untrusted SDK identity file')
            blob=regular_bytes(p,1024*1024,owned=False)
            if not blob or _session_stat(before)!=_session_stat(p.lstat()):raise Refused('SDK identity changed during read')
            files[name]=dict(sha256=digest(blob),bytes=len(blob));pins[str(p)]=_session_stat(before)
        if not files:raise Refused('missing SDK identity files')
        return dict(schema=1,metadata=files),dict(selected=str(selected),canonical=str(sdk),pins=pins)
    def identify(self,guard):
        if self.registration is not None:
            reused=self.registration.load(self,guard)
            if reused is not None:return reused
        result=self._identify_full(guard)
        if self.registration is not None:self.registration.save(self,guard)
        return result
    def _identify_full(self,guard):
        tools={};versions={}
        for name in('metal','metallib'):
            found=self.runner.run(macos_xcrun('--find',name),10,guard).decode().strip()
            if not found.startswith('/')or '\n'in found:raise Refused('invalid resolved tool path')
            tools[name]=self.fingerprint(found)
            versions[name]=self.runner.run(macos_xcrun(name,'--version'),10,guard).decode('utf-8').strip()
            if not versions[name]or len(versions[name].encode())>4096:raise Refused('empty or oversized tool version')
        build=self.runner.run(['/usr/bin/sw_vers','-buildVersion'],10,guard).decode().strip()
        if not build or len(build.encode())>256:raise Refused('empty or oversized OS build identity')
        tools['xcrun']=self.fingerprint('/usr/bin/xcrun')
        versions['xcrun']=self.runner.run(macos_xcrun('--version'),10,guard).decode().strip()
        if not versions['xcrun']or len(versions['xcrun'].encode())>1024:raise Refused('empty or oversized xcrun version')
        sdk,self.sdk_selection=self.sdk_identity(guard)
        # Keep the full observed version with the actual invocation pins for
        # provenance. Only the random cryptex mount token leaves the product ID.
        tools['metal']['reported_version']=versions['metal']
        versions['metal']=stable_metal_version(versions['metal'],selected=tools['metal']['path'])
        # Paths/stamps only pin this invocation. Real compiler, SDK release
        # metadata and OS define product compatibility; a timestamp touch does
        # not create another product. Old schema1 directories remain intact.
        manifest=dict(schema=2,recipe=RECIPE,tools={name:{'sha256':row['sha256'],'version':versions[name]}for name,row in tools.items()},os_build=build,sdk=sdk)
        self.pins=tools;self.build_manifest=manifest;self.build_id=digest(canonical(manifest))
        return self.build_id,manifest
    def unchanged(self,guard):
        guard.check(preflight=True)
        if self.registration is not None and self.registration.current(self,guard):return
        return self._unchanged_full(guard)
    def _unchanged_full(self,guard):
        guard.check(preflight=True)
        for name in('metal','metallib'):
            found=self.runner.run(macos_xcrun('--find',name),10,guard).decode().strip()
            if str(Path(found).resolve(strict=True))!=self.pins[name]['path']:raise Refused('selected toolchain changed during build')
        for row in self.pins.values():
            if list(_identity(Path(row['path']).stat()))!=row['stat']:raise Refused('tool binary changed during build')
        sdk,selection=self.sdk_identity(guard)
        # sdk_identity rereads and hashes actual version metadata. xattr/time
        # changes do not alter that compile input; location/inode/security
        # changes still refuse this invocation. Full stamps remain provenance
        # in preparation_environment's independent scheduling checks.
        stable=lambda s:dict(selected=s['selected'],canonical=s['canonical'],pins={p:v[:6]for p,v in s['pins'].items()})
        if sdk!=self.build_manifest['sdk']or stable(selection)!=stable(self.sdk_selection):raise Refused('selected SDK changed during build')
    def preparation_environment(self,guard):
        if self.registration is not None and self.registration.current(self,guard):
            return self.registration.active['toolchain']['environment']
        return self._preparation_environment_full(guard)
    def _preparation_environment_full(self,guard):
        # Session invalidation only, never a replacement for artifact/toolchain ID.
        guard.check(preflight=True)
        raw=self.runner.run(macos_xcrun('--show-sdk-path'),10,guard).decode().strip()
        if not raw.startswith('/')or '\n'in raw:raise Refused('invalid selected SDK path')
        selected=Path(raw);sdk=selected.resolve(strict=True)
        if not sdk.is_dir():raise Refused('selected SDK is not a directory')
        pins={str(selected):_session_stat(selected.lstat()),str(sdk):_session_stat(sdk.stat())}
        found=0
        for name in ('SDKSettings.json','SDKSettings.plist'):
            p=sdk/name
            if p.is_file():
                row=p.lstat()
                if not stat.S_ISREG(row.st_mode)or not 0<row.st_size<=1024*1024:raise Refused('invalid SDK identity file')
                pins[str(p)]=_session_stat(row);found+=1
        if not found:raise Refused('missing SDK identity files')
        for rawpath in ('/System/Library/CoreServices/SystemVersion.plist','/usr/bin/sw_vers'):
            p=Path(rawpath);row=p.lstat()
            if not stat.S_ISREG(row.st_mode):raise Refused('invalid OS identity file')
            pins[str(p)]=_session_stat(row)
        os_build=self.runner.run(['/usr/bin/sw_vers','-buildVersion'],10,guard).decode().strip()
        if not os_build or len(os_build.encode())>256 or os_build!=self.build_manifest['os_build']:raise Refused('OS build changed within preparation')
        return dict(sdk_selected=str(selected),sdk=str(sdk),os_build=os_build,pins=pins)
    def compile(self,source,air,library,request,guard):
        self._unchanged_full(guard)
        command=macos_xcrun('metal','-std='+request['language'],
                 '-fmetal-math-mode='+request['math_mode'],'-fmetal-math-fp32-functions='+request['math_fp32'],
                 '-c',str(source),'-o',str(air))
        if request['schema']==3:command.insert(7,'-fpreserve-invariance')
        self.runner.run(command,STAGE_TIMEOUT,guard,cwd=source.parent,outputs=((air,AIR_MAX),))
        row=air.lstat()
        if not stat.S_ISREG(row.st_mode)or row.st_uid!=os.getuid()or row.st_nlink!=1 or not 0<row.st_size<=AIR_MAX:raise Refused('invalid/empty AIR output')
        self.runner.run(macos_xcrun('metallib',str(air),'-o',str(library)),STAGE_TIMEOUT,guard,
                        cwd=source.parent,outputs=((library,LIBRARY_MAX),))
        self._unchanged_full(guard)

def real_services(private_tmp,runtime_profile=None):
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parent))
    from native_observer import NativeObserver
    from launcher_runtime_profile import preparation_memory_policy
    from qa_build_guard import require_no_games
    observer=NativeObserver()
    return observer,Safety(observer,require_no_games,preparation_memory_policy(runtime_profile)),NativeToolchain(FixedRunner(private_tmp))

def build(root=None,cohort=None,*,enabled=False,guard=None,toolchain=None,runtime_profile=None):
    # Do not move path resolution, imports, enumeration, hashes or services above this gate.
    if not enabled:return dict(status='off',build_id=None,built=0,skipped=0)
    summary=dict(status='failed',build_id=None,built=0,skipped=0,requests=0,results=[])
    observer=None;lock_fd=None;summary_path=None;index=None;results_fd=None;results_tmp=None
    try:
        if type(cohort)is not str or not HEX.fullmatch(cohort):raise Refused('cohort must be lowercase 64-hex')
        root=checked_root(root,**({'runtime_profile':runtime_profile}if runtime_profile is not None else{}));folder=child_directory(root,cohort);requests=child_directory(folder,'requests')
        artifact_root=child_directory(folder,'artifacts',create=True)
        private_tmp=child_directory(artifact_root,'.builder-tmp',create=True)
        lock_fd=os.open(artifact_root/'.builder.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        st=os.fstat(lock_fd)
        if not stat.S_ISREG(st.st_mode)or st.st_uid!=os.getuid()or st.st_nlink!=1:raise Refused('invalid builder lock')
        try:fcntl.flock(lock_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as error:raise Refused('another builder owns this cohort')from error
        summary_path=artifact_root/'builder-summary.json'
        if(guard is None)!=(toolchain is None):raise Refused('internal test services must be supplied together')
        if guard is None:observer,guard,toolchain=real_services(private_tmp,runtime_profile)
        guard.check(preflight=True)
        from paged_metal_requests import RequestIndex,PAGE_MAX
        index=RequestIndex(requests,private_tmp,guard,api=__import__('sys').modules[__name__])
        summary['requests']=index.request_count;summary['request_recovery']=index.recovery()
        results_fd,results_tmp=tempfile.mkstemp(prefix='builder-results-',suffix='.jsonl',dir=private_tmp)
        def record_result(value):
            if len(summary['results'])<PAGE_MAX:summary['results'].append(value)
            else:summary['results_preview_truncated']=True
            encoded=canonical(value)+b'\n'
            if os.write(results_fd,encoded)!=len(encoded):raise Refused('short result log write')
        for deferred in index.deferred():record_result(deferred)
        configure=getattr(toolchain,'configure_registration',None)
        if callable(configure):configure(root)
        build_id,manifest=toolchain.identify(guard)
        if not HEX.fullmatch(build_id)or digest(canonical(manifest))!=build_id:raise Refused('invalid toolchain build identity')
        summary['build_id']=build_id
        artifacts=child_directory(artifact_root,build_id,create=True)
        build_record=artifacts/'build.json'
        if os.path.lexists(build_record):
            if canonical(read_json(build_record))!=canonical(manifest):raise Refused('existing build manifest identity mismatch')
        else:atomic_json(build_record,manifest)
        # A previous COMPLETE audit may skip repeated request/library byte
        # reads only with continuous Darwin change history and the CURRENT
        # real compiler/SDK/OS identity. This is scheduling, never native import
        # authorization. Missing history or changed inputs use the original loop.
        from startup_change_journal import query as journal_query,unchanged as journal_unchanged
        audit_path=artifact_root/('.verified-'+build_id+'.json')
        audit_roots=[requests,artifacts];audit_token=None;audit_binding=None;reuse=False
        if callable(getattr(toolchain,'preparation_environment',None)):
            try:environment=toolchain.preparation_environment(guard)
            except (OSError,ValueError,Refused)as error:
                if isinstance(error,Stopped):raise
                environment=None
            if environment is None:audit_token=None
            else:
                audit_binding=dict(schema=1,root=str(root),cohort=cohort,build_id=build_id,
                                   environment=environment,requests=index.request_count,deferred=index.deferred_count)
                summary['preparation_environment_identity']=digest(canonical(environment))
                try:
                    audit=json.loads(regular_bytes(audit_path,MAX_AUDIT_BYTES),object_pairs_hook=_pairs)
                    body=audit['body']
                    reuse=(set(audit)=={'body','sha256'} and digest(canonical(body))==audit['sha256']
                           and body['binding']==audit_binding and index.deferred_count==0
                           and journal_unchanged(body['journal'],audit_roots))
                    if reuse:audit_token=body['journal']
                except(OSError,ValueError,TypeError,KeyError,Refused):pass
                if not reuse:audit_token=journal_query(audit_roots)
        if reuse:
            guard.check(preflight=True)
            # Recheck through the final adoption boundary, without advancing
            # the old cursor past anything not validated by the original pass.
            reuse=journal_unchanged(audit_token,audit_roots)
        if reuse:
            summary.update(status='passed',skipped=index.request_count,
                           persistent_audit_reused=True,request_window=dict(max_batch=0,max_final_pins=0),
                           validation='unchanged-journal-and-current-tools-sdk-os')
            return summary
        equivalent=equivalent_builds(artifact_root,build_id,manifest,guard)
        for batch in index.batches():
          for key in batch:
            guard.check(preflight=True)
            request,source=index.load_owned(key,cohort)
            from compilation_domains import library_product_key
            product_key=library_product_key(request,build_id)
            try:complete=already_succeeded(artifacts,request,build_id)
            except (OSError,ValueError,TypeError,Refused)as error:
                if isinstance(error,Stopped):raise
                complete=False
            if complete:
                try:remember_product(root,request,build_id,manifest)
                except(OSError,ValueError,TypeError,KeyError,Refused):pass
                summary['skipped']+=1;record_result(dict(key=key,status='skipped-verified',compile_product_id=product_key));continue
            previous=pair_pins(artifacts,key)
            with tempfile.TemporaryDirectory(prefix=key+'-',dir=private_tmp)as scratch:
                scratch=Path(scratch);snapshot=scratch/'source.metal';air=scratch/'output.air';library=scratch/'output.metallib'
                write_new(snapshot,source);del source
                adopted=retained_mount_product(equivalent,request,guard)
                if adopted is None:adopted=retained_product(root,request,regular_bytes(snapshot,SOURCE_MAX),build_id,manifest,guard)
                reused=adopted is not None;provenance=None
                # The current native request/key remains untouched. The adopted
                # binary gets its own ordinary receipt plus explicit provenance.
                if adopted is None:toolchain.compile(snapshot,air,library,request,guard)
                else:
                    toolchain.unchanged(guard);write_new(library,adopted[0])
                    provenance=adoption_provenance(root,request,build_id,adopted[1]);del adopted
                blob=regular_bytes(library,LIBRARY_MAX)
                if not blob:raise Refused('empty metallib output')
                receipt=expected_receipt(request,build_id,blob);del blob
                staged_receipt=scratch/'receipt.json';write_new(staged_receipt,canonical(receipt)+b'\n')
                # Valid pairs skipped above. Broken/orphan pairs are retained
                # only after a fully built/adopted replacement is available.
                history=None
                if previous:history=retain_invalid_pair(root,cohort,build_id,key,artifacts,previous,guard)
                elif pair_pins(artifacts,key):raise Refused('artifact appeared during serial build')
                guard.check(preflight=True)
                publish_new_file(library,artifacts/(key+'.metallib'))
                publish_new_file(staged_receipt,artifacts/(key+'.json'))
                fd=os.open(artifacts,os.O_RDONLY|os.O_DIRECTORY)
                try:os.fsync(fd)
                finally:os.close(fd)
            if reused:summary['reused_products']=summary.get('reused_products',0)+1
            else:summary['built']+=1
            if history is not None:summary['repaired_products']=summary.get('repaired_products',0)+1
            try:remember_product(root,request,build_id,manifest)
            except(OSError,ValueError,TypeError,KeyError,Refused):pass
            record_result(dict(key=key,status='reused-verified-product'if reused else'built',compile_product_id=product_key,
                               repaired=history is not None,retained_previous=str(history)if history is not None else None,
                               adoption_provenance=str(provenance)if provenance is not None else None))
        summary['status']='partial'if index.deferred_count else'passed'
        summary['request_window']=dict(max_batch=index.max_batch_len,max_final_pins=index.max_pin_count)
        if summary['status']=='passed' and audit_binding is not None and audit_token is not None:
            body=dict(binding=audit_binding,journal=audit_token)
            raw=canonical(dict(body=body,sha256=digest(canonical(body))))
            if len(raw)<=MAX_AUDIT_BYTES:
                # Never replace foreign, linked or non-regular hint nodes.
                from incremental_file_hashes import replaceable
                if replaceable(audit_path):
                    try:atomic_json(audit_path,json.loads(raw))
                    except OSError:pass # Optional speed hint cannot replace a verified build.
    except KeyboardInterrupt:
        summary['status']='stopped';summary['error']='interrupted'
    except Exception as error:
        summary['status']='stopped'if isinstance(error,Stopped)else'refused'if isinstance(error,(Refused,OSError,ValueError,TypeError))else'failed'
        summary['error']=str(error)[:1024]
    finally:
        if results_fd is not None:
            try:
                os.fsync(results_fd);os.close(results_fd);results_fd=None
                result_path=summary_path.parent/'builder-results.jsonl'
                os.replace(results_tmp,result_path);results_tmp=None
                summary['results_jsonl']=str(result_path)
            except Exception as error:
                if summary['status']!='stopped':summary['status']='failed'
                summary['results_write_error']=str(error)[:512]
        if index is not None:
            try:index.close()
            except Exception as error:
                if summary['status']!='stopped':summary['status']='failed'
                summary['index_cleanup_error']=str(error)[:512]
        if summary_path is not None:
            report_registration=getattr(toolchain,'registration_work',None)
            if callable(report_registration):summary['toolchain_registration']=report_registration()
            try:atomic_json(summary_path,summary)
            except Exception as error:
                # Preserve a lock/memory stop even when its diagnostic cannot be
                # written; auto fallback must not launch after a safety stop.
                if summary['status']!='stopped':summary['status']='failed'
                summary['summary_write_error']=str(error)[:512]
        if lock_fd is not None:os.close(lock_fd)
        if observer is not None:observer.close()
    return summary


MAX_AUDIT_BYTES=2*1024*1024

SESSION_ENTRY_MAX=262144
SESSION_LEDGER_MAX=64*1024*1024

class _SessionSnapshot:
    """Owned temporary disk ledger. No request bytes or unbounded Python pin map."""
    def __init__(self,roots,scratch,guard):
        self.roots=tuple(Path(p)for p in roots);self.count=0;self.db=None;self.path=None
        fd,name=tempfile.mkstemp(prefix='preparation-pins-',suffix='.sqlite',dir=scratch);os.close(fd);self.path=Path(name)
        try:
            self.db=sqlite3.connect(name)
            self.db.execute('PRAGMA max_page_count='+str(max(1,SESSION_LEDGER_MAX//4096)))
            self.db.execute('PRAGMA temp_store=FILE');self.db.execute('PRAGMA mmap_size=0');self.db.execute('PRAGMA cache_size=-512')
            self.db.execute('CREATE TABLE pins(root INTEGER,path TEXT,identity TEXT,PRIMARY KEY(root,path))')
            for i,root in enumerate(self.roots):
                for rel,row in self._walk(root,guard):
                    if self.count>=SESSION_ENTRY_MAX:raise Refused('optional session metadata entry budget')
                    self.db.execute('INSERT INTO pins VALUES(?,?,?)',(i,rel,canonical(_session_stat(row)).decode()));self.count+=1
            self.db.commit();guard.check(preflight=True)
        except BaseException as error:
            self.close()
            if isinstance(error,sqlite3.Error):raise Refused('optional session metadata ledger budget/error')from error
            raise
    def _walk(self,root,guard):
        if root!=root.resolve(strict=True):raise Refused('linked preparation-session root')
        ticks=0
        def tick():
            nonlocal ticks
            if ticks%256==0:guard.check(preflight=True)
            ticks+=1
        def walk(folder,depth):
            tick();row=folder.lstat()
            if not stat.S_ISDIR(row.st_mode)or row.st_uid!=os.getuid():raise Refused('unowned session input directory')
            yield str(folder.relative_to(root)),row
            with os.scandir(folder)as entries:
                for entry in entries:
                    tick();p=Path(entry.path);row=entry.stat(follow_symlinks=False)
                    if stat.S_ISDIR(row.st_mode):
                        if depth>=1:raise Refused('unexpected deep session input tree')
                        yield from walk(p,depth+1)
                    else:
                        if not stat.S_ISREG(row.st_mode)or row.st_uid!=os.getuid():raise Refused('unowned/nonregular session input')
                        yield str(p.relative_to(root)),row
        yield from walk(root,0)
    def unchanged(self,guard):
        count=0;guard.check(preflight=True)
        for i,root in enumerate(self.roots):
            for rel,row in self._walk(root,guard):
                old=self.db.execute('SELECT identity FROM pins WHERE root=? AND path=?',(i,rel)).fetchone()
                if old is None or old[0]!=canonical(_session_stat(row)).decode():return False
                count+=1
        guard.check(preflight=True);return count==self.count
    def close(self):
        if self.db is not None:self.db.close();self.db=None
        if self.path is not None:
            for suffix in ('','-journal','-wal','-shm'):
                try:Path(str(self.path)+suffix).unlink()
                except FileNotFoundError:pass
            self.path=None

class PreparationSession:
    """One prepare() call only; metadata pins never authorize native artifact use.

    Cache only a full original passed, zero-build verification enclosed by stable
    request/artifact metadata snapshots. Cold builds need a later zero-build pass.
    A record phase invalidates immediately. Every reuse still resolves the actual
    selected toolchain, checks authenticated binary pins and all input stat pins.
    """
    def __init__(self,private_tmp,*,clock=time.monotonic,runtime_profile=None):
        self.runtime_profile=runtime_profile
        self.tmp=Path(private_tmp);self.clock=clock;self.snapshot=None;self.toolchain=None;self.summary=None;self.scope=None;self.environment=None;self.audit=None;self.validation_generation=0
        self.stats=dict(calls=0,full_builds=0,reuses=0,invalidations=0,metadata_entries=0,seconds=0.0,full_build_seconds=0.0,reuse_validation_seconds=0.0,events=[])
    def invalidate(self,reason='explicit'):
        self.validation_generation+=1
        if self.snapshot is not None:self.snapshot.close()
        if self.summary is not None:self.stats['invalidations']+=1
        self.snapshot=self.toolchain=self.summary=self.scope=self.environment=self.audit=None
        if len(self.stats['events'])<64:self.stats['events'].append(dict(state='invalidated',reason=reason))
    def _tools_unchanged(self,toolchain,guard):
        # Original fixed xcrun selector checks + original guard; no alternate tool.
        toolchain.unchanged(guard)
        pins=getattr(toolchain,'pins',{})
        if set(pins)!={'metal','metallib','xcrun'}:return False
        for row in pins.values():
            p=Path(row['path'])
            if p!=p.resolve(strict=True)or row.get('session_stat')!=_session_stat(p.stat()):return False
        return True
    def build(self,root,cohort,*,guard,toolchain_factory,enabled=False,runtime_profile=None):
        if not enabled:return dict(status='off',build_id=None,built=0,skipped=0)
        if runtime_profile is not None:
            if self.runtime_profile is not None and self.runtime_profile is not runtime_profile:raise Refused('builder runtime profile changed')
            self.runtime_profile=runtime_profile
        started=self.clock();self.stats['calls']+=1;root=Path(root);scope=(str(root),cohort)
        try:
            guard.check(preflight=True)
            if self.summary is not None and self.scope==scope:
                lock=None
                try:
                    # Preserve the exact cohort builder exclusion during reuse.
                    checked_root(root,**({'runtime_profile':self.runtime_profile}if self.runtime_profile is not None else{}))
                    lock=os.open(root/cohort/'artifacts/.builder.lock',os.O_RDWR|os.O_NOFOLLOW)
                    row=os.fstat(lock)
                    if not stat.S_ISREG(row.st_mode)or row.st_uid!=os.getuid()or row.st_nlink!=1:raise Refused('invalid session builder lock')
                    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    if self._tools_unchanged(self.toolchain,guard)and self.toolchain.preparation_environment(guard)==self.environment and self.snapshot.unchanged(guard):
                        self.stats['reuses']+=1;self.stats['reuse_validation_seconds']+=self.clock()-started
                        return dict(self.summary,built=0,reused_products=0,repaired_products=0,skipped=self.summary['requests'],results=[],session_reused=True,
                                    session_verification='same-call-current-tools-sdk-os-and-authenticated-stat-pins')
                except (OSError,ValueError,Refused)as error:
                    if isinstance(error,Stopped):raise
                finally:
                    if lock is not None:os.close(lock)
                self.invalidate('inputs-or-tools-changed')
            elif self.summary is not None:self.invalidate('scope-changed')
            self.audit=None
            self.stats['full_builds']+=1;actual=toolchain_factory();before=None;environment_before=None
            session=self
            class EnclosedToolchain:
                def __getattr__(self,name):
                    if name!='preparation_environment':raise AttributeError(name)
                    target=getattr(actual,name)
                    def environment(guard):
                        nonlocal environment_before
                        try:return target(guard)
                        except (OSError,ValueError,Refused):
                            environment_before=None
                            raise
                    return environment
                def identify(self,inner_guard):
                    nonlocal before,environment_before
                    result=actual.identify(inner_guard)
                    # Builder has already validated the request inventory, and will
                    # now audit every source/receipt/library. Capture both sides of
                    # that full pass. New artifact folders/builds are not memoized.
                    roots=(root/cohort/'requests',root/cohort/'artifacts'/result[0])
                    try:
                        if not callable(getattr(actual,'preparation_environment',None)):return result
                        environment_before=actual.preparation_environment(inner_guard)
                        before=_SessionSnapshot(roots,session.tmp,inner_guard)
                    except (OSError,ValueError,Refused)as error:
                        if isinstance(error,Stopped):raise
                    return result
                def compile(self,*args,**kwargs):return actual.compile(*args,**kwargs)
                def unchanged(self,*args,**kwargs):return actual.unchanged(*args,**kwargs)
                def configure_registration(self,root):
                    configure=getattr(actual,'configure_registration',None)
                    if callable(configure):configure(root)
                def registration_work(self):
                    report=getattr(actual,'registration_work',None)
                    return report()if callable(report)else {}
            try:
                summary=build(root,cohort,enabled=True,guard=guard,toolchain=EnclosedToolchain(),**({'runtime_profile':self.runtime_profile}if self.runtime_profile is not None else{}))
                self.stats['full_build_seconds']+=self.clock()-started
                if summary.get('status')=='passed'and summary.get('requests',0)>0:
                    try:
                        stable_tools=self._tools_unchanged(actual,guard)and environment_before is not None and actual.preparation_environment(guard)==environment_before
                        if stable_tools:
                            # This is only an anchor for FUTURE actual native
                            # validation, never a successful-build memo.
                            self.audit=(scope,actual,dict(summary),environment_before)
                        if stable_tools and summary.get('built')==0 and before is not None and before.unchanged(guard):
                            self.snapshot=before;before=None;self.toolchain=actual;self.scope=scope;self.summary=dict(summary);self.environment=environment_before
                            self.stats['metadata_entries']=self.snapshot.count
                    except (OSError,ValueError,Refused)as error:
                        if isinstance(error,Stopped):raise
                        # Optional anchors cannot replace the original full audit.
                return dict(summary,session_reused=False)
            finally:
                if before is not None:before.close()
        finally:self.stats['seconds']+=self.clock()-started
    def begin_validation(self,root,cohort,guard):
        """Capture before native validation; no memo/native success authority."""
        if self.audit is None or self.audit[0]!=(str(Path(root)),cohort):return None
        scope,toolchain,summary,environment=self.audit;lock=None;snapshot=None
        try:
            checked_root(Path(root),**({'runtime_profile':self.runtime_profile}if self.runtime_profile is not None else{}));guard.check(preflight=True)
            lock=os.open(Path(root)/cohort/'artifacts/.builder.lock',os.O_RDWR|os.O_NOFOLLOW)
            row=os.fstat(lock)
            if not stat.S_ISREG(row.st_mode)or row.st_uid!=os.getuid()or row.st_nlink!=1:raise Refused('invalid validation builder lock')
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            if not self._tools_unchanged(toolchain,guard)or toolchain.preparation_environment(guard)!=environment:return None
            snapshot=_SessionSnapshot((Path(root)/cohort/'requests',Path(root)/cohort/'artifacts'/summary['build_id']),self.tmp,guard)
            return _ValidationAnchor(self,Path(root),cohort,toolchain,environment,snapshot)
        except (OSError,ValueError,Refused)as error:
            if snapshot is not None:snapshot.close()
            if isinstance(error,Stopped):raise
            return None
        finally:
            if lock is not None:os.close(lock)
    def close(self):
        self.validation_generation+=1
        if self.snapshot is not None:self.snapshot.close()
        self.snapshot=self.toolchain=self.summary=self.scope=self.environment=self.audit=None



class _ValidationAnchor:
    """Unchanged interval proof, valid only together with actual native outcomes."""
    def __init__(self,session,root,cohort,toolchain,environment,snapshot):
        self.session=session;self.root=root;self.cohort=cohort;self.toolchain=toolchain;self.environment=environment;self.snapshot=snapshot;self.generation=session.validation_generation
    def unchanged(self,guard):
        if self.snapshot is None or self.session.validation_generation!=self.generation:return False
        lock=None
        try:
            checked_root(self.root,**({'runtime_profile':self.session.runtime_profile}if self.session.runtime_profile is not None else{}));guard.check(preflight=True)
            lock=os.open(self.root/self.cohort/'artifacts/.builder.lock',os.O_RDWR|os.O_NOFOLLOW);row=os.fstat(lock)
            if not stat.S_ISREG(row.st_mode)or row.st_uid!=os.getuid()or row.st_nlink!=1:raise Refused('invalid validation builder lock')
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            return self.session._tools_unchanged(self.toolchain,guard)and self.toolchain.preparation_environment(guard)==self.environment and self.snapshot.unchanged(guard)
        except (OSError,ValueError,Refused)as error:
            if isinstance(error,Stopped):raise
            return False
        finally:
            if lock is not None:os.close(lock)
    def close(self):
        if self.snapshot is not None:self.snapshot.close();self.snapshot=None


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--enable',action='store_true');parser.add_argument('--root');parser.add_argument('--cohort')
    args=parser.parse_args();summary=build(args.root,args.cohort,enabled=args.enable)
    print(json.dumps(summary,indent=2,ensure_ascii=False));return 0 if summary['status']in('off','passed')else 1
if __name__=='__main__':raise SystemExit(main())
