from pathlib import Path
import json,subprocess,sys,time
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'))
from qa_build_guard import require_no_games
from analyze_stutter import read
from shader_phase_analysis import analyze_shader_phases
require_no_games();e=P/'evidence'/('shader-phase-audit-controls-'+str(time.time_ns()));e.mkdir();exe=e/'phase-test'
subprocess.run(['/usr/bin/clang++','-std=c++20','-arch','arm64','-UNDEBUG','-I'+str(P/'src'),str(P/'tests/test_shader_phase_audit.cpp'),'-o',str(exe)],check=True)
rows=[]
for mode in ['enabled','close-gate','gate-closed','disabled','no-trace','throw','bad-metadata']:
 d=e/mode;d.mkdir();gate=d/'gate';gate.write_bytes((0 if mode=='gate-closed'else 1).to_bytes(4,'little')+bytes(4092));env=dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin',HOME=str(d),BB_STUTTER_GATE=str(gate),BB_STUTTER_COARSE='1')
 if mode!='disabled':env['BB_SHADER_PHASE_AUDIT']='1'
 if mode!='no-trace':env['BB_STUTTER_AUDIT']=str(d/'trace')
 subprocess.run([str(exe),mode],env=env,check=True,timeout=10);files=list(d.glob('trace-*.bin'))
 if mode in ['disabled','no-trace']:assert not files;rows.append(dict(mode=mode,passed=True,trace_created=False));continue
 assert len(files)==1;t=read(files[0]);a=analyze_shader_phases(t['rows'])
 if mode in ['enabled','close-gate']:
  assert t['all_published']and t['issued']==6 and len(a['modules'])==1 and a['modules'][0]['complete'];assert a['modules'][0]['program_hash']=='123456789abcdef0'and a['modules'][0]['permutation']==0x87654321
 elif mode=='gate-closed':assert t['issued']==0 and not a['modules']
 elif mode=='throw':assert t['issued']==3 and not a['modules'][0]['complete']and not a['modules'][0]['returned_marker']
 else:assert t['dropped']==1 and not a['modules'][0]['complete']and not a['modules'][0]['identity_complete']
 rows.append(dict(mode=mode,passed=True,trace={k:v for k,v in t.items()if k!='rows'},analysis=a))
(e/'result.json').write_text(json.dumps(dict(passed=True,game_started=False,controls=rows),indent=2)+'\n');print(e);print('PASS 7 native phase trace controls')
