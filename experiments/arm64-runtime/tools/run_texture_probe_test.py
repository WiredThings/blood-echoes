"""Run authored texel-reader tests against an explicit built Metal library.

Uses the renderer's existing compilation database. No game or production state.
"""
from pathlib import Path
import argparse,json,os,shlex,subprocess,time
from qa_build_guard import require_no_games

def main():
 p=argparse.ArgumentParser();p.add_argument('--compile-commands',required=True,type=Path);p.add_argument('--library',required=True,type=Path);p.add_argument('--lineage',action='store_true');a=p.parse_args()
 require_no_games();source=Path(__file__).resolve().parents[1]
 library=a.library.resolve(strict=True);database=json.loads(a.compile_commands.read_text())
 suffix='/vendor/metal_api/shadps4/video_core/renderer_metal/mtl_rasterizer.mm'
 item=next(x for x in database if x['file'].endswith(suffix));old=item['file'][:-len(suffix)]
 command=list(item['arguments'])if'arguments'in item else shlex.split(item['command'])
 command=[x.replace(old,str(source))for x in command]
 root=Path('/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/state')
 assert root==root.resolve(strict=True)
 output=root/('texture-probe-authored-'+str(time.time_ns()));output.mkdir(mode=0o700)
 command.remove('-c');command[command.index('-o')+1]=str(output/'test-texture-probe')
 if '-MF'in command:command[command.index('-MF')+1]=str(output/'test.d')
 command[-1]=str(source/('tests/metal/test_texture_lineage.mm'if a.lineage else'tests/metal/test_texture_probe.mm'))
 command+=['-UNDEBUG','-x','none',str(library),'-framework','Metal','-framework','Foundation']
 (output/'compile-command.json').write_text(json.dumps(command,indent=2)+'\n')
 with(output/'compile.log').open('w')as log:subprocess.run(command,cwd=item['directory'],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=90)
 rows=[]
 cases=('good','bad-input','pending-cap','log-full','disabled','linked-marker')if a.lineage else('pixels','bc1','bc4','bc7','invalid','symlink-request','disabled','not-recording','no-match','symlink-output')
 for kind in cases:
  require_no_games();case=output/kind;case.mkdir(mode=0o700)
  env=dict(os.environ,BB_USER_DIR=str(case),BB_GPU_USER_DIR=str(case),BB_DATA_DIR=str(case),MTL_DEBUG_LAYER='1',DYLD_PRINT_LIBRARIES='1')
  if a.lineage:
   capture=case/'capture';capture.mkdir(mode=0o700)
   if kind=='linked-marker':
    (capture/'marker').touch(mode=0o600);(capture/'lineage-enable').symlink_to('marker')
   elif kind!='disabled':(capture/'lineage-enable').touch(mode=0o600)
   env.update(BB_CAPTURE_NATIVE='1',BB_CAPTURE_TEXTURE_BINDINGS='1',BB_TEXTURE_BINDING_CAPTURE_DIR=str(capture))
  result=subprocess.run([str(output/'test-texture-probe'),str(case/'capture'),kind],cwd=case,env=env,capture_output=True,text=True,timeout=30)
  (output/(kind+'.log')).write_text(result.stdout+result.stderr)
  loaded=str(library)in result.stderr
  rows.append(dict(test=kind,exit=result.returncode,actual_library_seen=loaded))
  (output/'result.json').write_text(json.dumps(dict(library=str(library),rows=rows,game_started=False,formal_data_modified=False),indent=2)+'\n')
  assert result.returncode==0 and loaded,(kind,str(output/(kind+'.log')))
 print(json.dumps(dict(passed=True,tests=len(rows),output=str(output),performance_evidence=False)))

if __name__=='__main__':main()
