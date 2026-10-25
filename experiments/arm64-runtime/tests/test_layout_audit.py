"""Compile and inspect the actual bounded observation helper, without loading GPU."""
from pathlib import Path
import json,os,subprocess,time
P=Path(__file__).resolve().parents[1]
def main():
 e=P/'evidence'/('layout-audit-controls-'+str(time.time_ns()));e.mkdir()
 cmd=['/usr/bin/clang++','-std=c++20','-arch','arm64','-O2','-UNDEBUG','-Wall','-Wextra','-Werror','-I'+str(P/'src'),str(P/'tests/test_layout_audit.cpp'),'-o',str(e/'native')]
 r=subprocess.run(cmd,capture_output=True,text=True,timeout=30);(e/'build.log').write_text(r.stdout+r.stderr);assert r.returncode==0,r.stderr
 cases=[]
 for mode,value,expected in [('disabled',None,0),('disabled','0',0),('disabled','1x',0),('budget',None,0),('enabled','1',64),('maximum','1',64)]:
  env=dict(os.environ);env.pop('BB_ARM_LAYOUT_AUDIT',None)
  if value is not None:env['BB_ARM_LAYOUT_AUDIT']=value
  r=subprocess.run([str(e/'native'),mode],env=env,text=True,capture_output=True,timeout=10);assert r.returncode==0,(mode,r.stdout,r.stderr)
  rows=[json.loads(x.removeprefix('BB_LAYOUT_AUDIT '))for x in r.stderr.splitlines()if x.startswith('BB_LAYOUT_AUDIT ')];assert len(rows)==expected
  if rows:
   assert [x['event']for x in rows]==list(range(1,65));assert all(x['schema']==1 and x['requested']['tile']==8 and x['selected']['tile']==13 for x in rows)
   assert all(len(x['requested']['mips'])==(16 if mode=='maximum'else 1)for x in rows)
  label=mode+'-'+str(value);(e/(label+'.log')).write_text(r.stdout+r.stderr);cases.append(dict(mode=mode,value=value,records=len(rows),passed=True))
 (e/'result.json').write_text(json.dumps(dict(passed=True,cases=cases,game_started=False,GPU_loaded=False,cache_modified=False),indent=2)+'\n');print(e)
if __name__=='__main__':main()
