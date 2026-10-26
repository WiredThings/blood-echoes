"""Bind a fresh private GPU app without replacing an existing app or build."""
from pathlib import Path
import argparse,json,shutil,subprocess,time
from qa_build_guard import require_no_games
from gpu_candidate_identity import verify,sha
PRIVATE_PROJECT=Path('/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype')

def prepare(candidate,*,project=PRIVATE_PROJECT):
 require_no_games();project=Path(project).resolve(strict=True);source=Path(candidate).absolute()
 if source!=source.resolve(strict=True)or not source.is_relative_to(project/'evidence'):
  raise ValueError('canonical isolated candidate manifest required')
 m=json.loads(source.read_text())
 if m.get('kind')=='blood-echoes-game-pso-component':
  if not(m.get('schema')==1 and m.get('production')is False and
         m.get('coherent_renderer')is True and m.get('diagnostic_only')is False):
   raise ValueError('coherent preparation component required')
  library=Path(m['library']);want=m['library_sha256']
 else:library=Path(m['candidate']);want=m['sha256']
 from game_pso_component import sha as artifact_sha
 if not library.is_relative_to(project)or artifact_sha(library)!=want:
  raise ValueError('candidate library identity')
 base=json.loads((project/'evidence/first-upload-gpu-app.json').read_text());verify(base,project/'backend')
 stamp=str(time.time_ns());app=project/'backend/bbport/bin'/('Bloodborne Metal Candidate QA '+stamp+'.app')
 if app.exists():raise ValueError('fresh private GPU app required')
 # Preserve the existing admission boundary: a DSO built in evidence is
 # copied byte-for-byte into the private backend, never admitted outside it.
 if not library.is_relative_to(project/'backend'):
  target_dir=project/'backend/bbport/bin'/('gpu-preparation-'+stamp);target_dir.mkdir(mode=0o700)
  copied=target_dir/library.name;shutil.copy2(library,copied)
  if artifact_sha(copied)!=want:raise ValueError('copied preparation DSO changed')
  library=copied
 shutil.copytree(Path(base['app']),app);target=app/'Contents/MacOS/bb-gpu'
 deps=[line.strip().split(' (compatibility')[0]for line in subprocess.check_output(
  ['/usr/bin/otool','-L',str(target)],text=True).splitlines()[1:]]
 hits=[d for d in deps if Path(d).name.startswith('libbbgpu')]
 if len(hits)!=1:raise ValueError('exactly one original renderer load edge required')
 subprocess.run(['/usr/bin/install_name_tool','-change',hits[0],str(library),str(target)],check=True)
 subprocess.run(['/usr/bin/codesign','--force','--deep','--sign','-',str(app)],check=True)
 meta=dict(target=str(target),library=str(library),library_sha256=want,app=str(app),
  source_candidate=str(source),source_candidate_sha256=artifact_sha(source),
  default_enabled=False,formal_app_modified=False)
 meta['load_identity']=verify(meta,project/'backend');out=project/'evidence'/('gpu-candidate-app-'+stamp+'.json')
 with out.open('x')as stream:json.dump(meta,stream,indent=2);stream.write('\n')
 return out

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--candidate',type=Path,required=True)
 ap.add_argument('--project',type=Path,default=PRIVATE_PROJECT);a=ap.parse_args()
 print(prepare(a.candidate,project=a.project))
if __name__=='__main__':main()
