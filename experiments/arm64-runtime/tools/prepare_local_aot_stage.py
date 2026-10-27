#!/usr/bin/env python3
"""New host-local component stage; preserve the previous stage byte-for-byte."""
from pathlib import Path
import argparse,ast,hashlib,json,os,shutil,stat,time

PRIVATE_PROJECT=Path('/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype')
TOOLS=('recipe_preparation_schedule.py','resource_parse_products.py','compilation_domains.py','module_product_schedule.py','texture_binding_request.py','pso_startup_schedule.py','startup_change_journal.py','incremental_file_hashes.py','launcher_fixed_helpers.py','launcher_local_aot.py','local_aot_policy.py','local_metal_builder.py',
       'warm_archive_manifest.py','gpu_candidate_identity.py','native_observer.py',
       'qa_memory_policy.py','qa_build_guard.py','paged_metal_requests.py',
       'launcher_game_pso.py','game_pso_component.py','game_pso_candidates.py',
       'prepare_game_psos.py','game_pso_batch.py','game_pso_queue.py','game_pso_progress_identity.py','game_pso_owned_audit.py',
       'inventory_shader_cache.py','decode_shader_cache.py','game_shader_catalog.py',
       'launcher_game_catalog.py','launcher_game_modules.py','full_game_catalog.py',
       'gnm_shader_metadata.py','static_program_links.py','complete_pipeline_inputs.py',
       'game_module_inputs.py','fetch_projection_inputs.py','owned_geometry_inputs.py',
       'game_pso_geometry_adapter.py','complete_geometry_psos.py','run_game_module_contracts.py',
       'prepare_fetch_capture_receipt.py','run_owned_geometry_contracts.py','launcher_source_view.py',
       'game_companion_modules.py','prepare_module_metadata.py','quick_restart_preparation.py','launcher_runtime_profile.py')
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
import hashlib,json,os,stat,plistlib
def main():
    stage=Path(__file__).resolve(strict=True).parents[2]
    project=Path('''+repr(str(project))+''')
    manifest=json.loads((stage/'ARM64-LOCAL-PREPARATION.json').read_text())
    app=stage.parent.parent.parent
    formal=False
    if app.suffix=='.app'and (app/'Contents/Info.plist').is_file():
        formal=plistlib.loads((app/'Contents/Info.plist').read_bytes()).get('CFBundleIdentifier')=='io.github.bbport.optimized'
    if not formal:
        if project!=project.resolve(strict=True)or not (project/'state').is_dir():
            raise ValueError('prepared Shared runtime/data root is unavailable')
        if manifest['runtime_project']!=str(project)or not manifest['host_local_only']:
            raise ValueError('wrong prepared runtime root')
    tools=stage/'native-runtime-tools'
    if tools!=tools.resolve(strict=True):raise ValueError('linked runtime tools')
    expected={'''+','.join(repr(name) for name in TOOLS)+'''}
    if set(manifest['runtime_tools'])!=expected:raise ValueError('incomplete runtime tool closure')
    for name,digest in manifest['runtime_tools'].items():
        path=tools/name; info=path.lstat()
        if not stat.S_ISREG(info.st_mode)or info.st_uid not in ((0,os.getuid())if formal else(os.getuid(),)):
            raise ValueError('runtime tool is not an owned regular file')
        with path.open('rb')as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
        if actual!=digest:raise ValueError('runtime tool identity changed: '+name)
    support=manifest['runtime_support']
    if set(support)!={'''+','.join(repr(name)for name in SUPPORT)+'''}:
        raise ValueError('incomplete original parser closure')
    for name,digest in support.items():
        path=stage/name;info=path.lstat()
        if path!=path.resolve(strict=True)or not stat.S_ISREG(info.st_mode)or info.st_uid not in ((0,os.getuid())if formal else(os.getuid(),)):
            raise ValueError('original parser is not canonical owned code')
        with path.open('rb')as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
        if actual!=digest:raise ValueError('original parser identity changed: '+name)
    sys.path.insert(0,str(tools))
    import local_metal_builder,local_aot_policy,launcher_local_aot
    for module in (local_metal_builder,local_aot_policy,launcher_local_aot):
        if Path(module.__file__).resolve().parent!=tools:
            raise ValueError('runtime code imported outside frozen tool closure')
    profile=None
    if formal:
        import launcher_runtime_profile
        if Path(launcher_runtime_profile.__file__).resolve().parent!=tools:
            raise ValueError('formal profile imported outside frozen tool closure')
        profile=launcher_runtime_profile.derive(stage,sys.argv[2:],os.environ,executable=sys.argv[1])
        if profile is None:raise ValueError('formal outer bundle admission unavailable')
        project=profile.work_root
    local_metal_builder.PROJECT=project
    local_aot_policy.P=project
    launcher_local_aot.cli(project=project,execution_root=stage,**({'runtime_profile':profile}if profile is not None else{}))
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

def validate_runtime_tools(tools):
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
    validate_runtime_tools(tools)
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
SOURCE_SCRIPTS=('run.sh','tools/macos/run_optimized.sh','tools/macos/supervise_restart.sh',
 'tools/macos/quicksave.py','patches/Bloodborne.xml','scripts/link_libc.py',
 'scripts/chinese_translation.py','scripts/online.py','scripts/content_profile.py',
 'scripts/link_modules.py','scripts/patches.py','scripts/game_check.py','scripts/mods.py',
 'scripts/prepare.py','scripts/ultrawide_ui.py','tools/macos/run.sh')
HOOK_ANCHOR='if [[ -n ${mod_game:-} ]]; then\n'
FINAL_EXEC='exec "$probe" "${probe_args[@]}"\n'

def projected_launcher_scripts(run,log):
    """Only the exact existing hook/log transform; no old revision is invented."""
    if run.count(HOOK)>1:raise ValueError('duplicate final-exec hook')
    had_hook=run.count(HOOK)==1
    original_run=run.replace(HOOK,'',1)if had_hook else run
    if 'local_metal_startup'in original_run or '# Native-runtime component preparation'in original_run:
        raise ValueError('partial or ambiguous final-exec hook')
    if original_run.count(HOOK_ANCHOR)!=1 or original_run.count(FINAL_EXEC)!=1:
        raise ValueError('unique original final-exec insertion anchor required')
    prefix,tail=original_run.split(HOOK_ANCHOR,1)
    if not prefix.rstrip().splitlines()or not prefix.rstrip().splitlines()[-1].startswith('probe_args=(')or FINAL_EXEC not in tail:
        raise ValueError('final-exec hook is not immediately after original probe arguments')
    effective_run=original_run.replace(HOOK_ANCHOR,HOOK+HOOK_ANCHOR,1)
    if had_hook and effective_run!=run:raise ValueError('existing hook is not in the exact original final-exec position')
    if log.count(FEX_RENDER_LOG)>1:raise ValueError('duplicate FEX runtime log')
    had_log=log.count(FEX_RENDER_LOG)==1
    original_log=log.replace(FEX_RENDER_LOG,LEGACY_RENDER_LOG,1)if had_log else log
    if 'FEX game runtime'in original_log:raise ValueError('partial or ambiguous FEX runtime log')
    effective_log=runtime_log_revision(original_log)
    if had_log and effective_log!=log:raise ValueError('FEX log projection failed its original forward proof')
    return original_run,effective_run,original_log,effective_log,dict(hook_already_present=had_hook,log_already_present=had_log,
      original_identity_kind='current-repository-exact-known-projection'if had_hook or had_log else'current-repository-bytes')

def _source_bytes(path,limit=4*1024*1024):
    path=Path(path).absolute()
    if path!=path.resolve(strict=True):raise ValueError('source closure input must be canonical and unlinked')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode)or before.st_uid!=os.getuid()or before.st_nlink!=1 or before.st_size<=0 or before.st_size>limit:
            raise ValueError('bounded owned regular source closure input required')
        with os.fdopen(fd,'rb',closefd=False)as stream:raw=stream.read(limit+1)
        stamp=lambda x:(x.st_dev,x.st_ino,x.st_uid,x.st_mode,x.st_nlink,x.st_size,x.st_mtime_ns,x.st_ctime_ns)
        if len(raw)!=before.st_size or stamp(before)!=stamp(os.fstat(fd))or stamp(before)!=stamp(path.lstat()):
            raise ValueError('source closure input changed while reading')
        return raw
    finally:os.close(fd)

def write_source_closure(*,source,repository,runtime_tools,destination,project):
    """Fresh code-only payload from a checkout; no QA artifacts or runtime ready claim.

    source is the explicit D source root; repository holds the sixteen original
    launcher scripts. project is only the unchanged development fallback literal
    embedded by bootstrap(), and is neither required to exist nor read here.
    """
    source=owned_dir(source);repository=owned_dir(repository);tools=owned_dir(runtime_tools)
    if source==repository or not source.is_relative_to(repository)or tools==source or not tools.is_relative_to(source):
        raise ValueError('explicit runtime source/tools must belong to the selected repository')
    project=Path(project)
    if project!=PRIVATE_PROJECT:raise ValueError('unchanged development fallback literal required')
    target=Path(destination).absolute();parent=owned_dir(target.parent)
    if target.parent!=parent or target!=target.resolve()or os.path.lexists(target):
        raise ValueError('source closure destination must be fresh and canonical')
    if any(target.is_relative_to(p)or p.is_relative_to(target)for p in(source,repository,tools)):
        raise ValueError('source closure output must not overlap source inputs')
    # All source and import/API validation precedes creation of the fresh root.
    files={name:_source_bytes(repository/name)for name in SOURCE_SCRIPTS}
    tool_files={name:_source_bytes(tools/name)for name in TOOLS}
    validate_runtime_tools(tools)
    old_run,new_run,old_log,new_log,projection=projected_launcher_scripts(files['run.sh'].decode(),files['tools/macos/run_optimized.sh'].decode())
    original={name:hashlib.sha256(raw).hexdigest()for name,raw in files.items()}
    original['run.sh']=hashlib.sha256(old_run.encode()).hexdigest()
    original['tools/macos/run_optimized.sh']=hashlib.sha256(old_log.encode()).hexdigest()
    payload=dict(files);payload['run.sh']=new_run.encode();payload['tools/macos/run_optimized.sh']=new_log.encode()
    for name,raw in tool_files.items():payload['native-runtime-tools/'+name]=raw
    support={}
    for name,original_name in SUPPORT.items():payload[name]=files[original_name];support[name]=hashlib.sha256(payload[name]).hexdigest()
    startup='tools/macos/local_metal_startup.py';payload[startup]=bootstrap(project).encode()
    if sum(map(len,payload.values()))>32*1024*1024:raise ValueError('source closure total byte budget')
    # No source-side import, subprocess or state tree is needed to emit code.
    inputs={str(repository/name):hashlib.sha256(raw).hexdigest()for name,raw in files.items()}
    inputs.update({str(tools/name):hashlib.sha256(raw).hexdigest()for name,raw in tool_files.items()})
    effective={name:hashlib.sha256(payload[name]).hexdigest()for name in SOURCE_SCRIPTS}
    changed=[name for name in SOURCE_SCRIPTS if original[name]!=effective[name]]
    parity=dict(original_sources=original,effective_sources=effective,script_bodies_changed=changed,final_exec_hook_only=True,
      runtime_log_only_correction='tools/macos/run_optimized.sh'in changed,source_projection=projection,
      source_repository_modified=False,previous_stage_read=False)
    manifest=dict(schema=1,host_local_only=True,portable_installer=False,production=False,code_closure_only=True,
      runtime_project=str(project),runtime_tools={name:hashlib.sha256(raw).hexdigest()for name,raw in tool_files.items()},
      runtime_support=support,startup_bootstrap_sha256=hashlib.sha256(payload[startup]).hexdigest(),default_mode='auto',
      unchanged_original_scripts=16-len(changed),game_or_data_copied=False,source_repository=str(repository),
      game_pso_component_manifest='GAME-PSO-COMPONENT.json',default_game_pso_preparation='when-final-component-verified',
      native_components_included=False,final_component_verified=False)
    for path,want in inputs.items():
        if hashlib.sha256(_source_bytes(path)).hexdigest()!=want:raise ValueError('source closure input changed before publication')
    target.mkdir(mode=0o700)
    for relative,raw in payload.items():
        out=target/relative;out.parent.mkdir(parents=True,exist_ok=True)
        for parent in out.parents:
            if parent==target:break
            parent.chmod(0o755)
        with out.open('xb')as stream:stream.write(raw)
        out.chmod(0o644)
        if hashlib.sha256(out.read_bytes()).hexdigest()!=hashlib.sha256(raw).hexdigest():raise ValueError('copied source closure changed')
    (target/startup).chmod(0o755)
    for name,value in [('SOURCE-PARITY.json',parity),('ARM64-LOCAL-PREPARATION.json',manifest)]:
        with(target/name).open('x')as stream:json.dump(value,stream,indent=2);stream.write('\n')
        (target/name).chmod(0o644)
    for path,want in inputs.items():
        if hashlib.sha256(_source_bytes(path)).hexdigest()!=want:raise ValueError('source closure input changed during publication')
    return dict(status='source-code-closure-written',stage=str(target),source=str(source),manifest=manifest,parity=parity,
      input_sha256=inputs,game_or_data_copied=False,native_components_included=False,formal_profile_enabled=False)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--source-repository',type=Path,required=True)
    ap.add_argument('--runtime-tools',type=Path);args=ap.parse_args()
    print(json.dumps(build_stage(project=PRIVATE_PROJECT,repository=args.source_repository,runtime_tools=args.runtime_tools),indent=2))
if __name__=='__main__':main()
