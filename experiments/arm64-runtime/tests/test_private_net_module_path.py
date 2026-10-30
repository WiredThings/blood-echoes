"""Native C admission policy, no network initialization/credentials/game."""
from pathlib import Path
import json,subprocess,tempfile
P=Path(__file__).resolve().parents[1]
root=str(P);exe='/private/qa/Blood Echoes ARM64 GUI QA.app/Contents/Resources/bbport/bin/bb-probe-metal';module=exe.rsplit('/',1)[0]+'/gpu-net/libbbnet.dylib'
rows=[(root+'/build/net/libbbnet.dylib',exe,1),(root+'/build-evil/net/libbbnet.dylib',exe,0),(module,exe,1),(module,exe.replace('/qa/','/other-qa/'),0),(module,exe.replace('bb-probe-metal','wrong-cpu'),0),('/tmp/libbbnet.dylib',exe,0),(module+'-other',exe,0),('/Applications/Blood Echoes.app/Contents/Resources/bbport/bin/gpu-net/libbbnet.dylib',exe,0)]
for name in('Bloodborne Game.app','Bloodborne bbcpu Game.app'):
    nested=exe.rsplit('/',1)[0]+'/'+name+'/Contents/MacOS/bb-probe-metal'
    rows.extend([(module,nested,1),(module,nested.replace('/qa/','/other-qa/'),0),
                 (module,nested.replace(name,name+'-other'),0)])
with tempfile.TemporaryDirectory(prefix='bb-net-path-')as root_dir:
    source=Path(root_dir)/'test.c';binary=Path(root_dir)/'test'
    text='#define BB_PROTOTYPE_ROOT '+json.dumps(root)+'\n#include "bb_private_net_module_path.h"\n#include <assert.h>\nstatic int formal=-1;\nint bb_bundled_net_module_path(const char*a,const char*b){(void)a;(void)b;return formal;}\nint main(void){'
    for module,cpu,expected in rows:text+='assert(bb_arm_private_net_module_path('+json.dumps(module)+','+json.dumps(cpu)+')=='+str(expected)+');'
    text+='assert(!bb_arm_private_net_module_path(0,"x"));assert(!bb_arm_private_net_module_path("x",0));formal=0;assert(!bb_arm_private_net_module_path('+json.dumps(root+'/build/net/libbbnet.dylib')+','+json.dumps(exe)+'));return 0;}'
    source.write_text(text);subprocess.run(['clang','-arch','arm64','-I'+str(P/'src'),str(source),'-o',str(binary)],check=True);subprocess.run([str(binary)],check=True)
print('PASS '+str(len(rows)+3)+' native module admission cases; no network or game started')
