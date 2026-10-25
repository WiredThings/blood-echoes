"""Build only the authored real-Metal regression against an explicit private library."""
from pathlib import Path
import argparse,hashlib,json,subprocess,time
from qa_build_guard import require_no_games
P=Path(__file__).resolve().parents[1]
ap=argparse.ArgumentParser();ap.add_argument('--suite',choices=['first-upload','expansion','partial-gpu','inplace-gpu','shader-phases','owned-shader','guest-capture','speculative-reads','stable-mirror','warm-archive','warm-stage','avplayer-exports'],default='first-upload');ap.add_argument('--library',type=Path);a=ap.parse_args()
library=(a.library or P/'backend/bbport/bin/gpu-metal-arm64/libbbgpu.dylib').resolve()
assert library.is_relative_to(P/'backend')and library.is_file()
require_no_games()
meta=json.loads((P/'evidence/texture-expansion-build.json').read_text())
e=P/'evidence'/('first-upload-regression-'+str(time.time_ns()));e.mkdir()
obj=e/'first-upload.o';cmd=list(meta['compile']);oldobj=cmd[cmd.index('-o')+1]
cmd[cmd.index('-o')+1]=str(obj);cmd[cmd.index('-MF')+1]=str(obj.with_suffix('.d'))
cmd[-1]=str(P/'tests/metal'/({'first-upload':'test_texture_first_upload.mm','expansion':'test_texture_expansion.mm','partial-gpu':'test_texture_partial_gpu.mm','inplace-gpu':'test_texture_inplace_gpu.mm','shader-phases':'test_shader_compile_phases.mm','owned-shader':'test_shader_owned_snapshot.mm','guest-capture':'test_shader_guest_capture.mm','speculative-reads':'test_shader_speculative_reads.mm','stable-mirror':'test_stable_mirror_library.mm','warm-archive':'test_warm_archive_process.mm','warm-stage':'test_warm_stage_storage.cpp','avplayer-exports':'test_avplayer_exports.mm'}[a.suite]));cmd.insert(cmd.index('-c'),'-UNDEBUG');cmd.insert(1,'-I'+str(P/'src'))
if a.suite=='warm-stage':cmd.insert(1,'-I'+str(P/'vendor/metal_api'))
b=Path('/private/tmp/blood-echoes-arm64-metal-20261023/build')
with(e/'build.log').open('w')as log:
    r=subprocess.run(cmd,cwd=b,stdout=log,stderr=subprocess.STDOUT)
    assert r.returncode==0,str(e/'build.log')
    link=[str(obj)if x==oldobj else str(library)if x.endswith('/gpu-metal-arm64/libbbgpu.dylib')else x for x in meta['link']]
    dest=e/'first-upload-test';link[link.index('-o')+1]=str(dest)
    r=subprocess.run(link,cwd=b,stdout=log,stderr=subprocess.STDOUT)
    assert r.returncode==0,str(e/'build.log')
def dependencies(path):
    return [x.strip().split(' (compatibility')[0]for x in subprocess.check_output(['otool','-L',str(path)],text=True).splitlines()[1:]]
expected={Path(x).name:str((library.parent/x.removeprefix('@loader_path/')).resolve())if x.startswith('@loader_path/')else x for x in dependencies(library)}
changes=[]
for reference in dependencies(dest):
    target=expected.get(Path(reference).name)
    if target and target!=reference:
        subprocess.run(['install_name_tool','-change',reference,target,str(dest)],check=True);changes.append((reference,target))
subprocess.run(['codesign','--force','--sign','-',str(dest)],check=True)
record=dict(suite=a.suite,evidence=str(e),binary=str(dest),compile=cmd,link=link,library=str(library),dependency_rewrites=changes,library_sha256=hashlib.file_digest(library.open('rb'),'sha256').hexdigest())
(e/'build.json').write_text(json.dumps(record,indent=2)+'\n')
(P/'evidence/first-upload-build-current.json').write_text(json.dumps(record,indent=2)+'\n');print(e)
