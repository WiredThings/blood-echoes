"""Assemble normal ARM64 build products; never consumes a QA app or user data.

The original Swift shell, launcher scripts, wrapper/icon algorithms and payload
checks are reused. This command only writes a fresh staging app, never installs
or starts it. Native scalar capability queries have no game/boot/user inputs.
"""
from pathlib import Path
import argparse, hashlib, json, os, plistlib, re, shutil, stat, subprocess, sys

MACOS = Path(__file__).resolve().parents[3]/'tools/macos'
sys.path.insert(0, str(MACOS))
from package_optimized import loads, copy, SYSTEM
from game_app import create_game_app, sign_game_app
from native_metal import create_native_gpu_bundle
from prepare_local_install import check_relative_payload, development_probe, ROLE_PATHS
from prepare_local_aot_stage import write_source_closure, PRIVATE_PROJECT
from configure_native_build import renderer_inputs, cache_values
from game_pso_owned_audit import load_canonical_layout
from game_pso_component import prepare_inputs, finalize_inputs, load as load_component
from launcher_runtime_profile import packaging_capabilities, bundle, FORMAL_ID

def need(value, message):
    if not value: raise ValueError(message)

def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def directory(path):
    path=Path(path).absolute()
    need(path==path.resolve(strict=True) and path.is_dir() and path.stat().st_uid==os.getuid(), 'owned canonical build directory required')
    return path

def regular(path):
    path=Path(path).absolute()
    need(path==path.resolve(strict=True), 'canonical source artifact required')
    info=path.stat()
    need(stat.S_ISREG(info.st_mode) and info.st_uid in (0,os.getuid()) and info.st_nlink==1, 'plain source artifact required')
    return path

def run(*args):
    return subprocess.check_output(list(map(str,args)), stderr=subprocess.STDOUT, timeout=60)

def write(path, value):
    Path(path).write_text(json.dumps(value,indent=2,default=str)+'\n')
    Path(path).chmod(0o644)

def native_source_identity(first, second):
    """Compare native source bodies, not paths, to permit a Git archive build."""
    def inventory(root):
        result={}
        for sub in ('src','vendor','config'):
            for path in (root/sub).rglob('*'):
                if not path.is_file() or '__pycache__' in path.parts or '.git' in path.parts: continue
                if path.suffix.lower() in ('.pyc','.o','.a','.dylib','.so') or path.name=='.DS_Store': continue
                relative=str(path.relative_to(root))
                if path.is_symlink():
                    need(path.resolve(strict=True).is_relative_to(root), 'native source link escaped checkout')
                    result[relative]='link:'+os.readlink(path)
                else: result[relative]=sha(path)
        for name in ('targets.cmake','tools/generate_native.py','tools/generate_gpu.py','tests/metadata_layout.cpp','config/gpu-adapter-headers.json'):
            result[name]=sha(root/name)
        return result
    a,b=inventory(first),inventory(second)
    need(a and a==b, 'compiled native source differs from corresponding packaging source')
    return dict(files=len(a),sha256=hashlib.sha256(json.dumps(a,sort_keys=True,separators=(',',':')).encode()).hexdigest())

def shell_info(app, revision, version):
    """Only a fresh source-built GUI shell, never an installed or QA engine."""
    info=plistlib.loads(regular(app/'Contents/Info.plist').read_bytes())
    need(info.get('CFBundleIdentifier')==FORMAL_ID, 'original formal GUI identifier required')
    need(info.get('BBSourceCommit')==revision and info.get('CFBundleShortVersionString')==version, 'GUI shell source/version mismatch')
    need(not any(k in info for k in ('BBPreviewDataPath','BBPreviewPreferencesPath','BBQALaunchReceipt','BBRepoPath')), 'private GUI preference/source override refused')
    need(not os.path.lexists(app/'Contents/Resources/bbport'), 'a bare source-built GUI shell is required, not a QA/old engine')
    regular(app/'Contents/MacOS/bbport');regular(app/'Contents/Resources/AppIcon.icns')
    need((app/'Contents/Resources/BBLauncher_BBLauncher.bundle').is_dir(), 'original localized GUI resources missing')
    return info

def input_contract(*, repository, native_source, build_root, renderer_build,
                   cpu_build, net_library, layout_result, launcher_app,
                   output, version, revision):
    repo=directory(repository);source=directory(native_source);root=directory(build_root)
    renderer=directory(renderer_build);cpu=directory(cpu_build);shell=directory(launcher_app)
    need(all(p.is_relative_to(root) and p!=root for p in (renderer,cpu,shell)), 'normal build products must belong to explicit build root')
    need(re.fullmatch('[0-9a-f]{40}',revision or '') is not None and re.fullmatch(r'[0-9]+(?:\.[0-9]+){1,2}',version or '') is not None, 'exact source revision and numeric version required')
    target=Path(output).absolute();parent=directory(target.parent)
    need(target.parent==parent and target==target.resolve() and target.suffix=='.app' and not os.path.lexists(target), 'fresh canonical output app required')
    need(not any(target.is_relative_to(Path(p)) for p in ('/Applications','/System/Applications')), 'packaging never writes installed applications')
    for path in (repo,source,renderer,cpu,shell):
        need(not target.is_relative_to(path) and not path.is_relative_to(target), 'app output overlaps a source/build product')
    identity=native_source_identity(repo/'experiments/arm64-runtime',source)
    selected=renderer_inputs(source,renderer)
    cache=cache_values(cpu/'CMakeCache.txt')
    need(Path(cache.get('BB_ARM64_PROTOTYPE_DIR','')).resolve()==source, 'CPU build source mismatch')
    need(Path(cache.get('BB_ARM64_RENDERER_BUILD_DIR','')).resolve()==renderer and Path(cache.get('BB_ARM64_CPU_HOST_LIBRARY','')).resolve()==renderer/'libbbgpu.dylib', 'CPU and GPU must use one normal renderer build')
    net=regular(net_library);layout_receipt=regular(layout_result)
    need(net.is_relative_to(root) and layout_receipt.is_relative_to(root), 'network/layout products outside selected build root')
    net_cache=cache_values(net.parent/'CMakeCache.txt')
    need(net_cache.get('CMAKE_OSX_ARCHITECTURES')=='arm64' and Path(net_cache.get('CMAKE_HOME_DIRECTORY','')).resolve()==source/'vendor/bbnet', 'normal ARM64 bbnet source mismatch')
    layout=json.loads(layout_receipt.read_text())
    need(layout.get('status')=='collected-normal-target' and Path(layout['renderer_build']).resolve()==renderer and Path(layout['source_root']).resolve()==source/'vendor/metal_api/shadps4', 'same-build sealed layout result required')
    canonical_layout=load_canonical_layout(regular(layout['layout']),directory(layout['source_root']),layout['layout_sha256'])
    raw_cpu=regular(cpu/'Bin/bb-game-arm64')
    products=dict(cpu=raw_cpu,library=regular(renderer/'libbbgpu.dylib'),gpu=regular(renderer/'bb-gpu'),worker=regular(renderer/'game-pso-preparer'),module=regular(renderer/'game-module-preparer'),net=net)
    for role,path in products.items():
        need(run('/usr/bin/lipo','-archs',path).strip()==b'arm64', 'thin ARM64 normal product required: '+role)
    from warm_archive_manifest import resolve_gpu_identity
    for role in ('cpu','gpu','worker','module'):
        edge=resolve_gpu_identity(products[role],root)
        need(edge['library']==str(products['library']) and edge['library_sha256']==sha(products['library']), 'normal product uses another renderer: '+role)
    info=shell_info(shell,revision,version)
    run('/usr/bin/codesign','--verify','--deep','--strict',shell)
    return dict(repo=repo,source=source,root=root,renderer=renderer,cpu_build=cpu,shell=shell,
                target=target,products=products,layout=layout,info=info,native_source=identity,
                source_sha256={str(p):sha(p) for p in (*products.values(),layout_receipt)})

def copy_notices(repo,engine,relocators):
    """Actual source-tree and linked package notices; no QA app as a license source."""
    copy(repo/'LICENSE',engine/'LICENSE')
    names={'license','license.txt','license.md','copying','copying.txt','copyright','copyright.txt','ofl.txt'}
    copied=[]
    def collect(root,destination):
        for path in sorted(root.rglob('*')):
            if not path.is_file() or path.is_symlink() or path.name.lower() not in names: continue
            need(path.stat().st_size<=2*1024*1024, 'unexpected license file bound')
            target=destination/path.relative_to(root);copy(path,target);copied.append(str(target.relative_to(engine)))
    collect(repo/'experiments/arm64-runtime/vendor',engine/'licenses/project-components')
    packages={}
    for relocator in relocators:
        for item in relocator.inputs:
            path=Path(item['source'])
            for parent in path.parents:
                if (parent/'INSTALL_RECEIPT.json').is_file():
                    packages[str(parent)]=parent;break
    for root in packages.values(): collect(root,engine/'licenses/build-packages'/root.parent.name/root.name)
    attribution=repo/'launcher-macos/Sources/BBLauncher/Resources/Artwork/ATTRIBUTION.txt'
    copy(attribution,engine/'licenses/icons/HuntersMark-ATTRIBUTION.txt')
    copy(repo/'tools/macos/resources/ATTRIBUTION.txt',engine/'licenses/icons/GameCover-ATTRIBUTION.txt')
    public=dict(source_commit=None,source='https://github.com/WiredThings/blood-echoes',
                components=['bbport','shadPS4','AetherPS4/FEXCore Darwin','SDL3','Vulkan loader','FFmpeg','fmt','xxHash','miniz','Zydis','SPIRV-Cross','Protobuf','Abseil','miniupnpc','OpenSSL','CPython'],
                license_files=copied,game_or_compiled_game_assets_included=False)
    return public

def _assemble(*,repository,native_source,build_root,renderer_build,cpu_build,
             net_library,layout_result,launcher_app,python_provider,output,
             version,revision,search_dirs=()):
    from native_package_assets import Relocator, python_provider as select_python, embed_python
    c=input_contract(repository=repository,native_source=native_source,build_root=build_root,
        renderer_build=renderer_build,cpu_build=cpu_build,net_library=net_library,
        layout_result=layout_result,launcher_app=launcher_app,output=output,version=version,revision=revision)
    app=c['target'];repo=c['repo'];products=c['products'];work=app.parent/(app.stem+'-assembly')
    need(not work.exists(), 'fresh assembly report directory required');work.mkdir(mode=0o700)
    run('/usr/bin/ditto','--norsrc','--noextattr',c['shell'],app)
    engine=app/'Contents/Resources/bbport'
    code=write_source_closure(source=repo/'experiments/arm64-runtime',repository=repo,
        runtime_tools=repo/'experiments/arm64-runtime/tools',destination=engine,project=PRIVATE_PROJECT)
    info=c['info'];info['LSMinimumSystemVersion']='27.0';(app/'Contents/Info.plist').write_bytes(plistlib.dumps(info))
    selection=select_python(python_provider)
    search=list(dict.fromkeys(Path(p).resolve(strict=True) for p in [c['root'],selection['framework_root'],*search_dirs]))
    source_lib=products['library'];host=engine/'bin/gpu-metal/libbbgpu.dylib';gpu_lib=engine/'bin/gpu-metal-arm64/libbbgpu.dylib'
    cpu_copy=Relocator(engine,search_dirs=search,bindings={source_lib:host})
    gpu_copy=Relocator(engine,search_dirs=search,bindings={source_lib:gpu_lib})
    net_copy=Relocator(engine,search_dirs=search,library_subdir='deps-net/lib')
    py_copy=Relocator(engine,search_dirs=search,bindings={selection['library']:engine/'python/lib/Python'})
    cpu_copy.copy(source_lib,host);gpu_copy.copy(source_lib,gpu_lib)
    cpu=cpu_copy.copy(products['cpu'],engine/'bin/bb-probe-metal')
    gpu_main=gpu_copy.copy(products['gpu'],engine/'bin/gpu-metal-arm64/bb-gpu')
    net=net_copy.copy(products['net'],engine/'bin/gpu-net/libbbnet.dylib')
    python=embed_python(engine,python_provider,relocate=py_copy)
    for relocator in (cpu_copy,gpu_copy,net_copy,py_copy): relocator.sign_copied()
    need(sha(host)==sha(gpu_lib), 'host and GPU copies diverged after identical relocation')
    nested=create_game_app(app);nested_app=nested.parents[2]
    nested_info=plistlib.loads((nested_app/'Contents/Info.plist').read_bytes());nested_info['LSUIElement']=True
    (nested_app/'Contents/Info.plist').write_bytes(plistlib.dumps(nested_info));sign_game_app(nested_app)
    source_component=dict(schema=1,kind='blood-echoes-game-pso-component',production=False,enabled=True,
        worker=str(products['worker']),worker_sha256=sha(products['worker']),
        module_consumer=str(products['module']),module_consumer_sha256=sha(products['module']),
        library=str(source_lib),library_sha256=sha(source_lib),layout=c['layout']['layout'],
        layout_sha256=c['layout']['layout_sha256'],source_root=c['layout']['source_root'],
        coherent_renderer=True,diagnostic_only=False,automatic_capture_schema=1)
    component_input=work/'GAME-PSO-COMPONENT.source.json';write(component_input,source_component)
    originals={'game-pso-preparer':products['worker'],'game-module-preparer':products['module']}
    def rewrite_worker(copied,old_library,final_library):
        need(Path(old_library)==source_lib and Path(final_library)==gpu_lib, 'worker renderer binding changed')
        gpu_copy.copy(originals[copied.name],copied);gpu_copy.sign_copied()
    candidate=prepare_inputs(engine,component_input,gpu_lib,rewrite=rewrite_worker,admission_root=c['root'])
    # Relocator's last signing pass is complete before replacing its temporary
    # GPU-main file by the original wrapper's relative executable alias.
    gpu=create_native_gpu_bundle(engine,gpu_main,version=version)
    gpu_main.unlink();gpu_main.symlink_to('../Bloodborne Metal.app/Contents/MacOS/bb-gpu')
    local=code['manifest']
    for key in ('runtime_project','source_repository'): local.pop(key,None)
    local.update(host_local_only=False,portable_installer=True,production=True,code_closure_only=False,
                 native_components_included=True,final_component_verified=False)
    write(engine/'ARM64-LOCAL-PREPARATION.json',local)
    native=dict(schema=1,default_enabled=True,minimum_os_major=27,production=True,
        game_runtime='fex-arm64',local_metal_preparation='auto',source_revision=revision,
        renderer_architecture='native arm64 FEX runtime and independent arm64 Metal host',
        frozen_runtime={name:sha(engine/name) for name in ROLE_PATHS})
    write(engine/'NATIVE-METAL.json',native)
    with packaging_capabilities(engine,scratch_parent=work) as caps:
        component=finalize_inputs(engine,candidate,admission_root=c['root'],runtime_profile=caps,production=True)
        load_component(engine,runtime_profile=caps)
    local.update(final_component_verified=True,default_game_pso_preparation=True)
    write(engine/'ARM64-LOCAL-PREPARATION.json',local)
    # bundle() validates only true code identities, without constructing game data.
    need(bundle(engine) is not None, 'final formal code bundle was not admitted')
    (engine/'.packaged').touch();(engine/'.version').write_text(version+'\n')
    notices=copy_notices(repo,engine,(cpu_copy,gpu_copy,net_copy,py_copy));notices['source_commit']=revision
    write(engine/'SOURCE-MANIFEST.json',notices)
    (engine/'THIRD-PARTY-NOTICES.txt').write_text('Blood Echoes native ARM64 build.\nCorresponding project source: https://github.com/WiredThings/blood-echoes/tree/'+revision+'\nComponent licenses are preserved under licenses/. CPython license is in python/LICENSE.\nNo game, save, private cache or compiled game library is distributed.\n')
    # Signs are complete before freezing roles; only the outer resource seal follows.
    need(all(sha(engine/name)==value for name,value in native['frozen_runtime'].items()), 'runtime changed after capability validation')
    for path in (cpu,host,gpu,gpu_lib,net,nested,engine/'bin/game-pso-preparer',engine/'bin/game-module-preparer'):
        need(run('/usr/bin/lipo','-archs',path).strip()==b'arm64', 'non-ARM final runtime role')
        need(not development_probe(path), 'development game probe refused')
    payload=check_relative_payload(app)
    # Info.plist and resource changes invalidate the original GUI seal. Sign
    # the completed outer bundle before verifying its main executable; strict
    # verification of a bundle's main also validates its Info.plist binding.
    run('/usr/bin/codesign','--force','--sign','-',app)
    for path in app.rglob('*'):
        if not path.is_file() or path.is_symlink(): continue
        with path.open('rb') as stream: magic=stream.read(4)
        if magic==b'\xcf\xfa\xed\xfe':
            need(run('/usr/bin/lipo','-archs',path).strip()==b'arm64', 'non-ARM packaged dependency')
            for edge in loads(path):
                if edge.startswith(SYSTEM): continue
                need(edge.startswith('@loader_path/'), 'final dependency is not relative')
                target=(path.parent/edge[len('@loader_path/'):]).resolve(strict=True)
                need(target.is_relative_to(engine), 'final dependency escaped engine')
            run('/usr/bin/codesign','--verify','--strict',path)
    run('/usr/bin/codesign','--verify','--deep','--strict',app)
    need(all(sha(path)==value for path,value in c['source_sha256'].items()), 'normal source product changed while packaging')
    result=dict(schema=1,status='native-source-app-assembled',app=str(app),source_revision=revision,
        native_source_identity=c['native_source'],frozen_runtime=native['frozen_runtime'],
        actual_fixed_helper_seed_schema=component['fixed_helper_seed_schema'],payload_files=len(payload),
        QA_inputs_used=False,game_inputs_used=False,user_data_used=False,application_started=False,
        formal_application_replaced=False,release_updated=False,python=python)
    write(work/'result.json',result)
    return result

def assemble(**arguments):
    # Private staging must stay admissible even in group-writable build shells.
    previous=os.umask(0o022)
    try: return _assemble(**arguments)
    finally: os.umask(previous)

def build_manifest_arguments(manifest, *, output, search_dirs=()):
    """Consume the normal builder's actual receipt, never an old app/QA stage.

    The receipt selects inputs, not trust: source identity and all recorded
    output bytes are rechecked here, then normal assembly checks CMake origins,
    real load edges, layout, architectures and the original bare GUI again.
    """
    from build_native_source import source_snapshot, identify_source, directory as source_directory, python_framework_provider
    receipt=regular(manifest)
    need(receipt.stat().st_size<=16*1024*1024, 'normal build receipt exceeds size bound')
    def unique_pairs(rows):
        result={}
        for key,value in rows:
            need(key not in result, 'duplicate build receipt field')
            result[key]=value
        return result
    m=json.loads(receipt.read_text(),object_pairs_hook=unique_pairs)
    required={'schema','status','repository','source_root','build_root','revision','version',
              'targets_built','installed','game_started','source_identity','artifacts',
              'renderer','cpu','net','library','layout_result','layout_result_sha256',
              'gui_app','gui_sha256','python_provider','dependency_prefixes','tools'}
    need(isinstance(m,dict) and required<=m.keys(), 'incomplete normal build receipt')
    need(m['schema']==1 and m['status']=='source-built-not-packaged' and m['targets_built'] is True
         and m['installed'] is False and m['game_started'] is False, 'successful uninstalled normal build required')
    root=directory(m['build_root']);repo=directory(m['repository'])
    need(receipt==root/'build-manifest.json', 'normal build receipt location mismatch')
    need(Path(m['source_root'])==repo/'experiments/arm64-runtime', 'receipt source-root mismatch')
    identity=m['source_identity']
    need(isinstance(identity,dict) and identity.get('revision')==m['revision'], 'receipt source revision mismatch')
    if identity.get('mode')=='source-export':
        need(not os.path.lexists(repo/'.git'), 'source-export identity became a Git checkout')
        snapshot=source_snapshot(repo)
        encoded=json.dumps(snapshot,sort_keys=True,separators=(',',':')).encode()
        need(snapshot==identity.get('source_files') and hashlib.sha256(encoded).hexdigest()==identity.get('snapshot_sha256'),
             'source changed since normal build')
    elif identity.get('mode')=='git':
        def git(*args):return run('/usr/bin/git','-C',repo,*args).decode().strip()
        actual=identify_source(repo,m['revision'],git_top=lambda:git('rev-parse','--show-toplevel'),git=git)
        need(actual==identity, 'Git source changed since normal build')
    else:raise ValueError('unknown normal build source identity')
    artifacts=m['artifacts']
    roles={'library':root/'renderer/libbbgpu.dylib','gpu':root/'renderer/bb-gpu',
           'worker':root/'renderer/game-pso-preparer','module':root/'renderer/game-module-preparer',
           'metadata':root/'renderer/metadata-layout','cpu':root/'cpu/Bin/bb-game-arm64',
           'net':root/'net/libbbnet.dylib'}
    need(isinstance(artifacts,dict) and set(artifacts)==set(roles), 'normal build artifact roles mismatch')
    for role,expected in roles.items():
        row=artifacts[role]
        need(isinstance(row,dict) and set(row)=={'path','sha256'}, 'invalid normal artifact receipt')
        need(Path(row['path'])==expected and sha(regular(expected))==row['sha256'], 'normal build artifact changed: '+role)
    need(Path(m['renderer'])==root/'renderer' and Path(m['cpu'])==roles['cpu']
         and Path(m['net'])==roles['net'] and Path(m['library'])==roles['library'], 'normal build artifact aliases mismatch')
    gui=directory(m['gui_app']);layout=regular(m['layout_result'])
    need(gui==root/'gui/Blood Echoes.app' and sha(regular(gui/'Contents/MacOS/bbport'))==m['gui_sha256'], 'normal GUI product changed')
    need(layout==root/'evidence/layout/result.json' and sha(layout)==m['layout_result_sha256'], 'normal layout receipt changed')
    prefixes=[source_directory(p) for p in m['dependency_prefixes']]
    need(prefixes and len(prefixes)==len(set(prefixes)), 'normal dependency providers missing or duplicated')
    provider,interpreter=python_framework_provider(m['python_provider'])
    need(isinstance(m['tools'],dict) and m['tools'].get('python')==interpreter,
         'normal Python interpreter differs from build receipt')
    selected=[*prefixes,*(p/'lib' for p in prefixes if (p/'lib').is_dir()),*search_dirs]
    return dict(repository=repo,native_source=Path(m['source_root']),build_root=root,
        renderer_build=root/'renderer',cpu_build=root/'cpu',net_library=roles['net'],
        layout_result=layout,launcher_app=gui,python_provider=provider,
        output=output,version=m['version'],revision=m['revision'],search_dirs=list(dict.fromkeys(selected)))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-manifest',type=Path,help='Consume build_native_source.py output; original package.sh uses this native route')
    fields=('repository','native-source','build-root','renderer-build','cpu-build','net-library','layout-result','launcher-app','python-provider')
    for name in fields:parser.add_argument('--'+name,type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--version');parser.add_argument('--revision')
    parser.add_argument('--search-dir',type=Path,action='append',default=[])
    args=vars(parser.parse_args());args['search_dirs']=args.pop('search_dir');manifest=args.pop('build_manifest')
    if sys.version_info<(3,11):
        parser.error('Packaging requires Python 3.11 or newer. Set BB_PACKAGE_PYTHON to the selected build Python executable when using package.sh.')
    if manifest is not None:
        if any(args[name.replace('-','_')] is not None for name in (*fields,'version','revision')):
            parser.error('--build-manifest cannot be mixed with explicit build/source identities')
        args=build_manifest_arguments(manifest,output=args['output'],search_dirs=args['search_dirs'])
    else:
        missing=[name for name in (*fields,'version','revision') if args[name.replace('-','_')] is None]
        if missing:parser.error('use --build-manifest or supply: '+', '.join('--'+name for name in missing))
    result=assemble(**args)
    if manifest is not None:
        # The source receipt must remain true for the entire assembly, including
        # copied scripts/resources, not only the already-built native objects.
        rechecked=build_manifest_arguments(manifest,output=args['output'],search_dirs=())
        need(all(rechecked[key]==args[key] for key in rechecked if key!='search_dirs'),
             'normal build receipt inputs changed during packaging')
    print(json.dumps(result,indent=2,default=str))

if __name__=='__main__': main()
