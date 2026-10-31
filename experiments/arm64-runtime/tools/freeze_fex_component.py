"""Freeze only new FEX CPU/scanner roles into an already isolated scratch stage.

The caller owns atomic QA application promotion, source closure and signing.
No game image, generated CPU code, saves, settings or existing cache is bundled.
"""
from pathlib import Path
import json,plistlib
from bbcpu_launch import private,owned,need,sha
from native_package_assets import Relocator

def freeze(stage,*,game,scanner,search_dirs):
    stage=private(stage);native=json.loads(owned(stage/'NATIVE-METAL.json').read_bytes())
    need(native.get('production')is False and native.get('game_runtime')=='arm64','FEX disk component is isolated only')
    info=stage.parents[2]/'Contents/Info.plist'
    if info.exists():need(plistlib.loads(info.read_bytes()).get('CFBundleIdentifier')!='io.github.bbport.optimized','refuse formal application')
    cpu_names=('bin/bb-probe-metal','bin/Bloodborne Game.app/Contents/MacOS/bb-probe-metal')
    need(all(name in native['frozen_runtime']for name in cpu_names),'existing original FEX roles required')
    resolver=Relocator(stage,search_dirs=search_dirs,library_subdir='fex-disk-deps/lib')
    for name in cpu_names:
        destination=stage/name
        need(not destination.exists(),'FEX role destination must be a fresh scratch stage');destination.parent.mkdir(parents=True,exist_ok=True);resolver.copy(owned(game),destination)
    destination=stage/'bin/fex-disk-tools/bbcpu-scan';destination.parent.mkdir(parents=True,exist_ok=True);resolver.copy(owned(scanner),destination);resolver.sign_copied()
    roles=set(cpu_names)|{'bin/fex-disk-tools/bbcpu-scan'}
    dependencies={str(path.relative_to(stage)):sha(path)for path in resolver.machos if str(path.relative_to(stage))not in roles}
    row=lambda name:dict(path=name,sha256=sha(stage/name))
    doc=dict(schema=1,development_only=True,backend='fex',cpu=row(cpu_names[0]),cpu_aliases=[row(cpu_names[1])],scanner=row('bin/fex-disk-tools/bbcpu-scan'),runtime_dependencies=dependencies,
             automatic_default=True,producer='same-original-game-host-after-original-runtime-hooks',runtime_loading='demand-copy-into-normal-invalidatable-FEX-JIT-buffer',generated_game_assets_included=False)
    for name in roles:native['frozen_runtime'][name]=sha(stage/name)
    native['frozen_runtime'].update(dependencies)
    (stage/'FEX-COMPONENT.json').write_text(json.dumps(doc,sort_keys=True,indent=2)+'\n');(stage/'NATIVE-METAL.json').write_text(json.dumps(native,sort_keys=True,indent=2)+'\n')
    return doc
