#!/usr/bin/env python3
"""One authored TU only. Never rebuild the GPU library or touch an old cache."""
from pathlib import Path
import argparse,hashlib,json,shutil,subprocess,sys,time
P=Path('/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype')
E=Path(__file__).resolve().parent
sys.path.insert(0,str(P/'tools'))
from qa_build_guard import require_no_games
ap=argparse.ArgumentParser()
ap.add_argument('--source-root',required=True,type=Path)
ap.add_argument('--library',type=Path)
ap.add_argument('--library-sha256')
ap.add_argument('--execution-root',type=Path)
ap.add_argument('--syntax-only',action='store_true')
a=ap.parse_args();require_no_games()
S=a.source_root.resolve(strict=True)
assert (S/'vendor/metal_api/shadps4/video_core/renderer_metal/mtl_pso_cache.h').is_file()
meta=json.loads((P/'evidence/texture-expansion-build.json').read_text())
cmd=list(meta['compile'])
for option in ('-o','-MF','-MT'):
 i=cmd.index(option);del cmd[i:i+2]
cmd.remove('-MD');cmd.remove('-c');cmd.remove('-DNDEBUG')
cmd[-1]=str(E/'derived_native.mm')
cmd[1:1]=['-I'+str(E),'-I'+str(P/'tests/metal'),'-I'+str(S/'vendor/metal_api'),
 '-I'+str(S/'vendor/metal_api/shim'),'-I'+str(S/'vendor/metal_api/shadps4')]
if a.syntax_only:
 cmd.insert(1,'-fsyntax-only')
 with (E/'syntax.log').open('w')as out:
  r=subprocess.run(cmd,stdout=out,stderr=subprocess.STDOUT)
 (E/'syntax.json').write_text(json.dumps(dict(command=cmd,exit=r.returncode),indent=2)+'\n')
 print((E/'syntax.log').read_text());sys.exit(r.returncode)
assert a.library and a.library_sha256 and a.execution_root
lib=a.library.resolve(strict=True)
sha=lambda p:hashlib.file_digest(p.open('rb'),'sha256').hexdigest()
assert sha(lib)==a.library_sha256
X=a.execution_root.resolve(strict=True)
assert X.parent==P/'evidence' and not a.execution_root.is_symlink()
assert not (X/'derived-native').exists() and not (X/'derived-native.o').exists()
shutil.copy2(E/'derived_native.mm',X/'derived_native.mm')
shutil.copytree(E/'generated',X/'generated',dirs_exist_ok=True)
for n in ('authored.vert','authored.frag'):
 shutil.copy2(E/n,X/n)
 subprocess.run(['/opt/homebrew/bin/glslangValidator','-V','--target-env','vulkan1.2',
                 str(X/n),'-o',str(X/(n+'.spv'))],check=True)
 subprocess.run(['/opt/homebrew/bin/spirv-val','--target-env','vulkan1.2',str(X/(n+'.spv'))],check=True)
obj=X/'derived-native.o';exe=X/'derived-native'
cmd[-1]=str(X/'derived_native.mm');cmd[1:1]=['-c','-o',str(obj)]
oldobj=meta['compile'][meta['compile'].index('-o')+1]
link=[str(obj)if x==oldobj else str(lib)if '/gpu-metal-arm64/libbbgpu.dylib' in x else x for x in meta['link']]
link[link.index('-o')+1]=str(exe)
with (E/'build.log').open('w')as out:
 for action in (cmd,link):
  r=subprocess.run(action,stdout=out,stderr=subprocess.STDOUT)
  if r.returncode:print((E/'build.log').read_text());sys.exit(r.returncode)
def dependencies(path):
 return [l.strip().split(' (compatibility')[0]for l in subprocess.check_output(['/usr/bin/otool','-L',str(path)],text=True).splitlines()[1:]]
expected={Path(x).name:str((lib.parent/x.removeprefix('@loader_path/')).resolve())if x.startswith('@loader_path/')else x for x in dependencies(lib)}
rewrites=[]
for ref in dependencies(exe):
 target=str(lib)if Path(ref).name.startswith('libbbgpu') else expected.get(Path(ref).name)
 if target and ref!=target:
  subprocess.run(['/usr/bin/install_name_tool','-change',ref,target,str(exe)],check=True)
  rewrites.append((ref,target))
subprocess.run(['/usr/bin/codesign','--force','--sign','-',str(exe)],check=True)
subprocess.run(['/usr/bin/codesign','--verify','--strict',str(exe)],check=True)
record=dict(binary=str(exe),binary_sha256=sha(exe),library=str(lib),library_sha256=sha(lib),
 source_root=str(S),execution_root=str(X),compile=cmd,link=link,rewrites=rewrites,
 full_gpu_build=False,source_sha256=sha(E/'derived_native.mm'),
 dependencies=dependencies(exe),authored_spirv={n:sha(X/n)for n in ('authored.vert.spv','authored.frag.spv')})
(E/'build.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps(record,indent=2))
