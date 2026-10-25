"""Actual GCN/SPIR-V/BuildLibrary capture or load, authored input only."""
from pathlib import Path
import argparse,hashlib,json,os,re,subprocess,time
from qa_build_guard import require_no_games
from warm_archive_manifest import resolve_gpu_identity
P=Path(__file__).resolve().parents[1]
ap=argparse.ArgumentParser();ap.add_argument('--binary',required=True,type=Path);ap.add_argument('--mode',choices=['record','load'],required=True);ap.add_argument('--build-id');a=ap.parse_args()
require_no_games();base=json.loads((P/'evidence/local-aot-authored-current.json').read_text());binary=a.binary.resolve(strict=True);identity=resolve_gpu_identity(binary,P);assert identity['library_sha256']==base['identity']['library_sha256']
case=Path(base['case']);folder=case/('phase-'+a.mode+'-'+str(time.time_ns()));folder.mkdir()
env={k:v for k,v in os.environ.items()if not k.startswith(('BB_','DYLD_'))}
env.update(BB_GPU_USER_DIR=str(case/'gpu'),HOME=str(case),TMPDIR=str(case/'tmp')+'/',DYLD_PRINT_LIBRARIES='1',BB_MTL_LOCAL_AOT_ROOT=base['root'],BB_MTL_LOCAL_AOT_COHORT=base['cohort'],BB_MTL_LOCAL_AOT_MODE=a.mode,BB_MTL_COMPILE_AUDIT='1')
if a.build_id:env['BB_MTL_LOCAL_AOT_BUILD_ID']=a.build_id
proc=subprocess.run([str(binary),str(folder)],env=env,capture_output=True,text=True,timeout=30);(folder/'console.log').write_text(proc.stdout+proc.stderr);assert proc.returncode==0,str(folder)
loaded=[x for x in proc.stderr.splitlines()if'libbbgpu'in x and'.dylib'in x];assert len(loaded)==1 and identity['library']in loaded[0]
rows=re.findall(r'Metal local AOT: (\S+) key=([0-9a-f]{64}) recorded=(\d)',proc.stdout);assert len(rows)==2 and rows[0][1]==rows[1][1]
libs=re.findall(r'Metal compile audit: kind=library .* ok=(\d) cached=(-?\d+)',proc.stdout);assert len(libs)==2 and all(x==('1','1'if a.mode=='load'else'0')for x in libs)
assert all(row[0]==('hit'if a.mode=='load'else'source')for row in rows)
assert hashlib.sha256((folder/'authored.spv').read_bytes()).hexdigest()=='623decccd1e59958cf087381db5a03d4a07ffbf50f161f3f368f1a2d78b99083'
r=dict(passed=True,mode=a.mode,folder=str(folder),identity=identity,rows=rows,shader_source_driver_calls_skipped=a.mode=='load',authored_spirv_unchanged=True,game_started=False)
(folder/'result.json').write_text(json.dumps(r,indent=2)+'\n');(case/('phase-'+a.mode+'-current.json')).write_text(json.dumps(r,indent=2)+'\n');print(folder);print('PASS actual BuildLibrary',a.mode,'two modules, stable key and original SPIR-V')
