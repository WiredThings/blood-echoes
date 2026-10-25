from pathlib import Path
import subprocess,json,time,sys,shlex,hashlib
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'));from qa_build_guard import require_no_games
require_no_games();e=P/'evidence'/('avplayer-bridge-native-'+str(time.time_ns()));e.mkdir();lib=Path(sys.argv[1]).resolve()if len(sys.argv)>1 else Path(json.loads((P/'evidence/avplayer-host-candidate-current.json').read_text())['candidate']);assert lib.is_relative_to(P/'backend')
cmd=['/usr/bin/clang++','-std=c++20','-O2','-arch','arm64','-UNDEBUG','-Wno-ignored-attributes','-I'+str(P/'src'),'-I'+str(P/'tests'),'-I'+str(P/'vendor/aether/src'),'-I'+str(P/'vendor/metal_api/shadps4'),'-I/opt/homebrew/include',str(P/'src/avplayer_bridge.cpp'),str(P/'tests/test_avplayer_bridge.cpp'),str(P/'vendor/aether/src/core/guest_cpu/hle_call_adapter.cpp'),str(lib),'-Wl,-export_dynamic','-o',str(e/'test')]
with(e/'build.log').open('w')as log:r=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,timeout=90)
if '--expect-legacy-link-rejection'in sys.argv:
 assert r.returncode!=0 and '_bbgpu_arm_avplayer_bridge_revision'in(e/'build.log').read_text(),str(e/'build.log')
 (e/'result.json').write_text(json.dumps(dict(passed=True,game_started=False,legacy_host_link_rejected=True,missing_symbol='bbgpu_arm_avplayer_bridge_revision',library=str(lib)),indent=2)+'\n');print(e);print('PASS legacy host rejected by linker');raise SystemExit(0)
assert r.returncode==0,str(e/'build.log')
(e/'build.json').write_text(json.dumps(dict(compile=cmd,library=str(lib),library_sha256=hashlib.file_digest(lib.open('rb'),'sha256').hexdigest(),binary=str(e/'test'),bridge_source_sha256=hashlib.sha256((P/'src/avplayer_bridge.cpp').read_bytes()).hexdigest(),test_source_sha256=hashlib.sha256((P/'tests/test_avplayer_bridge.cpp').read_bytes()).hexdigest()),indent=2)+'\n');(P/'evidence/avplayer-bridge-native-current.json').write_text(json.dumps(dict(evidence=str(e)))+'\n');print(e,flush=True)
