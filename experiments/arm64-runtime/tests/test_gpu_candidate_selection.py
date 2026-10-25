"""Exercise the actual optional GPU selection function; no native/game process."""
from pathlib import Path
import argparse,json,sys,time
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'))
from gpu_candidate_identity import select_for_isolated_run
ap=argparse.ArgumentParser();ap.add_argument('--manifest',type=Path,required=True);a=ap.parse_args()
e=P/'evidence'/('gpu-selection-controls-'+str(time.time_ns()));e.mkdir();results=[]
def select(path,**changes):
 args=dict(engine='arm64',graphics=True,private_root=P);args.update(changes);return select_for_isolated_run(path,**args)
def reject(label,path,**changes):
 try:select(path,**changes)
 except ValueError as error:results.append(dict(case=label,rejected=True,error=str(error)))
 else:raise AssertionError(label)
assert select(None,engine='rosetta',graphics=False,private_root='/nonexistent')is None;results.append(dict(case='default-no-selection',passed=True))
identity=select(a.manifest);m=json.loads(a.manifest.read_text());assert identity['library_sha256']==m['library_sha256'];results.append(dict(case='actual-signed-arm64-load-edge',passed=True))
reject('rosetta',a.manifest,engine='rosetta');reject('non-graphics',a.manifest,graphics=False)
wrong=dict(m,library_sha256='0'*64);f=e/'wrong-hash.json';f.write_text(json.dumps(wrong));reject('wrong-hash',f)
wrong=dict(m,target='/Applications/Blood Echoes.app/Contents/MacOS/Blood Echoes');f=e/'formal-target.json';f.write_text(json.dumps(wrong));reject('formal-target',f)
f=e/'linked.json';f.symlink_to(a.manifest.resolve(strict=True));reject('manifest-symlink',f)
f=P/'state'/('authored-gpu-selection-'+str(time.time_ns())+'.json');f.write_text(json.dumps(m));reject('outside-evidence',f)
r=dict(passed=True,cases=results,identity=identity,game_started=False,formal_files_modified=False);(e/'result.json').write_text(json.dumps(r,indent=2)+'\n');print(e);print('PASS',len(results),'actual selector controls')
