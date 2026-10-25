"""Actual x86 allocator/file/event callbacks through FEX and the original AvPlayer."""
from pathlib import Path
import hashlib,json,subprocess,sys,time
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tests'));sys.path.insert(0,str(P/'tools'))
from avplayer_fixture import make
from qa_build_guard import require_no_games
require_no_games();e=P/'evidence'/('avplayer-fex-'+str(time.time_ns()));e.mkdir();state=P/'state'/e.name;state.mkdir();gpu=state/'gpu';gpu.mkdir();media=e/'authored.mp4'
subprocess.run(['/opt/homebrew/bin/ffmpeg','-nostdin','-loglevel','error','-f','lavfi','-i','testsrc=size=16x16:rate=5','-t','1','-an','-c:v','libx264','-pix_fmt','yuv420p','-threads','1','-filter_threads','1',str(media)],check=True,timeout=20)
env=dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin',HOME=str(state),TMPDIR=str(state)+'/',BB_GPU_USER_DIR=str(gpu),BB_ONLINE='0',BB_QUICKSAVE='0',BB_AUDIO='none')
layout_architectures=[]
for arch in ['arm64','x86_64']:
 r=subprocess.run(['/usr/bin/clang++','-std=c++20','-arch',arch,'-Wno-ignored-attributes','-I'+str(P/'vendor/metal_api/shadps4'),'-fsyntax-only',str(P/'tests/test_avplayer_layout.cpp')],text=True,capture_output=True)
 (e/('layout-'+arch+'.log')).write_text(r.stdout+r.stderr);assert r.returncode==0,r.stderr;layout_architectures.append(arch)
rows=[]
for mode in sys.argv[1:]or['valid','bad-init-span','bad-callback','bad-frame-span','bad-allocated-span','bad-read-count']:
 fixture=P/'fixtures'/(e.name+'-'+mode+'.bbprobe');fixture.write_bytes(make(media.read_bytes(),mode));root=P/'state'/(e.name+'-'+mode)
 r=subprocess.run([str(P/'build/fex/Bin/bb-game-arm64'),'--authored-fixture','--boot',str(fixture),'--state',str(root)],env=env,capture_output=True,text=True,timeout=20);(e/(mode+'.log')).write_text(r.stdout+r.stderr);expect=0 if mode=='valid'else 74
 print(mode,r.returncode,flush=True)
 if r.returncode!=expect:print((r.stdout+r.stderr)[-4000:]);raise SystemExit(str(e))
 if mode=='valid':assert 'ARM AV FIXTURE handles=0 contexts=0 host-TCB='in r.stdout
 imports=[x.split('\t')for x in(root/'imports.tsv').read_text().splitlines()];av=[x for x in imports if x[1].startswith('sceAvPlayer')];assert len(av)==16 and all(x[2]=='native-avplayer'for x in av)
 rows.append(dict(mode=mode,passed=True,exit=r.returncode,AV_native_imports=len(av)))
(e/'result.json').write_text(json.dumps(dict(passed=True,game_started=False,real_FEX_callbacks=True,layout_architectures=layout_architectures,authored_media_only=True,binary_sha256=hashlib.file_digest((P/'build/fex/Bin/bb-game-arm64').open('rb'),'sha256').hexdigest(),cases=rows),indent=2)+'\n');print(e)
