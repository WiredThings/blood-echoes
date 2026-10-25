# Authored guest-read/decoder controls against the explicitly built isolated GPU library.
from pathlib import Path
import hashlib,json,subprocess,sys,time
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'))
from qa_build_guard import require_no_games
from gpu_candidate_identity import verify
require_no_games()
meta=json.loads((P/'evidence/first-upload-build-current.json').read_text())
assert meta['suite']=='speculative-reads','build --suite speculative-reads with the new isolated GPU library first'
exe=Path(meta['binary']);library=Path(meta['library'])
assert exe==exe.resolve() and exe.is_relative_to(P/'evidence')
identity=verify(dict(target=str(exe),library=str(library),library_sha256=meta['library_sha256']),P)
e=P/'evidence'/('shader-speculative-reads-controls-'+str(time.time_ns()));e.mkdir()
private=P/'state'/e.name;private.mkdir();(private/'tmp').mkdir();gpu=private/'gpu';gpu.mkdir()
env=dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin',HOME=str(private),TMPDIR=str(private/'tmp'),
         BB_GPU_USER_DIR=str(gpu),BB_PIPELINE_CACHE='0')
with(e/'console.log').open('w') as log:
    result=subprocess.run([str(exe)]+(['--bench'] if '--bench' in sys.argv else []),env=env,stdout=log,stderr=subprocess.STDOUT,timeout=30)
record=dict(passed=result.returncode==0,returncode=result.returncode,game_started=False,
            authored_inputs_only=True,benchmark='--bench' in sys.argv,gpu_load_identity=identity,
            source_sha256=hashlib.sha256((P/'tests/metal/test_shader_speculative_reads.mm').read_bytes()).hexdigest())
(e/'result.json').write_text(json.dumps(record,indent=2)+'\n')
assert result.returncode==0,str(e/'console.log')
print(e)
print('PASS speculative reads controls (or independent CPU-only successful-copy benchmark)')
