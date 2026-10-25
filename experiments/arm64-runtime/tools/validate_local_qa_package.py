from pathlib import Path
import argparse,hashlib,json,os,plistlib,subprocess,sys,time,zipfile
P=Path('/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype');sys.path.insert(0,str(P/'tests'));sys.path.insert(0,str(P/'tools'))
from game_fixture import make
from qa_build_guard import require_no_games
from manual_qa_policy import validate_manual_environment
require_no_games()
parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=Path('/Users/k.d./Documents/Codex/2026-10-16/https-github-com-loreanxavier-pt-pc/artifacts/arm64-qa-20261025'));args=parser.parse_args()
out=args.output.resolve();manifest=json.loads((out/'runtime-manifest.json').read_text());archive=out/'Blood-Echoes-ARM64-Local-QA.zip';expected=(out/'SHA256SUMS').read_text().split()[0]
assert hashlib.file_digest(archive.open('rb'),'sha256').hexdigest()==expected
with zipfile.ZipFile(archive)as z:
 assert z.testzip()is None
 forbidden={'savedata','state','fixtures','cache','caches','.arm-runtime-session','preferences.plist','boot-linked.bin','content.bin'}
 assert not any(forbidden.intersection(Path(n).parts)or n.endswith(('.prx','.sdat','.bbprobe','.dds'))for n in z.namelist())
 target=Path('/private/tmp')/('blood-echoes-frozen-qa-'+str(time.time_ns()));target.mkdir()
 subprocess.run(['/usr/bin/ditto','-xk',str(archive),str(target)],check=True)
apps=list(target.glob('*.app'));assert len(apps)==1;app=apps[0];stage=app/'Contents/Resources/bbport'
subprocess.run(['/usr/bin/codesign','--verify','--strict',str(app)],check=True)
for rel,sha in manifest['frozen_runtime'].items():
 f=stage/rel;assert hashlib.file_digest(f.open('rb'),'sha256').hexdigest()==sha
 subprocess.run(['/usr/bin/codesign','--verify','--strict',str(f)],check=True)
from gpu_candidate_identity import verify as verify_gpu_candidate
native_gpu_identity=verify_gpu_candidate(dict(target=str(stage/'bin/Bloodborne Metal.app/Contents/MacOS/bb-gpu'),library=str(stage/'bin/gpu-metal-arm64/libbbgpu.dylib'),library_sha256=manifest['frozen_runtime']['bin/gpu-metal-arm64/libbbgpu.dylib']),stage)
preferences=plistlib.loads(Path(manifest['private_case']['preferences']).read_bytes());extra=dict(row.split('=',1)for row in preferences['extraEnv'].splitlines()if row);validate_manual_environment(extra)
case=P/'evidence'/('frozen-qa-package-'+str(time.time_ns()));case.mkdir();scratch=P/'state'/('frozen-qa-'+str(time.time_ns()));scratch.mkdir();config=scratch/'bbport.ini';config.write_text('');gpu=scratch/'gpu';gpu.mkdir()
env=dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin',HOME=str(scratch),TMPDIR=str(scratch)+'/',BB_CONFIG=str(config),BB_GPU_USER_DIR=str(gpu),BB_AUDIO='none',BB_ONLINE='0',BB_QUICKSAVE='0')
rows=[]
for index,(mode,status)in enumerate([('guest-exit:0',0),('guest-exit:75',82),('guest-exit:76',82),('missing-import',80)]):
 fixture=P/'fixtures'/('frozen-package-'+str(time.time_ns())+'.bbprobe');fixture.write_bytes(make(mode))
 r=subprocess.run([str(stage/'bin/bb-probe-metal'),'--authored-fixture','--boot',str(fixture),'--state',str(P/'state'/('frozen-package-runtime-'+str(time.time_ns())))],env=env,capture_output=True,text=True,timeout=30)
 (case/(str(index)+'.log')).write_text(r.stdout+r.stderr);assert r.returncode==status and 'implemented in both'not in r.stderr,(mode,r.returncode,r.stderr[-1200:]);rows.append(dict(mode=mode,status=r.returncode))
pyenv=dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin',PYTHONHOME=str(stage/'python'),HOME=str(scratch))
r=subprocess.run([str(stage/'python/bin/python3'),'-c','import platform,sys; print(platform.machine()); print(sys.version_info[:2])'],env=pyenv,capture_output=True,text=True,timeout=10);assert r.returncode==0 and 'arm64'in r.stdout,(r.returncode,r.stderr)
result=dict(passed=True,native_gpu_load_identity=native_gpu_identity,archive_sha256=expected,strict_signature_after_unzip=True,private_manual_input_policy=True,authored_FEX=rows,bundled_python=r.stdout.strip(),game_started=False,formal_data_modified=False,unzip_location=str(target),qualification='No live GUI/F3/menu/network/death acceptance; authored fixtures only.')
(case/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(case);print(json.dumps(result,indent=2))
