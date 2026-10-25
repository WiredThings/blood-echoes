"""GPU ordering controls and real TextureCache invalidation defects; no game runs."""
from pathlib import Path
import argparse,json,subprocess,time,sys
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tests'))
from test_texture_layout_audit import prepared_template,dependencies,sha,require_no_games
sys.path.insert(0,str(P/'tools'))
from native_observer import NativeObserver
from qa_memory_policy import MemoryPolicy
from frozen_gpu_headers import verified_headers

def main():
 require_no_games();PINNED=verified_headers();ap=argparse.ArgumentParser();ap.add_argument('--candidate',type=Path);ap.add_argument('--audit',action='store_true');a=ap.parse_args();m=json.loads((a.candidate or (P/'evidence/tiler-bounds-candidate-current.json')).read_text());lib=Path(m['candidate']);assert sha(lib)==m['sha256'];base=prepared_template(lib)
 e=P/'evidence'/('texture-authority-order-'+str(time.time_ns()));e.mkdir();state=P/'state'/e.name;state.mkdir();home=state/'home';home.mkdir();source=P/'tests/texture_layout_contract_proposal/texture_authority_order.mm';obj=e/'controls.o';exe=e/'controls'
 cmd=[x.replace(str(P/'vendor/metal_api'),str(PINNED/'vendor/metal_api'))for x in base['compile']];cmd[1:1]=['-I'+str(PINNED/'src')];cmd.insert(cmd.index('-c'),'-UNDEBUG');cmd[cmd.index('-o')+1]=str(obj);cmd[cmd.index('-MF')+1]=str(obj.with_suffix('.d'));cmd[-1]=str(source)
 link=list(base['link']);old=base['compile'][base['compile'].index('-o')+1];link[link.index(old)]=str(obj);link[link.index('-o')+1]=str(exe)
 for phase,c in [('compile',cmd),('link',link)]:
  r=subprocess.run(c,capture_output=True,text=True,timeout=90);(e/(phase+'.log')).write_text(r.stdout+r.stderr);assert not r.returncode,r.stderr[-2500:]
 expected={Path(x).name:str((lib.parent/x.removeprefix('@loader_path/')).resolve())if x.startswith('@loader_path/')else x for x in dependencies(lib)}
 for x in dependencies(exe):
  if Path(x).name in expected and expected[Path(x).name]!=x:subprocess.run(['install_name_tool','-change',x,expected[Path(x).name],str(exe)],check=True)
 subprocess.run(['codesign','--force','--sign','-',str(exe)],check=True);subprocess.run(['codesign','--verify','--strict',str(exe)],check=True)
 cases=[('invalidate-base',0,'positive'),('invalidate-inside',7,'known-runtime-defect'),('invalidate-before',7,'known-runtime-defect'),('invalidate-zero',7,'known-runtime-defect'),('invalidate-repeat',7,'known-runtime-defect'),('invalidate-outside',0,'positive'),('invalidate-two-images',7,'known-runtime-defect'),('invalidate-many',0,'audit-limit-control'),('image-only',0,'positive'),('ordered-late-raw',0,'ordered-composition'),('ordered-two-writes',0,'ordered-composition'),('ordered-padding',0,'ordered-composition'),('retile-after-padding',0,'disjoint-composition'),('ordered-same-layout',0,'ordered-composition'),('ordered-mips-layers',0,'ordered-composition'),('late-retile',7,'unsafe-strategy-negative'),('raw-only-refresh',7,'unsafe-strategy-negative'),('stale-cpu',7,'unsafe-strategy-negative'),('pixel-copy',7,'unsafe-strategy-negative')]
 observer=NativeObserver();before=observer.system();assert not before['locked']and before['pressure']!=4;policy=MemoryPolicy(before);started=time.monotonic();rows=[]
 for index,(case,exit_code,category)in enumerate(cases):
  current=observer.system();reason=policy.observe(time.monotonic()-started,current);assert not reason,reason;require_no_games();private=state/str(index);private.mkdir();env=dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin',HOME=str(home),BB_GPU_USER_DIR=str(private),BB_AUDIO='none',DYLD_PRINT_LIBRARIES='1',BB_ARM_TILER_BOUNDS='1',MTL_DEBUG_LAYER='1',BB_ARM_RAW_WRITE_AUDIT=str(int(a.audit)))
  r=subprocess.run([str(exe),case],env=env,text=True,capture_output=True,timeout=30);log=r.stdout+r.stderr;(e/(str(index)+'-'+case+'.log')).write_text(log);assert r.returncode==exit_code,(case,r.returncode,exit_code,log[-4000:]);loaded=[l for l in log.splitlines()if 'dyld['in l and 'libbbgpu'in l];assert len(loaded)==1 and str(lib)in loaded[0],loaded;assert 'implemented in both'not in log
  records=[json.loads(l.removeprefix('BB_RAW_WRITE_AUDIT '))for l in r.stderr.splitlines()if l.startswith('BB_RAW_WRITE_AUDIT ')]
  expected_records=(64 if case=='invalidate-repeat'else 3 if case=='invalidate-many'else 1 if case in ('invalidate-inside','invalidate-before','invalidate-zero','invalidate-two-images')else 0)if a.audit else 0
  assert len(records)==expected_records,(case,len(records),expected_records)
  assert [x['event']for x in records]==list(range(1,expected_records+1))
  for record in records:
   assert record['mono_ns']>0 and record['pid']>0 and record['image_address']>0
   assert record['write_bytes']==0 or record['would_miss']==1
  if a.audit and case=='invalidate-repeat':assert 'BB_RAW_WRITE_AUDIT_SUMMARY probes=256 records=64 skipped=0 limited=0'in log
  summary=[l for l in r.stdout.splitlines()if l.startswith(('INVALIDATION','AUTHORITY'))];assert len(summary)==1
  rows.append(dict(case=case,exit=r.returncode,expected_outcome=exit_code,category=category,target_expected=0 if category=='known-runtime-defect'else exit_code,audit_records=len(records),control_verified=True,target_met=category!='known-runtime-defect',summary=summary[0]))
 result=dict(control_outcomes_verified=True,known_runtime_failure_cases=sum(x['category']=='known-runtime-defect'for x in rows), distinct_invalidation_defects=4,all_runtime_paths_correct=False,cases=rows,library=str(lib),library_sha256=sha(lib),compile=cmd,link=link,game_started=False,production_fix=False,audit_enabled=a.audit,pinned_headers=str(PINNED),raw_composition_is_not_BufferCache_integration=True,same_submission_proven=True,metal_debug_layer=True,memory_policy=MemoryPolicy.MANIFEST,before=before,after=observer.system())
 (e/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(e)
 for row in rows:print(row['summary'])
if __name__=='__main__':main()
