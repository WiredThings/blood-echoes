"""Read the original Swift QA launch receipt without rebuilding frontend policy.

No subprocesses, save/cache copies, state writes or native loader calls. Paths
remain admitted to the fixed Shared development namespace, never formal data.
"""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import stat
from types import MappingProxyType
import uuid

PRIVATE_PROJECT = Path('/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype')
PRODUCER = 'original-swift-LauncherModel.start-final-environment'
PREFIX = 'io.github.bbport.optimized.options-qa.arm64.gui.'
FORBIDDEN = ('TOKEN', 'SECRET', 'PASSWORD', 'CREDENTIAL', 'AUTH', 'BEARER', 'PRIVATE_KEY', 'PAIRING_CODE')
HEX = re.compile(r'[0-9a-f]{64}')
SCRIPTS = frozenset(('run.sh', 'tools/macos/run_optimized.sh', 'tools/macos/supervise_restart.sh',
    'tools/macos/quicksave.py', 'patches/Bloodborne.xml', 'scripts/link_libc.py',
    'scripts/chinese_translation.py', 'scripts/online.py', 'scripts/content_profile.py',
    'scripts/link_modules.py', 'scripts/patches.py', 'scripts/game_check.py',
    'scripts/mods.py', 'scripts/prepare.py', 'scripts/ultrawide_ui.py', 'tools/macos/run.sh'))
IDENTITIES = frozenset(('launcher', 'run.sh', 'tools/macos/run_optimized.sh', 'SOURCE-PARITY.json',
    'ARM64-QA-MANIFEST.json', 'BB_CONFIG', 'BB_MODS_CONFIG', 'BB_PATCHES_CONFIG'))
CONFIG_NAMES = dict(BB_CONFIG='bbport.ini', BB_MODS_CONFIG='mods.json', BB_PATCHES_CONFIG='patches.json')
PROTECTION_KEYS = ('BB_ARM_PAYLOAD_GATE', 'BB_ARM_RAW_PREWRITE', 'BB_LAYER_READ_TRAPS', 'BB_GPU_LIFETIME')
RUNTIME_FILES = frozenset(('bin/bb-probe-metal', 'bin/gpu-metal/libbbgpu.dylib',
    'bin/Bloodborne Metal.app/Contents/MacOS/bb-gpu', 'bin/gpu-metal-arm64/libbbgpu.dylib',
    'bin/gpu-net/libbbnet.dylib', 'bin/game-pso-preparer', 'bin/game-module-preparer'))


def need(ok, reason):
    if not ok:
        raise ValueError('QA launch conditions: ' + reason)


def canonical(value, *, directory=False, within=None):
    need(isinstance(value, (str, Path)), 'path type')
    path = Path(value)
    need(path.is_absolute() and str(path) == str(value) and path == path.resolve(strict=True), 'canonical path required')
    if within is not None:
        need(path.is_relative_to(within), 'path outside admitted root')
    info = path.lstat()
    need(info.st_uid == os.getuid() and (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)), 'owned plain input required')
    if not directory:
        need(info.st_nlink == 1, 'hard-linked input refused')
    return path


def stamp(info):
    return (info.st_dev, info.st_ino, info.st_uid, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class Reader:
    def __init__(self):
        self.inputs = {}

    def read(self, path, *, limit=1024 * 1024, expected=None, retain=True):
        path = canonical(path)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            before = os.fstat(fd)
            need(stamp(before) == stamp(path.lstat()) and 0 <= before.st_size <= limit, 'bounded stable input required')
            h = hashlib.sha256()
            chunks, total = [], 0
            while True:
                raw = os.read(fd, 64 * 1024)
                if not raw:
                    break
                total += len(raw)
                need(total <= before.st_size, 'input grew during read')
                h.update(raw)
                if retain:
                    chunks.append(raw)
            need(total == before.st_size and stamp(before) == stamp(os.fstat(fd)) == stamp(path.lstat()), 'input changed during read')
        finally:
            os.close(fd)
        identity = h.hexdigest()
        if expected is not None:
            need(isinstance(expected, str) and HEX.fullmatch(expected) and identity == expected, 'identity SHA mismatch')
        need(str(path) not in self.inputs or self.inputs[str(path)] == identity, 'inconsistent repeated input')
        self.inputs[str(path)] = identity
        return b''.join(chunks) if retain else identity

    def json(self, path, **kw):
        def pairs(items):
            row = {}
            for key, value in items:
                need(key not in row, 'duplicate JSON field')
                row[key] = value
            return row
        raw = self.read(path, **kw)
        value = json.loads(raw, object_pairs_hook=pairs)
        need(isinstance(value, dict), 'JSON object required')
        return value

    def unchanged(self):
        for path, identity in tuple(self.inputs.items()):
            self.read(path, limit=64 * 1024 * 1024, expected=identity, retain=False)


@dataclass(frozen=True)
class ConfigSnapshot:
    path: Path
    data: bytes
    sha256: str


def _source_qa_proof(reader,stage,project,manifest,parity,original,effective,identities):
    """Current normal source payload + independent CPU, not a fake old QA stage."""
    source_app=canonical(manifest['source_app'],directory=True,within=project/'evidence')
    source=canonical(source_app/'Contents/Resources/bbport',directory=True,within=source_app)
    source_info=plistlib.loads(reader.read(source_app/'Contents/Info.plist'))
    need(source_info.get('CFBundleIdentifier')=='io.github.bbport.optimized','original source-built bundle identity required')
    native=reader.json(source/'NATIVE-METAL.json');source_parity=reader.json(source/'SOURCE-PARITY.json')
    need(native.get('schema')==1 and native.get('production')is True and native.get('game_runtime')=='fex-arm64','original normal source runtime required')
    base=native.get('frozen_runtime');base_roles=RUNTIME_FILES|{'bin/Bloodborne Game.app/Contents/MacOS/bb-probe-metal'}
    need(isinstance(base,dict)and set(base)==base_roles and base==manifest.get('original_current_runtime'),'exact current source runtime roles required')
    fex_roles={}
    if(stage/'FEX-COMPONENT.json').is_file():
        from fex_launch import component as fex_component
        fex,_=fex_component(stage);reader.json(stage/'FEX-COMPONENT.json')
        fex_roles={row['path']:row['sha256']for row in(fex['cpu'],fex['scanner'],*fex.get('cpu_aliases',[]))};fex_roles.update(fex.get('runtime_dependencies',{}))
    for name,digest in base.items():
        reader.read(canonical(source/name,within=source),expected=digest,retain=False,limit=64*1024*1024)
        reader.read(canonical(stage/name,within=stage),expected=fex_roles.get(name,digest),retain=False,limit=64*1024*1024)
    need(source_parity.get('original_sources')==original,'source-built original script identities differ')
    from prepare_local_aot_stage import projected_launcher_scripts
    source_run=reader.read(source/'run.sh').decode();source_log=reader.read(source/'tools/macos/run_optimized.sh').decode()
    old_run,new_run,old_log,new_log,_=projected_launcher_scripts(source_run,source_log)
    bodies={name:reader.read(source/name)for name in original}
    bodies.update({'run.sh':old_run.encode(),'tools/macos/run_optimized.sh':old_log.encode()})
    need(all(hashlib.sha256(raw).hexdigest()==original[name]for name,raw in bodies.items()),'source script original projection differs')
    need(reader.read(stage/'run.sh')==new_run.encode()and reader.read(stage/'tools/macos/run_optimized.sh')==new_log.encode(),'source QA launcher projection changed')
    for name,digest in effective.items():reader.read(stage/name,expected=digest,retain=False)
    need(all(identities[name]==effective[name]for name in('run.sh','tools/macos/run_optimized.sh')),'source receipt/effective script identities differ')
    need(parity.get('image_preparation_reuse')is True and parity.get('previous_stage_read')is False and parity.get('source_repository_modified')is False,'source-closure projection proof required')
    from bbcpu_launch import component
    cpu,paths=component(stage)
    reader.json(stage/'BBCPU-COMPONENT.json')
    expected=dict(base);expected.update({row['path']:row['sha256']for key,row in cpu.items()if key in('cpu','preparer','scanner','generator','abi')});expected.update(cpu.get('runtime_dependencies',{}));expected.update(fex_roles)
    selected=reader.json(stage/'NATIVE-METAL.json')
    need(selected.get('frozen_runtime')==manifest.get('frozen_runtime')==expected,'independent CPU/native package roles differ')
    for name,digest in expected.items():reader.read(canonical(stage/name,within=stage),expected=digest,retain=False,limit=64*1024*1024)
    return frozenset(expected)

def load(path, *, stage, project):
    """Return verified original env and immutable source config bytes.

    This is historical final-launch intent, not proof that a game was started.
    Runtime Mach-O/load-edge admission remains the benchmark's existing job.
    """
    project = canonical(project, directory=True)
    need(project == PRIVATE_PROJECT, 'fixed Shared project required')
    stage = canonical(stage, directory=True, within=project / 'apps')
    need(stage.parts[-3:] == ('Contents', 'Resources', 'bbport'), 'packaged component stage required')
    app = canonical(stage.parents[2], directory=True)
    need(app.parent == project / 'apps', 'direct private QA app required')
    reader = Reader()
    info_path = canonical(app / 'Contents/Info.plist')
    info = plistlib.loads(reader.read(info_path))
    identifier = info.get('CFBundleIdentifier')
    need(isinstance(identifier, str) and identifier.startswith(PREFIX), 'private QA bundle required')
    tag = identifier[len(PREFIX):]
    need(bool(re.fullmatch('[0-9]+', tag)) and app.name == 'Blood Echoes ARM64 GUI QA ' + tag + '.app', 'numeric QA app identity required')
    root = canonical(project / 'state' / ('gui-root-' + tag), directory=True)
    data = canonical(root / 'optimized-launcher', directory=True)
    out = canonical(data / 'out', directory=True)
    path = canonical(path, within=out)
    need(path.parent == out and path.suffix == '.json' and not path.name.startswith('.'), 'receipt directly in this QA data out required')
    need(info.get('BBPreviewDataPath') == str(root) and info.get('BBQALaunchReceipt') == str(path), 'explicit same-QA receipt opt-in required')
    need(info.get('CFBundleExecutable') == 'bbport', 'original launcher executable required')
    value = reader.json(path, limit=256 * 1024)
    need(type(value.get('schema')) is int and value['schema'] == 1 and value.get('producer') == PRODUCER,
         'original Swift receipt schema/producer required')
    need(value.get('launch_boundary') == 'before-Process.run' and value.get('game_started_verified') is False and
         value.get('original_environment_unchanged') is True and value.get('offline') is True and
         value.get('formal_data_modified') is False, 'original offline launch boundary required')
    need(value.get('bundle_id') == identifier and value.get('bundle_path') == str(app) and
         value.get('working_directory') == str(stage) and value.get('executable') == '/bin/bash' and
         value.get('arguments') == ['tools/macos/run_optimized.sh'], 'original bundle/shell/arguments mismatch')
    need(type(value.get('source_pid')) is int and value['source_pid'] > 0 and
         type(value.get('created_unix_ns')) is int and value['created_unix_ns'] >= int(tag), 'launch PID/time identity required')
    need(isinstance(value.get('launch_id'), str) and str(uuid.UUID(value['launch_id'])).upper() == value['launch_id'], 'canonical launch UUID required')
    identities = value.get('identity_sha256')
    need(isinstance(identities, dict) and set(identities) == IDENTITIES and
         all(isinstance(v, str) and HEX.fullmatch(v) for v in identities.values()), 'complete receipt identity domain required')
    env = value.get('environment')
    need(isinstance(env, dict) and env, 'original environment dictionary required')
    for key, item in env.items():
        need(isinstance(key, str) and re.fullmatch(r'(BB|MTL)_[A-Z0-9_]+', key) and
             not any(word in key for word in FORBIDDEN) and isinstance(item, str) and '\0' not in item,
             'invalid or credential-like environment entry')
    need(env.get('BB_ONLINE') == '0' and env.get('BB_DATA_DIR') == str(data) and
         env.get('BB_RENDERER') == 'metal' and env.get('BB_ARM_LAUNCHER_COMPAT') == '1' and
         env.get('BB_METAL_ARM64') == '1', 'original offline native Metal selection required')
    need(all(env.get(key) == '1' for key in PROTECTION_KEYS), 'current four resource protections required')
    for key in ('BB_ARM_SESSION_NONCE', 'BB_ARM_RESTART_NONCE'):
        need(isinstance(env.get(key), str) and re.fullmatch('[0-9a-f]{32}', env[key]), 'original private session nonce required')
    executable = canonical(app / 'Contents/MacOS/bbport')
    reader.read(executable, limit=64 * 1024 * 1024, expected=identities['launcher'], retain=False)
    manifest = reader.json(stage.parent / 'ARM64-QA-MANIFEST.json', expected=identities['ARM64-QA-MANIFEST.json'])
    need(type(manifest.get('schema')) is int and manifest['schema'] == 2 and manifest.get('host_local_only') is True and
         manifest.get('formal_data_modified') is False and manifest.get('protected_resource_writeback') is True,
         'current host-local protected component manifest required')
    case = manifest['private_case']
    need(isinstance(case, dict) and case.get('bundle_id') == identifier and case.get('app') == str(app) and
         manifest.get('app') == str(app) and case.get('root') == str(root) and case.get('data') == str(data) and
         case.get('online') is False and case.get('formal_data_modified') is False, 'component QA case mismatch')
    evidence = canonical(project / 'evidence' / ('launcher-gui-run-' + tag), directory=True)
    need(case.get('evidence') == str(evidence), 'same-QA guard evidence required')
    guard = reader.json(evidence / 'guard-ready.json')
    need(type(guard.get('pid')) is int and guard['pid'] == value['source_pid'] and guard.get('path') == str(executable), 'source PID/app does not match its launch guard')
    user = canonical(env.get('BB_USER_DIR'), directory=True, within=project / 'state')
    cache = canonical(env.get('BB_GPU_USER_DIR'), directory=True, within=project / 'state')
    need(user == project / 'state' / ('gui-user-' + tag) and case.get('user') == str(user), 'source QA user mismatch')
    need(cache.parent == project / 'state' and re.fullmatch(r'gui-gpu-local-aot-[0-9]+', cache.name) and
         manifest.get('private_gpu_cache') == str(cache), 'source QA cache mismatch')
    assets = canonical(project.parent / 'assets', directory=True)
    game = canonical(env.get('BB_GAME_DIR'), directory=True, within=assets)
    need(game != assets, 'specific private asset game root required')
    if 'BB_CHINESE_TRANSLATION_DIR' in env:
        translation = canonical(env['BB_CHINESE_TRANSLATION_DIR'], directory=True, within=assets)
        need(translation != assets, 'specific private translation asset required')
    optional_directories = {}
    for key, name in (('BB_MODS_DIR', 'mods'), ('BB_PATCHES_DIR', 'patches')):
        target = data / name
        need(env.get(key) == str(target), 'original QA mod/patch directory mismatch')
        # An unused original UI mods/patch directory can legitimately not exist.
        # Never create it or treat it as an existing admitted source directory.
        exists = os.path.lexists(target)
        if exists:
            canonical(target, directory=True, within=data)
        optional_directories[key] = (target, exists)
    configs = {}
    for key, name in CONFIG_NAMES.items():
        config = canonical(env.get(key), within=data)
        need(config == data / name, 'original QA config path mismatch')
        raw = reader.read(config, expected=identities[key])
        configs[key] = ConfigSnapshot(config, raw, identities[key])
    parity = reader.json(stage / 'SOURCE-PARITY.json', expected=identities['SOURCE-PARITY.json'])
    original, effective = parity.get('original_sources'), parity.get('effective_sources')
    need(isinstance(original, dict) and isinstance(effective, dict) and set(original) == set(effective) == SCRIPTS and
         original == manifest.get('original_scripts') and effective == manifest.get('effective_scripts'), 'source/effective script provenance mismatch')
    need(all(isinstance(v, str) and HEX.fullmatch(v) for mapping in (original, effective) for v in mapping.values()), 'script identity format')
    if manifest.get('kind')!='source-built-independent-cpu-qa':
        need({k for k in original if original[k] != effective[k]} == {'run.sh', 'tools/macos/run_optimized.sh'} and
             parity.get('final_exec_hook_only') is True and parity.get('runtime_log_only_correction') is True and
             parity.get('previous_stage_modified') is False, 'controlled two-script delta required')
    source_built=manifest.get('kind')=='source-built-independent-cpu-qa'
    runtime_roles=RUNTIME_FILES
    if source_built:
        runtime_roles=_source_qa_proof(reader,stage,project,manifest,parity,original,effective,identities)
    else:
        baseline = canonical(project / 'launcher-component-stage', directory=True)
        need(parity.get('previous_stage') == str(baseline), 'original source stage mismatch')
        baseline_parity = reader.json(baseline / 'SOURCE-PARITY.json')
        need(baseline_parity.get('original_sources') == original, 'original baseline identity mismatch')
        original_bodies = {name: reader.read(baseline / name, expected=identity) for name, identity in original.items()}
        for name, identity in effective.items():
            reader.read(stage / name, expected=identity, retain=False)
        need(all(identities[name] == effective[name] for name in ('run.sh', 'tools/macos/run_optimized.sh')), 'receipt/effective script mismatch')
        from prepare_local_aot_stage import HOOK, runtime_log_revision
        new_run = reader.read(stage / 'run.sh')
        need(new_run.count(HOOK.encode()) == 1 and new_run.replace(HOOK.encode(), b'', 1) == original_bodies['run.sh'], 'run.sh changed beyond original final-exec hook')
        log_name = 'tools/macos/run_optimized.sh'
        need(reader.read(stage / log_name).decode() == runtime_log_revision(original_bodies[log_name].decode()), 'run_optimized changed beyond original FEX log correction')
    # Identity of actual binaries/load edges remains the existing native verifier;
    # expose only the exact packaged paths/declared hashes for that next boundary.
    runtime = manifest.get('frozen_runtime')
    need(isinstance(runtime, dict) and set(runtime) == runtime_roles and all(isinstance(k, str) and not Path(k).is_absolute() and
         '..' not in Path(k).parts and str(Path(k)) == k and isinstance(v, str) and HEX.fullmatch(v)
         for k, v in runtime.items()), 'frozen runtime path/identity domain')
    need(case.get('cpu_sha256') == runtime.get('bin/bb-probe-metal'), 'final CPU manifest identity mismatch')
    runtime_paths = {name: canonical(stage / name, within=stage) for name in runtime}
    reader.unchanged()
    return dict(environment=MappingProxyType(dict(env)), configs=MappingProxyType(configs),
        identity=MappingProxyType(dict(identities)), original_scripts=MappingProxyType(dict(original)),
        effective_scripts=MappingProxyType(dict(effective)), runtime=MappingProxyType(runtime_paths),
        runtime_sha256=MappingProxyType(dict(runtime)), input_sha256=MappingProxyType(dict(reader.inputs)),
        optional_directories=MappingProxyType(optional_directories),
        receipt=path, stage=stage, app=app, project=project, data_root=data, user_root=user,
        cache_root=cache, game_root=game, launch_id=value['launch_id'], source_pid=value['source_pid'],
        created_unix_ns=value['created_unix_ns'], arguments=tuple(value['arguments']), executable='/bin/bash',
        game_started_verified=False, producer=PRODUCER)
