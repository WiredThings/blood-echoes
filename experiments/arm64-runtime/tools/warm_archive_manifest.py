"""Once-per-explicit-startup PSO archive cohort; never called per draw."""
from pathlib import Path
import hashlib,json
from contextvars import ContextVar
from contextlib import contextmanager

_native_scope=ContextVar('admitted_native_code_scope',default=None)

@contextmanager
def native_validation_scope(profile):
    from launcher_runtime_profile import RuntimeProfile
    if not isinstance(profile,RuntimeProfile):raise ValueError('actual runtime code profile required')
    token=_native_scope.set(profile)
    try:yield
    finally:_native_scope.reset(token)

def file_identity(path):
    path=Path(path).resolve(strict=True)
    if not path.is_file(): raise ValueError("identity input must be a regular file")
    with path.open('rb') as stream: digest=hashlib.file_digest(stream,'sha256').hexdigest()
    return dict(sha256=digest,bytes=path.stat().st_size)

def make_manifest(*,translator_artifacts,game_artifacts,options,profile,identity_reader=file_identity):
    # Inputs are actual loaded/build artifacts and actual applied game patch inputs,
    # not a version label, program hash, cache inventory count or directory name.
    if not translator_artifacts or not game_artifacts or not profile:
        raise ValueError("complete translator, game/patch, and profile inputs required")
    manifest=dict(schema=1,translator={k:identity_reader(v)for k,v in sorted(translator_artifacts.items())},
                  game={k:identity_reader(v)for k,v in sorted(game_artifacts.items())},
                  options=dict(sorted(options.items())),profile=profile)
    payload=json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()
    return hashlib.sha256(payload).hexdigest(),manifest


def resolve_gpu_identity(target,backend_root,*,code_owners=None):
    scope=_native_scope.get()
    if scope is not None and Path(backend_root).resolve(strict=True)==scope.bundle.stage:
        return scope.native_identity(target)
    return resolve_gpu_identity_full(target,backend_root,code_owners=code_owners)


def resolve_gpu_identity_full(target,backend_root,*,code_owners=None):
    # Resolve the selected executable's actual single GPU load edge, then reuse
    # the existing ownership/architecture/signature/SHA verification contract.
    import subprocess
    from gpu_candidate_identity import verify,sha,resolve_gpu_edge,output
    target=Path(target).resolve(strict=True)
    deps=[line.strip().split(' (compatibility')[0]for line in output(['/usr/bin/otool','-L',str(target)]).splitlines()[1:]]
    edges=[edge for edge in deps if Path(edge).name.startswith('libbbgpu')]
    if len(edges)!=1:raise ValueError('selected executable must have exactly one GPU load edge')
    edge=edges[0]
    library=resolve_gpu_edge(target,edge)
    return verify(dict(target=str(target),library=str(library),library_sha256=sha(library)),backend_root,**({'code_owners':code_owners}if code_owners is not None else{}))


def prepare_startup_archive_policy(*,enabled,engine,selected_executable,backend_root,
                                   game_artifacts,options,profile,
                                   resolver=resolve_gpu_identity,manifest_builder=make_manifest):
    # These are execution branches used directly by run_game. OFF/non-graphics and
    # legacy Rosetta return before resolving a native ARM executable or hashing files.
    if not enabled:
        return {},dict(mode='inactive',namespaced=False,manifest_created=False)
    if engine=='rosetta':
        return {},dict(mode='legacy-rosetta',namespaced=False,manifest_created=False,
                       reason='preserves existing Rosetta archive experiment; not an ARM v3 cohort')
    if engine!='arm64':raise ValueError('unsupported archive producer engine')
    selected=resolver(Path(selected_executable),backend_root)
    token,manifest=manifest_builder(translator_artifacts={'gpu_library':Path(selected['library'])},
                                   game_artifacts=game_artifacts,options=options,profile=profile)
    identity=dict(mode='arm64-v3',namespaced=True,manifest_created=True,
                  token=token,manifest=manifest,selected_gpu=selected)
    # Actual per-source/full-state keys still own products. Resource selection
    # is retained as provenance; it is not a reason to replace the compiler's
    # whole namespace. Old namespaces remain on disk. SDK-qualified identical
    # library products can be adopted through the original builder's full proof.
    if isinstance(manifest,dict)and manifest.get('schema')==1 and set(manifest)=={'schema','translator','game','options','profile'}:
        from compilation_domains import split_manifest
        identity['domains']=split_manifest(manifest)
        identity['legacy_selection_token']=token
        identity['token']=identity['domains']['compilation_token']
        identity['compilation_identity_active']=True
    return {'BB_MTL_ARCHIVE_COMPATIBILITY_ID':identity['token']},identity
