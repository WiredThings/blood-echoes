"""Non-authorizing data prototype for one launcher final-exec role scope.

Pure validation never stats/opens formal paths. load_shared reads only a bounded
descriptor in the fixed development tree; observed facts are a separate caller
input, not claims read back from that same descriptor. No runtime uses this yet.
"""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
from types import MappingProxyType
import uuid

PRIVATE_PROJECT = Path('/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype')
HEX = re.compile(r'[0-9a-f]{64}')
ROLES = frozenset(('code', 'data', 'out', 'user', 'gpu', 'gpu_namespace', 'game', 'mod', 'work'))
COMPONENTS = frozenset(('cpu', 'cpu_host', 'gpu', 'gpu_library', 'pso_worker', 'module_worker', 'metadata_helper'))
OPTIONAL_COMPONENTS = frozenset(('python', 'net_module'))
COMPONENT_PATHS = dict(cpu=('bin/bb-probe-metal', 'bin/Bloodborne Game.app/Contents/MacOS/bb-probe-metal'),
    cpu_host=('bin/gpu-metal/libbbgpu.dylib',), gpu=('bin/Bloodborne Metal.app/Contents/MacOS/bb-gpu',),
    gpu_library=('bin/gpu-metal-arm64/libbbgpu.dylib',), pso_worker=('bin/game-pso-preparer',),
    module_worker=('bin/game-module-preparer',), metadata_helper=('native-runtime-tools/prepare_module_metadata.py',),
    python=('python/bin/python3',), net_module=('bin/gpu-net/libbbnet.dylib',))
POLICY = {
    'cpu': {'code': 'r', 'data': 'r', 'out': 'rw', 'user': 'rw', 'work': 'rw', 'game': 'r', 'mod': 'r'},
    'gpu': {'code': 'r', 'out': 'r', 'gpu_namespace': 'rw', 'work': 'rw', 'game': 'r', 'mod': 'r'},
    'pso_worker': {'code': 'r', 'out': 'r', 'gpu_namespace': 'rw', 'work': 'rw'},
    'module_worker': {'code': 'r', 'out': 'r', 'game': 'r', 'mod': 'r', 'work': 'rw'},
    'metadata_helper': {'code': 'r', 'out': 'r', 'game': 'r', 'mod': 'r', 'work': 'rw'},
}


def need(ok, message):
    if not ok:
        raise ValueError('runtime scope prototype: ' + message)


def path(value):
    need(type(value) is str and 0 < len(value.encode()) <= 4096 and
         not any(ord(c) < 32 or ord(c) == 127 for c in value), 'bounded path string required')
    result = PurePosixPath(value)
    need(result.is_absolute() and str(result) == value and '..' not in result.parts and
         not value.startswith('//'), 'lexically canonical absolute path required')
    return result


def digest_fields(domain, values):
    raw = bytearray(domain.encode() + b'\0')
    for value in values:
        encoded = value.encode()
        raw.extend(struct.pack('<I', len(encoded))); raw.extend(encoded)
    return hashlib.sha256(raw).hexdigest()


def argv_sha256(arguments):
    need(type(arguments) is list and 1 <= len(arguments) <= 32 and
         all(type(v) is str and 0 < len(v.encode()) <= 4096 and '\0' not in v for v in arguments), 'bounded final argv required')
    return digest_fields('BB-FINAL-ARGV1', arguments)


def compatibility_id(*, source_sha256, driver_sha256, profile_sha256, device_id):
    need(type(source_sha256) is str and HEX.fullmatch(source_sha256) and type(driver_sha256) is str and
         HEX.fullmatch(driver_sha256) and type(profile_sha256) is str and HEX.fullmatch(profile_sha256) and
         type(device_id) is str and 1 <= len(device_id) <= 128, 'cache compatibility identity')
    return digest_fields('BB-CACHE-SCOPE1', [source_sha256, driver_sha256, profile_sha256, device_id])


def frozen(value):
    if isinstance(value, dict):
        return MappingProxyType({key: frozen(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(frozen(item) for item in value)
    return value


@dataclass(frozen=True)
class PrototypeScope:
    profile: str
    roles: object
    components: object
    config_files: object
    final_arguments: tuple
    scope_id: str
    descriptor_sha256: str
    protected_inputs: tuple
    filesystem_verified_by_this_reader: bool = False
    native_integration: bool = False
    authorization: bool = False

    def classify(self, actor, role, candidate, operation):
        """Structural candidate only: never returns an authorization to open."""
        need(actor in POLICY and role in ROLES and operation in ('read', 'write'), 'known actor/role/operation required')
        candidate = path(candidate)
        base = PurePosixPath(self.roles[role])
        supported = ('r' if operation == 'read' else 'w') in POLICY[actor].get(role, '')
        inside = candidate.is_relative_to(base)
        if role == 'code':
            inside = any(candidate == PurePosixPath(row['path']) for row in self.components.values())
        if role == 'data':
            inside = str(candidate) in self.config_files.values()
        if operation == 'write':
            inside = inside and str(candidate) not in self.protected_inputs
            if self.roles['mod'] != self.roles['game']:
                inside = inside and not candidate.is_relative_to(PurePosixPath(self.roles['mod']))
            if candidate.parent == PurePosixPath(self.roles['user']):
                inside = inside and candidate.name not in ('.arm-runtime-session', '.arm-module-info.bin', '.arm-restart-request')
        # Existing mod trees can contain source links. A structural match here
        # does not prove a safe resolution; the eventual fd/overlay gate must.
        return dict(structural_candidate=bool(supported and inside), authorization=False,
                    leaf_resolution_verified=False, native_integration=False)


def _fact(observed, value, kind, uid, sha256=None):
    row = observed['nodes'].get(str(value))
    need(isinstance(row, dict) and row.get('canonical') == str(value) and row.get('kind') == kind and
         type(row.get('uid')) is int and row['uid'] == uid and row.get('is_symlink') is False,
         'separate owned/no-link observation required')
    if kind == 'file':
        need(type(row.get('nlink')) is int and row['nlink'] == 1 and row.get('sha256') == sha256,
             'separate ordinary file/SHA observation mismatch')


def _final_inputs(arguments, roles):
    need(path(arguments[0]) == roles['out'] / 'boot-linked.bin', 'original final boot role mismatch')
    options = {}; i = 1
    while i < len(arguments):
        key = arguments[i]; i += 1
        need(key not in options, 'duplicate final option')
        if key in ('--strict-imports', '--cpu-only'):
            options[key] = True; continue
        need(key in ('--user', '--app0', '--content-profile', '--patches', '--timeout') and i < len(arguments), 'unknown or incomplete original final option')
        options[key] = arguments[i]; i += 1
    expected = {'--user': roles['user'], '--app0': roles['mod'],
                '--content-profile': roles['out'] / 'content.bin', '--patches': roles['out'] / 'patches.bin'}
    for key, value in expected.items():
        need(key in options and path(options[key]) == value, 'original final argument role mismatch')
    if '--timeout' in options:
        need(re.fullmatch('[0-9]{1,3}', options['--timeout']) and int(options['--timeout']) <= 600, 'original timeout bound')
    return {'boot': str(roles['out'] / 'boot-linked.bin'), 'content': str(roles['out'] / 'content.bin'),
            'patches': str(roles['out'] / 'patches.bin')}


def validate(document, *, selection, observed, final_argv, session, uid):
    """Validate separate caller facts; never touches the paths in this data.

    selection: original launcher/final shell exact role paths + home;
    observed: caller-held bundle/component identities, load edges, inode facts;
    session: independently held supervisor UUID/nonces. None comes from env flags.
    """
    need(isinstance(document, dict) and type(document.get('schema')) is int and document['schema'] == 1 and
         document.get('kind') == 'blood-echoes-launcher-runtime-scope' and
         document.get('prototype_only') is True and document.get('production') is False, 'prototype-only schema required')
    need(set(document) == {'schema', 'kind', 'prototype_only', 'production', 'profile', 'roles', 'session', 'bundle',
         'components', 'actual_load_edges', 'executable', 'argv', 'argv_sha256', 'config_files', 'input_sha256',
         'compatibility', 'state_policy'}, 'closed descriptor field set required')
    profile = document.get('profile')
    need(profile in ('qa-shared', 'formal-prototype') and type(uid) is int and uid >= 0, 'explicit profile/owner required')
    need(selection.get('producer') == 'original-launcher-final-exec' and observed.get('producer') == 'caller-held-actual-file-observations',
         'separate original selection and observations required')
    need(isinstance(observed.get('nodes'), dict) and 1 <= len(observed['nodes']) <= 256, 'bounded separate observations required')
    need(type(document.get('roles')) is dict and set(document['roles']) == ROLES and
         document['roles'] == selection.get('roles'), 'exact role selection mismatch')
    roles = {key: path(value) for key, value in document['roles'].items()}
    home = path(selection['home'])
    for value in roles.values():
        need(value != home and value not in home.parents and value not in (PurePosixPath('/'), PurePosixPath('/Applications'), PurePosixPath('/Volumes')),
             'broad root or whole HOME is not a role')
        _fact(observed, value, 'directory', uid)
    need(roles['out'] == roles['data'] / 'out', 'out must belong to selected data')
    need(isinstance(session, dict) and set(session) == {'scope_id', 'nonce', 'restart_nonce'} and document.get('session') == session,
         'independent supervisor session binding required')
    need(str(uuid.UUID(session['scope_id'])) == session['scope_id'] and all(type(session[key]) is str and
         re.fullmatch('[0-9a-f]{32}', session[key]) for key in ('nonce', 'restart_nonce')), 'UUID/nonce format')
    need(roles['work'] == roles['out'] / 'runtime-sessions' / session['nonce'], 'runtime work must be the exact independent session subtree')
    need(not roles['work'].is_relative_to(roles['user']) and not roles['user'].is_relative_to(roles['out']) and
         not roles['user'].is_relative_to(roles['gpu']) and not roles['gpu'].is_relative_to(roles['user']) and
         not roles['gpu'].is_relative_to(roles['out']) and not roles['out'].is_relative_to(roles['gpu']), 'progress/work/cache roles overlap')
    bundle = document.get('bundle')
    need(isinstance(bundle, dict) and bundle == observed.get('bundle') and
         set(bundle) == {'id', 'path', 'launcher_path', 'launcher_sha256', 'component_manifest_sha256'} and
         all(type(bundle[k]) is str and HEX.fullmatch(bundle[k]) for k in ('launcher_sha256', 'component_manifest_sha256')),
         'actual bundle/manifest/launcher identity mismatch')
    app = path(bundle['path'])
    need(app.suffix == '.app' and roles['code'] == app / 'Contents/Resources/bbport' and
         path(bundle['launcher_path']) == app / 'Contents/MacOS/bbport', 'actual code/bundle role mismatch')
    _fact(observed, path(bundle['launcher_path']), 'file', uid, bundle['launcher_sha256'])
    _fact(observed, roles['code'] / 'NATIVE-METAL.json', 'file', uid, bundle['component_manifest_sha256'])
    compat = document.get('compatibility')
    need(isinstance(compat, dict) and set(compat) == {'id', 'source_sha256', 'driver_sha256', 'profile_sha256', 'device_id'} and
         compat == observed.get('compatibility') and compat['id'] == compatibility_id(**{k: v for k, v in compat.items() if k != 'id'}),
         'actual cache compatibility mismatch')
    project = PurePosixPath(str(PRIVATE_PROJECT))
    if profile == 'qa-shared':
        prefix = 'io.github.bbport.optimized.options-qa.arm64.gui.'
        need(type(bundle['id']) is str and bundle['id'].startswith(prefix) and re.fullmatch('[0-9]+', bundle['id'][len(prefix):]) and
             app.parent == project / 'apps' and roles['user'].parent == project / 'state' and
             roles['data'].is_relative_to(project / 'state') and roles['gpu'].parent == project / 'state' and
             roles['game'].is_relative_to(project.parent / 'assets') and roles['gpu_namespace'] == roles['gpu'], 'unchanged Shared QA boundary required')
        metadata_role = 'work'
    else:
        need(bundle['id'] == 'io.github.bbport.optimized' and all(not roles[k].is_relative_to(project)
             for k in ('code', 'data', 'out', 'user', 'gpu', 'work')), 'formal prototype cannot reuse QA identity/data')
        need(roles['gpu_namespace'] == roles['gpu'] / ('native-arm64-' + compat['id']), 'formal caches require a new exact compatibility namespace')
        metadata_role = 'work'
    need(document.get('state_policy') == dict(progress_role='user', session_marker_role=metadata_role,
         module_metadata_role=metadata_role, restart_request_role=metadata_role), 'runtime metadata/progress separation required')
    need(roles['mod'] == roles['game'] or (roles['mod'].parent == roles['out'] and roles['mod'].name.startswith('mod-game-')),
         'original final mod view must be the selected game or exact out overlay')
    for readonly in ('code', 'game'):
        need(all(not roles[readonly].is_relative_to(roles[write]) and not roles[write].is_relative_to(roles[readonly])
                 for write in ('out', 'user', 'gpu')), 'code/game overlap writable roles')
    components = document.get('components')
    need(isinstance(components, dict) and COMPONENTS <= set(components) <= COMPONENTS | OPTIONAL_COMPONENTS and
         components == observed.get('components'), 'complete actual component identities required')
    used_paths = set()
    for role, item in components.items():
        need(isinstance(item, dict) and set(item) == {'path', 'sha256'} and type(item['sha256']) is str and HEX.fullmatch(item['sha256']), 'component SHA domain')
        value = path(item['path'])
        need(value in {roles['code'] / relative for relative in COMPONENT_PATHS[role]} and value not in used_paths, 'exact selected component role mismatch')
        used_paths.add(value); _fact(observed, value, 'file', uid, item['sha256'])
    edges = {role: components[target]['path'] for role, target in
             (('cpu', 'cpu_host'), ('gpu', 'gpu_library'), ('pso_worker', 'gpu_library'), ('module_worker', 'gpu_library'))}
    need(document.get('actual_load_edges') == observed.get('actual_load_edges') == edges, 'actual CPU/GPU/helper load edges mismatch')
    need(document.get('executable') == components['cpu']['path'] and document.get('argv') == final_argv and
         document.get('argv_sha256') == argv_sha256(final_argv), 'exact final executable/argv binding required')
    inputs = _final_inputs(final_argv, roles)
    configs = document.get('config_files')
    need(isinstance(configs, dict) and configs == selection.get('config_files') and
         configs == {key: str(roles['data'] / name) for key, name in
                     (('settings', 'bbport.ini'), ('mods_config', 'mods.json'), ('patches_config', 'patches.json'))}, 'original exact configuration roles required')
    inputs.update(configs)
    hashes = document.get('input_sha256')
    need(isinstance(hashes, dict) and set(hashes) == set(inputs), 'complete final input identity domain required')
    for key, value in inputs.items():
        need(type(hashes[key]) is str and HEX.fullmatch(hashes[key]), 'final input SHA required')
        _fact(observed, path(value), 'file', uid, hashes[key])
    identity = hashlib.sha256(json.dumps(document, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    return PrototypeScope(profile, frozen(document['roles']), frozen(components), frozen(configs), tuple(final_argv), session['scope_id'], identity, tuple(inputs.values()))


def load_shared(descriptor, *, project, **facts):
    """Only load the descriptor in Shared dev evidence/state; no role path I/O."""
    project = Path(project)
    need(project == PRIVATE_PROJECT and project == project.resolve(strict=True), 'fixed canonical Shared reader root required')
    info = project.lstat()
    need(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid(), 'owned Shared descriptor root required')
    value = Path(descriptor)
    need(value.is_absolute() and (value.is_relative_to(project / 'state') or value.is_relative_to(project / 'evidence')),
         'descriptor must stay in Shared development data')
    need(value == value.resolve(strict=True), 'canonical Shared descriptor required')
    parent = value.parent
    while parent != project:
        info = parent.lstat()
        need(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid(), 'owned no-link descriptor ancestry required')
        parent = parent.parent
    fd = os.open(value, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid() and before.st_nlink == 1 and before.st_size <= 256 * 1024,
             'bounded owned no-link descriptor required')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(256 * 1024 + 1)
        key = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_uid, s.st_nlink)
        need(len(raw) == before.st_size and key(before) == key(os.fstat(fd)) == key(value.lstat()), 'descriptor changed')
    finally:
        os.close(fd)
    def pairs(items):
        result = {}
        for key, item in items:
            need(key not in result, 'duplicate descriptor field'); result[key] = item
        return result
    return validate(json.loads(raw, object_pairs_hook=pairs), **facts)
