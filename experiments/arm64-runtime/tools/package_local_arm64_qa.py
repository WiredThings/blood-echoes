"""Freeze the accepted local QA runtime, retaining original launcher scripts.

This is deliberately host-local: external dependencies and private QA paths remain
under the prepared Shared development tree. No game/save/cache is put in the zip.
"""
from pathlib import Path
import argparse, hashlib, json, os, plistlib, shutil, subprocess, sys, time, zipfile, stat, struct, tempfile, re

P=Path(__file__).resolve().parents[1]
if P.name!='arm64-runtime-prototype':
    P=Path('/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype')
sys.path.insert(0,str(Path(__file__).resolve().parent))
from qa_build_guard import require_no_games
from manual_qa_policy import validate_manual_environment
from prepare_local_aot_stage import build_stage
from qa_gpu_cache_seed import validate as validate_cache_seed,clone as clone_cache_seed
from complete_packaged_python import complete as complete_python_sqlite
from game_pso_component import freeze_inputs,sha as component_sha

def sha(p):
    with Path(p).open('rb')as f:return hashlib.file_digest(f,'sha256').hexdigest()

def launcher_code_identity(binary,scratch):
    """Compare all GUI code/data across the expected app-specific re-signing.
    Only a new scratch copy is stripped. codesign retains __LINKEDIT vmsize
    rounded for the old signature, so exclude that size word, never code bytes.
    """
    with tempfile.TemporaryDirectory(prefix='launcher-code-',dir=scratch)as tmp:
        copy=Path(tmp)/'launcher';shutil.copy2(binary,copy)
        subprocess.run(['/usr/bin/codesign','--remove-signature',str(copy)],check=True,capture_output=True)
        data=bytearray(copy.read_bytes());assert len(data)>=32
        magic,cpu,_,kind,count,size,_,_=struct.unpack_from('<8I',data)
        assert magic==0xfeedfacf and cpu==0x0100000c and kind==2 and 0<count<1024 and 32+size<=len(data)
        offset=32;linkedit=0
        for _ in range(count):
            command,length=struct.unpack_from('<II',data,offset)
            assert length>=8 and offset+length<=32+size
            if command==0x19 and data[offset+8:offset+24].split(b'\0')[0]==b'__LINKEDIT':
                assert length>=72;data[offset+32:offset+40]=b'\0'*8;linkedit+=1
            offset+=length
        assert offset==32+size and linkedit==1
        return hashlib.sha256(data).hexdigest()

def seed_qa_preferences(case,*,project=P,runner=subprocess.run):
    """Use the original launcher's UserDefaults, only for a fresh isolated QA ID.
    BBPreviewPreferencesPath is not a production preferences loader. Do not add
    another settings implementation to the UI just for the test packager.
    """
    project=project.resolve(strict=True);identifier=case['bundle_id']
    assert re.fullmatch(r'io\.github\.bbport\.optimized\.options-qa\.arm64\.gui\.[0-9]+',identifier),'fresh QA bundle only'
    app=Path(case['app']);info=plistlib.loads((app/'Contents/Info.plist').read_bytes())
    assert info['CFBundleIdentifier']==identifier and app.resolve().is_relative_to(project/'apps')
    path=Path(case['preferences']);assert path==path.resolve(strict=True) and path.is_relative_to(project/'state')
    values=plistlib.loads(path.read_bytes())
    assert values.get('optimizedFoldersImported')is True and values.get('cloudSaves')is False and values.get('online')is False
    for key in('gameFolder','userFolder'):
        folder=Path(values[key]).resolve(strict=True)
        assert folder.is_relative_to(project.parent) and folder!=project.parent
    assert values['userFolder']==case['user'] and info['BBPreviewDataPath']==case['root']
    assert Path(case['root']).resolve(strict=True).is_relative_to(project/'state')
    before=runner(['/usr/bin/defaults','export',identifier,'-'],capture_output=True,check=False)
    # macOS also reports an unknown domain as a successful empty plist. Admit
    # only absence/empty values; malformed or any existing value is never cleared.
    assert before.returncode!=0 or plistlib.loads(before.stdout)=={},'refuse overwriting existing preferences values'
    runner(['/usr/bin/defaults','import',identifier,str(path)],capture_output=True,check=True)
    result=runner(['/usr/bin/defaults','export',identifier,'-'],capture_output=True,check=True)
    actual=plistlib.loads(result.stdout)
    assert all(actual.get(key)==value for key,value in values.items()),'QA preferences readback differs'
    return dict(bundle_id=identifier,imported_into_original_user_defaults=True,
                source_sha256=sha(path),verified_keys=len(values),existing_values_overwritten=False)

def deps(p):
    return [s.strip().split(' (compatibility')[0]for s in subprocess.check_output(['/usr/bin/otool','-L',str(p)],text=True).splitlines()[1:]]

def freeze_consumer(executable,old_library,new_library):
    from gpu_candidate_identity import resolve_gpu_edge
    old=Path(old_library).resolve(strict=True);new=Path(new_library).resolve(strict=True)
    edges=[edge for edge in deps(executable)if Path(edge).name.startswith('libbbgpu')]
    assert len(edges)==1,'exactly one GPU load edge is required for relocation'
    edge=edges[0]
    assert resolve_gpu_edge(executable,edge)==old,'copied consumer does not load the admitted source library'
    target='@executable_path/'+os.path.relpath(new_library,executable.parent)
    subprocess.run(['/usr/bin/install_name_tool','-change',edge,target,str(executable)],check=True)
    subprocess.run(['/usr/bin/codesign','--force','--sign','-',str(executable)],check=True)
    assert [edge for edge in deps(executable)if Path(edge).name.startswith('libbbgpu')]==[target]
    assert resolve_gpu_edge(executable,target)==new,'relocated consumer does not load the copied library'

def load_cpu_runtime_manifest(path,expected_gpu_sha256=None):
    assert not path.is_symlink(), 'CPU manifest must be a regular Shared file'
    path=path.resolve(strict=True)
    assert path.is_relative_to(P/'evidence') and path.is_file(), 'Shared CPU runtime manifest required'
    record=json.loads(path.read_text())
    assert record.get('schema')==1 and record.get('kind')=='blood-echoes-isolated-cpu-runtime'
    assert record.get('production') is False
    root=Path(record['execution_root']).resolve(strict=True)
    assert root.parent==(P/'evidence').resolve() and root.name.startswith('resource-cpu-execution-')
    assert root.name.removeprefix('resource-cpu-execution-').isdigit() and path.is_relative_to(root)
    assert type(record['protocol_version']) is int and record['protocol_version']>0
    assert type(record['live_payload_version']) is int and record['live_payload_version']>0
    for name in ('game','host'):
        item=record[name];source=Path(item['path'])
        assert source.is_absolute() and not source.is_symlink() and source.is_file()
        assert source.resolve(strict=True).is_relative_to(root)
        assert len(item['sha256'])==64 and sha(source)==item['sha256'], (name,'CPU runtime hash mismatch')
    assert len(record['gpu_library_sha256'])==64
    if expected_gpu_sha256 is not None:assert record['gpu_library_sha256']==expected_gpu_sha256, 'CPU/GPU admitted build mismatch'
    return record

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True);ap.add_argument('--source-repository',type=Path,required=True);ap.add_argument('--gpu-app-manifest',type=Path,required=True);ap.add_argument('--cpu-runtime-manifest',type=Path,required=True);ap.add_argument('--runtime-tools',type=Path);ap.add_argument('--game-pso-component',type=Path,required=True);ap.add_argument('--python-provider',type=Path);ap.add_argument('--gpu-cache-seed-manifest',type=Path);ap.add_argument('--launcher-products',type=Path,required=True);a=ap.parse_args()
    require_no_games()
    assert not a.game_pso_component.is_symlink() and a.game_pso_component.resolve(strict=True).is_relative_to(P/'evidence'), 'admitted Shared game preparation manifest required'
    cpu_runtime=load_cpu_runtime_manifest(a.cpu_runtime_manifest)
    cache_seed=validate_cache_seed(a.gpu_cache_seed_manifest,project=P,expected_upstream_gpu_sha256=cpu_runtime['gpu_library_sha256'])if a.gpu_cache_seed_manifest else None
    output=a.output.absolute();assert not output.exists(), 'Preserve old QA outputs; use a fresh directory'
    output.mkdir(parents=True)
    prepared=build_stage(project=P,repository=a.source_repository,runtime_tools=a.runtime_tools)
    admitted_stage=Path(prepared['stage'])
    raw=subprocess.check_output([sys.executable,str(P/'tools/prepare_launcher_gui_qa.py')],text=True)
    case=json.loads(raw);app=Path(case['app']);resources=app/'Contents/Resources'
    # Observe the original Swift environment; never reconstruct its settings
    # in the benchmark harness. This opt-in is absent from production bundles.
    launch_receipt=Path(case['data'])/'out/launch-conditions.json'
    info_path=app/'Contents/Info.plist';info=plistlib.loads(info_path.read_bytes())
    assert info['CFBundleIdentifier']==case['bundle_id']
    info['BBQALaunchReceipt']=str(launch_receipt);info_path.write_bytes(plistlib.dumps(info))
    # The QA shell generator supplies isolated paths, not authority to reuse an
    # old GUI binary. Require the explicitly selected current native build.
    products=a.launcher_products.resolve(strict=True)
    assert products.is_relative_to(P/'evidence'), 'fresh isolated launcher build required'
    launcher=products/'BBLauncher';bundle=products/'BBLauncher_BBLauncher.bundle'
    assert launcher.is_file() and not launcher.is_symlink() and bundle.is_dir() and not bundle.is_symlink()
    assert subprocess.check_output(['/usr/bin/lipo','-archs',str(launcher)],text=True).strip()=='arm64'
    shutil.copy2(launcher,app/'Contents/MacOS/bbport')
    assert sha(launcher)==sha(app/'Contents/MacOS/bbport'),'GUI changed during copying'
    launcher_identity=launcher_code_identity(launcher,output)
    # Replace only the newly generated QA shell's stale resource bundle.
    old_bundle=resources/'BBLauncher_BBLauncher.bundle'
    assert old_bundle.is_relative_to(app) and not old_bundle.is_symlink()
    shutil.rmtree(old_bundle);shutil.copytree(bundle,old_bundle,symlinks=False)
    prefs=plistlib.loads(Path(case['preferences']).read_bytes())
    extra=dict(row.split('=',1)for row in prefs['extraEnv'].splitlines()if row)
    validate_manual_environment(extra)
    assert prefs['online']is False and prefs['cloudSaves']is False and prefs['userFolder']==case['user']
    alias=resources/'bbport';assert alias.is_symlink()and alias.resolve()==P/'launcher-component-stage'
    alias.unlink();stage=alias;stage.mkdir();original=admitted_stage
    baseline=prepared['parity']['original_sources'];source=prepared['parity']['effective_sources']
    for rel,digest in source.items():
        src=original/rel;dst=stage/rel;assert sha(src)==digest
        dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
    shutil.copy2(original/'.packaged',stage/'.packaged')
    for metadata in ('SOURCE-PARITY.json','ARM64-LOCAL-PREPARATION.json'):
        shutil.copy2(original/metadata,stage/metadata)
    shutil.copytree(original/'native-runtime-tools',stage/'native-runtime-tools',symlinks=False)
    # Original bounded SELF/SFO parser closure for automatic source preparation.
    # Only admitted source code, never game data or developer fixture inputs.
    for relative,digest in prepared['manifest']['runtime_support'].items():
        src=original/relative;assert sha(src)==digest
        dst=stage/relative;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
    shutil.copy2(original/'tools/macos/local_metal_startup.py',stage/'tools/macos/local_metal_startup.py')
    # Keep a new cache/data target; preserve the previous QA cache and all formal data.
    old_gpu=Path(extra['BB_GPU_USER_DIR']).resolve(strict=True)
    assert old_gpu.is_relative_to(P/'state') and old_gpu!=P/'state'
    new_gpu=P/'state'/('gui-gpu-local-aot-'+str(time.time_ns()))
    assert not new_gpu.exists()
    if cache_seed is not None:cache_seed=clone_cache_seed(cache_seed,new_gpu,project=P,baseline_root=old_gpu)
    else:shutil.copytree(old_gpu,new_gpu,symlinks=False)
    extra['BB_GPU_USER_DIR']=str(new_gpu)
    extra.pop('BB_MTL_LOCAL_AOT_MODE',None)
    # This new QA case exercises the validated resource path; formal preferences
    # and previous QA profiles remain untouched. No diagnostic input is injected.
    extra.update(BB_ARM_PAYLOAD_GATE='1',BB_ARM_RAW_PREWRITE='1',
                 BB_LAYER_READ_TRAPS='1',BB_GPU_LIFETIME='1')
    prefs['extraEnv']='\n'.join(k+'='+v for k,v in extra.items())
    Path(case['preferences']).write_bytes(plistlib.dumps(prefs))
    validate_manual_environment(extra)
    preferences_seed=seed_qa_preferences(case)
    for name in ['python','deps-metal-arm64','bin/gpu-net']:
        dst=stage/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copytree((original/name).resolve(),dst,symlinks=False)
    python_closure=complete_python_sqlite(stage/'python',writable_stage=stage,provider=a.python_provider)
    python_hashlib_closure=complete_python_sqlite(stage/'python',writable_stage=stage,provider=a.python_provider,module_name='_hashlib')
    cpu=stage/'bin/bb-probe-metal';cpu.parent.mkdir(exist_ok=True)
    cpu_game_source=Path(cpu_runtime['game']['path'])
    shutil.copy2(cpu_game_source,cpu)
    cpu_lib=stage/'bin/gpu-metal/libbbgpu.dylib';cpu_lib.parent.mkdir()
    # Explicitly bind the new CPU and its exact same-build host. Never select
    # P/build/fex or an old AvPlayer checkpoint implicitly.
    cpu_source=Path(cpu_runtime['host']['path'])
    assert str(cpu_source)in deps(cpu),'CPU load edge does not match the admitted host library'
    shutil.copy2(cpu_source,cpu_lib)
    freeze_consumer(cpu,cpu_source,cpu_lib)
    gpu_manifest=a.gpu_app_manifest.resolve(strict=True)
    assert gpu_manifest.is_relative_to(P/'evidence'), 'admitted private GPU manifest required'
    variant=json.loads(gpu_manifest.read_text())
    from gpu_candidate_identity import verify
    verify(variant,P/'backend')
    gpu_source=Path(variant['library']);assert sha(gpu_source)==variant['library_sha256']
    assert cpu_runtime['gpu_library_sha256']==variant['library_sha256'], 'CPU/GPU admitted build mismatch'
    gpu_lib=stage/'bin/gpu-metal-arm64/libbbgpu.dylib';gpu_lib.parent.mkdir();shutil.copy2(gpu_source,gpu_lib)
    gpu_app=stage/'bin/Bloodborne Metal.app';source_app=Path(variant['target']).parents[2]
    shutil.copytree(source_app,gpu_app,symlinks=True)
    gpu=gpu_app/'Contents/MacOS/bb-gpu';freeze_consumer(gpu,gpu_source,gpu_lib)
    component_source=json.loads(a.game_pso_component.read_text())
    assert component_source['library_sha256']==variant['library_sha256'], 'worker and selected renderer are not one admitted build'
    assert component_sha(Path(component_source['library']))==variant['library_sha256']
    component=freeze_inputs(stage,a.game_pso_component,gpu_lib,rewrite=freeze_consumer,admission_root=P)
    worker=stage/component['worker']
    # CPU and renderer must load one copied dependency closure, not both the
    # new SDL and its old absolute/rpath location (duplicate Objective-C classes).
    closure={f.name:f for f in (stage/'deps-metal-arm64/lib').glob('*.dylib')}
    net_lib=stage/'bin/gpu-net/libbbnet.dylib'
    module_worker=stage/component['module_consumer']if component.get('module_consumer')else None
    for binary in [cpu,cpu_lib,gpu,gpu_lib,net_lib,worker,*([module_worker]if module_worker else[]),*closure.values()]:
        changed=False
        links=deps(binary)
        if binary.suffix=='.dylib':links=links[1:] # first otool row is LC_ID_DYLIB
        for old in links:
            target=closure.get(Path(old).name)
            if target is None or target==binary:continue
            new='@loader_path/'+os.path.relpath(target,binary.parent)
            if old!=new:
                subprocess.run(['/usr/bin/install_name_tool','-change',old,new,str(binary)],check=True)
                changed=True
        if changed:subprocess.run(['/usr/bin/codesign','--force','--sign','-',str(binary)],check=True)
    # install_name_tool/codesign may alter both bytes. Seal final copied hashes,
    # never carry the development worker/DSO SHA across relocation.
    component['worker_sha256']=component_sha(worker)
    component['library_sha256']=component_sha(gpu_lib)
    if module_worker is not None:component['module_consumer_sha256']=component_sha(module_worker)
    (stage/'GAME-PSO-COMPONENT.json').write_text(json.dumps(component,indent=2)+'\n')
    from game_pso_component import load as load_component
    load_component(stage)
    subprocess.run(['/usr/bin/codesign','--force','--sign','-',str(gpu_app)],check=True)
    # The unchanged entry chooses the app wrapper first and still checks this path.
    (stage/'bin/gpu-metal-arm64/bb-gpu').symlink_to('../Bloodborne Metal.app/Contents/MacOS/bb-gpu')
    native=dict(schema=1,default_enabled=True,minimum_os_major=27,
        game_runtime='fex-arm64',local_metal_preparation='auto',metal_features=['recent-staging-reuse'],
        renderer_architecture='arm64 CPU via FEX and independent arm64 Metal GPU; isolated prototype',
        production=False,baseline_revision=json.loads((P/'SOURCE.json').read_text())['blood_echoes_commit'],
        frozen_runtime={str(f.relative_to(stage)):sha(f)for f in [cpu,cpu_lib,gpu,gpu_lib,net_lib,worker,*([module_worker]if module_worker else[])]})
    (stage/'NATIVE-METAL.json').write_text(json.dumps(native,indent=2)+'\n')
    # The shell receipt predates replacement of its placeholder CPU. Preserve
    # that provenance separately; the final case must identify the actual copy.
    case['shell_cpu_sha256']=case['cpu_sha256']
    case['cpu_sha256']=native['frozen_runtime']['bin/bb-probe-metal']
    case['cpu_source_sha256']=sha(cpu_game_source)
    case_path=Path(case['evidence'])/'case.json'
    assert case_path.resolve(strict=True).is_relative_to(P/'evidence')
    case_path.write_text(json.dumps(case,indent=2)+'\n')
    manifest=dict(schema=2,created_ns=time.time_ns(),app=str(app),private_case=case,
        host_local_only=True,portable_installer=False,formal_data_modified=False,
        default_local_metal_preparation=True,protected_resource_writeback=True,new_component_stage=str(admitted_stage),
        python_sqlite_closure=python_closure,python_hashlib_closure=python_hashlib_closure,local_metal_preparation=prepared['manifest'],game_pso_component=component,default_game_pso_preparation=True,
        private_gpu_cache=str(new_gpu),previous_QA_cache_modified=False,gpu_cache_seed=cache_seed,
        frozen_runtime=native['frozen_runtime'],cpu_runtime_manifest=dict(path=str(a.cpu_runtime_manifest.resolve()),sha256=sha(a.cpu_runtime_manifest),protocol_version=cpu_runtime['protocol_version'],live_payload_version=cpu_runtime['live_payload_version'],author_instrumentation=cpu_runtime.get('author_instrumentation',False)),
        launcher_build=dict(path=str(products),source_executable_sha256=sha(launcher),code_identity_sha256=launcher_identity),qa_preferences_seed=preferences_seed,
        qa_launch_conditions_receipt=dict(path=str(launch_receipt),producer='original-swift-LauncherModel.start-final-environment',game_started_verified=False),
        upstream_inputs=dict(cpu=sha(cpu_game_source),cpu_library=sha(cpu_source),gpu_library=sha(gpu_source),net_library=sha(original/'bin/gpu-net/libbbnet.dylib')),
        original_scripts=baseline,effective_scripts=source,controlled_changes=['run.sh final-exec hook','local Metal preparation runtime tools'],manual_physical_input=True,silent=True,
        diagnostic_capture=False,performance_logging=False,
        external_dependencies=['prepared Shared dependency tree','private Shared QA paths','locally dumped game and Chinese assets','new cloned private QA GPU cache'],
        known_limits=['combat white models not yet reproduced','remaining import/context contracts are tracked in GOAL.zh-CN.md','death, online and menu live retests deferred'])
    for rel,digest in source.items():assert sha(stage/rel)==digest
    for binary in [cpu,cpu_lib,gpu,gpu_lib,net_lib,worker]:
        arch=subprocess.check_output(['/usr/bin/lipo','-archs',str(binary)],text=True).strip()
        assert arch=='arm64',(binary,arch)
    (resources/'ARM64-QA-MANIFEST.json').write_text(json.dumps(manifest,indent=2)+'\n')
    subprocess.run(['/usr/bin/codesign','--force','--sign','-',str(app)],check=True)
    subprocess.run(['/usr/bin/codesign','--verify','--strict',str(app)],check=True)
    assert launcher_code_identity(app/'Contents/MacOS/bbport',output)==launcher_identity,'signed GUI code differs from selected build'
    for f in [cpu,cpu_lib,gpu,gpu_lib,net_lib,worker]:subprocess.run(['/usr/bin/codesign','--verify','--strict',str(f)],check=True)
    instructions='''Blood Echoes ARM64 本机隔离测试包

这是本机 QA，依赖 Shared 开发树、游戏/汉化资产和新建的独立 QA 数据目录，不能在其它 Mac 独立运行；不是正式安装包。
沿用现有启动器界面、键鼠/手柄、中文、F2/F3、网络和退出协议；不注入自动输入。默认离线、静音，不参与正式存档选择。

本机 Metal 离线准备默认开启：从当前选定隔离 GPU 缓存自动发现完整配方，在游戏进程启动前按原选项串行建库和准备管线；后续启动严格验证复用，不分发开发机游戏着色器产物。每次启动有时间预算，未完成项留待后续；不导入旧元数据或执行 SRT walker。
缺少 Metal Toolchain 时明确回退原运行时编译；损坏/不兼容缓存不会被删除。严重内存/锁屏保护停止仍保留。
源码覆盖和定向控制不等于白模已解决、所有场景零编译或已证明帧率提升；完整游戏/内存验收状态以runtime-manifest为准。

正式应用、存档、设置和缓存不改，旧 QA stage/包保留。Release不更新。
包不含游戏、存档、缓存、汉化资产、账号凭据或截图；不要删除 Shared 外部依赖和隔离数据。
源码：WiredThings/blood-echoes 的 dev/arm64-runtime-audit 分支。
'''
    (output/'runtime-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (output/'使用说明.txt').write_text(instructions)
    archive=output/'Blood-Echoes-ARM64-Local-QA.zip';temporary=archive.with_suffix('.tmp.zip')
    with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6)as z:
        for f in sorted(app.rglob('*')):
            arc=str(Path(app.name)/f.relative_to(app))
            if f.is_symlink():
                info=zipfile.ZipInfo(arc);info.create_system=3;info.external_attr=(stat.S_IFLNK|0o777)<<16;z.writestr(info,os.readlink(f))
            elif f.is_file():z.write(f,arc)
        z.write(output/'runtime-manifest.json','runtime-manifest.json');z.write(output/'使用说明.txt','使用说明.txt')
    os.replace(temporary,archive)
    (output/'SHA256SUMS').write_text(sha(archive)+'  '+archive.name+'\n')
    print(json.dumps(dict(app=str(app),archive=str(archive),sha256=sha(archive),bytes=archive.stat().st_size),indent=2))

if __name__=='__main__':main()
