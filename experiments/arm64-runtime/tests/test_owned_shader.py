"""Run the authored owned-shader test against an explicit private ARM64 library."""
from pathlib import Path
import argparse,hashlib,json,os,resource,subprocess,sys,time
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'))
from qa_build_guard import require_no_games
from native_observer import NativeObserver
from qa_memory_policy import MemoryPolicy

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--library',type=Path,required=True);a=ap.parse_args();require_no_games();library=a.library.resolve();assert library.is_relative_to(P/'backend')and library.is_file()
 subprocess.run([sys.executable,str(P/'tools/build_first_upload_test.py'),'--suite','owned-shader','--library',str(library)],check=True)
 m=json.loads((P/'evidence/first-upload-build-current.json').read_text());assert m['suite']=='owned-shader'and Path(m['library'])==library
 e=Path(m['evidence']);state=P/'state'/e.name;state.mkdir();home=state/'home';home.mkdir();gpu=state/'gpu';gpu.mkdir()
 observer=NativeObserver();before=observer.system();assert not before['locked']and before['pressure']!=4;policy=MemoryPolicy(before);started=time.monotonic()
 env=dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin',HOME=str(home),TMPDIR=str(state)+'/',BB_GPU_USER_DIR=str(gpu),BB_AUDIO='none',BB_ONLINE='0',BB_PIPELINE_CACHE='0',DYLD_PRINT_LIBRARIES='1',MTL_DEBUG_LAYER='1')
 with(e/'run.log').open('w')as log:
  proc=subprocess.Popen([m['binary'],str(state)],env=env,stdout=log,stderr=subprocess.STDOUT)
  try:
   while proc.poll()is None:
    reason=policy.observe(time.monotonic()-started,observer.system())
    if reason or time.monotonic()-started>45:raise RuntimeError(reason or 'timeout')
    time.sleep(.1)
  finally:
   if proc.poll()is None:proc.terminate();proc.wait(timeout=3)
 text=(e/'run.log').read_text();loaded=[x for x in text.splitlines()if 'dyld['in x and 'libbbgpu'in x];assert len(loaded)==1 and str(library)in loaded[0],loaded
 result=dict(passed=proc.returncode==0,exit=proc.returncode,evidence=str(e),library=str(library),library_sha256=hashlib.file_digest(library.open('rb'),'sha256').hexdigest(),actual_test='authored GCN/owned input/independent Metal context',game_started=False,scheduler_enabled=False,formal_data_modified=False,before=before,after=observer.system(),seconds=time.monotonic()-started,child_max_rss=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,child_max_rss_scope='all runner children including fixture compilation, not test-only peak',memory_policy=MemoryPolicy.MANIFEST)
 (e/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2));print('\n'.join(text.splitlines()[-14:]));assert proc.returncode==0
if __name__=='__main__':main()
