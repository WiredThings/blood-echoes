#!/usr/bin/env python3
"""Real frozen CPU/tool/source kit -> original launch hook -> authored static code.

No game, launcher window, user data or persistent application preference is used.
"""
from pathlib import Path
import argparse,json,os,plistlib,struct,subprocess,sys,time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from freeze_bbcpu_component import freeze
from bbcpu_launch import prepare
from run_bbcpu_authored_static import fixture

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in('build','game','compiler','linker','sdk','work'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--dependency-root',type=Path,action='append',required=True);a=p.parse_args()
    root=a.work.absolute();assert root.parent==Path('/private/tmp')and not root.exists();root.mkdir(mode=0o700)
    stage=root/'CPU QA.app/Contents/Resources/bbport';original=stage/'bin/Bloodborne Game.app/Contents'
    (original/'Resources').mkdir(parents=True,mode=0o700)
    (original/'Info.plist').write_bytes(plistlib.dumps(dict(CFBundleIdentifier='io.github.bbport.optimized.game',CFBundleExecutable='bb-probe-metal',CFBundleName='Bloodborne',CFBundlePackageType='APPL')))
    native=stage/'NATIVE-METAL.json';native.write_text(json.dumps(dict(schema=1,production=False,game_runtime='fex-arm64',frozen_runtime={})))
    toolchain=dict(compiler=str(a.compiler.resolve(strict=True)),linker=str(a.linker.resolve(strict=True)),sdk=str(a.sdk.resolve(strict=True)),dependency_roots=[str(path.resolve(strict=True))for path in a.dependency_root])
    frozen=freeze(stage,repository=Path(__file__).resolve().parents[3],build=a.build,game=a.game,toolchain=toolchain,search_dirs=a.dependency_root)
    project=root/'project';data=project/'state/case';out=data/'out';out.mkdir(parents=True,mode=0o700)
    source=out/'eboot.elf';source.write_bytes(fixture());raw=source.read_bytes()
    image=bytearray(24576);image[:8192]=raw[:8192];image[16384:]=raw[8192:]
    boot=out/'boot-linked.bin';boot.write_bytes(struct.pack('<8s6Q',b'BBPROBE2',len(image),4096,2,1,0,0)+struct.pack('<6Q',0,8192,5,16384,8192,4)+struct.pack('<4Q',16768,0,4096,0)+image)
    patches=out/'patches.bin';patches.write_bytes(b'BBPATCH2'+struct.pack('<2Q',0x400000,0))
    args=[str(boot),'--patches',str(patches)];env=dict(os.environ,BB_CPU_BACKEND='bbcpu',BB_DATA_DIR=str(data),BB_RESTART_NONCE='same-original-supervisor')
    exe,cold_env,cold=prepare('unused-fex-role',args,env,stage=stage,project=project)
    same,warm_env,warm=prepare('unused-fex-role',args,env,stage=stage,project=project)
    assert exe==same and cold_env['BB_RECOMP_LIB']==warm_env['BB_RECOMP_LIB'] and cold['effective_exported']and not warm['effective_exported']
    assert warm['producer']['reused_ready']and not any(warm['producer']['counters'].values())
    # Execute the resulting static code through real C++ admission, not this
    # Python scheduling receipt, and the actual pinned-fd dlopen path.
    r=subprocess.run([str(a.build/'bbcpu-static-admission-probe'),str(boot),str(Path(warm_env['BB_RECOMP_LIB']).parent)],capture_output=True,text=True,timeout=30)
    (root/'actual-admission.log').write_text(r.stdout+r.stderr);assert r.returncode==0,(r.stdout,r.stderr)
    safe={k:v for k,v in os.environ.items()if not k.startswith(('BB_','BBCPU_','DYLD_'))}
    with Path(warm_env['BB_RECOMP_LIB']).open('rb')as pinned:
        r=subprocess.run([str(a.build/'bbcpu-contract-probe'),f'/dev/fd/{pinned.fileno()}','static'],env=safe,pass_fds=(pinned.fileno(),),capture_output=True,text=True,timeout=30)
    (root/'actual-static-execution.log').write_text(r.stdout+r.stderr);assert r.returncode==0,(r.stdout,r.stderr)
    result=dict(status='passed',authored_only=True,real_frozen_kit=True,actual_library_execution=True,frozen=frozen,cold=cold,warm=warm,game_started=False)
    (root/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(status='passed',cold_seconds=cold['seconds'],warm_seconds=warm['seconds'],functions=cold['producer']['functions'],evidence=str(root))))
if __name__=='__main__':main()
