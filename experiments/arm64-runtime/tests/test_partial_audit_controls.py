"""Authored Metal controls: range visibility, NOT repaired pixel correctness.

Requires an explicit private partial-audit (optionally native-image) library and inplace fixture build.
Expected pixel failures are retained as negative controls, never called fixes.
"""
from pathlib import Path
import hashlib,json,os,subprocess,sys,time
P=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(P/'tools'))
from qa_build_guard import require_no_games
require_no_games()
meta=json.loads((P/'evidence/first-upload-build-current.json').read_text())
allowed={P/'backend/bbport/bin/gpu-metal-arm64/libbbgpu.partial-audit-candidate.dylib'}
combined=P/'evidence/present-image-gpu-app.json'
if combined.is_file():
 image=json.loads(combined.read_text())
 if image.get('range_audit'):allowed.add(Path(image['library']))
assert meta['suite']=='inplace-gpu'and Path(meta['library'])in allowed
assert hashlib.file_digest(Path(meta['library']).open('rb'),'sha256').hexdigest()==meta['library_sha256']
e=P/'evidence'/('partial-audit-controls-'+str(time.time_ns()));e.mkdir()
state=P/'state'/e.name;state.mkdir();rows=[]
for name in ['zero','potential','real-partial','real-full','real-interior','real-prefix']:
 d=e/name;d.mkdir();private=state/name;private.mkdir()
 env=dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin',HOME=str(private),TMPDIR=str(private)+'/',BB_GPU_USER_DIR=str(private),BB_STUTTER_AUDIT=str(d/'trace'),BB_STUTTER_COARSE='1')
 q=subprocess.run([meta['binary'],name],env=env,capture_output=True,text=True,timeout=30)
 text=q.stdout+q.stderr;(d/'console.log').write_text(text)
 hits=[l for l in text.splitlines()if l.startswith('PARTIAL_IMAGE_AUDIT ')]
 expected=0 if name=='real-full'else 7
 ok=q.returncode==expected and len(hits)==(0 if name=='real-full'else 1)and 'full_range=1'in text
 rows.append(dict(case=name,exit=q.returncode,expected_pixel_exit=expected,partial_intents=hits,diagnostic_control_passed=ok))
 print(name,q.returncode,len(hits),ok,flush=True)
r=dict(passed=all(x['diagnostic_control_passed']for x in rows),pixel_correctness_fixed=False,build=meta,controls=rows)
(e/'result.json').write_text(json.dumps(r,indent=2)+'\n');print(e)
assert r['passed']
