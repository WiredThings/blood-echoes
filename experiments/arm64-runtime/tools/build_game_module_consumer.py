#!/usr/bin/env python3
"""Build the CPU-only GCN consumer against a pinned prior native library.

A targeted translation test, not a fresh coherent renderer build. Old native
objects/library are read-only; all compiled outputs go to a new evidence root.
"""
import argparse, hashlib, json, pathlib, subprocess, time, re

def sha(p): return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--project',type=pathlib.Path,required=True);ap.add_argument('--diagnostic',action='store_true');args=ap.parse_args()
    root=pathlib.Path(__file__).resolve().parents[1]
    profile=root/'vendor/metal_api/shadps4/shader_recompiler/profile.h'
    bools=re.findall(r'\bbool\s+(\w+)\s*(?:\{|;)',profile.read_text())
    listed=re.findall(r'MODULE_PROFILE_BOOL_FIELD\((\w+)\)',(root/'src/module_profile_bool_fields.inc').read_text())
    if bools!=listed:raise ValueError('Profile boolean preflight must match canonical header')
    baseline=root/'evidence/tiling-library-integration-20261026/native/build.json'
    data=json.loads(baseline.read_text()); old=data['source_root'];lib=pathlib.Path(data['library'])
    if sha(lib)!=data['library_sha256']:raise ValueError('pinned native library changed')
    evidence=(args.project/'evidence').resolve();out=evidence/f'game-module-native-{time.time_ns()}'
    out.mkdir(); unit=next(u for u in data['units'] if 'game_artifact_native' in u['source'])
    units=[]
    sources=[root/'src/game_module_preparer.mm',root/'vendor/metal_api/shadps4/shader_recompiler/recompiler.cpp']
    for i,src in enumerate(sources):
        cmd=[v.replace(old,str(root)) for v in unit['command']]
        cmd[-1]=str(src);cmd[cmd.index('-o')+1]=str(out/f'unit{i}.o')
        if args.diagnostic:cmd.insert(1,'-DBB_MODULE_DIAGNOSTIC=1')
        cmd[-1:-1]=['-MMD','-MF',str(out/f'unit{i}.d')]
        log=out/f'compile{i}.log'
        with log.open('w') as f:r=subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,timeout=180)
        units.append({'source':str(src),'sha256':sha(src),'command':cmd,'exit':r.returncode,'log':str(log)})
        if r.returncode:print(log.read_text()[-12000:]);raise SystemExit(r.returncode)
    link=data['binaries']['worker']['link'][:]
    first=next(i for i,v in enumerate(link) if v.endswith('/worker.o'))
    link[first:first+1]=[str(out/'unit0.o'),str(out/'unit1.o')]
    binary=out/'game-module-preparer';link[link.index('-o')+1]=str(binary)
    with (out/'link.log').open('w') as f:r=subprocess.run(link,stdout=f,stderr=subprocess.STDOUT,timeout=180)
    if r.returncode:print((out/'link.log').read_text()[-12000:]);raise SystemExit(r.returncode)
    def deps(p):
        return [l.strip().split(' (compatibility')[0] for l in subprocess.check_output(['/usr/bin/otool','-L',str(p)],text=True).splitlines()[1:]]
    expected={pathlib.Path(x).name:str((lib.parent/x.removeprefix('@loader_path/')).resolve()) if x.startswith('@loader_path/') else x for x in deps(lib)}
    for edge in deps(binary):
        target=str(lib) if pathlib.Path(edge).name.startswith('libbbgpu') else expected.get(pathlib.Path(edge).name)
        if target and target!=edge:subprocess.run(['/usr/bin/install_name_tool','-change',edge,target,str(binary)],check=True,capture_output=True)
    subprocess.run(['/usr/bin/codesign','--force','--sign','-',str(binary)],check=True,capture_output=True)
    subprocess.run(['/usr/bin/codesign','--verify','--strict',str(binary)],check=True,capture_output=True)
    if subprocess.check_output(['/usr/bin/lipo','-archs',str(binary)],text=True).strip()!='arm64':raise ValueError('native consumer architecture')

    result={'kind':'targeted-cpu-module-consumer','gpu_started':False,'fresh_coherent_renderer':False,'diagnostic_only':args.diagnostic,'baseline_manifest':str(baseline),'baseline_sha256':sha(baseline),'pinned_library':str(lib),'pinned_library_sha256':sha(lib),'units':units,'link':link,'binary':str(binary),'binary_sha256':sha(binary),'profile_boolean_fields':bools,'dependencies':deps(binary)}
    (out/'build.json').write_text(json.dumps(result,indent=2)+'\n');print(out/'build.json')
if __name__=='__main__':main()
