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

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
    require_no_games()
    output=a.output.resolve();output.mkdir(parents=True,exist_ok=True)
    raw=subprocess.check_output([sys.executable,str(P/'tools/prepare_launcher_gui_qa.py')],text=True)
    case=json.loads(raw);app=Path(case['app']);resources=app/'Contents/Resources'
    prefs=plistlib.loads(Path(case['preferences']).read_bytes())
    extra=dict(row.split('=',1)for row in prefs['extraEnv'].splitlines()if row)
    validate_manual_environment(extra)
    assert prefs['online']is False and prefs['cloudSaves']is False and prefs['userFolder']==case['user']
    alias=resources/'bbport';assert alias.is_symlink()and alias.resolve()==P/'launcher-component-stage'
    alias.unlink();stage=alias;stage.mkdir();original=P/'launcher-component-stage'
    source=json.loads((original/'SOURCE-PARITY.json').read_text())['original_sources']
    for rel,digest in source.items():
        src=original/rel;dst=stage/rel;assert sha(src)==digest
        dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
    shutil.copy2(original/'.packaged',stage/'.packaged')
    for name in ['python','deps-metal-arm64','bin/gpu-net']:
        dst=stage/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copytree((original/name).resolve(),dst,symlinks=False)
    cpu=stage/'bin/bb-probe-metal';cpu.parent.mkdir(exist_ok=True)
    shutil.copy2(P/'build/fex/Bin/bb-game-arm64',cpu)
    cpu_lib=stage/'bin/gpu-metal/libbbgpu.dylib';cpu_lib.parent.mkdir()
    # Freeze the library the current CPU actually imports; never silently select
    # the older menu checkpoint after AV/callback runtime changes.
    host=json.loads((P/'evidence/avplayer-host-candidate-current.json').read_text())
    cpu_source=Path(host['candidate']);assert sha(cpu_source)==host['sha256']
    assert str(cpu_source)in deps(cpu),'CPU load edge does not match the admitted host library'
    shutil.copy2(cpu_source,cpu_lib)
    freeze_consumer(cpu,cpu_source,cpu_lib)
    variant=json.loads((P/'evidence/first-upload-gpu-app.json').read_text())
    gpu_source=Path(variant['library']);assert sha(gpu_source)==variant['library_sha256']
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
        frozen_runtime=native['frozen_runtime'],upstream_inputs=dict(cpu=sha(P/'build/fex/Bin/bb-game-arm64'),cpu_library=sha(cpu_source),gpu_library=sha(gpu_source),net_library=sha(original/'bin/gpu-net/libbbnet.dylib')),
        original_scripts=source,manual_physical_input=True,silent=True,
        diagnostic_capture=False,performance_logging=False,
        external_dependencies=['prepared Shared dependency tree','private Shared QA paths','locally dumped game and Chinese assets','existing QA GPU cache'],
        known_limits=['combat white models not yet reproduced','remaining import/context contracts are tracked in GOAL.zh-CN.md','death, online and menu live retests deferred'])
    for rel,digest in source.items():assert sha(stage/rel)==digest
    for binary in [cpu,cpu_lib,gpu,gpu_lib,net_lib]:
        arch=subprocess.check_output(['/usr/bin/lipo','-archs',str(binary)],text=True).strip()
        assert arch=='arm64',(binary,arch)
    (resources/'ARM64-QA-MANIFEST.json').write_text(json.dumps(manifest,indent=2)+'\n')
    subprocess.run(['/usr/bin/codesign','--force','--sign','-',str(app)],check=True)
    subprocess.run(['/usr/bin/codesign','--verify','--strict',str(app)],check=True)
    for f in [cpu,cpu_lib,gpu,gpu_lib,net_lib]:subprocess.run(['/usr/bin/codesign','--verify','--strict',str(f)],check=True)
    instructions='''Blood Echoes ARM64 本机隔离测试包（2026-10-25）

这是本机 QA 入口，不能在其它 Mac 独立运行，不是正式安装包。
解压打开其中的 .app；也可直接打开清单所列 Shared/apps 下的同一应用。
CPU、CPU 侧宿主库和纹理修复 GPU 已冻结在包内；依赖、游戏资源和隔离配置仍引用本机 Shared 开发树。

默认离线、静音、独立 QA 进度；键鼠和手柄沿用原启动器，不注入自动输入。
F2 若被系统拦截，请用 Fn+F2。F3 仍按原协议以离线模式恢复。
本轮不要求死亡、联网和菜单验收。正式应用、正式存档、设置和缓存未改。

已确认并修复：错误退出码与 75/76 重启协议冲突；ABI 宏漏接；未实现数据导入保护页；完整 libc 访问范围校验；GpuModified 首次上传未记 hash 导致旧 CPU 数据覆盖 GPU 内容的定向错误。
未宣称战斗白模已解决。AvPlayer 桥接、程序名、模块信息与 msync 契约已有定向控制；Mlock、模块终结和信号上下文仍待接入。
本次 CPU 完成了原启动器隔离场景短测，不能作为白模、长时间内存或 FPS 收益验收。死亡、联网、菜单和完整 F3 实测仍留待后续。

本包不带逐帧诊断、布局审查日志、自动输入或QoS实验；renderer仍冻结在原first-upload候选，不安装尚未验证的布局转换。新源码中的GPU覆盖合并只是自造控制证明，未启用到游戏。

源码：WiredThings/blood-echoes 的 dev/arm64-runtime-audit 分支。未更新 Release。
包不包含游戏、存档、缓存、汉化资源、账号凭据或截图。不要删除本机 Shared 依赖目录。
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
