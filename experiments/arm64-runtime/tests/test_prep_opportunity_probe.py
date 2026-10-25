from pathlib import Path
import json,subprocess,sys,time
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'))
from qa_build_guard import require_no_games
from analyze_stutter import read
from prep_opportunity_analysis import analyze
require_no_games();e=P/'evidence'/('prep-opportunity-controls-'+str(time.time_ns()));e.mkdir();exe=e/'probe-test'
subprocess.run(['/usr/bin/clang++','-std=c++20','-arch','arm64','-UNDEBUG','-I'+str(P/'src'),str(P/'tests/test_prep_opportunity_probe.cpp'),'-o',str(exe)],check=True)
cases=[]
for mode in ['enabled','identities','overflow','invalid','gate-closed','disabled','no-trace']:
 d=e/mode;d.mkdir();gate=d/'gate';gate.write_bytes((0 if mode=='gate-closed'else 1).to_bytes(4,'little')+bytes(4092));env=dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin',HOME=str(d),BB_STUTTER_GATE=str(gate),BB_STUTTER_COARSE='1')
 if mode!='disabled':env['BB_SHADER_PHASE_AUDIT']='1'
 if mode!='no-trace':env['BB_STUTTER_AUDIT']=str(d/'trace')
 subprocess.run([str(exe),mode],env=env,check=True,timeout=10);files=list(d.glob('trace-*.bin'))
 if mode in ['disabled','no-trace']:assert not files;cases.append(dict(mode=mode,passed=True,trace_created=False));continue
 assert len(files)==1;t=read(files[0]);a=analyze(t['rows']);assert t['all_published']and t['dropped']==0 and not a['invalid_probe_pairs']
 expected={'enabled':1,'identities':4,'overflow':128,'invalid':0,'gate-closed':0}[mode];assert len(a['probes'])==expected,(mode,a)
 assert a['overflow_markers']==int(mode=='overflow')
 assert t['issued']==expected*2+int(mode=='overflow')
 cases.append(dict(mode=mode,passed=True,trace={k:v for k,v in t.items()if k!='rows'},probe_count=expected))
(e/'result.json').write_text(json.dumps(dict(passed=True,game_started=False,cases=cases),indent=2)+'\n');print(e);print('PASS seven bounded native discovery trace controls')
