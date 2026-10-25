"""Headless load-edge, real FEX positive/negative and archive-content controls."""
from pathlib import Path
import argparse,hashlib,json,subprocess,sys,time,zipfile
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'))
from game_fixture import make
from qa_build_guard import require_no_games
def sha(f):
 with Path(f).open('rb')as s:return hashlib.file_digest(s,'sha256').hexdigest()
def main():
 ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path);a=ap.parse_args();require_no_games();folder=a.folder.resolve();manifest=json.loads((folder/'runtime-manifest.json').read_text());app=Path(manifest['app']);assert app.resolve()==app and app.is_relative_to(P/'apps');assert manifest['host_local_only']and not manifest['formal_data_modified']
 stage=app/'Contents/Resources/bbport';assert all(sha(stage/n)==h for n,h in manifest['frozen_runtime'].items());exe=stage/'bin/bb-probe-metal';expected_lib=stage/'bin/gpu-metal/libbbgpu.dylib'
 e=P/'evidence'/('frozen-contract-package-'+str(time.time_ns()));e.mkdir();root=P/'state'/e.name;root.mkdir();gpu=root/'gpu';gpu.mkdir();home=root/'home';home.mkdir();env=dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin',HOME=str(home),BB_GPU_USER_DIR=str(gpu),BB_AUDIO='none',BB_ONLINE='0',DYLD_PRINT_LIBRARIES='1');rows=[]
 for mode,expected in [('msync-image',0),('msync-query-bad-output',74)]:
  fixture=P/'fixtures'/(e.name+'-'+mode+'.bbprobe');fixture.write_bytes(make(mode));state=P/'state'/(e.name+'-'+mode)
  r=subprocess.run([str(exe),'--authored-fixture','--boot',str(fixture),'--state',str(state)],env=env,text=True,capture_output=True,timeout=20);log=r.stdout+r.stderr;(e/(mode+'.log')).write_text(log);assert r.returncode==expected,(mode,r.returncode,log[-2000:]);loaded=[x for x in log.splitlines()if 'dyld['in x and 'libbbgpu'in x];assert len(loaded)==1 and str(expected_lib)in loaded[0],loaded;assert 'implemented in both'not in log;rows.append(dict(mode=mode,exit=r.returncode,expected=expected,packaged_library_loaded=True))
 archive=folder/'Blood-Echoes-ARM64-Local-QA.zip';forbidden={'savedata','gpu-cache','CUSA03173-1.09','eboot.bin','boot-linked.bin','content.bin','patches.bin'}
 with zipfile.ZipFile(archive)as z:
  assert not any(forbidden.intersection(Path(n).parts)for n in z.namelist());assert z.testzip()is None;entries=len(z.namelist())
 report=dict(passed=True,cases=rows,GUI_opened=False,game_started=False,formal_data_modified=False,archive_sha256=sha(archive),archive_entries=entries,current_CPU_upstream_sha=manifest['upstream_inputs']['cpu'],copied_host_upstream_sha=manifest['upstream_inputs']['cpu_library']);(e/'result.json').write_text(json.dumps(report,indent=2)+'\n');print(e);print(json.dumps(report,indent=2))
if __name__=='__main__':main()
