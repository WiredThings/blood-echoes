"""Add an independent bbcpu execution kit to a fresh isolated QA stage only.

The original QA packager still owns the launcher, saves, scripts and renderer.
No compiled game code is included. Local producer sources are a frozen closure
with their original repository layout; they never read the Documents checkout.
"""
from pathlib import Path
import ast,json,os,plistlib,shutil,subprocess
from bbcpu_launch import need,owned,private,sha
from native_package_assets import Relocator

def freeze_source(repository,destination):
    repository=owned(repository,True);tools=repository/'experiments/arm64-runtime/tools'
    need(not destination.exists(),'preserve old CPU producer kits')
    selected=set();pending=['bbcpu_static_library.py','complete_packaged_python.py']
    while pending:
        name=pending.pop()
        if name in selected:continue
        selected.add(name);tree=ast.parse(owned(tools/name).read_text())
        for node in ast.walk(tree):
            imports=([a.name.split('.')[0]for a in node.names]if isinstance(node,ast.Import)else
                     [node.module.split('.')[0]]if isinstance(node,ast.ImportFrom)and node.module else[])
            for module in imports:
                child=module+'.py'
                if(tools/child).is_file()and child not in selected:pending.append(child)
    files={Path('experiments/arm64-runtime/tools')/name for name in selected}
    # native_package_assets uses this original parser via importlib, not a
    # Python import statement. Freeze it explicitly rather than searching PATH.
    files.add(Path('tools/macos/package_optimized.py'))
    vendor=repository/'experiments/arm64-runtime/vendor/bbcpu'
    manifest=json.loads(owned(vendor/'UPSTREAM.json').read_bytes())
    names=set(manifest['upstream_files'])|{'UPSTREAM.json'}
    for row in manifest['local_patches']:names.update(row.get('added_files',{}))
    files.update(Path('experiments/arm64-runtime/vendor/bbcpu')/name for name in names)
    need(len(files)<=256,'CPU producer source budget exceeded');destination.mkdir(mode=0o700)
    hashes={}
    for relative in sorted(files):
        source=owned(repository/relative);before=sha(source);target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,target);need(sha(target)==before==sha(source),'CPU producer source changed during freeze');hashes[str(relative)]=before
    return hashes

def freeze(stage,*,repository,build,game,toolchain,search_dirs):
    stage=private(stage);build=owned(build,True);game=owned(game)
    info=stage.parents[2]/'Contents/Info.plist'
    if info.exists():need(plistlib.loads(info.read_bytes()).get('CFBundleIdentifier')!='io.github.bbport.optimized','refuse modifying a formal application')
    target=stage/'BBCPU-COMPONENT.json';need(not target.exists(),'CPU component already frozen')
    native_path=stage/'NATIVE-METAL.json';native=json.loads(owned(native_path).read_bytes())
    need(native.get('production')is False and native.get('game_runtime')=='fex-arm64','existing isolated FEX stage required')
    need(subprocess.check_output(['/usr/bin/lipo','-archs',str(game)],text=True).strip()=='arm64','native bbcpu host required')
    symbols=subprocess.check_output(['/usr/bin/nm','-gU',str(game)],text=True)
    need('_bbcpu_run'in symbols and 'FEXCore'not in symbols,'whole independent bbcpu host required')
    abi_doc=json.loads(owned(build/'bbcpu-abi-identity.json').read_bytes())
    # The unchanged linked Metal library admits its data directory before
    # main(), including read-only ABI queries. Reuse the existing scalar
    # capability scope; never weaken that constructor's admission for a probe.
    from launcher_runtime_profile import capability_environment
    from prepare_local_aot_stage import PRIVATE_PROJECT
    with capability_environment(PRIVATE_PROJECT,qa_root=PRIVATE_PROJECT/'state',prefix='bbcpu-host-contract-')as env:
        contract=json.loads(subprocess.check_output([str(game),'--cpu-backend-contract'],env=env,text=True,timeout=15))
    need(contract.get('schema')==1 and contract.get('backend')=='bbcpu'and contract.get('abi')==5 and contract.get('native_tsc')is True and
         contract.get('header_contract_sha256')==abi_doc['header_contract_sha256'],'actual game host and selected CPU tools have different ABI contracts')
    app=stage/'bin/Bloodborne bbcpu Game.app';need(not app.exists(),'preserve existing CPU applications')
    original=stage/'bin/Bloodborne Game.app';meta=plistlib.loads(owned(original/'Contents/Info.plist').read_bytes())
    meta['CFBundleIdentifier']+='.'+'bbcpu';meta['CFBundleExecutable']='bb-probe-metal'
    (app/'Contents/MacOS').mkdir(parents=True);(app/'Contents/Info.plist').write_bytes(plistlib.dumps(meta))
    shutil.copytree(original/'Contents/Resources',app/'Contents/Resources',symlinks=False)
    resolver=Relocator(stage,search_dirs=search_dirs,library_subdir='bbcpu-deps/lib')
    cpu=app/'Contents/MacOS/bb-probe-metal';resolver.copy(game,cpu)
    roles={'cpu':cpu}
    for key,name in dict(preparer='bbcpu-code-preparer',scanner='bbcpu-scan',generator='bbrecomp').items():
        source=game if key=='preparer'and contract.get('runtime_image_export')==1 else owned(build/name);dest=stage/'bin/bbcpu-tools'/name
        resolver.copy(source,dest);roles[key]=dest
    abi=stage/'bin/bbcpu-tools/bbcpu-abi-identity.json';shutil.copy2(owned(build/'bbcpu-abi-identity.json'),abi);roles['abi']=abi
    headers=stage/'bbcpu-headers';header_files={}
    need(bool(abi_doc.get('dependency_headers')),'CPU dependency header contract required')
    for name,digest in abi_doc['dependency_headers'].items():
        relative=Path(name);need(not relative.is_absolute()and '..'not in relative.parts,'invalid CPU header role')
        source=next((Path(root)/relative for root in abi_doc['source_header_roots']if(Path(root)/relative).is_file()),None)
        need(source is not None,'compiled CPU header source unavailable: '+name)
        source=owned(source.resolve(strict=True));need(sha(source)==digest,'compiled CPU header changed: '+name)
        dest=headers/relative;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,dest)
        need(sha(dest)==digest,'CPU header changed during freeze');header_files[str(dest.relative_to(stage))]=digest
    toolchain=dict(toolchain,includes=[str(headers)])
    # Relocation can alter executable bytes. Seal only final signed bytes.
    resolver.sign_copied()
    subprocess.run(['/usr/bin/codesign','--force','--sign','-',str(app)],check=True,capture_output=True)
    source_root=stage/'bbcpu-producer';sources=freeze_source(repository,source_root)
    doc=dict(schema=1,development_only=True,backend='bbcpu',default_execution='static',
             runtime_image_export=contract.get('runtime_image_export',0),
             producer_root=str(source_root),producer_entry='experiments/arm64-runtime/tools/bbcpu_static_library.py',producer_files=sources,
             toolchain=toolchain,dependency_headers=header_files,runtime_dependencies={str(path.relative_to(stage)):sha(path)for path in resolver.machos if path not in roles.values()},
             **{name:dict(path=str(path.relative_to(stage)),sha256=sha(path))for name,path in roles.items()})
    target.write_text(json.dumps(doc,sort_keys=True,indent=2)+'\n')
    native.update(game_runtime='arm64',cpu_backends=['fex','bbcpu'])
    # Retain the original FEX roles and add the independently admitted CPU kit.
    native['frozen_runtime'].update({str(path.relative_to(stage)):sha(path)for path in roles.values()})
    native['frozen_runtime'].update(doc['runtime_dependencies'])
    native_path.write_text(json.dumps(native,sort_keys=True,indent=2)+'\n')
    return dict(manifest_sha256=sha(target),cpu_sha256=sha(cpu),producer_files=len(sources),engine='bbcpu',fallback='bbcpu-own-translator',FEX_bridge=False)
