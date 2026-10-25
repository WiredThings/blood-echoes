from pathlib import Path
import json,shutil,subprocess,time,sys
p=Path(__file__).resolve().parents[1];sys.path.insert(0,str(p/'tools'));from gpu_candidate_identity import verify
from qa_build_guard import require_no_games
require_no_games();m=json.loads((p/'evidence/raw-write-audit-candidate-current.json').read_text());base=json.loads((p/'evidence/first-upload-gpu-app.json').read_text());app=p/'backend/bbport/bin'/('Bloodborne Metal Raw Write QA '+str(time.time_ns())+'.app');shutil.copytree(Path(base['app']),app);target=app/'Contents/MacOS/bb-gpu'
deps=[l.strip().split(' (compatibility')[0]for l in subprocess.check_output(['otool','-L',str(target)],text=True).splitlines()[1:]];hits=[d for d in deps if Path(d).name.startswith('libbbgpu')];assert len(hits)==1;subprocess.run(['install_name_tool','-change',hits[0],m['candidate'],str(target)],check=True);subprocess.run(['codesign','--force','--deep','--sign','-',str(app)],check=True);meta=dict(target=str(target),original_target=base['original_target'],library=m['candidate'],library_sha256=m['sha256'],diagnostic_only=True,default_enabled=False,formal_app_modified=False);meta['load_identity']=verify(meta,p/'backend');(p/'evidence/raw-write-audit-gpu-app.json').write_text(json.dumps(meta,indent=2)+'\n')
print(app)
