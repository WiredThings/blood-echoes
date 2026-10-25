"""Bind a fresh private GPU app to a verified candidate without replacing any existing app."""
from pathlib import Path
import argparse,json,shutil,subprocess,time
from qa_build_guard import require_no_games
from gpu_candidate_identity import verify,sha
P=Path(__file__).resolve().parents[1]
ap=argparse.ArgumentParser();ap.add_argument('--candidate',type=Path,required=True);a=ap.parse_args()
require_no_games();source=a.candidate.resolve(strict=True);assert source.is_relative_to(P/'evidence')
m=json.loads(source.read_text());library=Path(m['candidate']);assert sha(library)==m['sha256']
base=json.loads((P/'evidence/first-upload-gpu-app.json').read_text());verify(base,P/'backend')
app=P/'backend/bbport/bin'/('Bloodborne Metal Candidate QA '+str(time.time_ns())+'.app')
assert not app.exists();shutil.copytree(Path(base['app']),app);target=app/'Contents/MacOS/bb-gpu'
deps=[l.strip().split(' (compatibility')[0]for l in subprocess.check_output(['otool','-L',str(target)],text=True).splitlines()[1:]]
hits=[d for d in deps if Path(d).name.startswith('libbbgpu')];assert len(hits)==1
subprocess.run(['install_name_tool','-change',hits[0],str(library),str(target)],check=True)
subprocess.run(['codesign','--force','--deep','--sign','-',str(app)],check=True)
meta=dict(target=str(target),library=str(library),library_sha256=m['sha256'],app=str(app),source_candidate=str(source),default_enabled=False,formal_app_modified=False)
meta['load_identity']=verify(meta,P/'backend');out=P/'evidence'/('gpu-candidate-app-'+str(time.time_ns())+'.json');out.write_text(json.dumps(meta,indent=2)+'\n');print(out)
