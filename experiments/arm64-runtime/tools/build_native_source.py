"""Plan, or explicitly build, current ARM64 sources with supplied dependencies.

The default mode only returns a plan.  --build creates one fresh output tree;
it does not assemble/install an app, start a game, or use private QA inputs.
External dependency construction and final packaged load/ABI validation are
separate from this source build.  No prepared command table is replayed.
"""
from pathlib import Path
import argparse
import hashlib
import importlib.util
import json
import os
import re
import stat
import time


def need(ok, message):
    if not ok:
        raise ValueError(message)


def directory(value):
    path = Path(value).absolute()
    need(path == path.resolve(strict=True) and path.is_dir(), 'canonical directory required: ' + str(path))
    need(path.stat().st_uid in (0, os.getuid()), 'directory owner mismatch')
    return path


def digest(path):
    path = Path(path)
    before = path.lstat()
    need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_uid in (0, os.getuid()),
         'ordinary single-link owned file required: ' + str(path))
    with path.open('rb') as stream:
        result = hashlib.file_digest(stream, 'sha256').hexdigest()
    after = path.lstat()
    fields = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    need(fields(before) == fields(after), 'file changed while hashing: ' + str(path))
    return result


def executable(path):
    path = Path(path).resolve(strict=True)
    need(os.access(path, os.X_OK), 'executable required: ' + str(path))
    return dict(path=str(path), sha256=digest(path))


def plan(*, repository, build_root, dependency_prefixes, protoc, python_provider,
         version, revision, openssl_root=None, sdk_root=None):
    """Read source/tool identities only: no subprocess, mkdir or QA-state read."""
    repo = directory(repository)
    source = directory(repo / 'experiments/arm64-runtime')
    root = Path(build_root).absolute()
    parent = directory(root.parent)
    need(parent.stat().st_uid == os.getuid(), 'build parent must belong to current user')
    need(root == root.resolve(strict=False) and not os.path.lexists(root), 'fresh canonical build root required')
    need(not root.is_relative_to(repo) and not repo.is_relative_to(root), 'build/source trees must not overlap')
    need(not any(p.endswith('.app') for p in root.parts) and not root.is_relative_to('/Applications')
         and not root.is_relative_to('/System/Applications'), 'build output cannot be an application location')
    prefixes = [directory(p) for p in dependency_prefixes]
    need(prefixes and len(prefixes) == len(set(prefixes)), 'distinct explicit dependency prefixes required')
    for path in [repo, root, *prefixes]:
        need(not any(c in str(path) for c in (';', '\n', '\r')), 'ambiguous command/CMake path')
    need(re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version), 'explicit release version required')
    need(re.fullmatch(r'[0-9a-f]{40}', revision), 'full lowercase source revision required')
    tools = {}
    for name in ('cmake', 'ninja', 'pkg-config'):
        found = next((p / 'bin' / name for p in prefixes if (p / 'bin' / name).exists()), None)
        need(found is not None, 'explicit dependency prefixes lack tool: ' + name)
        tools[name] = executable(found)
    tools['protoc'] = executable(protoc)
    need(any(Path(tools['protoc']['path']).is_relative_to(p) for p in prefixes), 'protoc must come from an explicit dependency prefix')
    provider = directory(python_provider)
    tools['python'] = executable(provider / 'bin/python3.11')
    need(Path(tools['python']['path']).is_relative_to(provider), 'python executable escaped explicit provider')
    sdk = str(directory(Path(sdk_root).resolve(strict=True))) if sdk_root is not None else '<SDKROOT>'
    openssl = str(directory(openssl_root)) if openssl_root is not None else None
    required = ['tools/macos/build_launcher.sh', 'experiments/arm64-runtime/tools/configure_native_build.py',
                'experiments/arm64-runtime/tools/build_metadata_layout.py', 'experiments/arm64-runtime/vendor/metal_api/CMakeLists.txt',
                'experiments/arm64-runtime/vendor/bbnet/CMakeLists.txt', 'experiments/arm64-runtime/targets.cmake',
                'tools/macos/build_identity.py']
    sources = {name: digest(repo / name) for name in required}
    launcher = (repo / required[0]).read_text()
    need('work=${BB_LAUNCHER_WORK:-out/launcher-build}' in launcher,
         'original launcher must support explicit fresh BB_LAUNCHER_WORK')
    cmake = tools['cmake']['path']
    common = ['-G', 'Ninja', '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_OSX_ARCHITECTURES=arm64',
              '-DCMAKE_OSX_DEPLOYMENT_TARGET=27.0', '-DCMAKE_OSX_SYSROOT=' + sdk,
              '-DCMAKE_C_COMPILER=/usr/bin/clang', '-DCMAKE_CXX_COMPILER=/usr/bin/clang++',
              '-DCMAKE_MAKE_PROGRAM=' + tools['ninja']['path'], '-DCMAKE_EXPORT_COMPILE_COMMANDS=ON',
              '-DCMAKE_FIND_USE_PACKAGE_REGISTRY=OFF', '-DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=OFF',
              '-DCMAKE_FIND_USE_CMAKE_ENVIRONMENT_PATH=OFF',
              '-DCMAKE_PREFIX_PATH=' + ';'.join(map(str, prefixes)),
              '-DCMAKE_PROGRAM_PATH=' + ';'.join(str(p / 'bin') for p in prefixes)]
    renderer = root / 'renderer'
    net = root / 'net'
    cpu = root / 'cpu'
    py = tools['python']['path']
    prepare = [py, str(source / 'tools/configure_native_build.py'), '--source-root', str(source),
               '--build-dir', str(cpu), '--generated-dir', str(root / 'generated'), '--renderer-build', str(renderer), '--prepare-only']
    for prefix in prefixes:
        prepare += ['--dependency-prefix', str(prefix)]
    net_config = [cmake, '-S', str(source / 'vendor/bbnet'), '-B', str(net), *common,
                  '-DBBNET_PROTOC_EXECUTABLE=' + tools['protoc']['path']]
    if openssl:
        net_config += ['-DOPENSSL_ROOT_DIR=' + openssl]
    commands = [dict(name='renderer-configure', argv=[cmake, '-S', str(source / 'vendor/metal_api'), '-B', str(renderer), *common,
                    '-DPKG_CONFIG_EXECUTABLE=' + tools['pkg-config']['path'], '-DBB_BUILD_GAME_PREPARATION=ON',
                    '-DBB_GAME_PREPARATION_SOURCE_ROOT=' + str(source), '-DBB_DEV_FETCH_CAPTURE=ON', '-DBB_LTO=OFF', '-DBB_PGO=off']),
                dict(name='renderer-build', argv=[cmake, '--build', str(renderer), '--parallel', '1', '--target',
                    'bbgpu', 'game-pso-preparer', 'game-module-preparer', 'bb-gpu', 'metadata-layout']),
                dict(name='cpu-generate', argv=prepare),
                dict(name='cpu-configure', argv=None, deferred='configure_native_build.plan: actual renderer FileAPI/includes/definitions'),
                dict(name='cpu-build', argv=[cmake, '--build', str(cpu), '--parallel', '1', '--target', 'bb-game-arm64']),
                dict(name='net-configure', argv=net_config),
                dict(name='net-build', argv=[cmake, '--build', str(net), '--parallel', '1', '--target', 'bbnet']),
                dict(name='launcher-build', argv=['/bin/bash', str(repo / 'tools/macos/build_launcher.sh')]),
                dict(name='layout-seal', argv=[py, str(source / 'tools/build_metadata_layout.py'), '--renderer-build', str(renderer),
                     '--project', str(root), '--source-root', str(source / 'vendor/metal_api/shadps4'), '--output', str(root / 'evidence/layout')])]
    environment = dict(PATH=':'.join([str(provider / 'bin'), *(str(p / 'bin') for p in prefixes), '/usr/bin', '/bin', '/usr/sbin', '/sbin']),
                       PKG_CONFIG_LIBDIR=':'.join(str(p / sub) for p in prefixes for sub in ('lib/pkgconfig', 'share/pkgconfig')),
                       BB_APP=str(root / 'gui/Blood Echoes.app'), BB_LAUNCHER_WORK=str(root / 'gui/work'),
                       BB_SWIFT_SCRATCH_PATH=str(root / 'gui/swift'), BB_RELEASE=version,
                       BB_SWIFT_JOBS='1', SDKROOT=sdk,
                       CMAKE_BUILD_PARALLEL_LEVEL='1', PYTHONDONTWRITEBYTECODE='1')
    return dict(schema=1, status='plan-only', repository=str(repo), source_root=str(source), build_root=str(root),
                dependency_prefixes=list(map(str, prefixes)), python_provider=str(provider), openssl_root=openssl, sdk_root=None if sdk_root is None else sdk,
                version=version, revision=revision, tools=tools, source_files=sources, environment=environment, commands=commands,
                sdk_query=None if sdk_root is not None else ['/usr/bin/xcrun', '--sdk', 'macosx', '--show-sdk-path'],
                no_private_stage_inputs=True, dependency_construction_verified=False, targets_built=False,
                app_assembled=False, installed=False, game_started=False)


def target_artifact(build, name):
    build = Path(build)
    reply = build / '.cmake/api/v1/reply'
    indices = sorted(reply.glob('index-*.json'))
    need(indices, 'actual normal FileAPI missing')
    index = json.loads(indices[-1].read_text())
    model = json.loads((reply / index['reply']['codemodel-v2']['jsonFile']).read_text())
    need(len(model['configurations']) == 1, 'single actual build configuration required')
    rows = [r for r in model['configurations'][0]['targets'] if r['name'] == name]
    need(len(rows) == 1, 'one actual target required: ' + name)
    target = json.loads((reply / rows[0]['jsonFile']).read_text())
    artifacts = target.get('artifacts', [])
    need(len(artifacts) == 1, 'one actual artifact required: ' + name)
    path = (build / artifacts[0]['path']).absolute()
    need(path == path.resolve(strict=True) and path.is_relative_to(build), 'target artifact escaped build')
    return path


def source_snapshot(repository):
    """Hash actual build source, original scripts and GUI resources, not QA trees.

    The fixed source roles exclude development evidence/output/game/cache trees.
    Internal source links record their spelling and their actual target content;
    external dependencies must instead use the explicit dependency providers.
    """
    repo = directory(repository)
    runtime = repo / 'experiments/arm64-runtime'
    roots = [repo / p for p in ('src', 'gpu', 'include', 'scripts', 'launcher', 'launcher-macos', 'tools')]
    roots += [runtime / p for p in ('src', 'vendor', 'tools', 'config', 'tests')]
    for parent in (repo, runtime):
        roots += [p for p in parent.iterdir() if p.name in ('CMakeLists.txt', 'SOURCE-MANIFEST.json', 'Package.swift', 'Package.resolved')
                  or p.suffix in ('.py', '.cmake', '.sh')]
    result, visited, queue = {}, set(), [p for p in roots if os.path.lexists(p)]
    while queue:
        path = queue.pop()
        relative = str(path.relative_to(repo))
        if relative in visited:
            continue
        visited.add(relative)
        need(len(visited) <= 500000, 'source snapshot node bound exceeded')
        row = path.lstat()
        need(row.st_uid in (0, os.getuid()), 'source snapshot owner mismatch')
        if stat.S_ISLNK(row.st_mode):
            target = path.resolve(strict=True)
            need(target.is_relative_to(repo), 'source export link escaped source tree')
            result[relative] = dict(kind='link', target=os.readlink(path))
            queue.append(target)
        elif stat.S_ISDIR(row.st_mode):
            result[relative] = dict(kind='directory', mode=stat.S_IMODE(row.st_mode))
            queue += [p for p in path.iterdir() if p.name not in ('.git', '__pycache__', '.build', '.DS_Store')]
        else:
            result[relative] = dict(kind='file', mode=stat.S_IMODE(row.st_mode), bytes=row.st_size, sha256=digest(path))
    return dict(sorted(result.items()))


def identify_source(repository, revision, *, git_top, git):
    """Callbacks are FixedRunner-controlled queries; author tests inject no tools."""
    repo = directory(repository)
    top = git_top()
    if top is not None and Path(top).resolve(strict=True) == repo:
        need(git('rev-parse', 'HEAD') == revision, 'repository revision differs from requested revision')
        need(not git('status', '--porcelain', '--untracked-files=all', '--ignore-submodules=none'),
             'source/submodule modifications or untracked inputs present')
        return dict(mode='git', revision=revision, tree=git('rev-parse', 'HEAD^{tree}'))
    # The original source_revision contract permits an export inside another
    # checkout. Never attribute that parent's HEAD/tree to this export.
    need(not os.path.lexists(repo / '.git'), 'repository marker disagrees with actual Git top level')
    manifest = repo / 'SOURCE-MANIFEST.json'
    need(manifest.is_file(), 'Git-less source export requires SOURCE-MANIFEST.json')
    need(manifest.stat().st_size <= 1024 * 1024, 'source manifest size bound exceeded')
    manifest_sha = digest(manifest)
    helper = repo / 'tools/macos/build_identity.py'
    helper_sha = digest(helper)
    spec = importlib.util.spec_from_file_location('_blood_echoes_source_identity', helper)
    module = importlib.util.module_from_spec(spec)
    # Avoid importlib's bytecode writer in an immutable source export.
    exec(compile(helper.read_bytes(), str(helper), 'exec'), module.__dict__)
    actual = module.manifest_revision(json.loads(manifest.read_text()))
    need(actual == revision, 'source export revision differs from requested revision')
    snapshot = source_snapshot(repo)
    need(digest(manifest) == manifest_sha and digest(helper) == helper_sha, 'source identity changed during snapshot')
    canonical = json.dumps(snapshot, sort_keys=True, separators=(',', ':')).encode()
    return dict(mode='source-export', revision=revision, manifest_sha256=manifest_sha,
                snapshot_sha256=hashlib.sha256(canonical).hexdigest(), source_files=snapshot)


class BuildGuard:
    def __init__(self, observer, policy, no_games, *, clock=time.monotonic, timeout=7200):
        self.observer, self.policy, self.no_games, self.clock = observer, policy, no_games, clock
        self.start, self.timeout, self.samples = clock(), timeout, []

    def check(self, **kwargs):
        self.no_games()  # Every poll, including while a compiler runs.
        elapsed = self.clock() - self.start
        sample = self.observer.system()
        reason = self.policy.observe(elapsed, sample)
        need(not reason, reason or 'build memory protection')
        need(elapsed <= self.timeout, 'source build total timeout')
        if kwargs.get('preflight'):
            need(sample['pressure'] != 4, 'severe pressure before new compiler stage')
        if not self.samples or elapsed - self.samples[-1]['seconds'] >= 1:
            self.samples.append(dict(seconds=elapsed, **sample))


def execute(value):
    """Explicit build only. All child groups remain under the original policy."""
    from configure_native_build import plan as cpu_plan
    from local_metal_builder import FixedRunner
    from native_observer import NativeObserver
    from qa_build_guard import require_no_games
    from qa_memory_policy import MemoryPolicy
    expected = plan(
        repository=value['repository'], build_root=value['build_root'], dependency_prefixes=value['dependency_prefixes'],
        protoc=value['tools']['protoc']['path'], python_provider=value['python_provider'], version=value['version'], revision=value['revision'],
        openssl_root=value['openssl_root'], sdk_root=value['sdk_root'])
    need(expected == value, 'source/tool/plan inputs changed')
    require_no_games()
    observer = NativeObserver()
    before = observer.system()
    guard = BuildGuard(observer, MemoryPolicy(before), require_no_games)
    root = Path(value['build_root'])
    report = dict(schema=1, status='incomplete', source_root=value['source_root'], repository=value['repository'],
                  build_root=str(root), dependency_construction_verified=False, packaged_ABI_validated=False,
                  app_assembled=False, installed=False, game_started=False, targets_built=False, no_private_stage_inputs=True)
    created = False
    try:
        guard.check(preflight=True)
        root.mkdir(mode=0o700)
        created = True
        for name in ('tmp', 'evidence', 'gui'):
            (root / name).mkdir(mode=0o700)
        runner = FixedRunner(root / 'tmp')
        (root / 'plan.json').write_text(json.dumps(value, indent=2) + '\n')
        def run(argv, *, name, env=None, timeout=1800):
            actual = ['/usr/bin/env', *[k + '=' + v for k, v in sorted((env or {}).items())], *argv]
            raw = runner.run(actual, timeout, guard, cwd=value['repository'])
            (root / 'evidence' / (name + '.log')).write_bytes(raw)
            return raw
        def git(*args):
            return run(['/usr/bin/git', '-C', value['repository'], *args], name='git-' + args[0], timeout=20).decode().strip()
        def git_top():
            # Preserve non-repository status without relaxing FixedRunner's
            # fail-closed command semantics. The wrapper/child share its group.
            code = ('import json,subprocess,sys; p=subprocess.run(sys.argv[1:],capture_output=True,text=True); '
                    'print(json.dumps(dict(returncode=p.returncode,stdout=p.stdout)))')
            raw = run([value['tools']['python']['path'], '-I', '-B', '-c', code, '/usr/bin/git', '-C',
                       value['repository'], 'rev-parse', '--show-toplevel'], name='git-top-level', timeout=20)
            query = json.loads(raw)
            need(query['returncode'] in (0, 128), 'Git top-level query failed')
            if query['returncode'] == 128:
                need(not query['stdout'].strip(), 'ambiguous non-repository Git output')
                return None
            top = query['stdout'].strip()
            need(top.startswith('/') and '\n' not in top, 'invalid Git top-level response')
            return top
        source_identity = identify_source(value['repository'], value['revision'], git_top=git_top, git=git)
        sdk = value['sdk_root'] or run(value['sdk_query'], name='sdk-query', timeout=20).decode().strip()
        sdk = str(directory(Path(sdk).resolve(strict=True)))
        env = {k: v.replace('<SDKROOT>', sdk) for k, v in value['environment'].items()}
        for section in ('renderer', 'net'):
            query = root / section / '.cmake/api/v1/query'
            query.mkdir(parents=True, mode=0o700)
            (query / 'codemodel-v2').touch()
        for command in value['commands']:
            guard.check(preflight=True)
            argv = command['argv']
            if command['name'] == 'cpu-configure':
                native = cpu_plan(source_root=value['source_root'], build_dir=root / 'cpu', generated_dir=root / 'generated',
                                  dependency_prefixes=value['dependency_prefixes'], renderer_build=root / 'renderer')
                (root / 'evidence/native-plan.json').write_text(json.dumps(native, indent=2) + '\n')
                argv = native['configure']
            argv = [a.replace('<SDKROOT>', sdk) for a in argv]
            run(argv, name=command['name'], env=env)
        need(identify_source(value['repository'], value['revision'], git_top=git_top, git=git) == source_identity, 'source changed during build')
        need(all(digest(Path(value['repository']) / p) == h for p, h in value['source_files'].items()), 'build source identity changed')
        artifacts = {}
        roles = [('renderer', 'library', 'bbgpu'), ('renderer', 'gpu', 'bb-gpu'), ('renderer', 'worker', 'game-pso-preparer'),
                 ('renderer', 'module', 'game-module-preparer'), ('renderer', 'metadata', 'metadata-layout'),
                 ('cpu', 'cpu', 'bb-game-arm64'), ('net', 'net', 'bbnet')]
        for subdir, role, target in roles:
            path = target_artifact(root / subdir, target)
            need(run(['/usr/bin/lipo', '-archs', str(path)], name='arch-' + role, timeout=20).decode().strip() == 'arm64', 'artifact must be thin ARM64: ' + role)
            artifacts[role] = dict(path=str(path), sha256=digest(path))
        app = root / 'gui/Blood Echoes.app'
        gui = app / 'Contents/MacOS/bbport'
        need(run(['/usr/bin/lipo', '-archs', str(gui)], name='arch-gui', timeout=20).decode().strip() == 'arm64', 'GUI must be thin ARM64')
        run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)], name='gui-seal', timeout=30)
        layout_result = root / 'evidence/layout/result.json'
        layout = json.loads(layout_result.read_text())
        need(layout['status'] == 'collected-normal-target' and layout['renderer_loaded'] is False, 'normal layout collection incomplete')
        from game_pso_owned_audit import load_canonical_layout
        load_canonical_layout(Path(layout['layout']), Path(layout['source_root']), layout['layout_sha256'])
        report.update(status='source-built-not-packaged', source_identity=source_identity, tools=value['tools'],
                      dependency_prefixes=value['dependency_prefixes'], python_provider=value['python_provider'], sdk_root=sdk,
                      version=value['version'], revision=value['revision'], artifacts=artifacts,
                      renderer=str(root / 'renderer'), cpu=artifacts['cpu']['path'], net=artifacts['net']['path'], library=artifacts['library']['path'],
                      layout_result=str(layout_result), layout_result_sha256=digest(layout_result), gui_app=str(app), gui_sha256=digest(gui), targets_built=True)
        return report
    except BaseException as error:
        report['error'] = str(error)
        raise
    finally:
        report.update(before=before, after=observer.system(), seconds=time.monotonic() - guard.start, memory_policy=MemoryPolicy.MANIFEST, samples=guard.samples)
        if created:
            (root / 'build-manifest.json').write_text(json.dumps(report, indent=2) + '\n')
        observer.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('repository', 'build-root', 'protoc', 'python-provider'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--dependency-prefix', type=Path, action='append', required=True)
    parser.add_argument('--openssl-root', type=Path)
    parser.add_argument('--sdk-root', type=Path)
    parser.add_argument('--version', required=True)
    parser.add_argument('--revision', required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--plan', action='store_true')
    mode.add_argument('--build', action='store_true')
    parser.add_argument('--stop-after-build', action='store_true', help='Compatibility spelling: no assembler is invoked in either mode')
    args = parser.parse_args()
    value = plan(repository=args.repository, build_root=args.build_root, dependency_prefixes=args.dependency_prefix,
                 protoc=args.protoc, python_provider=args.python_provider, version=args.version, revision=args.revision,
                 openssl_root=args.openssl_root, sdk_root=args.sdk_root)
    print(json.dumps(execute(value) if args.build else value, indent=2))


if __name__ == '__main__':
    main()
