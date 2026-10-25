"""Rebuild every binary actually used by the authored suite before running it.

This avoids testing stale statically linked copies after the common FEX/HLE
library changes. No game assets, GUI, production data or network accounts.
"""
from pathlib import Path
import ast,hashlib,json,subprocess,sys,time
from qa_build_guard import require_no_games
from native_observer import NativeObserver
P=Path(__file__).resolve().parents[1]
require_no_games();system=NativeObserver().system()
if system['pressure']==4:raise RuntimeError('critical pressure: regression build refused')
source=ast.parse((P/'tests/run_prototype.py').read_text())
targets=sorted({n.right.value for n in ast.walk(source)if isinstance(n,ast.BinOp)and isinstance(n.op,ast.Div)and isinstance(n.left,ast.Name)and n.left.id=='binary'and isinstance(n.right,ast.Constant)and isinstance(n.right.value,str)})
assert {'bb-game-arm64','test_guest_sync','test_producer_wait_policy'}<=set(targets)
e=P/'evidence'/('current-runtime-regression-'+str(time.time_ns()));e.mkdir();print(e,flush=True)
report=dict(passed=False,targets=targets,build_system_before=system,jobs=[])
def run(name,cmd):
 with(e/(name+'.log')).open('w')as log:r=subprocess.run(cmd,cwd=P,stdout=log,stderr=subprocess.STDOUT)
 report['jobs'].append(dict(name=name,status=r.returncode));(e/'result.json').write_text(json.dumps(report,indent=2)+'\n');print(name,r.returncode,flush=True)
 if r.returncode:raise SystemExit(str(e))
run('rebuild-suite',['ninja','-C',str(P/'build/fex'),'-j1',*targets])
report['binaries']={n:hashlib.file_digest((P/'build/fex/Bin'/n).open('rb'),'sha256').hexdigest()for n in targets}
for name,args in [('suite',['tests/run_prototype.py','--packed-veneers','--fast-hle-lookup']),('data',['tests/test_missing_data_imports.py']),('spans',['tests/test_libc_spans.py']),('exit',['tests/test_runtime_exit_contract.py']),('producer-analysis',['tests/test_producer_wait_analysis.py']),('signal-FEX',['tests/test_unaligned_audit_fex.py']),('writer-clock',['tests/test_trace_writer_clock.py']),('reader-clock',['tests/test_trace_clock_rejection.py']),('ABI-marker',['tests/test_native_abi_declarations.py']),('AvPlayer-FEX',['tests/test_avplayer_fex.py']),('progname-FEX',['tests/test_progname_fex.py']),('module-info-FEX',['tests/test_module_info_fex.py']),('msync-FEX',['tests/test_msync_fex.py']),('mlock-FEX',['tests/test_mlock_fex.py'])]:run(name,[sys.executable,*args])
report['passed']=True;(e/'result.json').write_text(json.dumps(report,indent=2)+'\n')
