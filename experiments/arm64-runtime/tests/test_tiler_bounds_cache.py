from pathlib import Path
import json,subprocess,time,sys
p=Path(__file__).resolve().parents[1];sys.path.insert(0,str(p/'tests'));from test_texture_layout_audit import prepared_template,dependencies,sha,require_no_games
require_no_games();m=json.loads((p/'evidence/tiler-bounds-candidate-current.json').read_text());lib=Path(m['candidate']);assert sha(lib)==m['sha256'];base=prepared_template(lib);e=p/'evidence'/('tiler-bounds-cache-controls-'+str(time.time_ns()));e.mkdir();state=p/'state'/e.name;state.mkdir();home=state/'home';home.mkdir();source=p/'tests/texture_layout_contract_proposal/tiler_bounds_cache.mm';obj=e/'controls.o';exe=e/'controls';cmd=list(base['compile']);cmd[1:1]=['-I'+str(p/'src')];cmd.insert(cmd.index('-c'),'-UNDEBUG');cmd[cmd.index('-o')+1]=str(obj);cmd[cmd.index('-MF')+1]=str(obj.with_suffix('.d'));cmd[-1]=str(source);link=list(base['link']);old=base['compile'][base['compile'].index('-o')+1];link[link.index(old)]=str(obj);link[link.index('-o')+1]=str(exe)
for phase,c in [('compile',cmd),('link',link)]:
 r=subprocess.run(c,capture_output=True,text=True,timeout=90);(e/(phase+'.log')).write_text(r.stdout+r.stderr);assert not r.returncode,r.stderr[-2000:]
expected={Path(x).name:str((lib.parent/x.removeprefix('@loader_path/')).resolve())if x.startswith('@loader_path/')else x for x in dependencies(lib)}
for x in dependencies(exe):
 if Path(x).name in expected and expected[Path(x).name]!=x:subprocess.run(['install_name_tool','-change',x,expected[Path(x).name],str(exe)],check=True)
subprocess.run(['codesign','--force','--sign','-',str(exe)],check=True)
env=dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin',HOME=str(home),BB_GPU_USER_DIR=str(state),BB_AUDIO='none',DYLD_PRINT_LIBRARIES='1',MTL_DEBUG_LAYER='1')
r=subprocess.run([str(exe)],env=env,capture_output=True,text=True,timeout=45);log=r.stdout+r.stderr;(e/'controls.log').write_text(log);assert not r.returncode,(r.returncode,log[-5000:]);loaded=[l for l in log.splitlines()if 'dyld['in l and 'libbbgpu'in l];assert len(loaded)==1 and str(lib)in loaded[0];assert 'implemented in both'not in log
rows=[l for l in log.splitlines()if l.startswith(('UBO_CACHE','BOUNDS_CONTROLS'))];assert len(rows)==7
(e/'result.json').write_text(json.dumps(dict(passed=True,library=str(lib),library_sha256=sha(lib),controls=rows,metal_debug_layer=True,game_started=False,compile=cmd,link=link),indent=2)+'\n');print(e);print('\n'.join(rows))
