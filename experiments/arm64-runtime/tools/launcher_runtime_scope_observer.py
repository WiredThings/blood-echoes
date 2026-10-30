"""Bounded Shared-only file observations for the non-authorizing scope prototype.

Uses the existing no-link/stable-stream Reader. Native loader results must be
supplied separately by the caller; this module never invokes native tools.
"""
from pathlib import Path, PurePosixPath
import os
import plistlib

import qa_launch_conditions as files
import launcher_runtime_scope as scope

PRIVATE_PROJECT = Path('/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype')
EDGE_TARGETS = dict(cpu='cpu_host', gpu='gpu_library', pso_worker='gpu_library', module_worker='gpu_library')


def need(ok, reason):
    if not ok:
        raise ValueError('runtime scope observer: ' + reason)


class StreamReader(files.Reader):
    """Same hashing/parser algorithm, with explicit per-component byte budgets."""
    def __init__(self):
        super().__init__()
        self.limits = {}

    def read(self, path, *, limit=1024 * 1024, **kw):
        self.limits[str(path)] = max(limit, self.limits.get(str(path), 0))
        return super().read(path, limit=limit, **kw)

    def unchanged(self):
        for path, identity in tuple(self.inputs.items()):
            self.read(path, limit=self.limits[path], expected=identity, retain=False)


def _preflight(document, selection, final_argv, project):
    # Lexical bounds precede resolve/stat/open, even for a malformed descriptor.
    need(isinstance(document, dict) and isinstance(selection, dict), 'validated descriptor/selection objects required')
    need(document.get('profile') == 'qa-shared' and document.get('prototype_only') is True and
         document.get('production') is False, 'only existing Shared QA prototype observations allowed')
    need(selection.get('producer') == 'original-launcher-final-exec' and document.get('roles') == selection.get('roles') and
         isinstance(selection.get('roles'), dict) and set(selection['roles']) == scope.ROLES, 'exact original selection required')
    roles = {key: scope.path(value) for key, value in selection['roles'].items()}
    root = PurePosixPath(str(project))
    code = roles['code']; need(len(code.parents) >= 3, 'packaged code role depth required'); app = code.parents[2]
    need(code == app / 'Contents/Resources/bbport' and app.parent == root / 'apps' and app.suffix == '.app', 'fixed Shared packaged code role required')
    need(all(roles[key].is_relative_to(root / 'state') for key in ('data', 'out', 'user', 'gpu', 'gpu_namespace', 'work')) and
         roles['user'].parent == root / 'state' and roles['gpu'].parent == root / 'state' and
         roles['out'] == roles['data'] / 'out' and roles['gpu_namespace'] == roles['gpu'] and
         roles['game'].is_relative_to(root.parent / 'assets'), 'unchanged Shared role bounds required')
    need(roles['mod'] == roles['game'] or (roles['mod'].parent == roles['out'] and roles['mod'].name.startswith('mod-game-')), 'exact final overlay role required')
    need(document.get('argv') == final_argv and document.get('argv_sha256') == scope.argv_sha256(final_argv), 'exact final argv required')
    inputs = scope._final_inputs(final_argv, roles)  # original parser contract, not another approximation
    configs = document.get('config_files')
    need(isinstance(configs, dict) and configs == selection.get('config_files') and configs ==
         {key: str(roles['data'] / name) for key, name in (('settings', 'bbport.ini'), ('mods_config', 'mods.json'), ('patches_config', 'patches.json'))}, 'exact selected config roles required')
    inputs.update(configs)
    components = document.get('components')
    need(isinstance(components, dict) and scope.COMPONENTS <= set(components) <= scope.COMPONENTS | scope.OPTIONAL_COMPONENTS, 'complete bounded component roles required')
    for role, item in components.items():
        need(isinstance(item, dict) and set(item) == {'path', 'sha256'} and
             scope.path(item['path']) in {code / relative for relative in scope.COMPONENT_PATHS[role]}, 'component path must match its precise role')
    return roles, app, inputs


def observe(document, *, selection, final_argv, session, uid, project, native_results):
    """Return independently measured observed data accepted by scope.validate.

    native_results carries existing verify/resolve results for cpu/gpu/two
    workers, game_pso_component.load's preparation result, and separately
    collected compatibility. No result is constructed from expected SHA fields.
    """
    project = Path(project)
    need(project == PRIVATE_PROJECT and type(uid) is int and uid == os.getuid(), 'fixed development root/current owner required')
    roles, app, inputs = _preflight(document, selection, final_argv, project)
    files.canonical(project, directory=True)
    need(isinstance(native_results, dict) and set(native_results) == set(EDGE_TARGETS) | {'preparation', 'compatibility'},
         'separate existing-loader results required')
    reader = StreamReader(); nodes = {}; directories = {}; file_nodes = {}

    def directory(value):
        value = files.canonical(str(value), directory=True)
        before = value.lstat()
        directories[str(value)] = files.stamp(before)
        nodes[str(value)] = dict(canonical=str(value), kind='directory', uid=before.st_uid, is_symlink=False, nlink=before.st_nlink)

    def regular(value, *, retain=False, limit=512 * 1024 * 1024):
        value = files.canonical(str(value))
        before = value.lstat()
        raw = reader.read(value, limit=limit, retain=retain)
        after = value.lstat()
        need(files.stamp(before) == files.stamp(after), 'file replaced while observed')
        need(str(value) not in file_nodes or file_nodes[str(value)] == files.stamp(after), 'file replaced between observations')
        file_nodes[str(value)] = files.stamp(after)
        identity = reader.inputs[str(value)]
        nodes[str(value)] = dict(canonical=str(value), kind='file', uid=after.st_uid, is_symlink=False,
                                 nlink=after.st_nlink, sha256=identity)
        return raw, identity

    for value in roles.values():
        directory(value)
    # Bundle and manifest observations are real reads, not expected.bundle copies.
    info_path = app / 'Contents/Info.plist'
    info = plistlib.loads(regular(info_path, retain=True, limit=1024 * 1024)[0])
    prefix = 'io.github.bbport.optimized.options-qa.arm64.gui.'
    identifier = info.get('CFBundleIdentifier')
    need(isinstance(identifier, str) and identifier.startswith(prefix) and identifier[len(prefix):].isascii() and
         identifier[len(prefix):].isdigit() and info.get('CFBundleExecutable') == 'bbport', 'actual private QA bundle identity required')
    launcher = app / 'Contents/MacOS/bbport'
    launcher_sha = regular(launcher, limit=64 * 1024 * 1024)[1]
    manifest_path = roles['code'] / 'NATIVE-METAL.json'
    # Reuse the existing bounded JSON/duplicate-field parser, with no expected SHA.
    manifest = reader.json(str(manifest_path), limit=1024 * 1024)
    manifest_sha = regular(manifest_path, limit=1024 * 1024)[1]
    cpu_profile=(manifest.get('game_runtime')=='fex-arm64' or
                 (manifest.get('game_runtime')=='arm64'and manifest.get('cpu_backends')==['fex','bbcpu']))
    need(type(manifest.get('schema')) is int and manifest['schema'] == 1 and cpu_profile and
         manifest.get('production') is False, 'actual isolated FEX component manifest required')
    components = {}
    for role, item in document['components'].items():
        actual_sha = regular(item['path'])[1]
        components[role] = dict(path=item['path'], sha256=actual_sha)
    for role, target in EDGE_TARGETS.items():
        item = native_results[role]
        need(isinstance(item, dict) and item.get('target') == components[role]['path'] and
             item.get('target_sha256') == components[role]['sha256'] and item.get('library') == components[target]['path'] and
             item.get('library_sha256') == components[target]['sha256'] and item.get('load_edge_verified') is True and
             item.get('strict_signatures') is True and item.get('architecture') == 'arm64', 'injected actual executable/DSO/signature result changed or mismatched')
    preparation = native_results['preparation']
    need(isinstance(preparation, dict) and preparation.get('enabled') is True and preparation.get('production') is False and
         preparation.get('kind') == 'blood-echoes-game-pso-component' and
         str(preparation.get('worker')) == components['pso_worker']['path'] and
         preparation.get('worker_sha256') == components['pso_worker']['sha256'] and
         str(preparation.get('module_consumer')) == components['module_worker']['path'] and
         preparation.get('module_consumer_sha256') == components['module_worker']['sha256'] and
         str(preparation.get('library')) == components['gpu_library']['path'] and
         preparation.get('library_sha256') == components['gpu_library']['sha256'], 'injected preparation loader result mismatch')
    compatibility = native_results['compatibility']
    need(isinstance(compatibility, dict) and set(compatibility) == {'source_sha256', 'driver_sha256', 'profile_sha256', 'device_id'},
         'separate actual source/driver/profile/device observation required')
    compatibility = dict(compatibility, id=scope.compatibility_id(**compatibility))
    for value in inputs.values():
        regular(value, limit=64 * 1024 * 1024)
    observed = dict(producer='caller-held-actual-file-observations',
        bundle=dict(id=identifier, path=str(app), launcher_path=str(launcher), launcher_sha256=launcher_sha,
                    component_manifest_sha256=manifest_sha),
        compatibility=compatibility, components=components,
        actual_load_edges={role: native_results[role]['library'] for role in EDGE_TARGETS}, nodes=nodes)
    reader.unchanged()
    for value, before in file_nodes.items():
        value = files.canonical(value)
        need(files.stamp(value.lstat()) == before, 'file changed or replaced before observation return')
    for value, before in directories.items():
        value = files.canonical(value, directory=True)
        need(files.stamp(value.lstat()) == before, 'role directory changed or replaced during observation')
    result = scope.validate(document, selection=selection, observed=observed, final_argv=final_argv, session=session, uid=uid)
    observed['observation_receipt'] = dict(schema=1, file_sha256=dict(reader.inputs), directory_nodes=len(directories),
        descriptor_sha256=result.descriptor_sha256, existing_native_results_injected=True, native_commands_executed=False,
        formal_paths_enabled=False, authorization=False, leaf_operation_authorized=False)
    return observed
