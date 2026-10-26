#!/usr/bin/env python3
"""New host-local component stage; preserve the previous stage byte-for-byte."""
from pathlib import Path
import argparse,ast,hashlib,json,os,shutil,stat,time

PRIVATE_PROJECT=Path('/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype')
TOOLS=('launcher_fixed_helpers.py','launcher_local_aot.py','local_aot_policy.py','local_metal_builder.py',
       'warm_archive_manifest.py','gpu_candidate_identity.py','native_observer.py',
       'qa_memory_policy.py','qa_build_guard.py','paged_metal_requests.py',
       'launcher_game_pso.py','game_pso_component.py','game_pso_candidates.py',
       'prepare_game_psos.py','game_pso_queue.py','game_pso_progress_identity.py','game_pso_owned_audit.py',
       'inventory_shader_cache.py','decode_shader_cache.py','game_shader_catalog.py',
       'launcher_game_catalog.py','launcher_game_modules.py','full_game_catalog.py',
       'gnm_shader_metadata.py','static_program_links.py','complete_pipeline_inputs.py',
       'game_module_inputs.py','fetch_projection_inputs.py','owned_geometry_inputs.py',
       'game_pso_geometry_adapter.py','complete_geometry_psos.py','run_game_module_contracts.py',
       'prepare_fetch_capture_receipt.py','run_owned_geometry_contracts.py','launcher_source_view.py',
       'game_companion_modules.py')
SUPPORT={'vendor/blood_echoes/loader/prepare.py':'scripts/prepare.py',
         'vendor/blood_echoes/loader/game_check.py':'scripts/game_check.py'}
LEGACY_RENDER_LOG='        echo "Metal: direct Metal API, ARM64 host, Rosetta game, shared no-copy buffers"\n'
FEX_RENDER_LOG='''        if [[ ${BB_ARM_LAUNCHER_COMPAT:-0} == 1 ]]; then
            echo "Metal: direct Metal API, ARM64 host, FEX game runtime, shared no-copy buffers"
        else
            echo "Metal: direct Metal API, ARM64 host, Rosetta game, shared no-copy buffers"
        fi
'''
def runtime_log_revision(text):
    if text.count(LEGACY_RENDER_LOG)>1:raise ValueError('ambiguous runtime log revision')
    return text.replace(LEGACY_RENDER_LOG,FEX_RENDER_LOG,1)
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
def validate_import_closure(tools):
    """Refuse a newly added local dependency before creating a partial stage."""
    expected=set(TOOLS)
    for name in TOOLS:
        tree=ast.parse((tools/name).read_text());imports=[]
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):imports.extend(alias.name.split('.')[0]for alias in node.names)
            elif isinstance(node,ast.ImportFrom)and node.module:imports.append(node.module.split('.')[0])
        for module in imports:
            dependency=module+'.py'
            if(tools/dependency).is_file()and dependency not in expected:
                raise ValueError('runtime preparation local dependency is not frozen: '+name+' -> '+dependency)
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
    support=manifest['runtime_support']
    if set(support)!={'''+','.join(repr(name)for name in SUPPORT)+'''}:
        raise ValueError('incomplete original parser closure')
    for name,digest in support.items():
        path=stage/name;info=path.lstat()
        if path!=path.resolve(strict=True)or not stat.S_ISREG(info.st_mode)or info.st_uid!=os.getuid():
            raise ValueError('original parser is not canonical owned code')
        with path.open('rb')as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
        if actual!=digest:raise ValueError('original parser identity changed: '+name)
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
    tools=owned_dir(runtime_tools or Path(__file__).resolve().parent)
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
    log_relative='tools/macos/run_optimized.sh'
    old_log=(original/log_relative).read_text()
    new_log=(repository/log_relative).read_text()
    if new_log!=runtime_log_revision(old_log):
        raise ValueError('run_optimized.sh changed beyond the exact FEX runtime log correction')
    for name in TOOLS:
        path=tools/name
        if not path.is_file()or path.is_symlink():raise ValueError('missing/linked tool dependency: '+name)
        ast.parse(path.read_text())
    validate_import_closure(tools)
    tool_api(tools/'launcher_local_aot.py','main',('project','execution_root'))
    tool_api(tools/'launcher_local_aot.py','cli',('project','execution_root'))
    tool_api(tools/'prepare_game_psos.py','prepare',('services',))
    runner=(tools/'prepare_game_psos.py').read_text()
    if "sys.path.insert(0,str(P/'tools'))"in runner:
        raise ValueError('PSO runner imports code from external data P/tools')
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
    shutil.copy2(repository/log_relative,target/log_relative)
    native_tools=target/'native-runtime-tools';native_tools.mkdir()
    hashes={}
    for name in TOOLS:
        shutil.copy2(tools/name,native_tools/name);hashes[name]=sha(native_tools/name)
    support={}
    for destination,original_name in SUPPORT.items():
        dst=target/destination;dst.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(original/original_name,dst)
        support[destination]=sha(dst)
    startup=target/'tools/macos/local_metal_startup.py'
    startup.write_text(bootstrap(project));startup.chmod(0o755)
    effective=dict(source);effective['run.sh']=sha(target/'run.sh')
    effective[log_relative]=sha(target/log_relative)
    changed=[name for name in source if effective[name]!=source[name]]
    parity=dict(original_sources=source,effective_sources=effective,
        script_bodies_changed=changed,final_exec_hook_only=True,
        runtime_log_only_correction=log_relative in changed,
        previous_stage=str(original),previous_stage_modified=False)
    (target/'SOURCE-PARITY.json').write_text(json.dumps(parity,indent=2)+'\n')
    manifest=dict(schema=1,host_local_only=True,portable_installer=False,
        runtime_project=str(project),runtime_tools=hashes,runtime_support=support,
        startup_bootstrap_sha256=sha(startup),default_mode='auto',
        unchanged_original_scripts=16-len(changed),original_stage_modified=False,
        game_or_data_copied=False,source_repository=str(repository),
        game_pso_component_manifest='GAME-PSO-COMPONENT.json',
        default_game_pso_preparation='when-final-component-verified')
    (target/'ARM64-LOCAL-PREPARATION.json').write_text(json.dumps(manifest,indent=2)+'\n')
    for relative,digest in source.items():
        if sha(original/relative)!=digest:raise RuntimeError('previous stage changed during preparation')
        if relative not in changed and sha(target/relative)!=digest:raise RuntimeError('unexpected original script delta')
    return dict(stage=str(target),manifest=manifest,parity=parity)
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--source-repository',type=Path,required=True)
    ap.add_argument('--runtime-tools',type=Path);args=ap.parse_args()
    print(json.dumps(build_stage(project=PRIVATE_PROJECT,repository=args.source_repository,runtime_tools=args.runtime_tools),indent=2))
if __name__=='__main__':main()
