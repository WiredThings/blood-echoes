"""Real runtime C key/finalize controls; only the documented guest callbacks are mocked."""
from pathlib import Path
import hashlib,json,subprocess,sys,time
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'))
from qa_build_guard import require_no_games
require_no_games();e=P/'evidence'/('module-finalizer-native-'+str(time.time_ns()));e.mkdir();rows=[]
report=dict(passed=False,game_started=False,GPU_started=False,ownership_is_mocked=True,cases=rows)
for name in ['test_module_keys_candidate','test_module_atexit_candidate']:
 source=P/'tests'/(name+'.c');binary=e/name
 cmd=['/usr/bin/clang','-std=c11','-O2','-arch','arm64','-UNDEBUG','-ffunction-sections','-fdata-sections','-Wl,-dead_strip',*[str('-I'+str(P/x))for x in ['vendor/bb_runtime/src','vendor/bb_runtime','src']],'-I/opt/homebrew/include',str(source),'-o',str(binary)]
 row=dict(test=name,source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),compile=cmd);rows.append(row)
 with(e/(name+'-compile.log')).open('w')as log:r=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT)
 row['compile_exit']=r.returncode;(e/'result.json').write_text(json.dumps(report,indent=2)+'\n');assert r.returncode==0,e
 with(e/(name+'-run.log')).open('w')as log:r=subprocess.run([str(binary)],stdout=log,stderr=subprocess.STDOUT,timeout=40)
 row['run_exit']=r.returncode;(e/'result.json').write_text(json.dumps(report,indent=2)+'\n');assert r.returncode==0,e
report['passed']=True;(e/'result.json').write_text(json.dumps(report,indent=2)+'\n');print(e)
