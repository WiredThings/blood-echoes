from pathlib import Path
import subprocess,json,sys,hashlib
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'));from gpu_candidate_identity import verify
from qa_build_guard import require_no_games
require_no_games();e=Path(json.loads((P/'evidence/avplayer-bridge-native-current.json').read_text())['evidence']);m=json.loads((e/'build.json').read_text());assert m['bridge_source_sha256']==hashlib.sha256((P/'src/avplayer_bridge.cpp').read_bytes()).hexdigest()and m['test_source_sha256']==hashlib.sha256((P/'tests/test_avplayer_bridge.cpp').read_bytes()).hexdigest(),'stale control build';subprocess.run(['codesign','--force','--sign','-',m['binary']],check=True);identity=verify(dict(target=m['binary'],library=m['library'],library_sha256=m['library_sha256']),P)
media=e/'authored.mp4';assert not media.exists()
subprocess.run(['/opt/homebrew/bin/ffmpeg','-nostdin','-loglevel','error','-f','lavfi','-i','testsrc=size=16x16:rate=5','-t','1','-an','-c:v','libx264','-pix_fmt','yuv420p','-threads','1','-filter_threads','1',str(media)],check=True,timeout=20)
state=P/'state'/e.name;state.mkdir();env=dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin',HOME=str(state),BB_GPU_USER_DIR=str(state),BB_ONLINE='0',**({'BB_AV_EXPECT_THREAD_CLEANUP':'1'}if '--expect-cleanup'in sys.argv else {}))
with(e/'run.log').open('w')as log:r=subprocess.run([m['binary'],str(media)],env=env,stdout=log,stderr=subprocess.STDOUT,timeout=15)
expected=0
(e/'result.json').write_text(json.dumps(dict(passed=r.returncode==expected,expected=expected,game_started=False,guest_x86_callbacks=False,load_identity=identity,exit=r.returncode),indent=2)+'\n');print(e);print((e/'run.log').read_text()[-4000:]);assert r.returncode==expected
