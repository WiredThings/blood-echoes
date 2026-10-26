"""Fresh private benchmark data derived from verified original GUI conditions.

Never selects formal data or starts a process. Large save/cache contents use
bounded streaming copies; nothing is linked to the source or removed from it.
"""
from pathlib import Path
import hashlib
import json
import os
import re
import secrets
import stat

import qa_launch_conditions as conditions_reader
import qa_gpu_cache_seed as cache_seed

PRIVATE_PROJECT = Path('/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype')
EXCLUSIONS = ('automatic-game-inputs', 'captures', 'log', 'temp')
PATH_KEYS = frozenset(('BB_DATA_DIR', 'BB_CONFIG', 'BB_USER_DIR', 'BB_MODS_DIR', 'BB_MODS_CONFIG',
                      'BB_PATCHES_DIR', 'BB_PATCHES_CONFIG', 'BB_GPU_USER_DIR'))
CONTROL_KEYS = frozenset(('BB_ARM_SESSION_NONCE', 'BB_ARM_RESTART_NONCE', 'BB_PAD_FILE',
    'BB_ARM_QA_PAD_FILE', 'BB_PAD_FILE_ONLY', 'BB_FRAME_TIMING', 'BB_FRAME_LOG_ONLY', 'BB_BENCHMARK_MUTE'))
ALLOWED_CHANGES = PATH_KEYS | CONTROL_KEYS


def need(ok, reason):
    if not ok:
        raise ValueError('QA launch clone: ' + reason)


def _project(project):
    root = conditions_reader.canonical(project, directory=True)
    need(root == PRIVATE_PROJECT, 'fixed Shared project required')
    conditions_reader.canonical(root / 'state', directory=True)
    return root


def _savedata_root(path):
    root = conditions_reader.canonical(path, directory=True)
    user = root.parent
    need(root.name == 'savedata' and user.parent == PRIVATE_PROJECT / 'state' and
         re.fullmatch(r'(gui-user|benchmark-user)-[0-9]+', user.name), 'only private QA savedata allowed')
    return root


def _tree_inventory(root):
    before = cache_seed._tree_snapshot(root)
    files = {str(path.relative_to(root)): cache_seed._hash_plain(path)
             for path, identity in sorted(before.items()) if stat.S_ISREG(identity[2])}
    need(cache_seed._tree_snapshot(root) == before, 'source tree changed during inventory')
    return before, files


def inventory_savedata(path):
    """Return relative file -> SHA; source must be a private QA savedata root."""
    return _tree_inventory(_savedata_root(path))[1]


def _copy_tree(root, destination):
    root = conditions_reader.canonical(root, directory=True)
    before, hashes = _tree_inventory(root)
    destination.mkdir(mode=0o700)
    cache_seed._copy_nodes(before, root, destination)
    after, source_hashes = _tree_inventory(root)
    copied, copied_hashes = _tree_inventory(destination)
    need(before == after and hashes == source_hashes == copied_hashes, 'source/destination tree contents changed')
    source_nodes = {str(path.relative_to(root)) for path in before}
    copied_nodes = {str(path.relative_to(destination)) for path in copied}
    need(source_nodes == copied_nodes, 'copied directory inventory differs')
    return before, hashes


def _write_new(path, raw):
    need(isinstance(raw, bytes), 'immutable bytes required')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    need(cache_seed._hash_plain(path) == hashlib.sha256(raw).hexdigest(), 'new copied configuration changed')


def create(conditions, cache_admission, *, project, tag, cache_mode='seeded'):
    """Clone only a verified QA selection; original Swift options remain intact.

    cache_admission is produced by the caller's existing native cache validator.
    This CPU-only step checks its exact source/manifest/build binding; it does
    not forge or rerun signature, Mach-O or actual DSO admission.
    """
    project = _project(project)
    need(type(tag) is str and re.fullmatch(r'[0-9]{1,24}', tag), 'numeric fresh case tag required')
    need(cache_mode in ('seeded', 'empty'), 'seeded or empty cache mode required')
    source_env = dict(conditions['environment'])
    need(conditions.get('producer') == conditions_reader.PRODUCER and conditions.get('game_started_verified') is False,
         'verified original Swift launch conditions required')
    source_user = conditions_reader.canonical(conditions['user_root'], directory=True, within=project / 'state')
    source_gpu = conditions_reader.canonical(conditions['cache_root'], directory=True, within=project / 'state')
    source_data = conditions_reader.canonical(conditions['data_root'], directory=True, within=project / 'state')
    stage = conditions_reader.canonical(conditions['stage'], directory=True, within=project / 'apps')
    need(source_env.get('BB_USER_DIR') == str(source_user) and source_env.get('BB_GPU_USER_DIR') == str(source_gpu) and
         source_env.get('BB_DATA_DIR') == str(source_data) and re.fullmatch(r'gui-user-[0-9]+', source_user.name) and
         source_user.parent == source_gpu.parent == project / 'state', 'original QA source roots mismatch')
    game = conditions_reader.canonical(conditions['game_root'], directory=True, within=project.parent / 'assets')
    need(source_env.get('BB_GAME_DIR') == str(game) and source_env.get('BB_ONLINE') == '0' and
         all(source_env.get(k) == '1' for k in conditions_reader.PROTECTION_KEYS), 'original protected offline selection required')
    inputs = conditions_reader.Reader()
    for path, want in conditions['input_sha256'].items():
        inputs.read(path, limit=64 * 1024 * 1024, expected=want, retain=False)
    manifest_path = stage.parent / 'ARM64-QA-MANIFEST.json'
    manifest = inputs.json(manifest_path, expected=conditions['identity']['ARM64-QA-MANIFEST.json'])
    source_case = manifest['private_case']
    need(source_case.get('user') == str(source_user) and source_case.get('data') == str(source_data) and
         source_case.get('app') == str(stage.parents[2]) and manifest.get('private_gpu_cache') == str(source_gpu),
         'source roots do not match this admitted QA case')
    need(cache_admission.get('source') == str(source_gpu) and cache_admission.get('source_manifest') == str(manifest_path) and
         cache_admission.get('source_manifest_sha256') == conditions['identity']['ARM64-QA-MANIFEST.json'] and
         cache_admission.get('upstream_gpu_library_sha256') == manifest['upstream_inputs']['gpu_library'] and
         cache_admission.get('source_packaged_gpu_library_sha256') == conditions['runtime_sha256']['bin/gpu-metal-arm64/libbbgpu.dylib'] and
         cache_admission.get('cache_parts') == list(cache_seed.CACHE_PARTS) and cache_admission.get('cache_only') is True and
         cache_admission.get('formal_data_modified') is False and cache_admission.get('save_config_seed_changed') is False,
         'cache admission does not bind this exact source QA app/cache/renderer')
    user = project / 'state' / ('benchmark-user-' + tag)
    data = project / 'state' / ('benchmark-data-' + tag)
    gpu = project / 'state' / ('gui-gpu-local-aot-' + tag)
    for target in (user, data, gpu):
        need(not os.path.lexists(target), 'all three benchmark destinations must be fresh')
    save_root = _savedata_root(source_user / 'savedata')
    # Preflight all source save/mod/patch trees before publishing any new root.
    save_before, source_save_manifest = _tree_inventory(save_root)
    optional_before = {}
    for key, name in (('BB_MODS_DIR', 'mods'), ('BB_PATCHES_DIR', 'patches')):
        path = source_data / name
        need(source_env.get(key) == str(path), 'source mod/patch path mismatch')
        optional_before[key] = _tree_inventory(path) if os.path.lexists(path) else None
    for key, name in conditions_reader.CONFIG_NAMES.items():
        snapshot = conditions['configs'][key]
        need(isinstance(snapshot, conditions_reader.ConfigSnapshot) and isinstance(snapshot.data, bytes) and
             snapshot.path == source_data / name and source_env.get(key) == str(snapshot.path) and
             snapshot.sha256 == conditions['identity'][key] == hashlib.sha256(snapshot.data).hexdigest(),
             'source immutable configuration snapshot mismatch')
        inputs.read(snapshot.path, expected=snapshot.sha256)
    # The original GPU is the baseline too: keep its exact non-diagnostic config,
    # never substitute an earlier QA profile or copy old source views/progress.
    cache_result = cache_seed.clone(cache_admission, gpu, project=project, baseline_root=source_gpu,
                                    extra_exclusions=EXCLUSIONS, cache_mode=cache_mode)
    user.mkdir(mode=0o700); data.mkdir(mode=0o700); (data / 'out').mkdir(mode=0o700)
    copied_save_nodes, copied_save_hashes = _copy_tree(save_root, user / 'savedata')
    need(copied_save_nodes == save_before and copied_save_hashes == source_save_manifest, 'source save changed after preflight')
    for key, name in conditions_reader.CONFIG_NAMES.items():
        _write_new(data / name, conditions['configs'][key].data)
    for key, name in (('BB_MODS_DIR', 'mods'), ('BB_PATCHES_DIR', 'patches')):
        if optional_before[key] is None:
            need(not os.path.lexists(source_data / name), 'source optional directory appeared')
            (data / name).mkdir(mode=0o700)
        else:
            copied = _copy_tree(source_data / name, data / name)
            need(copied == optional_before[key], 'source optional directory changed after preflight')
    nonce, restart_nonce = secrets.token_hex(16), secrets.token_hex(16)
    pad = data / 'pad.txt'; _write_new(pad, b'')
    session = 'ARMQA1\n' + str(data / 'out/boot-linked.bin') + '\n' + str(game) + '\n' + str(data / 'out/content.bin') + '\n' + nonce + '\n'
    _write_new(user / '.arm-runtime-session', session.encode())
    env = dict(source_env)
    env.update(BB_DATA_DIR=str(data), BB_CONFIG=str(data / 'bbport.ini'), BB_USER_DIR=str(user),
        BB_MODS_DIR=str(data / 'mods'), BB_MODS_CONFIG=str(data / 'mods.json'),
        BB_PATCHES_DIR=str(data / 'patches'), BB_PATCHES_CONFIG=str(data / 'patches.json'), BB_GPU_USER_DIR=str(gpu),
        BB_ARM_SESSION_NONCE=nonce, BB_ARM_RESTART_NONCE=restart_nonce,
        BB_PAD_FILE=str(pad), BB_ARM_QA_PAD_FILE=str(pad), BB_PAD_FILE_ONLY='1',
        BB_FRAME_TIMING='1', BB_FRAME_LOG_ONLY='1', BB_BENCHMARK_MUTE='1')
    changed = {key: dict(before=source_env.get(key), after=env.get(key))
               for key in sorted(set(source_env) | set(env)) if source_env.get(key) != env.get(key)}
    need(set(changed) <= ALLOWED_CHANGES and set(source_env) <= set(env), 'unapproved launch setting changed')
    unchanged = {key: value for key, value in source_env.items() if key not in changed}
    need(all(env[key] == value for key, value in unchanged.items()), 'original option was not preserved')
    final_save_nodes, final_save_hashes = _tree_inventory(save_root)
    need(final_save_nodes == save_before and final_save_hashes == source_save_manifest, 'source savedata changed before return')
    for key, name in (('BB_MODS_DIR', 'mods'), ('BB_PATCHES_DIR', 'patches')):
        actual = _tree_inventory(source_data / name) if os.path.lexists(source_data / name) else None
        need(actual == optional_before[key], 'source mod/patch tree changed before return')
    inputs.unchanged()
    config_sha = {key: snapshot.sha256 for key, snapshot in conditions['configs'].items()}
    return dict(environment=env, data_root=data, user_root=user, cache_root=gpu, pad=pad,
        source_save_manifest=source_save_manifest, config_sha256=config_sha, allowed_changes=sorted(ALLOWED_CHANGES),
        actual_changes=changed, unchanged_keys=sorted(unchanged),
        unchanged_environment_sha256=hashlib.sha256(json.dumps(unchanged, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
        source_unchanged=True, source_snapshot=dict(user=str(source_user), savedata=str(save_root),
            savedata_manifest=source_save_manifest, config_sha256=config_sha, input_sha256=dict(inputs.inputs)),
        cache_clone=cache_result, cache_mode=cache_mode, formal_data_modified=False, game_started=False,
        source_launch_id=conditions['launch_id'], source_pid=conditions['source_pid'])
