"""Record authored effective MSL requests through the actual native helper."""
from pathlib import Path
import argparse,hashlib,json,os,subprocess,time
from qa_build_guard import require_no_games
from warm_archive_manifest import resolve_gpu_identity
P=Path(__file__).resolve().parents[1]
ap=argparse.ArgumentParser();ap.add_argument('--binary',type=Path,required=True);ap.add_argument('--library',type=Path,required=True);a=ap.parse_args()
require_no_games();binary=a.binary.resolve(strict=True);library=a.library.resolve(strict=True);identity=resolve_gpu_identity(binary,P);assert Path(identity['library'])==library
case=P/'state'/('local-aot-authored-'+str(time.time_ns()));case.mkdir(mode=0o700)
for name in ['gpu','tmp']:(case/name).mkdir()
root=case/'gpu/local-metal';cohort=hashlib.sha256(b'authored local Metal AOT v1').hexdigest()
env={k:v for k,v in os.environ.items()if not k.startswith(('BB_','DYLD_'))}
env.update(BB_GPU_USER_DIR=str(case/'gpu'),HOME=str(case),TMPDIR=str(case/'tmp')+'/',DYLD_PRINT_LIBRARIES='1',BB_MTL_LOCAL_AOT_ROOT=str(root),BB_MTL_LOCAL_AOT_COHORT=cohort)
runs=[]
def run(name,mode,math,changed):
 d=case/name;d.mkdir();child_env=dict(env)
 if mode:child_env['BB_MTL_LOCAL_AOT_MODE']=mode
 proc=subprocess.run([str(binary),str(d),'0',math,changed],env=child_env,capture_output=True,text=True,timeout=30)
 (d/'console.log').write_text(proc.stdout+proc.stderr);assert proc.returncode==0,str(d)
 loaded=[line for line in proc.stderr.splitlines()if'libbbgpu'in line and'.dylib'in line];assert len(loaded)==1 and str(library)in loaded[0]
 result=json.loads((d/'result.json').read_text());assert result['readback_correct']and not result['hit'];runs.append(dict(name=name,**result))
run('off',None,'safe','plain');assert not root.exists()
root.mkdir(mode=0o700)
run('record-safe','record','safe','plain');run('record-relaxed','record','relaxed','plain');run('record-source','record','safe','changed')
requests=root/cohort/'requests';files=list(requests.glob('*.json'));assert len(files)==3
for f in files:
 m=json.loads(f.read_text());source=(f.with_suffix('.metal')).read_bytes();assert hashlib.sha256(source).hexdigest()==m['source_sha256']
 fields=[m[k]for k in ['recipe','cohort','source_sha256','entry','language','math_mode','math_fp32']]
 key=hashlib.sha256(b''.join(str(len(x.encode())).encode()+b':'+x.encode()for x in fields)).hexdigest();assert key==m['key']==f.stem
assert all(r['recorded']for r in runs[1:])and len({r['key']for r in runs[1:]})==3
r=dict(passed=True,case=str(case),root=str(root),cohort=cohort,binary=str(binary),library=str(library),identity=identity,runs=runs,game_started=False)
(case/'record-summary.json').write_text(json.dumps(r,indent=2)+'\n');(P/'evidence/local-aot-authored-current.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(dict(case=str(case),root=str(root),cohort=cohort,requests=3,passed=True),indent=2))
