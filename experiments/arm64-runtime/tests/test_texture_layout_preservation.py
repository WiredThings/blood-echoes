"""Real GPU-only preservation contracts; known unmerged defects remain expected failures.
Prepared private dependency stage required. No game, GUI or existing cache access.
"""
from pathlib import Path
import argparse,json,subprocess,time
from test_texture_layout_audit import P,sha,dependencies,prepared_template
FULL_CASES=[('linear-thin', [], 0, False), ('thin-linear', [], 0, False), ('display-thin', [], 0, False), ('thin-display', [], 0, False), ('macro-thin', [], 0, False), ('thin-macro', [], 0, False), ('macro-thin-mips', [], 0, False), ('thin-linear-mips-layers', [], 0, False), ('linear-thin-mips-layers', [], 0, False), ('thin-display-layers', [], 0, False), ('unorm-srgb', [], 0, False), ('srgb-unorm', [], 0, False), ('logical1d-2d', [], 0, False), ('logical2d-1d', [], 0, False), ('macro-srgb-thin', [], 0, False), ('thin-linear-padded', [], 0, False), ('linear-thin-padded', [], 0, False), ('thin-linear-small-mips', [], 0, False), ('macro-thin', [], 0, True), ('macro-thin-mips', [], 0, True), ('macro-srgb-thin', [], 0, True), ('linear-thin', ['skip-gpu-generation'], 9, False), ('linear-thin', ['stale-cpu-raw'], 7, False), ('linear-thin', ['pixel-copy'], 7, False)]
MERGE_CASES=[('thin-linear-padded',[],0,False),('thin-linear-small-mips',[],0,False),('thin-linear',[],0,False),('linear-thin',[],0,False),('thin-linear-padded',['unmerged'],7,False),('thin-linear-small-mips',['unmerged'],7,False),('linear-thin',['stale-cpu-raw'],7,False),('linear-thin',['pixel-copy'],7,False),('linear-thin',['skip-gpu-generation'],9,False)]
import sys
sys.path.insert(0,str(P/'tools'))
from qa_build_guard import require_no_games
def main():
 require_no_games()
 ap=argparse.ArgumentParser();ap.add_argument('--full',action='store_true');a=ap.parse_args();e=P/'evidence'/('texture-preservation-controls-'+str(time.time_ns()));e.mkdir();state=P/'state'/e.name;state.mkdir();home=state/'home';home.mkdir()
 admission=json.loads((P/'evidence/avplayer-host-candidate-current.json').read_text());library=Path(admission['candidate']);assert sha(library)==admission['sha256'];base=prepared_template(library);results=[]
 for label,source_name,cases in [('roundtrip','gpu_raw_roundtrip.mm',FULL_CASES if a.full else [x for x in FULL_CASES if x[0]in('linear-thin','thin-linear','thin-linear-padded','thin-linear-small-mips')and not x[1]and not x[3]]),('merge','gpu_coverage_merge.mm',MERGE_CASES)]:
  source=P/'tests/texture_layout_contract_proposal'/source_name;obj=e/(label+'.o');exe=e/label;cmd=list(base['compile']);cmd.insert(1,'-I'+str(P/'src'));cmd.insert(cmd.index('-c'),'-UNDEBUG');cmd[cmd.index('-o')+1]=str(obj);cmd[cmd.index('-MF')+1]=str(obj.with_suffix('.d'));cmd[-1]=str(source);link=list(base['link']);oldobj=base['compile'][base['compile'].index('-o')+1];assert link.count(oldobj)==1;link[link.index(oldobj)]=str(obj);link[link.index('-o')+1]=str(exe)
  for phase,command in [('compile',cmd),('link',link)]:
   r=subprocess.run(command,text=True,capture_output=True,timeout=90);(e/(label+'-'+phase+'.log')).write_text(r.stdout+r.stderr);assert r.returncode==0,r.stderr[-2000:]
  expected={Path(x).name:str((library.parent/x.removeprefix('@loader_path/')).resolve())if x.startswith('@loader_path/')else x for x in dependencies(library)};changes=[]
  for item in dependencies(exe):
   replacement=expected.get(Path(item).name)
   if replacement and replacement!=item:subprocess.run(['/usr/bin/install_name_tool','-change',item,replacement,str(exe)],check=True);changes.append([item,replacement])
  subprocess.run(['/usr/bin/codesign','--force','--sign','-',str(exe)],check=True);subprocess.run(['/usr/bin/codesign','--verify','--strict',str(exe)],check=True)
  (e/(label+'-build.json')).write_text(json.dumps(dict(compile=cmd,link=link,source_sha256=sha(source),library=str(library),library_sha256=sha(library),dependency_changes=changes),indent=2)+'\n')
  for index,(case,extra,expected_exit,fast)in enumerate(cases):
   private=state/(label+str(index));private.mkdir();env=dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin',HOME=str(home),BB_GPU_USER_DIR=str(private),BB_MTL_TEXTURE_TILING=str(int(fast)),BB_AUDIO='none',DYLD_PRINT_LIBRARIES='1')
   known_defect=label=='roundtrip'and case in ('thin-linear-padded','thin-linear-small-mips')and not extra
   observed_expected=7 if known_defect else expected_exit
   r=subprocess.run([str(exe),case,*extra],env=env,text=True,capture_output=True,timeout=40);log=r.stdout+r.stderr;(e/(label+'-'+str(index)+'.log')).write_text(log);assert r.returncode==observed_expected,(label,case,r.returncode,expected_exit,observed_expected,log[-2000:]);assert 'implemented in both'not in log
   loaded=[x for x in log.splitlines()if 'dyld['in x and 'libbbgpu'in x];assert len(loaded)==1 and str(library)in loaded[0],loaded
   summary=[x for x in log.splitlines()if x.startswith(('ROUNDTRIP','SOURCE_GPU_ORACLE_REJECTED','COVERAGE_MERGE'))];assert summary
   if fast and 'macro-thin'==case:assert 'Metal texture tiling'in log
   results.append(dict(group=label,case=case,mode=extra,target_expected=expected_exit,exit=r.returncode,passed=r.returncode==expected_exit,known_defect=known_defect,expected_outcome_matched=True,fast=fast,summary=summary))
 result=dict(control_outcomes_verified=True,all_preservation_paths_correct=not any(x['known_defect']for x in results),known_defects=sum(x['known_defect']for x in results),cases=results,full_matrix=a.full,game_started=False,production_fix=False,CPU_mask_is_test_oracle_only=True,library_sha256=sha(library));(e/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(e)
if __name__=='__main__':main()
