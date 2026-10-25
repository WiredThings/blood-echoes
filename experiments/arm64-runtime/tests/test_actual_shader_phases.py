"""Authored GCN/Metal positive control; no game files or windows."""
from pathlib import Path
import hashlib,json,os,subprocess,sys,time
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'))
from qa_build_guard import require_no_games
from gpu_candidate_identity import verify
from analyze_stutter import read
from shader_phase_analysis import analyze_shader_phases
require_no_games()
meta=json.loads((P/'evidence/first-upload-build-current.json').read_text())
assert meta['suite']=='shader-phases','build the shader-phases fixture first'
exe=Path(meta['binary']);library=Path(meta['library'])
assert exe==exe.resolve() and exe.is_relative_to(P/'evidence')
identity=verify(dict(target=str(exe),library=str(library),library_sha256=meta['library_sha256']),P)
tag=str(time.time_ns());e=P/'evidence'/('actual-shader-phase-controls-'+tag);e.mkdir();state=P/'state'/('authored-shader-phase-'+tag);state.mkdir()
runs=[]
for enabled in [False,True]:
 d=e/('on'if enabled else'off');d.mkdir();private=state/d.name;private.mkdir()
 env={k:v for k,v in os.environ.items()if not k.startswith(('BB_','BACHATA_','DYLD_'))}
 env.update(BB_STUTTER_AUDIT=str(d/'trace'),BB_STUTTER_COARSE='1',HOME=str(d),BB_GPU_USER_DIR=str(private))
 if enabled:env['BB_SHADER_PHASE_AUDIT']='1'
 with(d/'console.log').open('w')as log:subprocess.run([str(exe),str(d)],env=env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=30)
 files=list(d.glob('trace-*.bin'));assert len(files)==1
 trace=read(files[0]);analysis=analyze_shader_phases(trace['rows']);modules=analysis['modules']
 assert trace['all_published'] and not analysis['orphan_phase_sequences']
 if enabled:
  assert len(modules)==2 and all(x['complete'] and x['stage']==6 and x['nonzero_module_handle'] for x in modules)
  assert sorted(x['permutation'] for x in modules)==[0,1]
 else:assert not modules
 runs.append(dict(mode='on'if enabled else'off',passed=True,spirv_sha256=hashlib.sha256((d/'authored.spv').read_bytes()).hexdigest(),trace={k:v for k,v in trace.items()if k!='rows'},analysis=analysis))
assert runs[0]['spirv_sha256']==runs[1]['spirv_sha256'],'phase probe changed emitted SPIR-V'
(e/'result.json').write_text(json.dumps(dict(passed=True,game_started=False,authored_inputs_only=True,gpu_load_identity=identity,runs=runs),indent=2)+'\n')
print(e);print('PASS actual GCN to Metal controls, probe off/on SPIR-V identical')
