from pathlib import Path
import json,subprocess,time,sys
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'));from qa_build_guard import require_no_games
require_no_games();stage=P/'apps/Blood Echoes ARM64 GUI QA 1792881296726369000.app/Contents/Resources/bbport';assert(stage/'bin').is_dir();e=P/'evidence'/('avplayer-component-abba-'+str(time.time_ns()));e.mkdir();rows=[]
for i,variant in enumerate(['old-frozen','new-av','new-av','old-frozen']):
 cmd=['python3',str(P/'tools/run_launcher_engine_abba.py'),'--order','arm64','--seconds','90']
 if variant=='old-frozen':cmd+=['--component-stage',str(stage)]
 else:cmd+=['--gpu-variant','first-upload']
 with(e/(str(i)+'-'+variant+'.log')).open('w')as log:r=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT)
 text=(e/(str(i)+'-'+variant+'.log')).read_text();print(i,variant,r.returncode,flush=True)
 import re
 m=re.search(r'Evidence: (.+)',text);assert m and r.returncode==0,text[-3000:];case=Path(m[1]);report=json.loads((case/'result.json').read_text());assert report['passed'];rows.append(dict(index=i,variant=variant,evidence=str(case),run=report['runs'][0]));(e/'result.json').write_text(json.dumps(dict(passed=False,rows=rows),indent=2)+'\n')
 assert report['runs'][0].get('stop')is None or report['runs'][0].get('stop')=='time budget',report['runs'][0]
assert len({tuple(r['run'][k]for k in ['boot_sha256','content_sha256','patch_sha256','settings_sha256'])for r in rows})==1
(e/'result.json').write_text(json.dumps(dict(passed=True,rows=rows,profile_hashes_matched=True,desktop_capture=False,cua_used=False,qualification='Static inactive-AV comparison; does not validate playback, combat white-model fix, new-content compile latency, death, online or menus.'),indent=2)+'\n');print(e,flush=True)
