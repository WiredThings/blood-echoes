"""Explicit outer-formal-bundle scope; QA callers retain their original gates.

This is path/byte admission, not an artifact-ready receipt. Native processes
independently admit their actual executable role before touching these roots.
"""
from pathlib import Path
from dataclasses import dataclass
import hashlib,json,os,plistlib,re,stat,tempfile

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
    fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        before=os.fstat(fd);need(stat.S_ISREG(before.st_mode)and before.st_uid in(0,os.getuid())and before.st_nlink==1 and not(before.st_mode&0o022)and 0<before.st_size<=limit,'bounded bundled code role')
        value=hashlib.sha256();data=bytearray()if content else None;count=0
        with os.fdopen(fd,'rb',closefd=False)as stream:
            for part in iter(lambda:stream.read(1024*1024),b''):
                count+=len(part);need(count<=limit,'bundled code bound');value.update(part)
                if content:data.extend(part)
        after=os.fstat(fd);stamp=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        need(stamp(before)==stamp(after)and count==before.st_size,'bundled code changed during read')
        value=value.hexdigest()
        need(expected is None or value==expected,'bundled code identity changed')
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

def bundle(stage):
    if stage is None:return None
    root=Path(stage).absolute();app=root.parent.parent.parent
    if (root.name,root.parent.name,root.parent.parent.name)!=('bbport','Resources','Contents')or app.suffix!='.app':return None
    need(root==root.resolve(strict=True),'canonical bundle stage required')
    info=plistlib.loads(code_bytes(app/'Contents/Info.plist')[0])
    if info.get('CFBundleIdentifier')!=FORMAL_ID:return None
    native=json.loads(code_bytes(root/'NATIVE-METAL.json')[0]);local=json.loads(code_bytes(root/'ARM64-LOCAL-PREPARATION.json')[0])
    need(native.get('schema')==1 and native.get('game_runtime')=='fex-arm64'and native.get('production')is True,'formal runtime profile required')
    need(local.get('schema')==1 and local.get('production')is True,'formal local preparation profile')
    frozen=native['frozen_runtime'];allowed=set(ROLES)|{'bin/Bloodborne Game.app/Contents/MacOS/bb-probe-metal','bin/gpu-net/libbbnet.dylib'}
    need(isinstance(frozen,dict)and set(ROLES)<=set(frozen)<=allowed and len(frozen)<=32,'complete declared formal runtime roles')
    for role,want in frozen.items():
        need(isinstance(want,str)and re.fullmatch('[0-9a-f]{64}',want)is not None,'formal runtime digest shape')
        code_bytes(_relative(root,role),limit=512*1024*1024,expected=want,content=False)
    tools=local['runtime_tools'];support=local['runtime_support']
    need(isinstance(tools,dict)and 1<=len(tools)<=128,'formal frozen tool closure')
    need(set(support)=={'vendor/blood_echoes/loader/prepare.py','vendor/blood_echoes/loader/game_check.py'},'formal original parser closure')
    for name,want in tools.items():
        need(Path(name).name==name,'formal tool basename');code_bytes(_relative(root,'native-runtime-tools/'+name),expected=want)
    for name,want in support.items():code_bytes(_relative(root,name),expected=want)
    code_bytes(root/'tools/macos/local_metal_startup.py',expected=local['startup_bootstrap_sha256'])
    parity=json.loads(code_bytes(root/'SOURCE-PARITY.json')[0])['effective_sources']
    need(set(parity)==set(ORIGINAL_SCRIPTS),'formal original script set')
    for name,want in parity.items():code_bytes(_relative(root,name),limit=4*1024*1024,expected=want)
    return Bundle(root,app,native,local)

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
    admitted=bundle(stage)
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

def kw(profile):return {'runtime_profile':profile}if profile is not None else{}
def work(project,profile=None):return profile.work(project)if profile is not None else owned_dir(Path(project)/'state')
def cache_root(project,profile=None):return profile.gpu_root if profile is not None else owned_dir(Path(project)/'state')

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
