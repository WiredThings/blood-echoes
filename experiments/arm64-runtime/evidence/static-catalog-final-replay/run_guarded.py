from pathlib import Path
import sys,subprocess,time,json,signal,os,hashlib
P=Path('/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype')
D=Path('/Users/k.d./Documents/Codex/2026-10-16/https-github-com-loreanxavier-pt-pc/work/blood-echoes-arm64-audit/experiments/arm64-runtime')
sys.path.insert(0,str(P/'tools'))
from qa_build_guard import require_no_games
from native_observer import NativeObserver
from qa_memory_policy import MemoryPolicy
require_no_games()
label=str(time.time_ns());e=P/'evidence'/('static-catalog-final-'+label);e.mkdir();out=e/'scan'
args=[sys.executable,str(D/'tools/full_game_catalog.py'),'--game-root','/Users/k.d./Downloads/shadps4-game/CUSA03173-1.09','--prior-catalog',str(P/'evidence/fixed-game-shader-catalog-1792897460302182000/catalog.json'),'--out',str(out)]
observer=NativeObserver();before=observer.system();assert not before['locked']and before['pressure']!=4
policy=MemoryPolicy(before);started=time.monotonic();samples=[];stop=None
with(e/'scan.log').open('w')as log:
 proc=subprocess.Popen(args,cwd=D,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
 try:
  while proc.poll()is None:
   elapsed=time.monotonic()-started;s=observer.system();samples.append(dict(seconds=elapsed,**s));stop=policy.observe(elapsed,s)
   if stop or elapsed>900:
    stop=stop or 'bounded metadata scan deadline';os.killpg(proc.pid,signal.SIGTERM);break
   time.sleep(1)
  proc.wait(timeout=5)
 finally:
  if proc.poll()is None:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
result=dict(finished=proc.returncode==0,exit=proc.returncode,stop=stop,command=args,output=str(out),before=before,after=observer.system(),samples=samples,seconds=time.monotonic()-started,game_launched=False,formal_data_modified=False,source_sha256={str(f.relative_to(D)):hashlib.sha256(f.read_bytes()).hexdigest()for f in [D/'tools/full_game_catalog.py',D/'tools/game_shader_catalog.py']})
if(out/'summary.json').is_file():result['summary']=json.loads((out/'summary.json').read_text())
(e/'result.json').write_text(json.dumps(result,indent=2)+'\n');observer.close()
print(json.dumps({k:v for k,v in result.items()if k!='samples'},indent=2))
raise SystemExit(proc.returncode)
