#!/usr/bin/env python3
"""New host-local component stage; preserve the previous stage byte-for-byte."""
from pathlib import Path
import argparse,ast,hashlib,json,os,shutil,stat,time

PRIVATE_PROJECT=Path('/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype')
TOOLS=('launcher_local_aot.py','local_aot_policy.py','local_metal_builder.py',
       'warm_archive_manifest.py','gpu_candidate_identity.py','native_observer.py',
       'qa_memory_policy.py','qa_build_guard.py')
HOOK='''# Native-runtime component preparation happens after the original patch/mod
# pipeline, before either game process exists. The wrapper execs this same probe.
if [[ ${BB_ARM_LAUNCHER_COMPAT:-0} == 1 && ${BB_RENDERER:-} == metal ]]; then
    local_metal_startup=$PWD/tools/macos/local_metal_startup.py
    [[ -f $local_metal_startup ]] || { echo "STOP: native Metal preparation component is missing" >&2; exit 1; }
    probe_args=("$local_metal_startup" "$probe" "${probe_args[@]}")
    probe=$PYTHON
fi
'''
def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def owned_dir(path):
    path=Path(path).absolute()
    if path!=path.resolve(strict=True)or not path.is_dir()or path.stat().st_uid!=os.getuid():
        raise ValueError('owned canonical directory required')
    return path
def tool_api(path, function, names):
    tree=ast.parse(path.read_text());node=next((n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name==function),None)
    if node is None or not set(names)<={n.arg for n in node.args.kwonlyargs}:
        raise ValueError('packaged preparation API not ready: '+function)
def bootstrap(project):
    return '''#!/usr/bin/env python3
import sys
sys.dont_write_bytecode=True
from pathlib import Path
import hashlib,json,os,stat
def main():
    stage=Path(__file__).resolve(strict=True).parents[2]
    project=Path('''+repr(str(project))+''')
    if project!=project.resolve(strict=True)or not (project/'state').is_dir():
        raise ValueError('prepared Shared runtime/data root is unavailable')
    manifest=json.loads((stage/'ARM64-LOCAL-PREPARATION.json').read_text())
    if manifest['runtime_project']!=str(project)or not manifest['host_local_only']:
        raise ValueError('wrong prepared runtime root')
    tools=stage/'native-runtime-tools'
    if tools!=tools.resolve(strict=True):raise ValueError('linked runtime tools')
    expected={'''+','.join(repr(name) for name in TOOLS)+'''}
    if set(manifest['runtime_tools'])!=expected:raise ValueError('incomplete runtime tool closure')
    for name,digest in manifest['runtime_tools'].items():
        path=tools/name; info=path.lstat()
        if not stat.S_ISREG(info.st_mode)or info.st_uid!=os.getuid():
            raise ValueError('runtime tool is not an owned regular file')
        with path.open('rb')as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
        if actual!=digest:raise ValueError('runtime tool identity changed: '+name)
    sys.path.insert(0,str(tools))
    import local_metal_builder,local_aot_policy,launcher_local_aot
    for module in (local_metal_builder,local_aot_policy,launcher_local_aot):
        if Path(module.__file__).resolve().parent!=tools:
            raise ValueError('runtime code imported outside frozen tool closure')
    local_metal_builder.PROJECT=project
    local_aot_policy.P=project
    launcher_local_aot.cli(project=project,execution_root=stage)
if __name__=='__main__':
    try:main()
    except Exception as error:
        print('Metal local preparation stage failed: '+str(error),file=sys.stderr,flush=True)
        raise SystemExit(74)
'''

def copy_stage_dependency(original,target,name):
    """Relocate internal links, keep external host-local dependencies anchored."""
    src=original/name;dst=target/name
    if src.is_symlink():
        destination=src.resolve(strict=True)
        try:destination=target/destination.relative_to(original)
        except ValueError:pass
        dst.symlink_to(destination,target_is_directory=src.is_dir())
        return
    shutil.copytree(src,dst,symlinks=True)
    for directory,subdirs,files in os.walk(src,followlinks=False):
        for child in subdirs+files:
            source=Path(directory)/child
            if not source.is_symlink():continue
            destination=source.resolve(strict=True)
            try:destination=target/destination.relative_to(original)
            except ValueError:pass
            copied=target/source.relative_to(original)
            copied.unlink()
            copied.symlink_to(destination,target_is_directory=source.is_dir())

def build_stage(*,project,repository,runtime_tools=None,destination=None):
    project=owned_dir(project);repository=owned_dir(repository)
    original=owned_dir(project/'launcher-component-stage')
    state=owned_dir(project/'state')
    tools=owned_dir(runtime_tools or project/'tools')
    source=json.loads((original/'SOURCE-PARITY.json').read_text())['original_sources']
    if len(source)!=16:raise ValueError('expected the admitted original 16-script stage')
    for relative,digest in source.items():
        name=Path(relative)
        if name.is_absolute()or '..'in name.parts or str(name)!=relative:
            raise ValueError('invalid original source path')
        if sha(original/relative)!=digest:raise ValueError('previous component stage changed: '+relative)
    new_run=(repository/'run.sh').read_text();old_run=(original/'run.sh').read_text()
    if new_run.count(HOOK)!=1 or new_run.replace(HOOK,'',1)!=old_run:
        raise ValueError('new run.sh is not exactly the admitted final-exec hook')
    for name in TOOLS:
        path=tools/name
        if not path.is_file()or path.is_symlink():raise ValueError('missing/linked tool dependency: '+name)
        ast.parse(path.read_text())
    tool_api(tools/'launcher_local_aot.py','main',('project','execution_root'))
    tool_api(tools/'launcher_local_aot.py','cli',('project','execution_root'))
    builder=(tools/'local_metal_builder.py').read_text()
    if "sys.path.insert(0,str(PROJECT/'tools'))"in builder:
        raise ValueError('builder still imports code from external data PROJECT/tools')
    parent=project/'launcher-component-stages'
    parent.mkdir(mode=0o700,exist_ok=True);owned_dir(parent)
    target=Path(destination).absolute()if destination else parent/('local-aot-'+str(time.time_ns()))
    if target.parent!=parent or os.path.lexists(target):raise ValueError('stage destination must be fresh under private stages')
    target.mkdir(mode=0o700)
    for relative in source:
        dst=target/relative;dst.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(original/relative,dst)
    for name in ('.packaged','NATIVE-METAL.json'):
        shutil.copy2(original/name,target/name)
    for name in ('bin','python','deps-metal-arm64'):
        copy_stage_dependency(original,target,name)
    # Previous stage and its sixteen source bodies remain untouched. Record both
    # the old admitted source set and the effective new hook explicitly.
    shutil.copy2(repository/'run.sh',target/'run.sh')
    native_tools=target/'native-runtime-tools';native_tools.mkdir()
    hashes={}
    for name in TOOLS:
        shutil.copy2(tools/name,native_tools/name);hashes[name]=sha(native_tools/name)
    startup=target/'tools/macos/local_metal_startup.py'
    startup.write_text(bootstrap(project));startup.chmod(0o755)
    effective=dict(source);effective['run.sh']=sha(target/'run.sh')
    parity=dict(original_sources=source,effective_sources=effective,
        script_bodies_changed=['run.sh'],final_exec_hook_only=True,
        previous_stage=str(original),previous_stage_modified=False)
    (target/'SOURCE-PARITY.json').write_text(json.dumps(parity,indent=2)+'\n')
    manifest=dict(schema=1,host_local_only=True,portable_installer=False,
        runtime_project=str(project),runtime_tools=hashes,
        startup_bootstrap_sha256=sha(startup),default_mode='auto',
        unchanged_original_scripts=15,original_stage_modified=False,
        game_or_data_copied=False,source_repository=str(repository))
    (target/'ARM64-LOCAL-PREPARATION.json').write_text(json.dumps(manifest,indent=2)+'\n')
    for relative,digest in source.items():
        if sha(original/relative)!=digest:raise RuntimeError('previous stage changed during preparation')
        if relative!='run.sh'and sha(target/relative)!=digest:raise RuntimeError('unexpected original script delta')
    return dict(stage=str(target),manifest=manifest,parity=parity)
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--source-repository',type=Path,required=True)
    ap.add_argument('--runtime-tools',type=Path);args=ap.parse_args()
    print(json.dumps(build_stage(project=PRIVATE_PROJECT,repository=args.source_repository,runtime_tools=args.runtime_tools),indent=2))
if __name__=='__main__':main()
