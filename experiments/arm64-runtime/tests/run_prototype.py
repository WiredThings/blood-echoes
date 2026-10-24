#!/usr/bin/env python3
"""Authored fixtures only. Every runtime run gets fresh state. No production data."""
import argparse, hashlib, json, pathlib, plistlib, re, subprocess, time, sys
from game_fixture import make as game_fixture

ROOT = pathlib.Path(__file__).resolve().parents[1]

def fixture(metal=False, unknown=False):
    import struct
    code=bytearray.fromhex('48 83 ec 08'); branches=[]; reloc=[]; names=[]
    def emit(text): code.extend(bytes.fromhex(text))
    def call(name):
        index=len(names); names.append(name); emit('ff 15'); patch=len(code)
        code.extend(b'\0'*4); reloc.append((patch,index))
    def guard(): emit('75 00'); branches.append(len(code)-1)
    emit('bf 13 00 00 00 be 17 00 00 00')
    call('unknown.import' if unknown else 'bb.prototype.add_pair')
    emit('48 83 f8 2a'); guard()
    for seg in ['64','65']:
        emit(seg+' 48 8b 04 25 08 00 00 00 48 ba 21 43 ba dc cd ab 34 12 48 39 d0'); guard()
    call('bb.prototype.offline'); emit('48 83 f8 01'); guard()
    if metal:
        call('bb.prototype.gpu_idle'); emit('48 83 f8 01'); guard()
    emit('b8 2a 00 00 00 48 83 c4 08 c3'); fail=len(code)
    emit('31 c0 48 83 c4 08 c3')
    for patch in branches:
        delta=fail-patch-1
        assert 0<=delta<=127
        code[patch]=delta
    while len(code)%8: code.append(0x90)
    records=[]
    for patch,index in reloc:
        target=len(code); code.extend(b'\0'*8)
        struct.pack_into('<i',code,patch,target-patch-4)
        records.append((target,1,index,0))
    code.extend(b'\0'*(16384-len(code)))
    data=b'BBPROBE2'+struct.pack('<6Q',16384,0,1,len(records),len(names),0)
    data+=struct.pack('<3Q',0,16384,5)
    data+=b''.join(n.encode().ljust(128,b'\0') for n in names)
    data+=b''.join(struct.pack('<4Q',*r) for r in records)+code
    return data

def preflight():
    locked=plistlib.loads(subprocess.check_output(['ioreg','-n','Root','-d1','-a']))['IOConsoleLocked']
    pressure=int(subprocess.check_output(['sysctl','-n','kern.memorystatus_vm_pressure_level']))
    swap_text=subprocess.check_output(['sysctl','vm.swapusage'],text=True)
    swap=float(re.search(r'used = ([\d.]+)M',swap_text)[1])
    return dict(locked=locked,pressure=pressure,swap_mib=swap)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--metal',type=pathlib.Path);ap.add_argument('--packed-veneers',action='store_true');ap.add_argument('--fast-hle-lookup',action='store_true'); args=ap.parse_args()
    (ROOT/'fixtures').mkdir(exist_ok=True); (ROOT/'state').mkdir(exist_ok=True)
    evidence=ROOT/'evidence'/f'suite-{time.time_ns()}'; evidence.mkdir()
    for name,data in [('cpu',fixture()),('metal',fixture(True)),('unknown',fixture(unknown=True))]:
        (ROOT/'fixtures'/f'{name}.bbprobe').write_bytes(data)
    (ROOT/'fixtures/modules-tls.bbprobe').write_bytes(game_fixture())
    for mode in ['dialogs','extra-heaps','travel-entry','bad-timespec','bad-file-buffer','bad-callback','long-string','heap-bridge','gpu-clock','gpu-clock-wrong-role','opaque-callbacks','bad-netctl-callback','ajm-builder','bad-ajm-builder','vma-valid','vma-readonly','vma-freed','thread-churn','net-workers']:(ROOT/'fixtures'/f'{mode}.bbprobe').write_bytes(game_fixture(mode))
    binary=ROOT/'build/fex/Bin'; result=[]
    private_gpu=ROOT/'state'/f'suite-gpu-{time.time_ns()}';private_gpu.mkdir(mode=0o700)
    env=dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin',HOME=str(ROOT/'state'),TMPDIR=str(ROOT/'state')+'/',BB_GPU_USER_DIR=str(private_gpu),BB_ONLINE='0',BB_QUICKSAVE='0',BB_UPSCALER='off',BB_AUDIO='none')
    if args.packed_veneers:env['BB_ARM_PACKED_VENEERS']='1'
    if args.fast_hle_lookup:env['BB_ARM_HLE_FAST']='1'
    def run(name,command,expected=0,monitored=False):
        samples=[]; before=preflight() if monitored else None
        if monitored and (before['locked'] or before['pressure']==4):
            raise RuntimeError('locked or critical: Metal run refused')
        started=time.monotonic(); proc=None
        with (evidence/f'{name}.log').open('w') as log:
            proc=subprocess.Popen(list(map(str,command)),stdout=log,stderr=log,env=env,cwd=ROOT)
            try:
                while proc.poll() is None:
                    if time.monotonic()-started>20: raise RuntimeError('20 second timeout')
                    if monitored:
                        sample=preflight(); sample['seconds']=time.monotonic()-started
                        ps=subprocess.check_output(['ps','-axo','pid,ppid,rss,comm'],text=True)
                        sample['processes']=[l for l in ps.splitlines()[1:] if str(proc.pid) in l.split()[:2]]
                        samples.append(sample)
                        if sample['locked'] or sample['pressure']==4: raise RuntimeError('protective stop')
                        if sample['swap_mib']-before['swap_mib']>1024: raise RuntimeError('unexpected >1GiB swap growth in tiny test')
                    time.sleep(.1)
                entry=dict(test=name,exit=proc.returncode,expected=expected,seconds=time.monotonic()-started,samples=samples)
                result.append(entry)
                assert proc.returncode==expected, f'{name}: see {evidence/name}.log'
            finally:
                if proc.poll() is None:
                    # Identify only our direct child before terminating the parent.
                    ps=subprocess.check_output(['ps','-axo','pid,ppid'],text=True)
                    for line in ps.splitlines()[1:]:
                        pid,parent=map(int,line.split())
                        if parent==proc.pid: subprocess.run(['kill','-TERM',str(pid)],check=False)
                    proc.terminate()
                    try:proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:proc.kill();proc.wait()
                (evidence/'results.json').write_text(json.dumps(result,indent=2))
    run('original-dialog-macro-adapters',[binary/'bb-game-arm64','--authored-fixture','--boot',ROOT/'fixtures/dialogs.bbprobe','--state',ROOT/'state'/f'dialog-suite-{time.time_ns()}'])
    run('live-metadata-default-capacity',[binary/'test_live_metadata'])
    run('live-metadata-private-history-and-overflow',[binary/'test_live_metadata','extra'])
    run('extra-heap-plan-and-profile-rejections',[binary/'test_extra_heaps'])
    env.update(BB_ARM_EXTRA_HEAPS='1',BB_GPU_LIFETIME='1')
    run('extra-heap-real-fex-allocate-free-failure-cpu-aba',[binary/'bb-game-arm64','--authored-fixture','--boot',ROOT/'fixtures/extra-heaps.bbprobe','--state',ROOT/'state'/f'extra-heap-suite-{time.time_ns()}'])
    env.pop('BB_ARM_EXTRA_HEAPS');env.pop('BB_GPU_LIFETIME')
    run('resource-inspection-request-validation',[binary/'test_resource_query'])
    run('memory-physical-alias-readonly-inspection',[binary/'test_memory_inspection'])
    run('travel-unload-load-state-and-rejections',[binary/'test_arm_travel_state'])
    run('travel-detour-plan-and-rejections',[binary/'test_arm_travel_hooks'])
    env['BB_ARM_TRAVEL_FIXTURE']='1'
    run('travel-real-fex-seven-args-vector-nested-call',[binary/'bb-game-arm64','--authored-fixture','--boot',ROOT/'fixtures/travel-entry.bbprobe','--state',ROOT/'state'/f'travel-suite-{time.time_ns()}'])
    env['BB_ARM_ALLOCATION_AUDIT_FIXTURE']='1'
    run('texture-allocation-inline-full-preservation',[binary/'bb-game-arm64','--authored-fixture','--boot',ROOT/'fixtures/travel-entry.bbprobe','--state',ROOT/'state'/f'allocation-probe-suite-{time.time_ns()}'])
    env.pop('BB_ARM_ALLOCATION_AUDIT_FIXTURE')
    env.pop('BB_ARM_TRAVEL_FIXTURE')
    run('allocation-probe-original-bounds-and-overlap-rejection',[binary/'test_allocation_probe'])
    run('resource-startup-detour-plan-and-rejections',[binary/'test_guest_resource_detours'])
    run('resource-lifecycle-real-fex-and-fail-closed',[sys.executable,ROOT/'tests/run_resource_hooks.py']+([] if args.fast_hle_lookup else ['--legacy-hle-lookup']))
    run('resource-guest-dispatch-boundaries-and-small-alignment',[sys.executable,ROOT/'tests/run_resource_hooks.py','--fast-dispatch']+([] if args.fast_hle_lookup else ['--legacy-hle-lookup']))
    run('resource-bounded-timing-all-kinds',[sys.executable,ROOT/'tests/run_resource_hooks.py','--fast-dispatch','--audit']+([] if args.fast_hle_lookup else ['--legacy-hle-lookup']))
    run('resource-leaf-profile-digest',[binary/'test_resource_leaf_profile'])
    run('resource-leaf-original-once-gpu-observed-cpu-bypassed',[sys.executable,ROOT/'tests/run_resource_hooks.py','--leaf-dispatch']+([] if args.fast_hle_lookup else ['--legacy-hle-lookup']))
    run('native-network-fex-tls-and-seven-argument-callbacks',[binary/'bb-game-arm64','--authored-fixture','--boot',ROOT/'fixtures/net-workers.bbprobe','--state',ROOT/'state'/f'net-worker-suite-{time.time_ns()}'])
    run('arm64-bbnet-fex-responses-and-reject-bad-pointers',[sys.executable,ROOT/'tests/run_net_module.py']+(['--fast-hle-lookup'] if args.fast_hle_lookup else []))
    run('native-network-worker-lifecycle',[binary/'test_native_net_threads'])
    run('network-actual-thd-pointer-policies',[binary/'test_bbnet_thd_pointers'])
    run('network-canonical-callback-token-policies',[binary/'test_bbnet_callback_tokens'])
    run('network-full-byte-ranges',[binary/'test_bbnet_byte_ranges'])
    run('network-minimum-byte-range-red-control',[binary/'test_bbnet_byte_ranges_original'],expected=1)
    run('bounded-stutter-audit-concurrency',[binary/'test_stutter_audit'])
    run('coarse-stutter-audit-excludes-hot-path-clocks',[binary/'test_stutter_audit','--coarse'])
    run('native-observer-parity-pids-and-fail-closed',[sys.executable,ROOT/'tests/test_native_observer.py'])
    run('sparse-packet-clock-cost-and-cpu-bounds',[binary/'test_sparse_packet'])
    run('packet-identity-bounds-and-periodic-coverage',[binary/'test_packet_metadata'])
    run('guest-submit-selected-calls-and-rate-bounds',[binary/'test_guest_frame'])
    run('guest-submit-disabled-original-result',[binary/'test_guest_frame','--disabled'])
    run('guest-condition-slot-lifecycle-and-real-wake',[binary/'test_guest_sync'])
    run('guest-condition-disabled-original-calls',[binary/'test_guest_sync','--disabled'])
    run('producer-wait-policy-bounds-and-ABA',[binary/'test_producer_wait_policy'])
    run('producer-wait-real-notification',[binary/'test_guest_sync','--producer'])
    run('fex-mapping-audit-disabled-original-mapping',[binary/'test_fex_map_audit'])
    run('fex-mapping-audit-events-bounds-and-limit',[binary/'test_fex_map_audit','enabled'])
    run('static-code-file-identity-and-exclusions',[binary/'test_static_code_files',evidence/'static-code-fixture'])
    run('pmr-destruction-returns-owned-pages',[binary/'test_pmr_lifetime'])
    run('pmr-cleared-links-release-and-repopulate',[binary/'test_pmr_lifetime','clear'])
    run('async-channel-contention-and-audit',[binary/'test_channel_audit'])
    run('guest-sleep-request-versus-elapsed',[binary/'test_sleep_audit'])
    run('hle-published-lookup-concurrent-registration-lifetime',[binary/'test_hle_published_lookup'])
    for mode in ('legacy','packed'):
        run('hle-veneer-native-pages-'+mode,[binary/'test_hle_veneer_pages',mode])
        run('hle-veneer-actual-fex-typed-and-late-'+mode,[binary/'test_hle_veneer_execution',mode])
    run('network-header-string-ownership-and-sso',[binary/'test_guest_header_string'])
    run('arm64-bbnet-real-http-header-and-body',[sys.executable,ROOT/'tests/run_http_roundtrip.py']+(['--fast-hle-lookup'] if args.fast_hle_lookup else []))
    run('hardware-tso-thread-admission-and-fallback',[binary/'test_hardware_tso'])
    for repeat in range(3):
        run(f'concurrent-512-guest-thread-churn-{repeat}',[binary/'bb-game-arm64','--authored-fixture','--boot',ROOT/'fixtures/thread-churn.bbprobe','--state',ROOT/'state'/f'churn-suite-{time.time_ns()}'])
    run('retired-sibling-bootstrap-and-own-permissions',[binary/'test_thread_bootstrap'])
    run('host-mapping-query-port-lifetime',[binary/'test_thread_bootstrap','ports'])
    run('private-session-resume-guards',[binary/'test_session_state'])
    run('native-restart-protocol-and-checkpoint-transactions',[sys.executable,ROOT/'tests/test_restart_policy.py'])
    run('immutable-range-equivalence-and-concurrency',[binary/'test_static_range_index'])
    run('published-ranges-revoke-and-concurrency',[binary/'test_published_ranges'])
    for legacy in [False,True]:
        if legacy:env['BB_ARM_RANGE_FAST']='0'
        for mode in ['vma-valid','vma-readonly','vma-freed']:
            run(('legacy-' if legacy else 'indexed-')+mode,[binary/'bb-game-arm64','--boot',ROOT/'fixtures'/f'{mode}.bbprobe','--state',ROOT/'state'/f'{mode}-{time.time_ns()}'],0 if mode=='vma-valid' else 74)
        env.pop('BB_ARM_RANGE_FAST',None)
    run('native-host-page-original-red',[binary/'test_host_page_protection_original'],7)
    run('native-host-page-owners-and-boundaries',[binary/'test_host_page_protection'])
    for mode in ['valid','mismatch','bounds','overlap','truncated','many','excess']:
        run('startup-patch-'+mode,[binary/'test_startup_patches',mode])
    run('darwin-writer-handoff-original-red',[binary/'test_writer_handoff_original'],7)
    run('darwin-writer-handoff',[binary/'test_writer_handoff'])
    run('darwin-real-protection-vs-alignment',[binary/'test_darwin_fault'])
    run('qa-seed-outside-fixed-root-refused',[binary/'bb-game-arm64','--boot',ROOT/'fixtures/modules-tls.bbprobe','--seed-user',ROOT/'state','--state',ROOT/'state'/f'bad-seed-{time.time_ns()}'],1)
    run('ajm-readonly-input-return-address',[binary/'bb-game-arm64','--boot',ROOT/'fixtures/ajm-builder.bbprobe','--state',ROOT/'state'/f'ajm-builder-{time.time_ns()}'])
    run('ajm-builder-full-write-span',[binary/'bb-game-arm64','--boot',ROOT/'fixtures/bad-ajm-builder.bbprobe','--state',ROOT/'state'/f'bad-ajm-builder-{time.time_ns()}'],74)
    run('readonly-callback-opaque-user',[binary/'bb-game-arm64','--boot',ROOT/'fixtures/opaque-callbacks.bbprobe','--state',ROOT/'state'/f'opaque-callbacks-{time.time_ns()}'])
    run('nonexecutable-netctl-callback',[binary/'bb-game-arm64','--boot',ROOT/'fixtures/bad-netctl-callback.bbprobe','--state',ROOT/'state'/f'bad-netctl-callback-{time.time_ns()}'],74)
    run('arm-frontend-wrong-clock-role-red',[binary/'bb-game-arm64','--authored-fixture','--boot',ROOT/'fixtures/gpu-clock-wrong-role.bbprobe','--state',ROOT/'state'/f'gpu-wrong-clock-{time.time_ns()}'],2)
    run('arm-frontend-clock-hook-role',[binary/'bb-game-arm64','--authored-fixture','--boot',ROOT/'fixtures/gpu-clock.bbprobe','--state',ROOT/'state'/f'gpu-clock-{time.time_ns()}'])
    run('actual-fex-heap-callbacks',[binary/'bb-game-arm64','--heap-fixture','--boot',ROOT/'fixtures/heap-bridge.bbprobe','--state',ROOT/'state'/f'heap-bridge-{time.time_ns()}'])
    run('long-cross-page-strlen',[binary/'bb-game-arm64','--boot',ROOT/'fixtures/long-string.bbprobe','--state',ROOT/'state'/f'long-string-{time.time_ns()}'])
    run('heap-relocated-6-callbacks',[binary/'test_heap_bridge'])
    run('heap-invalid-range',[binary/'test_heap_bridge','invalid-range'])
    run('heap-invalid-target',[binary/'test_heap_bridge','invalid-target'])
    run('heap-original-fixed-base-red',[binary/'test_heap_bridge_original'],-10)
    for mode in ['bad-timespec','bad-file-buffer','bad-callback']:
        run(mode,[binary/'bb-game-arm64','--boot',ROOT/'fixtures'/f'{mode}.bbprobe','--state',ROOT/'state'/f'{mode}-{time.time_ns()}'],74)
    run('launcher-original-input-formats',[binary/'test_launcher_inputs'])
    for mode in ('valid','disabled','mismatch','short'):
        run('original-game-menu-actual-fex-'+mode,[binary/'test_game_menu_fex',mode])
    run('launcher-original-restart-contract',[sys.executable,ROOT/'tests/test_launcher_restart.py'])
    run('abi',[binary/'test_abi'])
    run('abi-original-red',[binary/'test_abi_original'],1)
    run('lookup',[binary/'test_lookup','lookup'])
    run('jit-clear-execute',[binary/'test_lookup','jit-clear'])
    run('darwin-no-x18',[binary/'test_lookup','darwin-registers'])
    run('nested-jit-scope',[binary/'test_jit_scope'])
    run('nested-jit-scope-original-red',[binary/'test_jit_scope_original'],-10)
    run('lookup-original-red',[binary/'test_lookup_original','lookup'],7)
    run('smoke',[binary/'fexcore-smoke'])
    run('harness',[binary/'fexcore-guest-harness'])
    run('lifetime',[binary/'test_lifetime'])
    run('lifetime-processes',[binary/'test_lifetime','--cross-peer',binary/'test_lifetime'])
    run('real-module-tls-thread-vm',[binary/'bb-game-arm64','--boot',ROOT/'fixtures/modules-tls.bbprobe','--state',ROOT/'state'/f'module-tls-{time.time_ns()}'])
    run('inspect-fixture',[binary/'bb-runtime-arm64','--inspect',ROOT/'fixtures/cpu.bbprobe'])
    checkpoint=ROOT/'state'/f'authored-checkpoint-{time.time_ns()}.bin'
    checkpoint.write_bytes(b'Authored offline restore policy fixture\n'); digest=hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    # Fresh-process restart foundation, not a claim of full in-game F3 support.
    for name in ['cpu','offline-relaunch']:
        run(name,[binary/'bb-runtime-arm64','--boot',ROOT/'fixtures/cpu.bbprobe','--state',ROOT/'state'/f'{name}-{time.time_ns()}'])
    assert hashlib.sha256(checkpoint.read_bytes()).hexdigest()==digest
    run('unknown',[binary/'bb-runtime-arm64','--boot',ROOT/'fixtures/unknown.bbprobe','--state',ROOT/'state'/f'unknown-{time.time_ns()}'],1)
    run('online-refused',[binary/'bb-runtime-arm64','--online','1'],1)
    run('state-reuse-refused',[binary/'bb-runtime-arm64','--boot',ROOT/'fixtures/cpu.bbprobe','--state',ROOT/'state'],1)
    if args.metal:
        run('metal',[binary/'bb-runtime-arm64','--boot',ROOT/'fixtures/metal.bbprobe','--state',ROOT/'state'/f'metal-{time.time_ns()}','--metal',args.metal],monitored=True)
    print(json.dumps(dict(evidence=str(evidence),tests=len(result),packed_veneers=args.packed_veneers,fast_hle_lookup=args.fast_hle_lookup,passed=True),indent=2))

if __name__=='__main__':main()
