"""Actual Mach-O negative controls: silent unmatched install-name replacement."""
from pathlib import Path
import hashlib,json,shutil,subprocess,sys,time
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'))
from qa_build_guard import require_no_games
from gpu_candidate_identity import verify,sha
require_no_games();e=P/'evidence'/('gpu-candidate-identity-'+str(time.time_ns()));e.mkdir();root=e/'private-backend';root.mkdir();rows=[]
libsrc=e/'lib.c';libsrc.write_text('int authored_value(void){return 7;}\n');mainsrc=e/'main.c';mainsrc.write_text('extern int authored_value(void); int main(void){return authored_value()!=7;}\n')
def run(args):return subprocess.run(args,check=True,capture_output=True,text=True)
def sign(path):run(['/usr/bin/codesign','--force','--sign','-',str(path)])
def rejected(name,meta,reason):
 try:verify(meta,root)
 except ValueError as exc:
  assert reason in str(exc),(name,str(exc));rows.append(dict(case=name,rejected=True,reason=str(exc)))
 else:raise AssertionError(name+' accepted')
a=root/'libbbgpu.A.dylib';b=root/'libbbgpu.B.dylib';child=root/'child'
for lib in(a,b):run(['/usr/bin/clang','-arch','arm64','-dynamiclib',str(libsrc),'-Wl,-install_name,'+str(lib),'-o',str(lib)]);sign(lib)
run(['/usr/bin/clang','-arch','arm64',str(mainsrc),str(a),'-Wl,-headerpad_max_install_names','-o',str(child)]);sign(child)
meta=dict(target=str(child),library=str(b),library_sha256=sha(b))
rejected('wrong source child',meta,'load edge')
run(['/usr/bin/install_name_tool','-change',str(root/'does-not-exist.dylib'),str(b),str(child)])
rejected('unmatched replacement reports success',meta,'load edge')
run(['/usr/bin/install_name_tool','-change',str(a),str(b),str(child)]);sign(child);proof=verify(meta,root);rows.append(dict(case='correct load edge',passed=True,proof=proof))
run(['/usr/bin/install_name_tool','-change',str(b),'@loader_path/'+b.name,str(child)]);sign(child);verify(meta,root);rows.append(dict(case='loader-relative edge',passed=True))
run(['/usr/bin/install_name_tool','-change','@loader_path/'+b.name,'@loader_path/'+a.name,str(child)]);sign(child);rejected('wrong loader-relative edge',meta,'load edge')
run(['/usr/bin/install_name_tool','-change','@loader_path/'+a.name,'@loader_path/'+b.name,str(child)]);sign(child)
run(['/usr/bin/install_name_tool','-change','@loader_path/'+b.name,'@executable_path/'+b.name,str(child)]);sign(child);verify(meta,root);rows.append(dict(case='executable-relative edge',passed=True))
rejected('dylib is not a main child',dict(meta,target=str(b)),'main executable')
original=b.read_bytes();b.write_bytes(original+b'tamper');rejected('changed library hash',meta,'identity changed');b.write_bytes(original)
tampered=bytearray(original);assert len(tampered)>4096;tampered[4096]^=1;b.write_bytes(tampered);rejected('updated hash cannot hide invalid signature',dict(meta,library_sha256=sha(b)),'signature rejected');b.write_bytes(original)
alias=root/'alias';alias.symlink_to(b);rejected('symlink candidate',dict(meta,library=str(alias)),'outside private')
rejected('outside private root',dict(meta,target=str(Path('/usr/bin/true'))),'outside private')
x86=root/'x86-child';run(['/usr/bin/clang','-arch','x86_64',str(mainsrc),str(libsrc),'-o',str(x86)]);sign(x86);rejected('x86 host residue',dict(meta,target=str(x86)),'native ARM64')
result=dict(passed=True,controls=rows,no_game_started=True,formal_data_modified=False,unmatched_replacement_was_real_command=True)
(e/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(e);print('PASS',len(rows),'actual Mach-O load identity controls')
