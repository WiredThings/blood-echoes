"""Old native GPU resolver only. Never calls guest or CPU-runtime resolver."""
from pathlib import Path
import json,subprocess,time,sys
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'))
from qa_build_guard import require_no_games
from gpu_candidate_identity import verify
require_no_games();m=json.loads((P/'evidence/first-upload-build-current.json').read_text());assert m['suite']=='avplayer-exports';tag=str(time.time_ns());e=P/'evidence'/('legacy-nine-import-resolver-'+tag);e.mkdir();state=P/'state'/('legacy-nine-import-resolver-'+tag);state.mkdir();identity=verify(dict(target=m['binary'],library=m['library'],library_sha256=m['library_sha256']),P)
with(e/'run.log').open('w')as log:r=subprocess.run([m['binary']],stdout=log,stderr=subprocess.STDOUT,timeout=15,env=dict(PATH='/usr/bin:/bin',HOME=str(state),BB_GPU_USER_DIR=str(state),BB_ONLINE='0'))
text=(e/'run.log').read_text();passed=r.returncode==0 and text.count('UNRESOLVED_HOST ')==9 and text.count('FOUND ')==16
result=dict(passed=passed,game_started=False,original_host_gpu_resolver_only=True,CPU_runtime_resolver_executed=False,positive_AV_exports=16,unresolved_imports=9,identity=identity,exit=r.returncode);(e/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(e);assert passed,text[-2000:]
