"""Isolated dual-CPU QA using the current source-built application payload.

Reuse the original GUI QA namespace/save seed, normal bundle code admission,
source-closure writer and CPU kit freezer. No new launcher or save protocol.
"""
from pathlib import Path
import argparse,json,os,plistlib,shutil,subprocess,sys,time
from bbcpu_launch import need,owned,private,sha
from freeze_bbcpu_component import freeze
from launcher_runtime_profile import bundle
from prepare_local_aot_stage import PRIVATE_PROJECT,write_source_closure
from package_local_arm64_qa import launcher_code_identity,seed_qa_preferences
from game_pso_component import load as load_component
from qa_build_guard import require_no_games
from manual_qa_policy import validate_manual_environment

P=PRIVATE_PROJECT
QUALITY_KEYS=('fullscreen','ultrawide','resolution','wideResolution','hdr','presentMode','fpsMode','fpsLimit',
              'drawPipe','readbacks','preupload','nativeGpu','renderer','metalObjectMotion','metalOcclusion',
              'metalOptimizations','gameMenu','display','language','communityChinese')
def apply_quality(prefs,source):
    """Transfer rendering choices, never paths, progress, credentials or online mode."""
    need(source.get('renderer','metal')=='metal','current selected renderer is not supported by this CPU experiment')
    selected={key:source[key]for key in QUALITY_KEYS if key in source};prefs.update(selected);return selected
def write(path,value):path.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')
def assemble(*,source_app,repository,launcher_products,build,game,toolchain,search_dirs,output,settings_root):
    require_no_games();source_app=private(source_app);repository=owned(repository,True);products=owned(launcher_products,True)
    source=source_app/'Contents/Resources/bbport';admitted=bundle(source)
    need(admitted is not None,'actual normal source-built bundle admission required')
    subprocess.run(['/usr/bin/codesign','--verify','--strict',str(source_app)],check=True,capture_output=True)
    need(output.parent==output.parent.resolve(strict=True)and not output.exists()and output.is_relative_to(P/'evidence'),'fresh isolated package output required');output.mkdir(mode=0o700)
    # This existing Shared helper supplies only a new namespace and a copy of
    # the established QA seed. It never selects a formal save as the QA seed.
    helper=owned(P/'tools/prepare_launcher_gui_qa.py');helper_sha=sha(helper)
    raw=subprocess.check_output([sys.executable,str(helper)],text=True);case=json.loads(raw)
    need(sha(helper)==helper_sha,'QA namespace helper changed')
    app=private(case['app']);resources=app/'Contents/Resources';stage=resources/'bbport'
    need(stage.is_symlink()and stage.resolve()==P/'launcher-component-stage','unexpected new QA shell payload')
    stage.unlink();shutil.copytree(source,stage,symlinks=True)
    # Refresh the original code closure from this checkout; retain the exact
    # current FEX/Metal/net payload, icons and relative native load edges.
    closure=write_source_closure(source=repository/'experiments/arm64-runtime',repository=repository,
        runtime_tools=repository/'experiments/arm64-runtime/tools',destination=output/'code',project=P)
    code=Path(closure['stage'])
    for file in code.rglob('*'):
        if file.is_file():
            dest=stage/file.relative_to(code);need(not dest.is_symlink(),'source overlay must not follow links')
            dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(file,dest)
    # A normal source payload becomes a private QA payload without claiming a
    # different renderer build. Its actual copied bytes/DSO edges are rechecked.
    component_path=stage/'GAME-PSO-COMPONENT.json';component=json.loads(component_path.read_bytes())
    component['production']=False;write(component_path,component)
    need(load_component(stage)is not None,'copied current renderer/component admission failed')
    local=closure['manifest'];local.update(code_closure_only=False,native_components_included=True,final_component_verified=True,default_game_pso_preparation=True)
    write(stage/'ARM64-LOCAL-PREPARATION.json',local)
    native_path=stage/'NATIVE-METAL.json';native=json.loads(native_path.read_bytes());native['production']=False;write(native_path,native)
    for name,expected in admitted.native['frozen_runtime'].items():need(sha(owned(stage/name))==expected,'copied current runtime changed: '+name)
    cpu_component=freeze(stage,repository=repository,build=build,game=game,toolchain=toolchain,search_dirs=search_dirs)
    launcher=owned(products/'BBLauncher');bundle_source=owned(products/'BBLauncher_BBLauncher.bundle',True)
    identity=launcher_code_identity(launcher,output);shutil.copy2(launcher,app/'Contents/MacOS/bbport')
    old=resources/'BBLauncher_BBLauncher.bundle';need(old.is_dir()and not old.is_symlink(),'unexpected new QA GUI resources');shutil.rmtree(old);shutil.copytree(bundle_source,old)
    info_path=app/'Contents/Info.plist';info=plistlib.loads(info_path.read_bytes());source_info=plistlib.loads((source_app/'Contents/Info.plist').read_bytes())
    for key in('CFBundleShortVersionString','CFBundleVersion','BBSourceCommit'):
        if key in source_info:info[key]=source_info[key]
    info.update(CFBundleName='Blood Echoes Dual CPU QA',CFBundleDisplayName='Blood Echoes Dual CPU QA')
    info['BBQALaunchReceipt']=str(Path(case['data'])/'out/launch-conditions.json');info_path.write_bytes(plistlib.dumps(info))
    prefs_path=Path(case['preferences']);prefs=plistlib.loads(prefs_path.read_bytes())
    need(prefs['online']is False and prefs['cloudSaves']is False and prefs['userFolder']==case['user'],'original isolated QA preferences required')
    # The old QA seed is for progress only, not an authority for present-day
    # rendering settings. Match current original-frontend choices read-only.
    formal_ini=owned(Path(settings_root)/'bbport.ini');formal_sha=sha(formal_ini)
    raw=subprocess.check_output(['/usr/bin/defaults','export','io.github.bbport.optimized','-'])
    formal_preferences=plistlib.loads(raw);quality=apply_quality(prefs,formal_preferences)
    config=Path(case['data'])/'bbport.ini';shutil.copy2(formal_ini,config)
    need(sha(formal_ini)==formal_sha==sha(config),'settings snapshot changed during isolated copy')
    extra=dict(row.split('=',1)for row in prefs['extraEnv'].splitlines()if '='in row)
    gpu=P/'state'/('gui-gpu-local-aot-'+str(time.time_ns()));gpu.mkdir(mode=0o700)
    extra.update(BB_GPU_USER_DIR=str(gpu),BB_AUDIO='none')
    # Current source payloads need the same enabled lifetime/payload policies
    # as the existing full-runtime QA packager, without its old cache target.
    extra.update(BB_ARM_PAYLOAD_GATE='1',BB_ARM_RAW_PREWRITE='1',BB_LAYER_READ_TRAPS='1',BB_GPU_LIFETIME='1')
    validate_manual_environment(extra);prefs.update(cpuBackend='bbcpu',extraEnv='\n'.join(key+'='+value for key,value in extra.items()))
    prefs_path.write_bytes(plistlib.dumps(prefs));preferences=seed_qa_preferences(case)
    subprocess.run(['/usr/bin/codesign','--force','--sign','-',str(app)],check=True,capture_output=True)
    subprocess.run(['/usr/bin/codesign','--verify','--strict',str(app)],check=True,capture_output=True)
    need(launcher_code_identity(app/'Contents/MacOS/bbport',output)==identity,'GUI code changed during QA signing')
    for name,expected in admitted.native['frozen_runtime'].items():need(sha(owned(stage/name))==expected,'current FEX/Metal/net bytes changed during assembly')
    case['shell_cpu_sha256']=case['cpu_sha256'];case['cpu_sha256']=sha(stage/'bin/bb-probe-metal')
    native=json.loads(native_path.read_bytes())
    report=dict(schema=2,kind='source-built-independent-cpu-qa',host_local_only=True,formal_data_modified=False,protected_resource_writeback=True,
        status='isolated-dual-cpu-package-built-not-run',app=str(app),case=case,private_case=case,source_app=str(source_app),
        original_current_runtime=admitted.native['frozen_runtime'],source_revision=admitted.native.get('source_revision'),
        original_scripts=closure['parity']['original_sources'],effective_scripts=closure['parity']['effective_sources'],
        frozen_runtime=native['frozen_runtime'],private_gpu_cache=str(gpu),
        original_launcher_source_closure=closure['parity'],independent_cpu=cpu_component,source_gui_executable_sha256=sha(launcher),launcher_code_identity_sha256=identity,
        namespace_helper_sha256=helper_sha,QA_preferences=preferences,new_GPU_cache=str(gpu),formal_writes=False,
        quality_snapshot=dict(rendering_preferences=quality,original_ini_sha256=formal_sha,formal_source_read_only=True),
        game_started=False,performance_verified=False,Release_updated=False)
    # The existing Swift observation hashes this exact QA manifest before
    # Process.run. Supply that contract, not a separate environment exporter.
    write(resources/'ARM64-QA-MANIFEST.json',report)
    subprocess.run(['/usr/bin/codesign','--force','--sign','-',str(app)],check=True,capture_output=True)
    subprocess.run(['/usr/bin/codesign','--verify','--strict',str(app)],check=True,capture_output=True)
    need(launcher_code_identity(app/'Contents/MacOS/bbport',output)==identity,'QA manifest signing changed GUI code')
    # Signature/resource sealing is not GUI code identity. Record the final
    # full executable SHA externally; do not introduce a self-referential hash.
    report['signed_gui_executable_sha256']=sha(app/'Contents/MacOS/bbport')
    write(output/'result.json',report);return report
def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in('source-app','repository','launcher-products','build','game','toolchain','output','settings-root'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--dependency-root',type=Path,action='append',required=True);a=p.parse_args()
    result=assemble(source_app=a.source_app,repository=a.repository,launcher_products=a.launcher_products,build=a.build,game=a.game,
        toolchain=json.loads(owned(a.toolchain).read_bytes()),search_dirs=a.dependency_root,output=a.output.absolute(),settings_root=a.settings_root)
    print(json.dumps(dict(status=result['status'],app=result['app'],result=str(a.output/'result.json'))))
if __name__=='__main__':main()
