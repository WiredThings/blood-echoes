"""Explicit outer-formal-bundle scope; QA callers retain their original gates.

This is path/byte admission, not an artifact-ready receipt. Native processes
independently admit their actual executable role before touching these roots.
"""
from pathlib import Path
from dataclasses import dataclass,field
import hashlib,json,os,plistlib,re,stat,tempfile
from collections import OrderedDict
from threading import RLock

# One interpreter's verified digests, not cross-launch or pipeline authority.
# No file contents/handles are retained. Changes in inode, ownership, links,
# permissions, size or nanosecond timestamps force the original full read.
_CODE_MEMO_MAX=256
_code_memo=OrderedDict()
_code_memo_lock=RLock()
_code_work=dict(hash_reads=0,hash_bytes=0,metadata_digest_hits=0)

def _code_stamp(s):
    return (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,
            s.st_size,s.st_mtime_ns,s.st_ctime_ns)

def code_validation_work():
    with _code_memo_lock:return dict(_code_work,memo_entries=len(_code_memo))

FORMAL_ID='io.github.bbport.optimized'
WORK='arm64-runtime-work'
NAMESPACE='arm64-native-v1'
ROLES=('bin/bb-probe-metal','bin/gpu-metal/libbbgpu.dylib',
       'bin/gpu-metal-arm64/libbbgpu.dylib','bin/Bloodborne Metal.app/Contents/MacOS/bb-gpu',
       'bin/game-pso-preparer','bin/game-module-preparer')
ORIGINAL_SCRIPTS=('run.sh','tools/macos/run_optimized.sh','tools/macos/supervise_restart.sh',
 'tools/macos/quicksave.py','patches/Bloodborne.xml','scripts/link_libc.py',
 'scripts/chinese_translation.py','scripts/online.py','scripts/content_profile.py',
 'scripts/link_modules.py','scripts/patches.py','scripts/game_check.py','scripts/mods.py',
 'scripts/prepare.py','scripts/ultrawide_ui.py','tools/macos/run.sh')

def need(value,why):
    if not value:raise ValueError(why)

def code_bytes(path,*,limit=1024*1024,expected=None,content=True):
    p=Path(path).absolute();need(p==p.resolve(strict=True),'canonical bundled code required')
    observed=p.lstat()
    need(stat.S_ISREG(observed.st_mode)and observed.st_uid in(0,os.getuid())and observed.st_nlink==1 and not(observed.st_mode&0o022)and 0<observed.st_size<=limit,'bounded bundled code role')
    if not content:
        with _code_memo_lock:
            cached=_code_memo.get(str(p))
            if cached is not None and cached[0]==_code_stamp(observed):
                need(expected is None or cached[1]==expected,'bundled code identity changed')
                _code_memo.move_to_end(str(p));_code_work['metadata_digest_hits']+=1
                return None,dict(path=str(p),sha256=cached[1],bytes=observed.st_size)
    fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        before=os.fstat(fd);need(stat.S_ISREG(before.st_mode)and before.st_uid in(0,os.getuid())and before.st_nlink==1 and not(before.st_mode&0o022)and 0<before.st_size<=limit,'bounded bundled code role')
        value=hashlib.sha256();data=bytearray()if content else None;count=0
        with _code_memo_lock:_code_work['hash_reads']+=1
        with os.fdopen(fd,'rb',closefd=False)as stream:
            for part in iter(lambda:stream.read(1024*1024),b''):
                count+=len(part);need(count<=limit,'bundled code bound');value.update(part)
                with _code_memo_lock:_code_work['hash_bytes']+=len(part)
                if content:data.extend(part)
        after=os.fstat(fd)
        need(_code_stamp(observed)==_code_stamp(before)==_code_stamp(after)==_code_stamp(p.lstat())and count==before.st_size,'bundled code changed during read')
        value=value.hexdigest()
        need(expected is None or value==expected,'bundled code identity changed')
        with _code_memo_lock:
            _code_memo[str(p)]=(_code_stamp(after),value);_code_memo.move_to_end(str(p))
            while len(_code_memo)>_CODE_MEMO_MAX:_code_memo.popitem(last=False)
        return bytes(data)if content else None,dict(path=str(p),sha256=value,bytes=count)
    finally:os.close(fd)

def owned_dir(path,*,create=False):
    p=Path(path).absolute()
    if create and not p.exists():p.mkdir(mode=0o700)
    need(p==p.resolve(strict=True),'canonical data directory required');s=p.stat()
    need(stat.S_ISDIR(s.st_mode)and s.st_uid==os.getuid(),'owned data directory required')
    return p

def _relative(root,value):
    p=Path(value);need(not p.is_absolute()and '..'not in p.parts and str(p)==value,'bundled relative role')
    result=root/p;need(result==result.resolve(strict=True)and result.is_relative_to(root),'bundled role escaped')
    return result

@dataclass(frozen=True)
class Bundle:
    stage:Path
    app:Path
    native:dict
    local:dict
    validation_work:dict=None

def bundle(stage,*,registration_root=None):
    if stage is None:return None
    root=Path(stage).absolute();app=root.parent.parent.parent
    if (root.name,root.parent.name,root.parent.parent.name)!=('bbport','Resources','Contents')or app.suffix!='.app':return None
    need(root==root.resolve(strict=True),'canonical bundle stage required')
    info_raw,info_id=code_bytes(app/'Contents/Info.plist');info=plistlib.loads(info_raw)
    if info.get('CFBundleIdentifier')!=FORMAL_ID:return None
    native_raw,native_id=code_bytes(root/'NATIVE-METAL.json');native=json.loads(native_raw)
    local_raw,local_id=code_bytes(root/'ARM64-LOCAL-PREPARATION.json');local=json.loads(local_raw)
    parity_raw,parity_id=code_bytes(root/'SOURCE-PARITY.json');parity=json.loads(parity_raw)['effective_sources']
    need(native.get('schema')==1 and native.get('game_runtime')=='fex-arm64'and native.get('production')is True,'formal runtime profile required')
    need(local.get('schema')==1 and local.get('production')is True,'formal local preparation profile')
    closure=None
    if registration_root is not None:
        from code_registration import Closure
        if callable(registration_root):registration_root=registration_root()
        def hasher(p,check):
            check();_,row=code_bytes(p,limit=512*1024*1024,content=False);return row['sha256'],row['bytes']
        closure=Closure(app,registration_root,{key:value['sha256']for key,value in [('info',info_id),('native',native_id),('local',local_id),('parity',parity_id)]},hasher)
    def verify(base,name,want,limit=1024*1024):
        if closure is not None:return closure.check(base,name,want,limit)
        return code_bytes(_relative(base,name),limit=limit,expected=want,content=False)
    frozen=native['frozen_runtime'];allowed=set(ROLES)|{'bin/Bloodborne Game.app/Contents/MacOS/bb-probe-metal','bin/gpu-net/libbbnet.dylib'}
    need(isinstance(frozen,dict)and set(ROLES)<=set(frozen)<=allowed and len(frozen)<=32,'complete declared formal runtime roles')
    for role,want in frozen.items():
        need(isinstance(want,str)and re.fullmatch('[0-9a-f]{64}',want)is not None,'formal runtime digest shape')
        verify(root,role,want,512*1024*1024)
    tools=local['runtime_tools'];support=local['runtime_support']
    need(isinstance(tools,dict)and 1<=len(tools)<=128,'formal frozen tool closure')
    need(set(support)=={'vendor/blood_echoes/loader/prepare.py','vendor/blood_echoes/loader/game_check.py'},'formal original parser closure')
    for name,want in tools.items():
        need(Path(name).name==name,'formal tool basename');verify(root,'native-runtime-tools/'+name,want)
    for name,want in support.items():verify(root,name,want)
    verify(root,'tools/macos/local_metal_startup.py',local['startup_bootstrap_sha256'])
    need(set(parity)==set(ORIGINAL_SCRIPTS),'formal original script set')
    for name,want in parity.items():verify(root,name,want,4*1024*1024)
    return Bundle(root,app,native,local,closure.finish()if closure is not None else None)

@dataclass(frozen=True)
class PackagingCapabilityProfile:
    """Code-only packaging query scope. Deliberately NOT a game RuntimeProfile."""
    bundle:Bundle
    data_root:Path
    _data_identity:tuple

    def revalidate(self):
        current=bundle(self.bundle.stage)
        need(current is not None and current.app==self.bundle.app and current.native==self.bundle.native and current.local==self.bundle.local,'packaging bundle code identity changed')
        need(set(current.native['frozen_runtime'])==set(ROLES)|{'bin/Bloodborne Game.app/Contents/MacOS/bb-probe-metal','bin/gpu-net/libbbnet.dylib'},'packaging requires eight actual frozen runtime roles')
        root=owned_dir(self.data_root);info=root.stat()
        need((info.st_dev,info.st_ino,info.st_uid,stat.S_IMODE(info.st_mode))==self._data_identity and stat.S_IMODE(info.st_mode)==0o700,'packaging capability data root replaced or changed')
        return current

    def capability_root(self):
        self.revalidate()
        work=owned_dir(self.data_root/WORK);capabilities=owned_dir(work/'capabilities')
        need(stat.S_IMODE(work.stat().st_mode)==stat.S_IMODE(capabilities.stat().st_mode)==0o700,'packaging scalar scratch must stay private')
        return capabilities


def packaging_capabilities(stage,*,scratch_parent):
    """Context manager owning fresh disposable packaging data, never P/state."""
    from contextlib import contextmanager
    @contextmanager
    def enter():
        admitted=bundle(stage);need(admitted is not None,'actual formal bundle code closure required before packaging capability')
        parent=owned_dir(scratch_parent)
        need(not parent.is_relative_to(admitted.app),'packaging scratch must stay outside signed application')
        with tempfile.TemporaryDirectory(prefix='packaging-capability-',dir=parent)as temporary:
            data=owned_dir(temporary);work=owned_dir(data/WORK,create=True);owned_dir(work/'capabilities',create=True)
            info=data.stat();profile=PackagingCapabilityProfile(admitted,data,(info.st_dev,info.st_ino,info.st_uid,stat.S_IMODE(info.st_mode)))
            profile.revalidate()
            yield profile
    return enter()


@dataclass(frozen=True)
class RuntimeProfile:
    bundle:Bundle
    data_root:Path
    work_root:Path
    gpu_base:Path
    gpu_root:Path
    game_root:Path
    app0:Path
    user:Path
    preparation_work:dict=field(default_factory=dict,compare=False)

    def native_identity(self,target):
        from code_registration import native_gpu_identity
        return native_gpu_identity(target,self.bundle.stage,self)

    def archive_manifest(self,**values):
        from incremental_file_hashes import Hashes
        from launcher_game_catalog import file_sha
        from warm_archive_manifest import make_manifest
        game=values['game_artifacts']
        need(set(game)=={'boot','content_profile','patches','settings'},'complete original final launch inputs')
        paths={name:str(Path(path).resolve(strict=True))for name,path in game.items()}
        need(paths['settings']==str(self.data_root/'bbport.ini')and all(Path(path).is_relative_to(self.data_root/'out')for name,path in paths.items()if name!='settings'),'original final inputs registration scope')
        hashes=Hashes(self.work_root/'final-input-digests.json',dict(schema=1,role='original-final-launch-inputs',paths=paths),hasher=file_sha)
        def identity(path):
            path=Path(path)
            if str(path)in paths.values():sha,size=hashes.get(path)
            else:
                code=self.code(path);role=str(code.relative_to(self.bundle.stage))
                need(role in self.bundle.native['frozen_runtime'],'translator frozen code role')
                row=code_bytes(code,limit=512*1024*1024,expected=self.bundle.native['frozen_runtime'][role],content=False)[1];sha,size=row['sha256'],row['bytes']
            return dict(sha256=sha,bytes=size)
        result=make_manifest(**values,identity_reader=identity);hashes.publish();self.preparation_work['final_input_digests']=hashes.report();return result

    def gpu(self,path):
        need(owned_dir(path)==self.gpu_root,'formal selected GPU namespace mismatch');return self.gpu_root
    def work(self,project):
        need(owned_dir(project)==self.work_root,'formal preparation work root mismatch');return self.work_root
    def code(self,path):
        p=Path(path).absolute();need(p==p.resolve(strict=True)and p.is_relative_to(self.bundle.stage),'formal code root mismatch')
        info=p.stat();need(info.st_uid in(0,os.getuid())and not(info.st_mode&0o022),'formal code owner/mode mismatch');return p
    def environment(self,env):
        return dict(env,BB_ARM_GPU_BASE_DIR=str(self.gpu_base),BB_GPU_USER_DIR=str(self.gpu_root))
    def cpu(self,path):
        p=self.code(Path(path).resolve(strict=True));relative=str(p.relative_to(self.bundle.stage))
        need(relative in('bin/bb-probe-metal','bin/Bloodborne Game.app/Contents/MacOS/bb-probe-metal')and relative in self.bundle.native['frozen_runtime'],'formal CPU role mismatch')
        code_bytes(p,limit=256*1024*1024,expected=self.bundle.native['frozen_runtime'][relative],content=False);return p
    def renderer(self,path):
        p=self.code(Path(path).resolve(strict=True));relative='bin/Bloodborne Metal.app/Contents/MacOS/bb-gpu'
        need(p==self.bundle.stage/relative,'formal GPU role mismatch')
        code_bytes(p,limit=256*1024*1024,expected=self.bundle.native['frozen_runtime'][relative],content=False);return p
    def child_environment(self):return dict(BB_DATA_DIR=str(self.data_root),BB_ARM_GPU_BASE_DIR=str(self.gpu_base))
    def capability_root(self):return owned_dir(self.work_root/'capabilities',create=True)
    def cache(self,path):
        p=Path(path).absolute();need(p==p.resolve(strict=True)and p.is_relative_to(self.gpu_root)and p!=self.gpu_root,'formal cache read escaped new namespace');return p
    def source(self,path):
        p=owned_dir(path)
        need(p==self.app0 or p==self.game_root or p.is_relative_to(self.gpu_root/'automatic-game-inputs'),'formal source-view role mismatch')
        return p

def derive(stage,arguments,environment,*,executable=None):
    def registration():return owned_dir(owned_dir(environment['BB_DATA_DIR'])/WORK,create=True)/'code-registration'
    admitted=bundle(stage,registration_root=registration)
    if admitted is None:return None
    env=environment;data=owned_dir(env['BB_DATA_DIR']);config=Path(env['BB_CONFIG']).absolute()
    need(config==data/'bbport.ini','formal original config role mismatch')
    from game_pso_owned_audit import checked_bytes
    checked_bytes(config)
    def argument(name):
        positions=[i for i,x in enumerate(arguments)if x==name]
        need(len(positions)==1 and positions[0]+1<len(arguments),'formal original option role')
        return Path(arguments[positions[0]+1]).absolute()
    for p in(Path(arguments[0]).absolute(),argument('--content-profile'),argument('--patches')):
        need(p.is_relative_to(data/'out'),'formal startup artifact outside original out')
        need(p==p.resolve(strict=True),'formal startup artifact linked');s=p.stat()
        need(stat.S_ISREG(s.st_mode)and s.st_uid==os.getuid(),'formal startup artifact must be owned regular data')
    user=argument('--user');game=owned_dir(env['BB_GAME_DIR']);app0=owned_dir(argument('--app0'))
    need(app0==game or(app0.parent==data/'out'and app0.name.startswith('mod-game-')),'formal original app0 role mismatch')
    base=owned_dir(env.get('BB_ARM_GPU_BASE_DIR',env['BB_GPU_USER_DIR']))
    expected=base/NAMESPACE;selected=Path(env['BB_GPU_USER_DIR']).absolute()
    need(selected in(base,expected),'formal GPU base/namespace mismatch')
    work=data/WORK
    need(not expected.is_relative_to(game)and not work.is_relative_to(game),'formal preparation outputs overlap game input')
    need(not(user==work or user.is_relative_to(work)or work.is_relative_to(user)),'formal preparation work overlaps user data')
    need(not(user==expected or user.is_relative_to(expected)or expected.is_relative_to(user)),'formal GPU namespace overlaps user data')
    result=RuntimeProfile(admitted,data,work,base,expected,game,app0,user)
    if executable is not None:result.cpu(executable)
    result.renderer(env['BB_GPU_PROCESS'])
    owned_dir(work,create=True);owned_dir(expected,create=True)
    return result

def _game_profile(profile):
    need(not isinstance(profile,PackagingCapabilityProfile),'packaging capability profile is not a game runtime profile')
    return profile

def preparation_memory_policy(profile):
    """QA stop rules belong to private harnesses, never normal application use.

    Selection comes from the admitted runtime scope, not an environment switch.
    A QA harness testing this same application still owns its OUTER MemoryPolicy.
    Compilation/input/key/log/time/concurrency bounds are independent of this.
    """
    if profile is not None:
        need(isinstance(profile,RuntimeProfile),'actual runtime profile required for application preparation')
        return None
    from qa_memory_policy import MemoryPolicy
    return MemoryPolicy

def kw(profile):
    _game_profile(profile);return {'runtime_profile':profile}if profile is not None else{}
def work(project,profile=None):
    _game_profile(profile);return profile.work(project)if profile is not None else owned_dir(Path(project)/'state')
def cache_root(project,profile=None):
    _game_profile(profile);return profile.gpu_root if profile is not None else owned_dir(Path(project)/'state')

@dataclass(frozen=True)
class MetadataInputs:
    bundle:Bundle
    boot:Path
    game:Path
    out:Path
    loader:Path

def metadata_inputs(admitted,boot,game,out,environment):
    """CPU's separate normal module-info output, never Python's prep scratch."""
    data=owned_dir(environment['BB_DATA_DIR']);nonce=environment.get('BB_ARM_SESSION_NONCE','')
    need(re.fullmatch('[0-9a-f]{32}',nonce)is not None,'formal module session nonce')
    config=Path(environment['BB_CONFIG']).absolute();need(config==data/'bbport.ini'and config==config.resolve(strict=True),'formal module config role')
    info=config.stat();need(stat.S_ISREG(info.st_mode)and info.st_uid==os.getuid(),'owned formal module config')
    boot=Path(boot).resolve(strict=True);game=owned_dir(game);out=Path(out).absolute()
    need(boot==data/'out/boot-linked.bin','formal module boot role')
    base=owned_dir(environment['BB_GAME_DIR'])
    need(game==base or(game.parent==data/'out'and game.name.startswith('mod-game-')),'formal module app0 role')
    expected=data/'out/runtime-sessions'/nonce/'.arm-module-info.bin'
    need(out==expected and out.parent==owned_dir(out.parent),'formal module metadata output role')
    return MetadataInputs(admitted,boot,game,out,admitted.stage/'vendor/blood_echoes/loader')

def capability_environment(project,profile=None,prefix='capability-',*,qa_root=None):
    """Context manager: fresh scalar data; never inherited HOME or old GPU data."""
    from contextlib import contextmanager
    @contextmanager
    def enter():
        root=profile.capability_root()if profile is not None else(owned_dir(qa_root)if qa_root is not None else work(project))
        with tempfile.TemporaryDirectory(prefix=prefix,dir=root)as temporary:
            env=dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin',HOME=temporary,TMPDIR=temporary,BB_GPU_USER_DIR=temporary)
            if profile is not None:env.update(BB_DATA_DIR=str(profile.data_root))
            yield env
    return enter()
