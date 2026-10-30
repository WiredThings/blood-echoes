#!/usr/bin/env python3
"""Private functional QA: original full pipeline, one separately admitted CPU handoff.

Default is a read-only plan. Execution requires --execute --reviewed-code.
No normal script, bundle, manifest or receipt is rewritten. The existing sampler
owns admission, private cloning, NativeObserver, memory policy and process cleanup.
"""
from dataclasses import dataclass, field
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import runpy
import shlex
import stat
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PROJECT = Path('/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype')
APP = PROJECT / 'evidence/upstream-refresh-20261030/Blood Echoes Reviewed.app'
CPU_ROOT = PROJECT / 'evidence/upstream-private-death-cache-20261030'
CPU_ROLE = 'bin/Bloodborne Game.app/Contents/MacOS/bb-probe-metal'
EXPECTED = {
    'run.sh': 'c3a6e47118ca305ca09b67205038a9cf579426869c635e0ed6725db0f1e8f8c1',
    'tools/macos/run_optimized.sh': '8be773022072505ce041e02039d67ce96978baf85eb702653a1a6bfa83f89ff7',
    'tools/macos/supervise_restart.sh': 'b9ca23612b51732a4e0a1dc2b83115c8ede9693faf9389b87ebe387ec87adc2a',
    'native-runtime-tools/launcher_local_aot.py': 'ba280ad2271d583b66dbc85675ce1e15bffbbe96d7a26b6c4d83c4f84212ecc2',
    'tools/macos/local_metal_startup.py': '0d89df59d17826105d77cf21313ec0b1623d021f934ce9dc0d46433fbc77e9b8',
}
COMPLETED = 'private death two-cycle completed'
RESUME_TAG = '1793349284558147000'
VERTEX_FLAG = 'BB_MTL_RELAX_VERTEX'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def owned(path, *, limit=4 * 1024 * 1024):
    path = Path(path)
    info = path.lstat()
    if (path != path.resolve(strict=True) or not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_nlink != 1 or info.st_mode & 0o022 or not 0 < info.st_size <= limit):
        raise ValueError('bounded canonical owned private input required')
    return path


def plain_json(path):
    return json.loads(owned(path).read_text())


def atomic(path, data):
    path = Path(path)
    temporary = path.with_name(path.name + '.host-pending')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
        os.replace(temporary, path)
    finally:
        if temporary.exists():temporary.unlink()


def projected_routes(inputs, stage, output):
    if set(inputs) != set(EXPECTED) or any(hashlib.sha256(raw).hexdigest() != EXPECTED[name] for name, raw in inputs.items()):
        raise ValueError('exact reviewed normal launch source closure required')
    stage, output = Path(stage), Path(output)
    if (not stage.is_absolute() or not stage.resolve().is_relative_to(PROJECT/'evidence')
            or stage.parent.parent.parent.suffix!='.app' or output.parent!=PROJECT/'evidence'
            or not output.name.removeprefix('upstream-private-death-run-').isdigit()
            or output.exists() or output.is_symlink()):
        raise ValueError('canonical normal evidence stage and fresh private route output required')
    q = lambda p: shlex.quote(str(p)).encode()
    def change(raw, old, new):
        if raw.count(old) != 1:raise ValueError('routing anchor is missing or ambiguous')
        return raw.replace(old, new, 1)
    optimized = change(inputs['tools/macos/run_optimized.sh'], b'cd -- "$(dirname -- "$0")/../.."', b'cd -- ' + q(stage))
    optimized = change(optimized, b'exec bash tools/macos/supervise_restart.sh "$@"', b'exec bash ' + q(output/'supervise.sh') + b' "$@"')
    optimized = change(optimized, b'exec bash run.sh "$@"', b'exec bash ' + q(output/'run.sh') + b' "$@"')
    supervisor = change(inputs['tools/macos/supervise_restart.sh'], b'cd -- "$(dirname -- "$0")/../.."', b'cd -- ' + q(stage))
    supervisor = change(supervisor, b'bash run.sh "$@" &', b'bash ' + q(output/'run.sh') + b' "$@" &')
    run = change(inputs['run.sh'], b'cd -- "$(dirname -- "$0")"', b'cd -- ' + q(stage))
    run = change(run, b'local_metal_startup=$PWD/tools/macos/local_metal_startup.py', b'local_metal_startup=' + q(output/'handoff.py'))
    return {'run_optimized.sh': optimized, 'supervise.sh': supervisor, 'run.sh': run}


def verify_cpu(root):
    """Never accept the earlier CPU that retained the normal bridge header."""
    root = Path(root)
    if root.parent != PROJECT/'evidence' or not root.name.startswith('upstream-private-death-') or root != root.resolve(strict=True):
        raise ValueError('canonical dedicated private CPU root required')
    result = plain_json(root/'build-result.json')
    plan = plain_json(root/'plan.json')
    identity = plan.get('private_cpu_cache_build', {})
    if (result.get('status') != 'private-cpu-built-not-run' or result.get('architecture') != 'arm64'
            or result.get('game_started') is not False or not result.get('original_input_hashes_unchanged')
            or not result.get('current_normal_renderer_load_edge')
            or not identity.get('own_cpu_cache_namespace_required')
            or identity.get('normal_bridge_build') == identity.get('bridge_build')):
        raise ValueError('built private CPU with accurate separate bridge identity required')
    if result.get('private_cpu_cache_build') != identity or any(
            not isinstance(identity.get(k),str) or len(identity[k])!=64 or set(identity[k])-set('0123456789abcdef')
            for k in ('runtime_build','bridge_build','normal_bridge_build')):
        raise ValueError('private built CPU cache identity absent or malformed')
    for name, digest in plan['overlay_sha256'].items():
        if sha(owned(root/'source'/name)) != digest:raise ValueError('private source/header changed')
    if 'bb_cpu_cache_build.h' not in plan['overlay_sha256']:
        raise ValueError('private CPU still uses normal cache header')
    target = owned(root/'code/bb-game-arm64', limit=256*1024*1024)
    if result.get('executable') != str(target) or sha(target) != result['executable_sha256']:
        raise ValueError('private CPU actual bytes differ')
    for path, digest in result['normal_input_sha256'].items():
        if sha(owned(path, limit=256*1024*1024)) != digest:raise ValueError('normal linked input changed')
    return target, plan, result


def cpu_cache_control(source, private_file):
    """Preserve source off/empty semantics; redirect only a source-enabled cache."""
    key='BB_ARM_CPU_CACHE';present=key in source;value=source.get(key)
    if present and (not isinstance(value,str) or value=='0'):
        raise ValueError('source CPU cache must be absent, empty or a nonempty cache path; never 0')
    mode='absent' if not present else 'empty' if value=='' else 'enabled'
    expected=str(private_file) if mode=='enabled' else value
    control=dict(source_mode=mode,source_present=present,source_value=value,
                 expected_present=present,expected_value=expected)
    return control,({key:expected} if mode=='enabled' else {})


def cpu_cache_observation(control, environment):
    key='BB_ARM_CPU_CACHE'
    if (key in environment)!=control['expected_present'] or environment.get(key)!=control['expected_value']:
        raise ValueError('actual CPU cache mode differs from original source semantics')
    return dict(source_mode=control['source_mode'],source_present=control['source_present'],source_value=control['source_value'],
                actual_present=key in environment,actual_value=environment.get(key),source_semantics_preserved=True)


def vertex_change(source, base, final, *, enabled):
    """One user-authorized control, after the ordinary source environment check."""
    if (VERTEX_FLAG in base)!=(VERTEX_FLAG in source) or base.get(VERTEX_FLAG)!=source.get(VERTEX_FLAG):
        raise ValueError('vertex flag changed before ordinary environment validation')
    expected=dict(base)
    if enabled:
        if VERTEX_FLAG in source and source[VERTEX_FLAG]!='0':
            raise ValueError('vertex-on requires the original source flag absent or exactly 0')
        expected[VERTEX_FLAG]='1'
    if final!=expected:raise ValueError('unapproved environment change outside the exact vertex control')
    return dict(mode='user-authorized-default-vertex-on' if enabled else 'original-source-vertex',
        requested=enabled,variable=VERTEX_FLAG,before=base.get(VERTEX_FLAG),after=final.get(VERTEX_FLAG),
        ordinary_environment_checked_first=True,allowed_changes=[VERTEX_FLAG] if enabled else [],
        actual_changes={VERTEX_FLAG:dict(before=base.get(VERTEX_FLAG),after='1')} if enabled else {})


def dispatch_arguments(target, argv, environment, manifest):
    """Pure admission: original full argv/env, except the explicit QA executable."""
    expected = manifest['original_cpu']
    if (target != expected or not isinstance(argv, list) or not argv or argv[0] != expected
            or any(not isinstance(v,str) or '\0' in v for v in argv) or sum(map(len,argv))>32768):
        raise ValueError('unexpected exec; only one original admitted CPU transfer allowed')
    if not isinstance(environment,dict) or any(not isinstance(k,str) or not isinstance(v,str) or '\0' in k+v for k,v in environment.items()):
        raise ValueError('actual exec environment must contain only strings')
    env = dict(environment)
    if any((k in env) if v is None else env.get(k)!=v for k,v in manifest['required_environment'].items()):
        raise ValueError('private execution environment changed before final handoff')
    if 'cpu_cache_control' in manifest:cpu_cache_observation(manifest['cpu_cache_control'],env)
    if any(word in key.upper() for key in env for word in ('PASSWORD', 'BEARER', 'CREDENTIAL', 'THD_TOKEN', 'SHADNET_TOKEN')):
        raise ValueError('account credential forbidden in this offline death case')
    options = {}
    for key in ('--user', '--app0', '--patches', '--content-profile'):
        if argv.count(key) != 1 or argv.index(key)+1 >= len(argv):raise ValueError('original native argv incomplete')
        options[key] = argv[argv.index(key)+1]
    if (options['--user'] != manifest['user'] or len(argv)<2 or argv[1] != manifest['boot']
            or options['--content-profile'] != manifest['content'] or options['--patches'] != manifest['patches']):
        raise ValueError('original final argv does not belong to this private case')
    if any(not Path(value).is_relative_to(PROJECT.parent) or not Path(value).resolve().is_relative_to(PROJECT.parent) for value in options.values()):
        raise ValueError('original final input escaped private Shared data')
    # In particular, preserve the live merged app0 selected by run.sh exactly.
    return manifest['private_cpu'], [manifest['private_cpu'], *argv[1:]], env


class ExecOnlyProxy:
    """A single module's os view, without mutating the real/global os module."""
    def __init__(self, original, callback):self.original,self.callback,self.called=original,callback,False
    def __getattr__(self, name):return getattr(self.original,name)
    def execve(self, target, argv, env):
        if self.called:raise ValueError('repeated CPU handoff refused')
        self.called=True
        actual,args,final_env=self.callback(target,argv,env)
        return self.original.execve(actual,args,final_env)


def handoff(manifest_path):
    """Run the real bootstrap; proxy only launcher_local_aot.os.execve, once."""
    manifest_path=Path(manifest_path)
    route_root=manifest_path.parent
    if (manifest_path.name!='handoff.json' or route_root.parent!=PROJECT/'evidence'
            or not route_root.name.removeprefix('upstream-private-death-run-').isdigit()):
        raise ValueError('handoff outside the dedicated private execution case')
    manifest = plain_json(manifest_path)
    stage = Path(manifest['stage'])
    if (stage!=stage.resolve(strict=True) or not stage.is_relative_to(PROJECT/'evidence')
            or manifest['original_cpu']!=str(stage/CPU_ROLE)):
        raise ValueError('handoff original code does not match admitted normal role')
    for name, digest in EXPECTED.items():
        if sha(owned(stage/name)) != digest:raise ValueError('normal bootstrap/source changed')
    cpu, plan, result = verify_cpu(manifest['cpu_root'])
    if str(cpu) != manifest['private_cpu'] or result['executable_sha256'] != manifest['private_cpu_sha256']:
        raise ValueError('private handoff artifact changed')
    if sha(owned(manifest['original_cpu'], limit=256*1024*1024)) != manifest['original_cpu_sha256']:
        raise ValueError('normal preparation CPU bytes changed')
    tools = stage/'native-runtime-tools'
    sys.path.insert(0, str(tools))
    import launcher_local_aot
    if Path(launcher_local_aot.__file__).resolve() != tools/'launcher_local_aot.py':
        raise ValueError('original preparation module imported outside admitted closure')
    def transfer(target,argv,env):
        actual, args, final_env = dispatch_arguments(target, argv, env, manifest)
        verify_cpu(manifest['cpu_root'])  # Recheck actual code/dependencies at transfer.
        if sha(owned(target,limit=256*1024*1024))!=manifest['original_cpu_sha256']:
            raise ValueError('normal preparation CPU changed before final transfer')
        audit = dict(schema=1, original_cpu=target, private_cpu=actual,
            original_argv=argv, actual_argv=args, argv_tail_unchanged=argv[1:]==args[1:],
            environment_sha256=hashlib.sha256(json.dumps(final_env,sort_keys=True).encode()).hexdigest(),
            final_mod_app0=args[args.index('--app0')+1], original_preparation_ran=True,
            actual_render_environment={k:v for k,v in final_env.items() if k.startswith(('BB_MTL_','BB_METAL_',
                'BB_RENDERER','BB_DMEM','BB_RES','BB_FPS','BB_FRAME_','BB_ARM_CPU_CACHE'))},
            private_bridge_build=plan['private_cpu_cache_build']['bridge_build'],
            normal_role_impersonated=False, performance_sample=False)
        audit['cpu_cache']=cpu_cache_observation(manifest['cpu_cache_control'],final_env)
        if 'vertex_change' in manifest:
            audit['vertex_change']=dict(manifest['vertex_change'],actual_value=final_env.get(VERTEX_FLAG))
        atomic(Path(manifest_path).parent/'handoff-receipt.json', (json.dumps(audit,indent=2)+'\n').encode())
        return actual,args,final_env
    launcher_local_aot.os = ExecOnlyProxy(launcher_local_aot.os,transfer)
    bootstrap = stage/'tools/macos/local_metal_startup.py'
    sys.argv[0] = str(bootstrap)
    runpy.run_path(str(bootstrap), run_name='__main__')
    raise ValueError('original preparation returned without its one CPU handoff')


@dataclass
class DeathCheck:
    phase: str = 'waiting'
    sequence: int = 0
    last_time: float | None = None
    since: float = 0
    last_seconds: float = 0
    events: dict = field(default_factory=dict)
    cycles: list = field(default_factory=list)
    stable_since: float | None = None
    frame_start: float | None = None
    frame_latest: float | None = None
    frame_advanced_at: float | None = None

    def step(self, seconds, row, *, cpu_alive=True, frame_clock=None):
        if type(seconds) not in (float,int) or not math.isfinite(seconds) or seconds<self.last_seconds:
            raise ValueError('private host observation clock invalid')
        self.last_seconds=seconds
        if self.phase == 'completed':return None
        if self.phase != 'waiting' and seconds-self.since > 180:raise ValueError('private death cycle exceeded180s')
        if frame_clock is not None:
            if type(frame_clock) not in (int,float) or not math.isfinite(frame_clock) or frame_clock<0:
                raise ValueError('private confirmation frame clock invalid')
            if self.frame_latest is not None and frame_clock<self.frame_latest:raise ValueError('private confirmation frame clock moved backwards')
            if self.frame_latest is None or frame_clock>self.frame_latest:self.frame_advanced_at=seconds
            self.frame_latest=frame_clock
        if row.get('schema') != 1 or row.get('error') or type(row.get('time')) not in (float,int) or not math.isfinite(row['time']):
            raise ValueError('private death status invalid or failed')
        if self.last_time is not None and row['time'] < self.last_time:raise ValueError('private death clock moved backwards')
        if self.last_time == row['time']:return None
        self.last_time = row['time']
        if any(type(row.get(k)) is not bool for k in ('world','loading','health_valid')):
            raise ValueError('private death status flags malformed')
        if any(type(row.get(k)) is not int for k in ('hp','max_hp','stable','player','death_sequence')):
            raise ValueError('private death status integers malformed')
        if row['death_sequence'] != self.sequence:
            # A tick may have read command.txt just before the host atomic write.
            # Do not count old evidence; permit only a bounded acknowledgement lag.
            if self.phase=='dying' and row['death_sequence']==self.sequence-1 and seconds-self.since<=5:return None
            raise ValueError('stale, skipped or unexpected death sequence')
        ready = (row['world'] and not row['loading'] and row['health_valid'] and row['player']>0
                 and 0<row['hp']<=row['max_hp']<=100000 and 5<=row['stable']<=2**32-1)
        if self.phase in ('settling-first','confirming-second'):
            if not ready or not cpu_alive:raise ValueError('private respawn lost stable health/world or live CPU')
            limit=5 if self.sequence==1 else 10
            if self.frame_start is None and frame_clock is not None:self.frame_start=frame_clock
            frame_ok=(self.frame_start is not None and self.frame_latest is not None and self.frame_latest>self.frame_start
                      and self.frame_advanced_at is not None and self.frame_advanced_at>=self.stable_since+10)
            if seconds-self.stable_since<limit or (self.sequence==2 and not frame_ok):return None
            self.cycles.append(dict(sequence=self.sequence,completed_seconds=seconds,evidence=dict(self.events),player=row['player'],
                stable_seconds=seconds-self.stable_since,cpu_alive_confirmed=cpu_alive,
                frame_progress_confirmed=bool(frame_ok),frame_clock_at_return=self.frame_start,frame_clock_at_completion=self.frame_latest))
            if self.sequence==2:self.phase='completed';return None
            self.sequence+=1;self.phase='dying';self.since=seconds;self.stable_since=None
            self.events=dict(hp_zero=False,world_down=False,loading=False)
            return self.sequence
        if self.phase != 'waiting':
            self.events['hp_zero'] |= row['hp']==0 and row['health_valid']
            self.events['world_down'] |= not row['world']
            self.events['loading'] |= row['loading']
            if not (ready and all(self.events.values())):return None
            self.phase='settling-first' if self.sequence==1 else 'confirming-second'
            self.stable_since=seconds;self.frame_start=frame_clock
            return None
        elif not ready:return None
        self.sequence += 1;self.phase='dying';self.since=seconds
        self.events=dict(hp_zero=False,world_down=False,loading=False)
        return self.sequence


class DeathPolicy:
    """Delegate all protection; request only two commands and bounded completion."""
    def __init__(self, original, driver, case, cpu, directory, seconds):
        self.original,self.driver,self.case,self.cpu=original,driver,case,str(cpu)
        self.directory,self.seconds=Path(directory),seconds
        self.check=DeathCheck();self.armed=False;self.clock=None;self.fresh=None;self.cpu_pid=None
        self.next_frame_poll=0;self.latest_frame_clock=None
    def observe(self, seconds, sample):
        stopped=self.original.observe(seconds,sample)
        if stopped:return stopped
        if seconds>=self.seconds:return 'private death total time budget'
        rows=sample.get('processes', [])
        pids={pid for pid,ppid,path in rows if path==self.cpu}
        if len(pids)>1 or (self.cpu_pid is not None and pids and pids!={self.cpu_pid}):
            raise ValueError('unexpected overlapping or replaced private CPU during death case')
        if self.armed and self.cpu_pid is not None and not pids:
            raise ValueError('private CPU disappeared while armed; stale status cannot authorize progress')
        if pids:self.cpu_pid=next(iter(pids))
        if not self.armed:
            if not pids:return None
            frames=self.case['data_root']/'out/frames.csv'
            if not frames.exists():return None
            try:tail=self.driver.read_tail(frames)
            except ValueError:tail=[]  # Same initial/incomplete-row treatment as the original driver.
            if not tail or not self.driver.scene_seen(tail):return None
            clock=float(tail[-1]['t_s'])
            if not math.isfinite(clock):raise ValueError('invalid scene frame clock')
            if self.clock is None or clock>self.clock:self.fresh=seconds
            self.clock=clock
            if self.fresh!=seconds:return None
            atomic(self.directory/'armed', self.case['environment']['BB_ARM_SESSION_NONCE'].encode())
            self.armed=True
        status=self.directory/'death-status.json'
        if not status.exists():return None
        if seconds>=self.next_frame_poll:
            self.next_frame_poll=seconds+1
            frames=self.case['data_root']/'out/frames.csv'
            if frames.exists():
                try:tail=self.driver.read_tail(frames)
                except ValueError:tail=[]
                if tail:self.latest_frame_clock=float(tail[-1]['t_s'])
        command=self.check.step(seconds,plain_json(status),cpu_alive=bool(pids),frame_clock=self.latest_frame_clock)
        if command is not None:atomic(self.directory/'death-command.txt', (str(command)+'\n').encode())
        if self.check.phase=='completed':return COMPLETED
        return None


def valid_handoff(receipt, manifest, bridge):
    if not isinstance(receipt,dict):return False
    args,actual=receipt.get('original_argv'),receipt.get('actual_argv')
    if (not isinstance(args,list) or not isinstance(actual,list) or not args or not actual
            or args[0]!=manifest['original_cpu'] or actual[0]!=manifest['private_cpu'] or args[1:]!=actual[1:]
            or receipt.get('original_cpu')!=manifest['original_cpu'] or receipt.get('private_cpu')!=manifest['private_cpu']
            or receipt.get('private_bridge_build')!=bridge or receipt.get('argv_tail_unchanged') is not True
            or receipt.get('original_preparation_ran') is not True or receipt.get('normal_role_impersonated') is not False
            or receipt.get('performance_sample') is not False):return False
    if len(args)<2 or args[1]!=manifest['boot']:return False
    for flag,key in (('--user','user'),('--patches','patches'),('--content-profile','content')):
        if args.count(flag)!=1 or args.index(flag)+1>=len(args) or args[args.index(flag)+1]!=manifest[key]:return False
    if 'cpu_cache_control' in manifest:
        control=manifest['cpu_cache_control']
        expected_env={'BB_ARM_CPU_CACHE':control['expected_value']} if control['expected_present'] else {}
        if receipt.get('cpu_cache')!=cpu_cache_observation(control,expected_env):return False
    return args.count('--app0')==1 and args.index('--app0')+1<len(args) and receipt.get('final_mod_app0')==args[args.index('--app0')+1]


def finalize_result(row, check, receipt, manifest, bridge):
    result=dict(row,raw_driver_status=row['status'])
    cycles=check.cycles
    passed=(row.get('stop')==COMPLETED and check.phase=='completed' and len(cycles)==2
        and [cycle.get('sequence') for cycle in cycles]==[1,2]
        and all(cycle.get('evidence')==dict(hp_zero=True,world_down=True,loading=True) for cycle in cycles)
        and all(type(cycle.get('stable_seconds')) in (int,float) and math.isfinite(cycle['stable_seconds'])
            and cycle['stable_seconds']>=limit and cycle.get('cpu_alive_confirmed') is True for cycle,limit in zip(cycles,(5,10)))
        and cycles[1].get('frame_progress_confirmed') is True
        and row.get('source_save_unchanged') is True
        and row.get('whole_app_seal',{}).get('pre',{}).get('status')=='verified'
        and row.get('whole_app_seal',{}).get('post',{}).get('status')=='verified'
        and row.get('whole_app_seal',{}).get('post_after_owned_process_cleanup') is True
        and valid_handoff(receipt,manifest,bridge))
    result['functional_death']=dict(phase=check.phase,cycles=cycles,passed=passed)
    result['cpu_cache_actual']=receipt.get('cpu_cache') if isinstance(receipt,dict) else None
    if passed:result['status']='functional-checked'
    result['qualification']='Private QA CPU after original full preparation; two SetHp deaths only. No FPS, F3, visual or network acceptance.'
    return result


def resume_case(root, source, formal, driver, *, cpu, cpu_plan, cpu_result):
    """Read-only continuation of exactly R0's pre-scene partial-header exception."""
    import re
    from qa_launch_conditions import Reader,canonical,CONFIG_NAMES
    from qa_launch_clone import ALLOWED_CHANGES,inventory_savedata,_tree_inventory
    from qa_memory_policy import MemoryPolicy
    def need(ok,why):
        if not ok:raise ValueError('private death resume: '+why)
    root=canonical(root,directory=True,within=PROJECT/'evidence')
    need(root==PROJECT/'evidence'/('upstream-private-death-run-'+RESUME_TAG),'only the specific reviewed R0 may resume')
    reader=Reader();old=reader.json(root/'result.json');manifest=reader.json(root/'handoff.json');handoff_record=reader.json(root/'handoff-receipt.json')
    need(old.get('status')=='incomplete' and old.get('raw_run_status')=='incomplete'
        and old.get('stop')=='driver exception' and old.get('error')=='missing bounded frame header'
        and old.get('scene_ready') is None and 'scene_sample' not in old and 'functional_death' not in old
        and old.get('source_save_unchanged') is True,'only this pre-scene header exception is eligible')
    need(old.get('memory_policy')==MemoryPolicy.MANIFEST and old.get('performance_sample') is False
        and old.get('silent') is True and old.get('Cua') is False and old.get('formal_data_modified') is False
        and old.get('normal_package_modified') is False,'old isolation/protection identity differs')
    seal=old.get('whole_app_seal',{})
    command=['/usr/bin/codesign','--verify','--deep','--strict',str(formal['app'])]
    need(seal.get('post_after_owned_process_cleanup') is True and all(seal.get(k,{}).get('status')=='verified'
        and seal[k].get('command')==command for k in ('pre','post')),'old same-app pre/post seal absent')
    need(old.get('source_receipt_sha256')==driver.sha(source['receipt']),'frozen source receipt changed')
    reader.read(source['receipt'],expected=old['source_receipt_sha256'])
    need(old.get('projected_source_sha256')==EXPECTED and set(formal['runtime_sha256'])==driver.FORMAL_ROLES
        and formal['identity']['runtime_sha256']==formal['runtime_sha256'],
        'source closure or current full normal admission differs')
    package_path=formal['app'].parent/'package-reviewed-result.json'
    package=reader.json(package_path)
    roles=package.get('actual_frozen_roles',{})
    need(package.get('status')=='reviewed-normal-package-completed' and package.get('app')==str(formal['app'])
        and package.get('packaging_revision')=='214c843a6f5119c9219529f5f50ea25632cacba9'
        and package.get('native_product_revision')=='5c0f2b72bb2f1153513ce018f2a26814d8c61e7e'
        and package.get('native_products_unchanged') is True and package.get('strict_deep_signature_verified') is True
        and package.get('source_parity_entries_verified')==16 and set(roles)==driver.FORMAL_ROLES
        and all(roles[key]==dict(sha256=value,architecture='arm64') for key,value in formal['runtime_sha256'].items())
        and package_path.stat().st_mtime_ns<=(root/'handoff.json').stat().st_mtime_ns,
        'original pre-run package/eight-role identity differs')
    reader.read(formal['stage']/'SOURCE-PARITY.json',expected=package['source_parity_sha256'])
    for name,digest in EXPECTED.items():reader.read(formal['stage']/name,expected=digest)
    for name,digest in old.get('projected_routes_sha256',{}).items():
        need(name in ('run.sh','supervise.sh','run_optimized.sh'),'unknown old projected route')
        reader.read(root/name,expected=digest)
    need(set(old.get('projected_routes_sha256',{}))=={'run.sh','supervise.sh','run_optimized.sh'},'old route proof incomplete')
    need(manifest==old.get('private_cpu') and manifest.get('cpu_root')==str(cpu.parent.parent)
        and manifest.get('private_cpu')==str(cpu) and manifest.get('private_cpu_sha256')==cpu_result['executable_sha256']
        and manifest.get('stage')==str(formal['stage']) and manifest.get('original_cpu')==str(formal['runtime'][CPU_ROLE])
        and manifest.get('original_cpu_sha256')==formal['runtime_sha256'][CPU_ROLE],'old actual CPU/handoff identity differs')
    bridge=cpu_plan['private_cpu_cache_build']['bridge_build']
    need(valid_handoff(handoff_record,manifest,bridge),'old final CPU transfer proof differs')
    changes=old.get('ordinary_changes');need(isinstance(changes,dict) and ALLOWED_CHANGES<=set(changes),'old private after values incomplete')
    for value in changes.values():need(isinstance(value,dict) and set(value)=={'before','after'},'old ordinary delta malformed')
    data=canonical(changes['BB_DATA_DIR']['after'],directory=True,within=PROJECT/'state')
    user=canonical(changes['BB_USER_DIR']['after'],directory=True,within=PROJECT/'state')
    cache=canonical(changes['BB_GPU_USER_DIR']['after'],directory=True,within=PROJECT/'state')
    need(data.name=='benchmark-data-'+RESUME_TAG and user.name=='benchmark-user-'+RESUME_TAG
        and cache.name=='gui-gpu-local-aot-'+RESUME_TAG and data.parent==user.parent==cache.parent==PROJECT/'state'
        and all(stat.S_IMODE(path.stat().st_mode)==0o700 for path in (data,user,cache)),'case roots/tag/ownership mode differ')
    canonical(data/'out',directory=True,within=data)
    need(not os.path.lexists(data/'out/frames.csv'),'old frame log was not archived')
    reader.read(root/'frames.csv',limit=64*1024*1024,retain=False)
    pad=data/'pad.txt';need(reader.read(pad,limit=1024)==b'' and not os.path.lexists(pad.with_name(pad.name+'.next')),'old pad is not idle')
    nonces={key:changes[key]['after'] for key in ('BB_ARM_SESSION_NONCE','BB_ARM_RESTART_NONCE')}
    need(all(isinstance(value,str) and re.fullmatch('[0-9a-f]{32}',value) for value in nonces.values()),'old nonces malformed')
    marker=('ARMQA1\n'+str(data/'out/boot-linked.bin')+'\n'+str(source['game_root'])+'\n'+str(data/'out/content.bin')+'\n'+nonces['BB_ARM_SESSION_NONCE']+'\n').encode()
    need(reader.read(user/'.arm-runtime-session',limit=4096)==marker,'old session marker differs')
    directory=canonical(user/'world-warper',directory=True,within=user)
    need(not list(directory.iterdir()) and reader.read(user/'.private-world-warper',limit=64)==nonces['BB_ARM_SESSION_NONCE'].encode(),
        'world-warper must be empty and keep the same existing private marker')
    need(b'ARM PRIVATE DEATH sequence=' not in reader.read(root/'console.log',limit=16*1024*1024),'old death command already ran')
    expected=dict(BB_DATA_DIR=str(data),BB_CONFIG=str(data/'bbport.ini'),BB_USER_DIR=str(user),
        BB_MODS_DIR=str(data/'mods'),BB_MODS_CONFIG=str(data/'mods.json'),BB_PATCHES_DIR=str(data/'patches'),
        BB_PATCHES_CONFIG=str(data/'patches.json'),BB_GPU_USER_DIR=str(cache),**nonces,BB_PAD_FILE=str(pad),
        BB_ARM_QA_PAD_FILE=str(pad),BB_PAD_FILE_ONLY='1',BB_FRAME_TIMING='1',BB_FRAME_LOG_ONLY='1',BB_BENCHMARK_MUTE='1')
    need(set(expected)==ALLOWED_CHANGES and all(changes[key]['after']==value for key,value in expected.items()),'old fixed clone fields differ')
    config_sha={key:source['configs'][key].sha256 for key in CONFIG_NAMES}
    for key,name in CONFIG_NAMES.items():
        reader.read(source['configs'][key].path,expected=config_sha[key]);reader.read(data/name,expected=config_sha[key])
    for name in ('mods','patches'):
        original,private=source['data_root']/name,data/name
        before=_tree_inventory(original) if os.path.lexists(original) else None
        nodes,files=_tree_inventory(private)
        kinds={str(path.relative_to(private)):stat.S_IFMT(identity[2]) for path,identity in nodes.items()}
        if before is None:
            need(kinds=={'.':stat.S_IFDIR} and not files and not os.path.lexists(original),'private tree differs from absent source '+name)
        else:
            source_nodes,source_files=before
            need(kinds=={str(path.relative_to(original)):stat.S_IFMT(identity[2]) for path,identity in source_nodes.items()}
                and files==source_files and _tree_inventory(original)==before,'private/source resource structure or bytes differ '+name)
    saves=inventory_savedata(source['user_root']/'savedata')
    need(inventory_savedata(user/'savedata')==saves,'private/source savedata byte content differs')
    clone=dict(source['environment']);clone.update(expected)
    case=dict(environment=clone,data_root=data,user_root=user,cache_root=cache,pad=pad,
        source_save_manifest=saves,config_sha256=config_sha,allowed_changes=sorted(ALLOWED_CHANGES),source_unchanged=True)
    env=driver.execution_environment(source,case,formal,functional_f3=False)
    actual,allowed=driver.benchmark_changes(source,case,env,formal=formal,functional_f3=False)
    need(actual==changes and allowed==old.get('ordinary_allowed'),'ordinary execution environment no longer reproduces old proof')
    private=dict(BB_DEV_WORLD_WARPER_DIR=str(directory),BB_ARM_QA_PAD_FILE=str(pad),
        BB_ARM_CPU_CACHE=str(root/'cpu-cache'/bridge/'startup.bin'))
    need(old.get('private_control_after')==private and old.get('private_control_before')=={key:env.get(key) for key in private},'old private controls differ')
    required={key:env[key] for key in ('BB_ONLINE','BB_AUDIO','BB_QUICKSAVE','BB_PAD_FILE_ONLY','BB_ARM_SESSION_NONCE')}
    required.update(private,BB_RESTART_SUPERVISED='1',BB_RENDERER='metal',BB_ARM_LAUNCHER_COMPAT='1',
        BB_GPU_PROCESS=env['BB_GPU_PROCESS'],BB_USER_DIR=str(user))
    need(manifest['required_environment']==required and manifest['user']==str(user)
        and manifest['boot']==str(data/'out/boot-linked.bin') and manifest['content']==str(data/'out/content.bin')
        and manifest['patches']==str(data/'out/patches.bin'),'old handoff role/env/paths differ')
    reader.unchanged()
    return case,dict(root=str(root),prior_status='incomplete',prior_stop=old['stop'],prior_error=old['error'],
        previous_run_counted_as_pass=False,old_evidence_modified=False,
        old_result_sha256=driver.sha(root/'result.json'),old_handoff_sha256=driver.sha(root/'handoff.json'),
        old_transfer_sha256=driver.sha(root/'handoff-receipt.json'),source_savedata_files=len(saves),source_receipt_sha256=old['source_receipt_sha256'],
        historical_packaging_record=str(package_path),historical_packaging_record_sha256=driver.sha(package_path))


def execute(args):
    if not args.reviewed_code:raise ValueError('independent review required')
    if not 240<=args.seconds<=1800:raise ValueError('bounded private duration required')
    if getattr(args,'vertex_on',False) and getattr(args,'resume_from',None) is not None:
        raise ValueError('vertex-on requires a fresh isolated create case; no resume')
    import signal
    import subprocess
    sys.dont_write_bytecode=True
    sys.path.insert(0,str(ROOT/'tools'))
    import run_qa_launch_benchmark as driver
    import run_upstream_refresh_game as harness
    from qa_launch_conditions import load
    from qa_launch_clone import create
    from qa_build_guard import require_no_games
    from native_observer import NativeObserver
    from qa_memory_policy import MemoryPolicy
    require_no_games()
    status=subprocess.run(['/Users/k.d./.local/bin/cua-driver','status'],capture_output=True,text=True,timeout=10)
    if 'daemon is not running' not in (status.stdout+status.stderr).lower():
        raise ValueError('Cua must remain stopped during isolated game regression')
    cpu, cpu_plan, cpu_result=verify_cpu(args.cpu_root)
    source=load(harness.RECEIPT,stage=harness.SOURCE_STAGE,project=PROJECT)
    native,cache=driver.admit(source)
    formal=driver.admit_formal(args.app,source)
    stage=formal['stage'];normal_cpu=formal['runtime'][CPU_ROLE]
    inputs={name:owned(stage/name).read_bytes() for name in EXPECTED}
    tag=str(time.time_ns());root=PROJECT/'evidence'/('upstream-private-death-run-'+tag)
    routes=projected_routes(inputs,stage,root)
    resumed=None
    if getattr(args,'resume_from',None) is not None:
        case,resumed=resume_case(args.resume_from,source,formal,driver,cpu=cpu,cpu_plan=cpu_plan,cpu_result=cpu_result)
    else:case=create(source,cache,project=PROJECT,tag=tag,cache_mode='empty')
    env=driver.execution_environment(source,case,formal,functional_f3=False)
    changes,allowed=driver.benchmark_changes(source,case,env,formal=formal,functional_f3=False)
    original_env=dict(env)
    if getattr(args,'vertex_on',False):env[VERTEX_FLAG]='1'
    vertex=vertex_change(source['environment'],original_env,env,enabled=getattr(args,'vertex_on',False))
    if env.get('BB_ONLINE')!='0' or env.get('BB_AUDIO')!='none' or env.get('BB_QUICKSAVE')!='0':
        raise ValueError('original offline silent quicksave-off conditions required')
    root.mkdir(mode=0o700)
    directory=case['user_root']/'world-warper'
    if resumed is None:
        directory.mkdir(mode=0o700)
        atomic(case['user_root']/'.private-world-warper',env['BB_ARM_SESSION_NONCE'].encode())
    elif list(directory.iterdir()) or (case['user_root']/'.private-world-warper').read_bytes()!=env['BB_ARM_SESSION_NONCE'].encode():
        raise ValueError('resumed private command directory or marker changed before run')
    cache_file=root/'cpu-cache'/cpu_plan['private_cpu_cache_build']['bridge_build']/'startup.bin'
    cache_control,cache_delta=cpu_cache_control(source['environment'],cache_file)
    if cache_delta:cache_file.parent.mkdir(parents=True,mode=0o700)
    private_delta=dict(BB_DEV_WORLD_WARPER_DIR=str(directory),BB_ARM_QA_PAD_FILE=str(case['pad']),**cache_delta)
    private_before={key:env.get(key) for key in private_delta}
    env.update(private_delta)
    required={key:env[key] for key in ('BB_ONLINE','BB_AUDIO','BB_QUICKSAVE','BB_PAD_FILE_ONLY',
        'BB_ARM_SESSION_NONCE','BB_DEV_WORLD_WARPER_DIR','BB_ARM_QA_PAD_FILE')}
    required['BB_ARM_CPU_CACHE']=cache_control['expected_value']
    required[VERTEX_FLAG]=env.get(VERTEX_FLAG)
    required.update(BB_RESTART_SUPERVISED='1',BB_RENDERER='metal',BB_ARM_LAUNCHER_COMPAT='1',
                    BB_GPU_PROCESS=env['BB_GPU_PROCESS'],BB_USER_DIR=str(case['user_root']))
    manifest=dict(schema=1,stage=str(stage),cpu_root=str(args.cpu_root),private_cpu=str(cpu),
        private_cpu_sha256=cpu_result['executable_sha256'],original_cpu=str(normal_cpu),
        original_cpu_sha256=formal['runtime_sha256'][CPU_ROLE],required_environment=required,
        cpu_cache_control=cache_control,vertex_change=vertex,
        user=str(case['user_root']),boot=str(case['data_root']/'out/boot-linked.bin'),
        content=str(case['data_root']/'out/content.bin'),patches=str(case['data_root']/'out/patches.bin'))
    driver.write(root/'handoff.json',manifest)
    for name,raw in routes.items():(root/name).write_bytes(raw)
    # Child code stays wholly in Shared; never make the QA process read Documents.
    child_source=Path(__file__).read_bytes()
    (root/'run_upstream_death_case.py').write_bytes(child_source)
    wrapper=('import sys,hashlib\nfrom pathlib import Path\nsys.dont_write_bytecode=True\n'
             'p=Path('+repr(str(root/'run_upstream_death_case.py'))+')\n'
             'assert hashlib.sha256(p.read_bytes()).hexdigest()=='+repr(hashlib.sha256(child_source).hexdigest())+'\n'
             'sys.path.insert(0,'+repr(str(root))+')\n'
             'from run_upstream_death_case import handoff\nhandoff('+repr(str(root/'handoff.json'))+')\n')
    (root/'handoff.py').write_text(wrapper)
    command=[source['executable'],str(root/'run_optimized.sh'),'--benchmark']
    execution=driver.execution_conditions(source,formal)
    observer=NativeObserver();started=time.monotonic();before=observer.system()
    policy=DeathPolicy(MemoryPolicy(before),driver,case,cpu,directory,args.seconds)
    report=dict(schema=1,status='running',performance_sample=False,silent=True,Cua=False,
        formal_data_modified=False,normal_package_modified=False,source_receipt_sha256=driver.sha(harness.RECEIPT),
        private_cpu=manifest,ordinary_changes=changes,ordinary_allowed=allowed,
        private_control_before=private_before,private_control_after=private_delta,
        cpu_cache_control=cache_control,vertex_change=vertex,
        projected_source_sha256=EXPECTED,projected_routes_sha256={k:hashlib.sha256(v).hexdigest() for k,v in routes.items()},
        memory_policy=MemoryPolicy.MANIFEST,settings_sha256=case['config_sha256'],native_identity=native,
        formal_native_identity=formal['identity'],resume=resumed)
    handlers={sig:signal.signal(sig,driver.stop_signal) for sig in (signal.SIGTERM,signal.SIGINT)}
    try:
        row=driver.sample(execution,case,env,command,report,root,startup_seconds=args.seconds,
            scene_seconds=args.seconds,observer=observer,policy=policy,protection_start=started)
        receipt=plain_json(root/'handoff-receipt.json') if (root/'handoff-receipt.json').exists() else None
        row=finalize_result(row,policy.check,receipt,manifest,cpu_plan['private_cpu_cache_build']['bridge_build'])
        driver.write(root/'result.json',row)
    finally:
        observer.close()
        for sig,handler in handlers.items():signal.signal(sig,handler)
    return row


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    modes=parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--plan',action='store_true');modes.add_argument('--execute',action='store_true')
    parser.add_argument('--reviewed-code',action='store_true');parser.add_argument('--app',type=Path,default=APP)
    parser.add_argument('--cpu-root',type=Path,default=CPU_ROOT);parser.add_argument('--seconds',type=int,default=1200)
    parser.add_argument('--resume-from',type=Path,help='only the reviewed pre-scene R0 header exception, with all immutable evidence rechecked')
    parser.add_argument('--vertex-on',action='store_true',help='fresh isolated case: only add the user-authorized BB_MTL_RELAX_VERTEX=1 after ordinary validation')
    args=parser.parse_args()
    if args.vertex_on and args.resume_from is not None:parser.error('--vertex-on requires a fresh case, without --resume-from')
    if args.plan:
        print(json.dumps(dict(status='plan-only-no-admission-no-game',app=str(args.app),cpu_root=str(args.cpu_root),
            private_cache_identity_required=True,original_preparation_and_mod_lifetime_preserved=True,
            routes='three exact private shell routes plus one module-local final exec proxy',
            resume_from=str(args.resume_from) if args.resume_from is not None else None,
            vertex_on=args.vertex_on,
            compile_started=False,game_started=False,performance_sample=False),indent=2))
    else:print(json.dumps(execute(args),indent=2))


if __name__=='__main__':main()
