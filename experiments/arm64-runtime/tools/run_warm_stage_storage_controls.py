"""Private authored LoadPipelineStage/Storage controls with actual library verification."""
from pathlib import Path
import argparse,json,os,subprocess,time
from qa_build_guard import require_no_games
from warm_archive_manifest import resolve_gpu_identity
P=Path(__file__).resolve().parents[1]
ap=argparse.ArgumentParser();ap.add_argument('--binary',type=Path,required=True);ap.add_argument('--library',type=Path,required=True);a=ap.parse_args()
require_no_games();binary=a.binary.resolve(strict=True);library=a.library.resolve(strict=True)
identity=resolve_gpu_identity(binary,P);assert Path(identity['library'])==library
case=P/'state'/('warm-stage-authored-'+str(time.time_ns()));case.mkdir(mode=0o700)
for name in ['gpu','tmp']:(case/name).mkdir()
env={k:v for k,v in os.environ.items()if not k.startswith(('BB_','DYLD_'))}
env.update(HOME=str(case),TMPDIR=str(case/'tmp')+'/',BB_GPU_USER_DIR=str(case/'gpu'),DYLD_PRINT_LIBRARIES='1')
proc=subprocess.run([str(binary),str(case)],env=env,text=True,capture_output=True,timeout=60)
(case/'stdout.log').write_text(proc.stdout);(case/'stderr.log').write_text(proc.stderr)
loaded=[line for line in proc.stderr.splitlines()if 'libbbgpu'in line and '.dylib'in line]
verified=len(loaded)==1 and str(library)in loaded[0]
result=dict(exit=proc.returncode,passed=proc.returncode==0 and verified,actual_load_verified=verified,load_identity=identity,case=str(case),backend_module_factory='authored counting mock only',actual_storage_meta_and_stage_path=True,game_launched=False)
(case/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2));print(proc.stdout[-2500:]);assert result['passed']
