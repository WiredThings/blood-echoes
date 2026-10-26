"""Once-per-explicit-startup PSO archive cohort; never called per draw."""
from pathlib import Path
import hashlib,json

def file_identity(path):
    path=Path(path).resolve(strict=True)
    if not path.is_file(): raise ValueError("identity input must be a regular file")
    with path.open('rb') as stream: digest=hashlib.file_digest(stream,'sha256').hexdigest()
    return dict(sha256=digest,bytes=path.stat().st_size)

def make_manifest(*,translator_artifacts,game_artifacts,options,profile):
    # Inputs are actual loaded/build artifacts and actual applied game patch inputs,
    # not a version label, program hash, cache inventory count or directory name.
    if not translator_artifacts or not game_artifacts or not profile:
        raise ValueError("complete translator, game/patch, and profile inputs required")
    manifest=dict(schema=1,translator={k:file_identity(v)for k,v in sorted(translator_artifacts.items())},
                  game={k:file_identity(v)for k,v in sorted(game_artifacts.items())},
                  options=dict(sorted(options.items())),profile=profile)
    payload=json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()
    return hashlib.sha256(payload).hexdigest(),manifest


def resolve_gpu_identity(target,backend_root):
    # Resolve the selected executable's actual single GPU load edge, then reuse
    # the existing ownership/architecture/signature/SHA verification contract.
    import subprocess
    from gpu_candidate_identity import verify,sha,resolve_gpu_edge
    target=Path(target).resolve(strict=True)
    deps=[line.strip().split(' (compatibility')[0]for line in subprocess.check_output(['/usr/bin/otool','-L',str(target)],text=True).splitlines()[1:]]
    edges=[edge for edge in deps if Path(edge).name.startswith('libbbgpu')]
    if len(edges)!=1:raise ValueError('selected executable must have exactly one GPU load edge')
    edge=edges[0]
    library=resolve_gpu_edge(target,edge)
    return verify(dict(target=str(target),library=str(library),library_sha256=sha(library)),backend_root)


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
    return {'BB_MTL_ARCHIVE_COMPATIBILITY_ID':token},dict(mode='arm64-v3',namespaced=True,
        manifest_created=True,token=token,manifest=manifest,selected_gpu=selected)
