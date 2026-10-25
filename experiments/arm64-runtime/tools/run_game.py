#!/usr/bin/env python3
"""Bounded private execution; offline default, explicit QA-only account mode."""
from pathlib import Path
import hashlib,argparse,json,time,subprocess,sys,plistlib,re,os,signal,secrets,ctypes,shutil
from restart_policy import take_request,offline_environment,clone_private_session,fresh_travel_controls
from prepare_startup import prepare
from launcher_environment import load as launcher_environment
from patch_adapter import convert as rosetta_patches
from native_observer import NativeObserver
from qa_memory_policy import MemoryPolicy
P=Path(__file__).resolve().parents[1]
class Usage(ctypes.Structure):
    _fields_=[('uuid',ctypes.c_uint8*16)]+[(name,ctypes.c_uint64) for name in ('user','system','idle_wakeups','interrupt_wakeups','pageins','wired','resident','footprint','start','exit')]
class MachTimebase(ctypes.Structure):
    _fields_=[('numer',ctypes.c_uint32),('denom',ctypes.c_uint32)]
TIMEBASE=MachTimebase()
assert ctypes.CDLL('/usr/lib/libSystem.B.dylib').mach_timebase_info(ctypes.byref(TIMEBASE))==0 and TIMEBASE.denom
libproc=ctypes.CDLL('/usr/lib/libproc.dylib',use_errno=True)
libproc.proc_pid_rusage.argtypes=[ctypes.c_int,ctypes.c_int,ctypes.c_void_p];libproc.proc_pid_rusage.restype=ctypes.c_int
def usage(lines):
    rows=[]
    for line in lines:
        fields=line.strip().split(None,3)
        if len(fields)!=4 or Path(fields[3]).name not in ('bb-probe-metal','bb-gpu','bb-game-arm64'):continue
        data=Usage()
        if libproc.proc_pid_rusage(int(fields[0]),0,ctypes.byref(data))==0:
            rows.append(dict(pid=int(fields[0]),role=Path(fields[3]).name,footprint_bytes=data.footprint,resident_bytes=data.resident,cpu_user_ns=data.user*TIMEBASE.numer//TIMEBASE.denom,cpu_system_ns=data.system*TIMEBASE.numer//TIMEBASE.denom,cpu_user_ticks=data.user,cpu_system_ticks=data.system,pageins=data.pageins))
    return rows
def system():
    pressure=int(subprocess.check_output(['sysctl','-n','kern.memorystatus_vm_pressure_level']))
    locked=plistlib.loads(subprocess.check_output(['ioreg','-n','Root','-d1','-a']))['IOConsoleLocked']
    swap=float(re.search(r'used = ([\d.]+)M',subprocess.check_output(['sysctl','vm.swapusage'],text=True))[1])
    return dict(pressure=pressure,locked=locked,swap_mib=swap)
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--launch-profile',type=Path);ap.add_argument('--gpu-candidate',type=Path);ap.add_argument('--extra-heaps-fresh',action='store_true');ap.add_argument('--extra-heaps',action='store_true');ap.add_argument('--allocation-audit',action='store_true');ap.add_argument('--dev-world-warper',action='store_true');ap.add_argument('--engine',choices=['arm64','rosetta'],default='arm64');ap.add_argument('--net-module-smoke',action='store_true');ap.add_argument('--qa-online',action='store_true');ap.add_argument('--qa-online-manual',action='store_true');ap.add_argument('--resource-hooks',action='store_true');ap.add_argument('--resource-fast-dispatch',action='store_true');ap.add_argument('--resource-leaf-dispatch',action='store_true');ap.add_argument('--lifetime-audit',action='store_true');ap.add_argument('--hardware-tso',choices=['0','1','2'],default='0');ap.add_argument('--stutter-audit',action='store_true');ap.add_argument('--coarse-audit',action='store_true');ap.add_argument('--native-observer',action='store_true');ap.add_argument('--slow-packet-audit',action='store_true');ap.add_argument('--guest-frame-audit',action='store_true');ap.add_argument('--guest-sync-audit',action='store_true');ap.add_argument('--pipeline-archive',action='store_true');ap.add_argument('--compile-audit',action='store_true');ap.add_argument('--staging-audit',action='store_true');ap.add_argument('--hot-staging-reuse',action='store_true');ap.add_argument('--packed-veneers',action='store_true');ap.add_argument('--fast-hle-lookup',action='store_true');ap.add_argument('--legacy-hle-lookup',action='store_true');ap.add_argument('--clone-private-session',type=Path);ap.add_argument('--settings-template',type=Path,default=P/'fixtures/launcher-settings.ini');ap.add_argument('--boot',type=Path,required=True);ap.add_argument('--app0',type=Path);ap.add_argument('--content-profile',type=Path);ap.add_argument('--gpu-state',type=Path);ap.add_argument('--capture-trigger',type=Path);ap.add_argument('--legacy-range-checks',action='store_true');ap.add_argument('--frame-timing',action='store_true');ap.add_argument('--texture-diag',action='store_true');ap.add_argument('--toggle-file',type=Path);ap.add_argument('--file-trace',action='store_true');ap.add_argument('--seed-user',type=Path);ap.add_argument('--pad-file',type=Path);ap.add_argument('--authored-fixture',action='store_true');ap.add_argument('--stop-before-submit',action='store_true');ap.add_argument('--memory-diag',action='store_true');ap.add_argument('--fex-mapping-audit',action='store_true');ap.add_argument('--cpu-code-map',action='store_true');ap.add_argument('--cpu-cache-control',action='store_true');ap.add_argument('--jit-reserve-mib',type=int,choices=[16,128],default=16);ap.add_argument('--cpu-cache',type=Path);ap.add_argument('--quicksave',action='store_true');ap.add_argument('--graphics',action='store_true');ap.add_argument('--modules-only',action='store_true');ap.add_argument('--seconds',type=int,default=30);a=ap.parse_args()
    from launcher_language_policy import load as load_language
    language_env=load_language(a.launch_profile) if a.launch_profile else None
    assert 0<a.seconds<=600
    if a.engine=='rosetta':assert a.clone_private_session and a.graphics and a.hardware_tso=='0','Rosetta requires fixed cloned QA and unchanged CPU policy'
    assert not(a.fast_hle_lookup and a.legacy_hle_lookup),'conflicting HLE policies'
    use_fast_hle=a.engine=='arm64' and not a.legacy_hle_lookup
    if a.resource_leaf_dispatch:assert a.resource_fast_dispatch,'leaf dispatch requires guarded dispatch'
    if a.resource_fast_dispatch:assert a.resource_hooks,'dispatch filter requires complete resource hooks'
    if a.resource_hooks:assert a.engine=='arm64','FEX resource detours are ARM-only'
    if a.fast_hle_lookup:assert a.engine=='arm64','published HLE lookup is private ARM-only'
    if a.qa_online_manual:assert a.qa_online,'manual online input requires private QA online mode'
    if a.qa_online:assert a.engine=='arm64'and a.graphics and a.clone_private_session and a.seed_user and a.pad_file and a.quicksave and not a.net_module_smoke and not a.dev_world_warper and not a.modules_only,'QA online requires private ARM normal boot, bounded file input and no development warps'
    if a.net_module_smoke:assert a.engine=='arm64' and a.graphics and a.clone_private_session,'network ABI smoke requires a cloned private ARM session'
    if a.dev_world_warper:assert a.engine=='arm64' and a.graphics and a.clone_private_session and a.seed_user and a.pad_file and not a.quicksave and not a.net_module_smoke,'travel requires a private file-only offline ARM normal-save session'
    if a.allocation_audit:assert a.dev_world_warper and a.lifetime_audit,'allocation metadata requires explicit private travel/lifetime diagnostic'
    if a.extra_heaps:assert a.resource_hooks,'extra heaps require complete original resource hooks'
    if a.extra_heaps_fresh:assert a.extra_heaps,'fresh control requires extra heaps'
    observer=NativeObserver()if a.native_observer else None
    probe_system=observer.system if observer else system
    before=probe_system();assert not before['locked'] and before['pressure']!=4,'locked or severe pressure'
    memory_policy=MemoryPolicy(before)
    running=subprocess.check_output(['ps','-axo','pid,comm'],text=True)
    assert not any(l.rstrip().endswith(('/bb-probe-metal','/bb-probe','/bb-gpu','/bb-game-arm64')) for l in running.splitlines()),'another game is running'
    identifier=str(time.time_ns());e=P/'evidence'/('game-'+identifier);e.mkdir();state=P/'state'/('game-'+identifier)
    session_nonce=secrets.token_hex(16)
    if a.clone_private_session:
        assert a.graphics and (a.quicksave or a.dev_world_warper) and a.seed_user,'QA clone requires the isolated checkpoint/travel profile'
        session_nonce=clone_private_session(a.clone_private_session,state,P/'state')
        if a.engine=='rosetta':
            # Original probe uses param.sfo INSTALL_DIR_SAVEDATA=CUSA00207; the
            # ARM prototype's private namespace is CUSA03173. Only duplicate the
            # new QA clone's namespace, preserving all save/checkpoint bytes.
            mappings=[]
            for parent,old,new in [('savedata/1','CUSA03173','CUSA00207'),('savedata/1','CUSA03173.memory','CUSA00207.memory'),('quicksaves','CUSA03173','CUSA00207')]:
                src=state/parent/old;dst=state/parent/new
                if src.exists():
                    assert not dst.exists() and not src.is_symlink()
                    if src.is_dir():shutil.copytree(src,dst)
                    else:shutil.copy2(src,dst)
                    mappings.append((parent,old,new))
            (e/'qa-save-namespace.json').write_text(json.dumps({'only_new_private_clone':True,'mappings':mappings,'game_title':'CUSA03173','sfo_save_namespace':'CUSA00207'},indent=2)+'\n')
        # Compile the cloned session's own settings, preserving the original evidence.
        template=P/'fixtures'/f'cloned-session-{identifier}.ini'
        assert (state/'bbport.ini').is_file() and not (state/'bbport.ini').is_symlink()
        template.write_bytes((state/'bbport.ini').read_bytes());a.settings_template=template
    cmd=[str(P/'build/fex/Bin/bb-game-arm64'),'--boot',str(a.boot.resolve()),'--state',str(state)]
    if a.graphics:
        startup=e/'startup.bin';prepare(a.boot,startup,a.settings_template,a.app0,online=a.qa_online,language_environment=language_env);cmd+=['--settings-template',str(a.settings_template.resolve())];cmd+=['--startup-patches',str(startup)]
    if a.engine=='rosetta':
        patch=e/'rosetta-patches.bin';rosetta_patches(startup,patch)
        cmd=[str(P/'rosetta-reference/bin/bb-probe-metal'),str(a.boot.resolve()),'--patches',str(patch),'--user',str(state),'--timeout','0']
    if a.seed_user and a.engine=='arm64':cmd+=['--seed-user',str(a.seed_user.resolve())]
    if a.app0:cmd+=['--app0',str(a.app0.resolve())]
    if a.content_profile:cmd+=['--content-profile',str(a.content_profile.resolve())]
    if a.modules_only:cmd+=['--modules-only']
    if a.graphics and a.engine=='arm64':cmd+=['--graphics']
    if a.authored_fixture:cmd+=['--authored-fixture']
    env={'PATH':'/usr/bin:/bin:/usr/sbin:/sbin','HOME':str(P/'state'),'TMPDIR':str(P/'state')+'/','BB_ONLINE':'0','BB_QUICKSAVE':'0','BB_CONFIG':str(state/'bbport.ini'),'BB_UPSCALER':'off','BB_LANGUAGE':'11','BB_SKIP_NETWORK_CHOICE':'1','BB_VBLANK_HZ':'480'}
    if language_env:env.update(language_env)
    gpu=a.gpu_state.resolve() if a.gpu_state else P/'state'/('gpu-'+identifier)
    assert gpu.parent==P/'state','GPU cache must stay directly inside private state'
    gpu.mkdir(mode=0o700,exist_ok=a.gpu_state is not None)
    shared_environment=launcher_environment()
    if a.pipeline_archive:
        # Only the explicitly selected Shared QA cache; normal private controls stay OFF.
        shared_environment.update(BB_MTL_PERSIST_WARM_ARCHIVE='1',BB_MTL_RELEASE_WARM_ARCHIVE='1',BB_MTL_COMPILE_AUDIT='1')
    env.update(shared_environment)
    if a.compile_audit:env['BB_MTL_COMPILE_AUDIT']='1'
    if a.staging_audit:env['BB_MTL_STAGING_AUDIT']='1'
    if a.hot_staging_reuse:env['BB_MTL_HOT_STAGING_REUSE']='1'
    (e/'gpu-experimental-policy.json').write_text(json.dumps(dict(hot_staging_reuse=a.hot_staging_reuse,cache_capacity_unchanged=True),indent=2)+'\n')
    if a.staging_audit or a.capture_trigger:
        (e/'gpu-diagnostic-policy.json').write_text(json.dumps(dict(staging_audit=a.staging_audit,native_frame_capture=bool(a.capture_trigger),performance_accepted=False,quality_and_cache_budgets_unchanged=True),indent=2)+'\n')
    if a.allocation_audit:env['BB_ARM_ALLOCATION_AUDIT']='1'
    (e/'launcher-environment.json').write_text(json.dumps({'profile':'shared launcher, V1 Safe math, selected private archive experiment' if a.pipeline_archive else 'shared launcher, original V1 Safe math, archive residency disabled in both private engines','environment':shared_environment},indent=2)+'\n')
    env.update(BB_NATIVE_GPU='1',ROSETTA_ADVERTISE_AVX='1',BB_TEXTURE_RELEASED_STORAGE='0')
    env.update(BB_USER_DIR=str(state),BB_GPU_USER_DIR=str(gpu),BB_DATA_DIR=str(state),BB_RENDERER='metal',BB_PC_MODEL='1',BB_GUEST_IN_PLACE='1',BB_LAYER_MEMORY='1',BB_GUEST_MEMORY='host',BB_GUEST_HOOKS='0',BB_GPU_PROCESS=str(P/'backend/bbport/bin/Bloodborne Metal.app/Contents/MacOS/bb-gpu'))
    from gpu_candidate_identity import select_for_isolated_run
    candidate_identity=select_for_isolated_run(a.gpu_candidate,engine=a.engine,graphics=a.graphics,private_root=P)
    if candidate_identity:
        env['BB_GPU_PROCESS']=candidate_identity['target']
        (e/'gpu-candidate-identity.json').write_text(json.dumps(candidate_identity,indent=2)+'\n')
    if a.resource_hooks:env.update(BB_ARM_RESOURCE_HOOKS='1',BB_GUEST_HOOKS='1',BB_GPU_LIFETIME='1')
    if a.extra_heaps:env['BB_ARM_EXTRA_HEAPS']='1'
    if a.extra_heaps_fresh:env['BB_ARM_EXTRA_HEAPS_FRESH']='1'
    if a.resource_fast_dispatch:env['BB_ARM_RESOURCE_FAST']='1'
    if a.resource_leaf_dispatch:env['BB_ARM_RESOURCE_LEAF']='1'
    if a.pad_file:
        pad=a.pad_file.resolve();assert pad.is_file() and pad.is_relative_to(P/'state'),'pad control must stay in isolated state'
        env['BB_PAD_FILE']=str(pad);env['BB_PAD_FILE_ONLY']='1'
    # Overnight QA is always silent. Scripted benchmarks retain SDL's real
    # device/queue/cadence and set gain=0; interactive diagnostics use the timer
    # sink until both CPU engines expose an independently gated stream mute.
    env['BB_BENCHMARK_MUTE']='1'
    audio_mode='zero-gain SDL stream' if a.pad_file else 'silent timer sink (not an audio/performance comparison)'
    if not a.pad_file:env['BB_AUDIO']='none'
    (e/'audio-policy.json').write_text(json.dumps({'silent':True,'mode':audio_mode,'formal_settings_modified':False},indent=2)+'\n')
    if a.toggle_file:
        toggle=a.toggle_file.resolve();assert toggle.is_file() and toggle.is_relative_to(P/'state'),'diagnostic toggle must stay in isolated state'
        env['BB_TOGGLE_FILE']=str(toggle)
    if a.capture_trigger:
        trigger=a.capture_trigger.resolve();assert trigger.is_relative_to(P/'state'),'capture trigger must stay in isolated state'
        env.update(BB_CAPTURE_NATIVE='1',BB_CAPTURE_TRIGGER=str(trigger),BB_CAPTURE_DIR=str(e))
    if a.legacy_range_checks:env['BB_ARM_RANGE_FAST']='0'
    if a.frame_timing:env.update(BB_FRAME_LOG_ONLY='1',BB_FRAME_TIMING='1',BB_FRAME_LOG=str(e/'frames.csv'))
    if a.net_module_smoke:
        env.update(BB_ONLINE='1',BB_ARM_ALLOW_NETWORK='1',BB_NET_BACKEND='thd',BB_NET_MODULE=str(P/'build/bbnet-arm64/libbbnet.dylib'))
        (e/'network-policy.json').write_text(json.dumps({'mode':'module ABI smoke, no credentials, default offline game','online_login_verified':False},indent=2)+'\n')
    if a.qa_online:
        from qa_online_policy import load_private_environment,prepare_private_routes
        env.update(load_private_environment());env.update(prepare_private_routes(state,env))
        (e/'network-policy.json').write_text(json.dumps(dict(mode='authenticated private QA account, normal save boot, no initial checkpoint restore',credential_source='exact existing QA Keychain service; noninteractive',credential_values_logged=False,formal_account_queried=False,online_login_verified=False),indent=2)+'\n')
    if a.packed_veneers:
        assert a.engine=='arm64','packed guest veneers are ARM-only'
        env['BB_ARM_PACKED_VENEERS']='1'
    if a.engine=='arm64':env['BB_ARM_HLE_FAST']='1' if use_fast_hle else '0'
    (e/'cpu-policy.json').write_text(json.dumps({'engine':a.engine,'packed_veneers':a.packed_veneers,'fast_hle_lookup':use_fast_hle,'resource_hooks':a.resource_hooks,'extra_heaps':a.extra_heaps,'extra_heaps_fresh':a.extra_heaps_fresh,'resource_fast_dispatch':a.resource_fast_dispatch,'resource_leaf_dispatch':a.resource_leaf_dispatch,'hardware_tso_requested':a.hardware_tso,'software_tso_not_bypassed':True},indent=2)+'\n')
    env['BB_ARM_HW_TSO']=a.hardware_tso
    assert not a.guest_sync_audit or a.guest_frame_audit,'condition tracing requires selected guest frame audit'
    if a.guest_sync_audit:env['BB_GUEST_SYNC_AUDIT']='1'
    assert not a.guest_frame_audit or (a.engine=='arm64' and a.coarse_audit and a.native_observer),'guest frame tracing requires ARM coarse/native diagnostics'
    if a.guest_frame_audit:
        env['BB_GUEST_FRAME_AUDIT']='1'
        artifacts=[P/'build/fex/Bin/bb-game-arm64',P/'backend/bbport/bin/gpu-metal-arm64/libbbgpu.dylib']
        (e/'guest-frame-artifacts.json').write_text(json.dumps({str(f):hashlib.file_digest(f.open('rb'),'sha256').hexdigest()for f in artifacts},indent=2)+'\n')
    assert not a.slow_packet_audit or (a.coarse_audit and a.native_observer),'slow packets require explicit coarse/native observation'
    if a.slow_packet_audit:
        env['BB_SLOW_PACKET_AUDIT']='1'
        gpu_library=P/'backend/bbport/bin/gpu-metal-arm64/libbbgpu.dylib'
        (e/'packet-audit-artifacts.json').write_text(json.dumps({'gpu_library':str(gpu_library),'sha256':hashlib.file_digest(gpu_library.open('rb'),'sha256').hexdigest()},indent=2)+'\n')
    assert not a.coarse_audit or a.stutter_audit,'coarse audit requires trace enabled'
    if a.coarse_audit:env['BB_STUTTER_COARSE']='1'
    if a.stutter_audit:
        gate=e/'stutter-control.bin';gate.write_bytes(bytes(4096))
        env['BB_STUTTER_AUDIT']=str(e/'stutter');env['BB_STUTTER_GATE']=str(gate)
    if a.lifetime_audit:
        assert a.resource_hooks and a.engine=='arm64','lifetime metadata requires explicit ARM resource hooks'
        audit_dir=e/'texture-lifetimes';audit_dir.mkdir();env['BB_TEXTURE_AUDIT_DIR']=str(audit_dir)
        if a.extra_heaps:env['BB_ARM_EXTRA_HEAP_DIAG']='1'
    if a.texture_diag:env['BB_ARM_TEXTURE_DIAG']='1'
    if a.stop_before_submit:env['BB_ARM_STOP_AT_SUBMIT']='1'
    if a.memory_diag:env['BB_ARM_MEM_DIAG']='1'
    assert not a.fex_mapping_audit or a.engine=='arm64','FEX mapping records require ARM64'
    if a.fex_mapping_audit:env['BB_FEX_MAPPING_AUDIT']='1'
    assert not a.cpu_code_map or a.engine=='arm64','CPU code maps require ARM64'
    if a.cpu_code_map:env['BB_ARM_CPU_CODEMAP']='1'
    if a.jit_reserve_mib!=16:
        assert a.engine=='arm64','private reserve experiment is ARM-only'
        env['BB_ARM_JIT_RESERVE_MIB']=str(a.jit_reserve_mib)
    if a.cpu_cache_control or a.cpu_cache:
        assert a.engine=='arm64','experimental CPU cache is ARM64-only'
        env['BB_ARM_CPU_CACHE_IDENTITY']='1'
    if a.cpu_cache:
        cache=a.cpu_cache.resolve();assert cache.is_file()and cache.is_relative_to(P/'evidence'),'CPU cache must be an existing private candidate'
        env['BB_ARM_CPU_CACHE']=str(cache)
    if a.file_trace:env['BB_FILE_TRACE']='1'
    if a.quicksave:
        assert a.graphics and a.seed_user, 'checkpoints require the private real-game QA profile'
        env.update(BB_QUICKSAVE='1',BB_QUICKSAVE_HELPER=str(P/'vendor/blood_echoes/quicksave.py'),BB_QUICKSAVE_PYTHON=sys.executable,BB_QUICKSAVE_SCANCODE='59',BB_QUICKLOAD_SCANCODE='60',BB_QUICKSAVE_STATUS=str(state/'quicksave-status.txt'))
    env['BB_ARM_SESSION_NONCE']=session_nonce
    if a.dev_world_warper:
        travel=fresh_travel_controls(state,P/'state')
        (state/'.private-world-warper').write_text(session_nonce)
        env.update(BB_DEV_WORLD_WARPER_DIR=str(travel),BB_ARM_AUTO_CONTINUE='1')
    if a.clone_private_session:
        if a.engine=='arm64':cmd+=['--resume-state']
        if a.net_module_smoke or a.dev_world_warper or(a.qa_online and not a.qa_online_manual):env['BB_ARM_AUTO_CONTINUE']='1'
        elif not a.qa_online:env['BB_QUICKLOAD_PENDING']='1'
    env['BB_ARM_RESTART_REQUEST']=str(state/'.arm-restart-request')
    env['BB_ARM_RESTART_NONCE']=secrets.token_hex(16)
    if a.pipeline_archive:
        # Once before process creation, only for the explicit archive experiment.
        # The selected library SHA binds the actual compiled translator/rewrites.
        # Native PsoCache adds its actual GPU registry/name and OS build itself.
        from warm_archive_manifest import prepare_startup_archive_policy
        option_keys=('BB_MTL_', 'BB_LAYER_', 'BB_PC_', 'BB_GUEST_')
        options={k:v for k,v in env.items()if k.startswith(option_keys) and not any(x in k for x in ('AUDIT','LOG','CAPTURE','FILE','NONCE','RESTART'))}
        inputs={'boot':a.boot,'startup_patches':startup,'settings':a.settings_template} if a.graphics else {}
        if a.content_profile: inputs['content_profile']=a.content_profile
        for key,value in env.items():
            if key.startswith('BB_MTL_') and key.endswith('_FILE') and Path(value).is_file():
                inputs['shader_control_'+key]=Path(value)
        if a.engine=='rosetta': inputs['rosetta_patches']=patch
        updates,archive_policy=prepare_startup_archive_policy(enabled=a.graphics,engine=a.engine,
            selected_executable=Path(env['BB_GPU_PROCESS']),backend_root=P/'backend',
            game_artifacts=inputs,options=options,
            profile={'engine':a.engine,'namespace':'arm64-srt1' if a.engine=='arm64' else 'rosetta','profile_abi':'SPV10-meta7-key5-msl3'})
        env.update(updates)
        (e/'warm-archive-compatibility.json').write_text(json.dumps(archive_policy,indent=2)+'\n')
    samples=[];generations=[];stop=None;start=time.monotonic();cleanup_warnings=[]
    def stop_owned(proc,sig):
        # Positive PIDs only. Darwin may refuse a whole process group after its
        # leader exits; that must not hide the original runtime exit/result.
        table={}
        for line in subprocess.check_output(['ps','-axo','pid,ppid,comm'],text=True).splitlines()[1:]:
            fields=line.strip().split(None,2)
            if len(fields)==3:table[int(fields[0])]=(int(fields[1]),fields[2])
        observed={proc.pid}
        for sample in samples:
            for line in sample['processes']:
                fields=line.strip().split(None,3)
                if len(fields)==4 and fields[3].startswith(str(P)+'/'):observed.add(int(fields[0]))
        for pid in observed:
            if pid not in table or not table[pid][1].startswith(str(P)+'/'):continue
            try:os.kill(pid,sig)
            except ProcessLookupError:pass
            except PermissionError:cleanup_warnings.append(f'cannot signal owned pid {pid}')
    with (e/'runtime.log').open('w') as log:
        proc=subprocess.Popen(cmd,env=env,stdout=log,stderr=log,start_new_session=True)
        try:
            while True:
                if proc.poll() is not None:
                    if proc.returncode!=85:break
                    # The native host waits for and reaps its backend before exit85.
                    # Exit85 without the per-generation request is a failure, never a relaunch.
                    try:
                        loading=take_request(state/'.arm-restart-request',env['BB_ARM_RESTART_NONCE'])
                        assert len(generations)<4,'isolated restart limit'
                        env=offline_environment(env,loading)
                        check=probe_system()
                        assert not check['locked'] and check['pressure']!=4,'protective stop before restart'
                        assert memory_policy.observe(time.monotonic()-start,check) is None,'memory protection before restart'
                        assert time.monotonic()-start<a.seconds,'time budget before restart'
                        remaining=subprocess.check_output(['ps','-axo','pid,comm'],text=True)
                        assert not any(l.rstrip().endswith(('/bb-gpu','/bb-game-arm64')) for l in remaining.splitlines()),'old backend still alive'
                    except (OSError,ValueError,AssertionError) as error:
                        stop='restart refused: '+str(error);break
                    generation=len(generations)+1
                    template=P/'fixtures'/f'resume-{identifier}-{generation}.ini'
                    assert (state/'bbport.ini').is_file() and not (state/'bbport.ini').is_symlink()
                    template.write_bytes((state/'bbport.ini').read_bytes())
                    startup=e/f'startup-g{generation}.bin';prepare(a.boot,startup,template,a.app0,language_environment=language_env)
                    cmd[cmd.index('--settings-template')+1]=str(template)
                    cmd[cmd.index('--startup-patches')+1]=str(startup)
                    if '--resume-state' not in cmd:cmd+=['--resume-state']
                    env['BB_ARM_RESTART_NONCE']=secrets.token_hex(16)
                    if a.frame_timing:env['BB_FRAME_LOG']=str(e/f'frames-g{generation}.csv')
                    generations.append(dict(generation=generation,previous_pid=proc.pid,loading_checkpoint=loading,seconds=time.monotonic()-start))
                    log.flush();proc=subprocess.Popen(cmd,env=env,stdout=log,stderr=log,start_new_session=True)
                    generations[-1]['pid']=proc.pid
                    continue
                observation_start=time.clock_gettime_ns(time.CLOCK_MONOTONIC) if (a.coarse_audit or a.native_observer) else 0
                sample=probe_system();sample['seconds']=time.monotonic()-start;sample['generation']=len(generations)
                system_end=time.clock_gettime_ns(time.CLOCK_MONOTONIC) if (a.coarse_audit or a.native_observer) else 0
                if observer:
                    process_rows=observer.processes(proc.pid)
                    sample['processes']=[f'{pid} {parent} 0 {path}'for pid,parent,path in process_rows]
                else:
                    ps=subprocess.check_output(['ps','-axo','pid,ppid,rss,comm'],text=True)
                    sample['processes']=[l for l in ps.splitlines()[1:] if str(proc.pid) in l.split()[:2]]
                process_end=time.clock_gettime_ns(time.CLOCK_MONOTONIC) if (a.coarse_audit or a.native_observer) else 0
                sample['usage']=usage(sample['processes'])
                if observer:
                    resident={u['pid']:u['resident_bytes']//1024 for u in sample['usage']}
                    sample['processes']=[f'{pid} {parent} {resident.get(pid,0)} {path}'for pid,parent,path in process_rows]
                if a.coarse_audit or a.native_observer:
                    observation_end=time.clock_gettime_ns(time.CLOCK_MONOTONIC)
                    sample['observation']=dict(start_mono_ns=observation_start,end_mono_ns=observation_end,system_ns=system_end-observation_start,process_snapshot_ns=process_end-system_end,usage_ns=observation_end-process_end)
                samples.append(sample)
                finish=state/'.private-test-finished'
                if (a.dev_world_warper or a.qa_online) and finish.is_file() and not finish.is_symlink() and finish.read_text()==session_nonce:stop='private test completed'
                memory_stop=memory_policy.observe(sample['seconds'],sample)
                if memory_stop:stop=memory_stop
                elif sample['seconds']>a.seconds:stop='time budget'
                if stop:
                    stop_owned(proc,signal.SIGTERM)
                    try:proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:stop_owned(proc,signal.SIGKILL);proc.wait()
                    break
                time.sleep(.2)
        except Exception as error:
            stop='supervisor observation failed: '+type(error).__name__+': '+str(error)
            raise
        finally:
            if proc.poll() is None:stop_owned(proc,signal.SIGKILL);proc.wait()
            try:stop_owned(proc,signal.SIGTERM)
            except (ProcessLookupError,PermissionError):pass
            result=dict(memory_policy=MemoryPolicy.MANIFEST,observer='native SDK APIs' if observer else 'legacy subprocess commands',cpu_time_schema_version=2,mach_timebase={'numer':TIMEBASE.numer,'denom':TIMEBASE.denom},engine=a.engine,exit=proc.returncode,generations=generations,cleanup_warnings=cleanup_warnings,stopped=stop,seconds=time.monotonic()-start,state=str(state),samples=samples,command=cmd)
            (e/'result.json').write_text(json.dumps(result,indent=2))
            if observer:observer.close()
    print(json.dumps({k:v for k,v in result.items() if k not in ['samples','command']},indent=2));print('Evidence:',e);lines=(e/'runtime.log').read_text().splitlines();print('\n'.join(l for l in lines if any(k in l for k in ['ARM ','GPU:','NativeGPU','UNIMPLEMENTED','Error','error','failed']))[-6500:])
if __name__=='__main__':main()
