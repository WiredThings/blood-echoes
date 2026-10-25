from pathlib import Path
import json,subprocess,time,sys
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/"tools"))
from qa_build_guard import require_no_games
require_no_games();m=json.loads((P/"evidence/tiler-bounds-generated-current.json").read_text());e=P/"evidence"/("tiler-bounds-rewriter-"+str(time.time_ns()));e.mkdir()
command=["/usr/bin/clang++","-std=c++23","-Wall","-Wextra","-Werror","-I"+m["include"],"-I"+str(P/"vendor/metal_api/shadps4"),str(P/"tests/texture_layout_contract_proposal/tiler_bounds_rewriter.cpp"),"-o",str(e/"controls")]
r=subprocess.run(command,text=True,capture_output=True,timeout=30);(e/"build.log").write_text(r.stdout+r.stderr);assert not r.returncode,r.stderr
r=subprocess.run([str(e/"controls")],text=True,capture_output=True,timeout=10);(e/"controls.log").write_text(r.stdout+r.stderr);assert not r.returncode,r.stderr
(e/"result.json").write_text(json.dumps(dict(passed=True,command=command,generated=m,controls=r.stdout.splitlines()),indent=2)+"\n");print(e)
