"""Freeze the accepted local QA runtime, retaining original launcher scripts.

This is deliberately host-local: external dependencies and private QA paths remain
under the prepared Shared development tree. No game/save/cache is put in the zip.
"""
from pathlib import Path
import argparse, hashlib, json, os, plistlib, shutil, subprocess, sys, time, zipfile, stat

P=Path(__file__).resolve().parents[1]
if P.name!='arm64-runtime-prototype':
    P=Path('/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype')
sys.path.insert(0,str(P/'tools'))
from qa_build_guard import require_no_games
from manual_qa_policy import validate_manual_environment
from prepare_local_aot_stage import build_stage

def sha(p):
    with Path(p).open('rb')as f:return hashlib.file_digest(f,'sha256').hexdigest()

def deps(p):
    return [s.strip().split(' (compatibility')[0]for s in subprocess.check_output(['/usr/bin/otool','-L',str(p)],text=True).splitlines()[1:]]

def freeze_consumer(executable,old_library,new_library):
    old=str(old_library)
    assert old in deps(executable),(executable,old)
    target='@executable_path/'+os.path.relpath(new_library,executable.parent)
    subprocess.run(['/usr/bin/install_name_tool','-change',old,target,str(executable)],check=True)
    subprocess.run(['/usr/bin/codesign','--force','--sign','-',str(executable)],check=True)
    assert target in deps(executable)and old not in deps(executable)

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
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True);ap.add_argument('--source-repository',type=Path,required=True);ap.add_argument('--gpu-app-manifest',type=Path,required=True);ap.add_argument('--cpu-runtime-manifest',type=Path,required=True);ap.add_argument('--runtime-tools',type=Path);a=ap.parse_args()
    require_no_games()
    output=a.output.absolute();assert not output.exists(), 'Preserve old QA outputs; use a fresh directory'
    output.mkdir(parents=True)
    prepared=build_stage(project=P,repository=a.source_repository,runtime_tools=a.runtime_tools)
    admitted_stage=Path(prepared['stage'])
    raw=subprocess.check_output([sys.executable,str(P/'tools/prepare_launcher_gui_qa.py')],text=True)
    case=json.loads(raw);app=Path(case['app']);resources=app/'Contents/Resources'
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
    shutil.copy2(original/'tools/macos/local_metal_startup.py',stage/'tools/macos/local_metal_startup.py')
    # Keep a new cache/data target; preserve the previous QA cache and all formal data.
    old_gpu=Path(extra['BB_GPU_USER_DIR']).resolve(strict=True)
    assert old_gpu.is_relative_to(P/'state') and old_gpu!=P/'state'
    new_gpu=P/'state'/('gui-gpu-local-aot-'+str(time.time_ns()))
    assert not new_gpu.exists();shutil.copytree(old_gpu,new_gpu,symlinks=False)
    extra['BB_GPU_USER_DIR']=str(new_gpu)
    extra.pop('BB_MTL_LOCAL_AOT_MODE',None)
    # This new QA case exercises the validated resource path; formal preferences
    # and previous QA profiles remain untouched. No diagnostic input is injected.
    extra.update(BB_ARM_PAYLOAD_GATE='1',BB_ARM_RAW_PREWRITE='1',
                 BB_LAYER_READ_TRAPS='1',BB_GPU_LIFETIME='1')
    prefs['extraEnv']='\n'.join(k+'='+v for k,v in extra.items())
    Path(case['preferences']).write_bytes(plistlib.dumps(prefs))
    validate_manual_environment(extra)
    for name in ['python','deps-metal-arm64','bin/gpu-net']:
        dst=stage/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copytree((original/name).resolve(),dst,symlinks=False)
    cpu=stage/'bin/bb-probe-metal';cpu.parent.mkdir(exist_ok=True)
    cpu_runtime=load_cpu_runtime_manifest(a.cpu_runtime_manifest)
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
    # CPU and renderer must load one copied dependency closure, not both the
    # new SDL and its old absolute/rpath location (duplicate Objective-C classes).
    closure={f.name:f for f in (stage/'deps-metal-arm64/lib').glob('*.dylib')}
    net_lib=stage/'bin/gpu-net/libbbnet.dylib'
    for binary in [cpu,cpu_lib,gpu,gpu_lib,net_lib,*closure.values()]:
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
    subprocess.run(['/usr/bin/codesign','--force','--sign','-',str(gpu_app)],check=True)
    # The unchanged entry chooses the app wrapper first and still checks this path.
    (stage/'bin/gpu-metal-arm64/bb-gpu').symlink_to('../Bloodborne Metal.app/Contents/MacOS/bb-gpu')
    native=dict(schema=1,default_enabled=True,minimum_os_major=27,
        renderer_architecture='arm64 CPU via FEX and independent arm64 Metal GPU; isolated prototype',
        production=False,baseline_revision=json.loads((P/'SOURCE.json').read_text())['blood_echoes_commit'],
        frozen_runtime={str(f.relative_to(stage)):sha(f)for f in [cpu,cpu_lib,gpu,gpu_lib,net_lib]})
    (stage/'NATIVE-METAL.json').write_text(json.dumps(native,indent=2)+'\n')
    manifest=dict(schema=2,created_ns=time.time_ns(),app=str(app),private_case=case,
        host_local_only=True,portable_installer=False,formal_data_modified=False,
        default_local_metal_preparation=True,protected_resource_writeback=True,new_component_stage=str(admitted_stage),
        local_metal_preparation=prepared['manifest'],
        private_gpu_cache=str(new_gpu),previous_QA_cache_modified=False,
        frozen_runtime=native['frozen_runtime'],cpu_runtime_manifest=dict(path=str(a.cpu_runtime_manifest.resolve()),sha256=sha(a.cpu_runtime_manifest),protocol_version=cpu_runtime['protocol_version'],live_payload_version=cpu_runtime['live_payload_version'],author_instrumentation=cpu_runtime.get('author_instrumentation',False)),
        upstream_inputs=dict(cpu=sha(cpu_game_source),cpu_library=sha(cpu_source),gpu_library=sha(gpu_source),net_library=sha(original/'bin/gpu-net/libbbnet.dylib')),
        original_scripts=baseline,effective_scripts=source,controlled_changes=['run.sh final-exec hook','local Metal preparation runtime tools'],manual_physical_input=True,silent=True,
        diagnostic_capture=False,performance_logging=False,
        external_dependencies=['prepared Shared dependency tree','private Shared QA paths','locally dumped game and Chinese assets','new cloned private QA GPU cache'],
        known_limits=['combat white models not yet reproduced','remaining import/context contracts are tracked in GOAL.zh-CN.md','death, online and menu live retests deferred'])
    for rel,digest in source.items():assert sha(stage/rel)==digest
    for binary in [cpu,cpu_lib,gpu,gpu_lib,net_lib]:
        arch=subprocess.check_output(['/usr/bin/lipo','-archs',str(binary)],text=True).strip()
        assert arch=='arm64',(binary,arch)
    (resources/'ARM64-QA-MANIFEST.json').write_text(json.dumps(manifest,indent=2)+'\n')
    subprocess.run(['/usr/bin/codesign','--force','--sign','-',str(app)],check=True)
    subprocess.run(['/usr/bin/codesign','--verify','--strict',str(app)],check=True)
    for f in [cpu,cpu_lib,gpu,gpu_lib,net_lib]:subprocess.run(['/usr/bin/codesign','--verify','--strict',str(f)],check=True)
    instructions='''Blood Echoes ARM64 本机隔离测试包

这是本机 QA，依赖 Shared 开发树、游戏/汉化资产和新建的独立 QA 数据目录，不能在其它 Mac 独立运行；不是正式安装包。
沿用现有启动器界面、键鼠/手柄、中文、F2/F3、网络和退出协议；不注入自动输入。默认离线、静音，不参与正式存档选择。

本机 Metal 离线准备默认开启：已收集输入在游戏进程启动前按原选项串行建库；后续启动验证复用，不分发开发机游戏着色器产物。
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
