#!/usr/bin/env python3
"""Plan/materialize/build a bounded private SetHp source overlay; never run it.

The host/game implementation is the previously verified private-death headers.
Only four checked include/call insertions are made to current normal sources.
No normal source, application, receipt, save, setting or cache is modified.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

PROJECT = Path('/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype')
NORMAL = PROJECT / 'evidence/upstream-refresh-20261030'
REVISION = '5c0f2b72bb2f1153513ce018f2a26814d8c61e7e'
EXPECTED = {
    'src/game_runtime.cpp': '84ac385cd134b6a41840215f9502300144baf69da741c482f96cdf06b0c14c1a',
    'src/dev_travel_runtime.h': 'd8a834216c9a7c32364af4eb68be547e5167dc8340cbbdec472f8bf3578ec354',
    'tests/private_death_contract.h': '97cfb07976be281d5f49a1f26fb147bc058195d50f17589f4a0e0f15f3d65e37',
    'tests/private_death_runtime.h': '5e420cb822ebd80887cd70328d26245a74553bcdb070566bd4d4726ae3144483',
}
TICK = b'void Tick(){'
OBSERVE = b' if(read_failures)return;\n const WWStage previous='
INSTALL = b'memcpy(mapping->ptr,plan.stub.data(),plan.stub.size());check(mprotect'
POLICY_SHA256 = '8be773022072505ce041e02039d67ce96978baf85eb702653a1a6bfa83f89ff7'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def once(raw, before, after):
    if raw.count(before) != 1:
        raise ValueError('private overlay anchor missing or not unique')
    return raw.replace(before, after, 1)


def overlay_sources(inputs):
    if set(inputs) != set(EXPECTED):
        raise ValueError('exact four normal input files required')
    if any(not isinstance(raw, bytes) or digest(raw) != EXPECTED[name] for name, raw in inputs.items()):
        raise ValueError('reviewed current normal source identity changed')
    cpp = inputs['src/game_runtime.cpp']
    travel = inputs['src/dev_travel_runtime.h']
    if b'private_death' in cpp or b'PrepareDeath(' in travel or b'ObserveDeath(' in travel:
        raise ValueError('source already instrumented')
    cpp = b'#include "private_death_contract.h"\n' + cpp
    travel = once(travel, TICK, b'#include "private_death_runtime.h"\n' + TICK)
    travel = once(travel, OBSERVE,
                  b' if(read_failures)return;\n ObserveDeath(now,world,loading,player);\n const WWStage previous=')
    travel = once(travel, INSTALL,
                  b'memcpy(mapping->ptr,plan.stub.data(),plan.stub.size());if(!authored)PrepareDeath(image,boot,*mapping,plan.stub.size());check(mprotect')
    result = dict(game_runtime=cpp, travel=travel)
    # Reverse the entire delta, proving no unrelated host/runtime source rewrite.
    restored_cpp = cpp.removeprefix(b'#include "private_death_contract.h"\n')
    restored_travel = travel.replace(b'#include "private_death_runtime.h"\n', b'', 1)
    restored_travel = restored_travel.replace(b' ObserveDeath(now,world,loading,player);\n', b'', 1)
    restored_travel = restored_travel.replace(b'if(!authored)PrepareDeath(image,boot,*mapping,plan.stub.size());', b'', 1)
    if restored_cpp != inputs['src/game_runtime.cpp'] or restored_travel != inputs['src/dev_travel_runtime.h']:
        raise ValueError('overlay was not a minimal reversible source delta')
    return {'game_runtime.cpp': result['game_runtime'], 'dev_travel_runtime.h': result['travel'],
            'private_death_contract.h': inputs['tests/private_death_contract.h'],
            'private_death_runtime.h': inputs['tests/private_death_runtime.h']}


def within_evidence(value):
    path = Path(value)
    root = PROJECT / 'evidence'
    if not path.is_absolute() or not path.is_relative_to(root) or not path.resolve().is_relative_to(root):
        raise ValueError('only the Shared private evidence tree is permitted')
    return path


def policy_environment_projection(raw, stage, destination):
    """Exact reviewed launcher shell, changing cwd and its two terminal execs only.

    Only these shell defaults are captured, NOT the actual CPU environment:
    run.sh and local preparation add further values and manage live mod views.
    The death execution entry therefore uses the complete original pipeline.
    Not executed here. Parent must supply the admitted offline QA environment.
    """
    if digest(raw) != POLICY_SHA256:
        raise ValueError('reviewed launcher policy bytes changed')
    stage, destination = within_evidence(stage), within_evidence(destination)
    if destination.name != 'effective-environment.bin' or destination.exists() or destination.is_symlink():
        raise ValueError('new private environment snapshot required; no overwrite')
    result = once(raw, b'cd -- "$(dirname -- "$0")/../.."\n',
                  ('cd -- ' + shlex.quote(str(stage)) + '\n').encode())
    export = ('/usr/bin/env -0 > ' + shlex.quote(str(destination)) + '; exit 0').encode()
    for line in (b'    exec bash tools/macos/supervise_restart.sh "$@"', b'exec bash run.sh "$@"'):
        result = once(result, line, export)
    if b'exec bash ' in result:
        raise ValueError('unprojected game launch in policy shell')
    return result


def rewrite_build_commands(record, link_argv, *, source_root, cpu_build, output):
    """Preserve normal flags/current archives; replace one unit and private outputs."""
    source_root, cpu_build, output = map(Path, (source_root, cpu_build, output))
    original = source_root / 'src/game_runtime.cpp'
    if Path(record['directory']).resolve() != cpu_build.resolve() or Path(record['file']).resolve() != original.resolve():
        raise ValueError('compile database belongs to another normal source/build')
    compile_argv = list(record.get('arguments') or shlex.split(record['command']))
    if compile_argv.count(str(original)) != 1 or compile_argv.count('-o') != 1 or '-c' not in compile_argv:
        raise ValueError('one actual game_runtime compile command required')
    if any(arg.startswith(('-MJ', '--serialize-diagnostics', '-serialize-diagnostics', '-save-temps',
                           '-fprofile-generate', '-ftime-trace', '-fmodules')) for arg in compile_argv):
        raise ValueError('additional compile outputs need explicit independent review')
    old_object = compile_argv[compile_argv.index('-o') + 1]
    if not (cpu_build / old_object).resolve().is_relative_to((cpu_build / 'CMakeFiles/bb-game-arm64.dir').resolve()):
        raise ValueError('normal game target compile object required; snapshot target forbidden')
    obj = output / 'game_runtime.cpp.o'
    compile_argv[compile_argv.index(str(original))] = str(output / 'source/game_runtime.cpp')
    compile_argv[compile_argv.index('-o') + 1] = str(obj)
    if '-MF' in compile_argv:
        compile_argv[compile_argv.index('-MF') + 1] = str(output / 'game_runtime.cpp.d')
    compile_argv[1:1] = ['-I' + str(output / 'source'), '-I' + str(source_root / 'src')]
    link = list(link_argv)
    if '-c' in link or link.count('-o') != 1 or link.count(old_object) != 1:
        raise ValueError('one original normal linker command/object required')
    if Path(link[link.index('-o') + 1]).name != 'bb-game-arm64':
        raise ValueError('normal game target required')
    if any(arg.startswith(('-Wl,-map', '-Wl,-object_path_lto')) for arg in link):
        raise ValueError('additional linker outputs need explicit independent review')
    if not any(Path(arg).name == 'libbb_fex_embedding.a' and
               (cpu_build / arg).resolve().is_relative_to(cpu_build.resolve()) for arg in link):
        raise ValueError('current normal FEX embedding archive required; old raw CPU forbidden')
    if not any(Path(arg).name.startswith('libbbgpu') and arg.endswith('.dylib') and
               (cpu_build / arg).resolve().is_relative_to((cpu_build.parent / 'renderer').resolve()) for arg in link):
        raise ValueError('current normal renderer CPU-host library required')
    link[link.index(old_object)] = str(obj)
    link[link.index('-o') + 1] = str(output / 'code/bb-game-arm64')
    for i, arg in enumerate(link):
        if arg.startswith('-Wl,-dependency_info,'):
            link[i] = '-Wl,-dependency_info,' + str(output / 'link.d')
        elif arg == '-dependency_info':
            if link[i - 1:i] != ['-Xlinker'] or link[i + 1:i + 2] != ['-Xlinker'] or i + 2 >= len(link):
                raise ValueError('unknown dependency-output link spelling')
            link[i + 2] = str(output / 'link.d')
    return dict(cwd=str(cpu_build), compile=compile_argv, link=link,
                only_replaced_normal_object=old_object, normal_archives_and_flags_preserved=True)


def normal_compile_record(records, source, cpu):
    matches = []
    for record in records:
        if Path(record['file']) != source / 'src/game_runtime.cpp':
            continue
        args = record.get('arguments') or shlex.split(record['command'])
        if args.count('-o') != 1:
            raise ValueError('compile database game unit has ambiguous output')
        obj = (cpu / args[args.index('-o') + 1]).resolve()
        if obj.is_relative_to((cpu / 'CMakeFiles/bb-game-arm64.dir').resolve()):
            if record.get('output') and Path(record['output']).resolve() != obj:
                raise ValueError('compile database output field disagrees with command')
            matches.append(record)
    if len(matches) != 1:
        raise ValueError('normal game target compile unit missing or duplicated')
    return matches[0]


def private_cache_identity(record, overlay, *, combine):
    """Retain actual translator identity; accurately replace the QA bridge inputs."""
    runtime, bridge = record['runtime_parts'], record['bridge_parts']
    if (record.get('schema') != 1 or combine('BE-common-runtime-build-v1', runtime) != record['runtime_build']
            or combine('BE-common-bridge-build-v1', bridge) != record['bridge_build']):
        raise ValueError('normal complete CPU-cache build identity inconsistent')
    expected = {'game-frontend': EXPECTED['src/game_runtime.cpp'],
                'frontend-header-dev_travel_runtime.h': EXPECTED['src/dev_travel_runtime.h']}
    if any(bridge.get(k) != v for k, v in expected.items()):
        raise ValueError('normal bridge does not bind the reviewed frontend/travel source')
    private = dict(bridge)
    private['game-frontend'] = digest(overlay['game_runtime.cpp'])
    private['frontend-header-dev_travel_runtime.h'] = digest(overlay['dev_travel_runtime.h'])
    for name in ('private_death_contract.h', 'private_death_runtime.h'):
        label = 'qa-only-header-' + name
        if label in private:
            raise ValueError('normal cache identity already contains private death overlay')
        private[label] = digest(overlay[name])
    value = combine('BE-common-bridge-build-v1', private)
    if value == record['bridge_build']:
        raise ValueError('private bridge must not impersonate normal frontend identity')
    result = dict(schema=1, kind='private-death-cpu-cache-build', runtime_parts=runtime,
        runtime_build=record['runtime_build'], bridge_parts=private, bridge_build=value,
        normal_bridge_build=record['bridge_build'], modified_bridge_labels=sorted(set(expected) |
            {'qa-only-header-private_death_contract.h', 'qa-only-header-private_death_runtime.h'}),
        normal_runtime_retained=True, own_cpu_cache_namespace_required=True)
    literal = lambda v: ','.join('0x' + v[i:i+2] for i in range(0, len(v), 2))
    header = ('// QA-only identity from actual normal linked parts and private frontend inputs.\n#pragma once\n'
              '#include "bb_cpu_cache_identity.h"\nnamespace BbCacheBuild {\n'
              'inline constexpr BbCpuCache::Digest Runtime={' + literal(result['runtime_build']) + '};\n'
              'inline constexpr BbCpuCache::Digest Bridge={' + literal(result['bridge_build']) + '};\n}\n')
    return result, header.encode()


def normal_link(cpu_build):
    """Read Ninja's actual command graph only; no target is executed."""
    command = ['/opt/homebrew/bin/ninja', '-C', str(cpu_build), '-t', 'commands', 'bb-game-arm64']
    result = subprocess.run(command, text=True, capture_output=True, timeout=20)
    if result.returncode or len(result.stdout.encode()) > 16 * 1024 * 1024:
        raise ValueError('normal Ninja command graph unavailable or oversized')
    matches = []
    for line in result.stdout.splitlines():
        tokens = shlex.split(line)
        if '-c' in tokens or '-o' not in tokens or Path(tokens[tokens.index('-o') + 1]).name != 'bb-game-arm64':
            continue
        start = next((i for i, value in enumerate(tokens) if Path(value).name in ('clang++', 'c++')), None)
        if start is None:
            continue
        end = next((i for i in range(start, len(tokens)) if tokens[i] in ('&&', ';')), len(tokens))
        matches.append(tokens[start:end])
    if len(matches) != 1:
        raise ValueError('one actual normal game linker command required')
    return matches[0]


def build_plan(normal, output, *, inspect_link=False):
    normal, output = within_evidence(normal), within_evidence(output)
    if output.parent.resolve() != (PROJECT / 'evidence').resolve() or not output.name.startswith('upstream-private-death-'):
        raise ValueError('a dedicated upstream-private-death-* evidence root is required')
    if (output.resolve() == normal.resolve() or output.resolve().is_relative_to((normal / 'source').resolve())
            or output.resolve().is_relative_to((normal / 'build').resolve())):
        raise ValueError('private overlay output must be separate from normal source/build')
    selection = json.loads((normal / 'source-selection.json').read_text())
    if selection.get('revision') != REVISION or Path(selection['source']) != normal / 'source':
        raise ValueError('reviewed current normal source selection required')
    source = normal / 'source/experiments/arm64-runtime'
    inputs = {name: (source / name).read_bytes() for name in EXPECTED}
    overlay = overlay_sources(inputs)
    cpu = normal / 'build/cpu'
    identity_record = json.loads((cpu / 'generated/cpu_cache_build.json').read_text())
    builder = source / 'tools/make_cpu_cache_build.py'
    if digest(builder.read_bytes()) != identity_record['bridge_parts']['builder-source']:
        raise ValueError('actual normal cache identity algorithm source changed')
    spec = importlib.util.spec_from_file_location('private_death_normal_cache_builder', builder)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cache_identity, overlay['bb_cpu_cache_build.h'] = private_cache_identity(identity_record, overlay, combine=module.combine)
    database = cpu / 'compile_commands.json'
    commands = None
    if database.is_file() and inspect_link:
        record = normal_compile_record(json.loads(database.read_text()), source, cpu)
        commands = rewrite_build_commands(record, normal_link(cpu), source_root=source,
                                           cpu_build=cpu, output=output)
    plan = dict(schema=1, status='plan-only-no-build-no-game', revision=REVISION,
        normal_source=str(source), normal_cpu_build=str(cpu), output=str(output),
        input_sha256=EXPECTED, overlay_sha256={name:digest(raw) for name,raw in overlay.items()},
        normal_sources_modified=False, normal_application_modified=False,
        compiled=False, game_started=False, formal_data_accessed=False,
        build_commands_ready=commands is not None, build_commands=commands,
        original_policy_sha256=POLICY_SHA256,
        private_cpu_cache_build=cache_identity,
        pending_build_note='After normal CPU build/configure completes, repeat --plan --inspect-link; never use old private raw CPU.',
        required_before_link=['normal embedding/FEX/runtime archives and CPU-host dylib from this exact new build',
                              'fresh private source/object/code output; only game_runtime object replaced',
                              'record dependency hashes, ARM64 architecture, dyld load edge and private executable signature'],
        runtime_environment_contract=['reuse the original frozen receipt and admitted new normal app',
            'reuse a fresh isolated QA case or the candidate case from completed QA preparation, never formal saves',
            'derive all actual shell defaults by a checked private run_optimized no-game environment projection',
            'BB_AUDIO=none, BB_QUICKSAVE=0, BB_PAD_FILE_ONLY=1, BB_ARM_LAUNCHER_COMPAT=1, BB_RESTART_SUPERVISED=1',
            'new raw CPU uses original generated boot/patches/app0/content/user argv; actual merged app0 must be retained',
            'BB_GPU_PROCESS points to admitted new normal nested GPU, same new CPU-host library; no THD credentials',
            'private user/world-warper, matching .private-world-warper nonce and armed nonce',
            'death-command.txt allows only 1 then 2; do not write the travel command.txt or issue combat input',
            'observe HP zero, world down/loading, health valid positive and stable>=5 twice; address reuse is permitted',
            'outer original MemoryPolicy/lock/protection and owned-process cleanup remain; no new guest exit code'],
        separate_from_performance_and_f3=True, runtime_instrumentation_is_QA_only=True)
    return plan, overlay


def file_hash(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def build_private(plan):
    """Explicit build-only operation on the materialized private four-file overlay."""
    commands = plan['build_commands']
    if not commands or not plan['build_commands_ready']:
        raise ValueError('actual normal target commands required before private build')
    output, cwd = Path(plan['output']), Path(commands['cwd'])
    for name, sha in plan['overlay_sha256'].items():
        if file_hash(output / 'source' / name) != sha:
            raise ValueError('private source changed before compilation')
    if commands['compile'][0] != '/usr/bin/c++' or commands['link'][0] != '/usr/bin/c++':
        raise ValueError('actual reviewed Apple normal compiler driver required')
    inputs = [cwd / 'Bin/bb-game-arm64', cwd / commands['only_replaced_normal_object']]
    for arg in commands['link']:
        if arg.endswith(('.a', '.o', '.dylib')) and arg != str(output / 'game_runtime.cpp.o'):
            inputs.append((cwd / arg).resolve())
    inputs = sorted(set(path.resolve() for path in inputs))
    before = {str(path):file_hash(path) for path in inputs}
    result = dict(schema=1, status='building-private-unit-only', normal_input_sha256=before,
                  game_started=False, compiled=False, normal_application_modified=False,
                  private_cpu_cache_build=plan['private_cpu_cache_build'])
    (output / 'build-result.json').write_text(json.dumps(result, indent=2) + '\n')

    def run(name, argv, timeout=300):
        print(name, flush=True)
        with (output / (name + '.log')).open('wb') as log:
            proc = subprocess.run(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=log,
                                  stderr=subprocess.STDOUT, timeout=timeout, env=os.environ.copy())
        if proc.returncode:
            raise ValueError(name + ' failed; inspect private log')
        return (output / (name + '.log')).read_text(errors='replace')

    run('compile-private-game-runtime', commands['compile'])
    run('link-private-game-runtime', commands['link'])
    binary = output / 'code/bb-game-arm64'
    run('sign-private-game-runtime', ['/usr/bin/codesign', '--force', '--sign', '-', str(binary)], 30)
    run('verify-private-signature', ['/usr/bin/codesign', '--verify', '--strict', str(binary)], 30)
    architecture = run('private-architecture', ['/usr/bin/lipo', '-archs', str(binary)], 30).strip()
    if architecture != 'arm64':
        raise ValueError('private CPU is not exclusively ARM64')
    libraries = run('private-libraries', ['/usr/bin/otool', '-L', str(binary)], 30)
    loads = run('private-load-commands', ['/usr/bin/otool', '-l', str(binary)], 30)
    if 'libbbgpu.dylib' not in libraries or str(cwd.parent / 'renderer') not in loads:
        raise ValueError('private CPU did not retain the same normal renderer load edge')
    after = {str(path):file_hash(path) for path in inputs}
    if after != before:
        raise ValueError('normal build input changed during private build')
    result.update(status='private-cpu-built-not-run', compiled=True, executable=str(binary),
                  executable_sha256=file_hash(binary), architecture=architecture,
                  original_input_hashes_unchanged=True, current_normal_renderer_load_edge=True)
    (output / 'build-result.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--plan', action='store_true')
    mode.add_argument('--write-overlay', action='store_true', help='write only four private source files and plan; never compile/run')
    mode.add_argument('--build-private', action='store_true', help='fresh private overlay + one-unit compile/link/sign; no game execution')
    parser.add_argument('--normal-root', type=Path, default=NORMAL)
    parser.add_argument('--output', type=Path, default=PROJECT / 'evidence/upstream-private-death-cache-20261030')
    parser.add_argument('--inspect-link', action='store_true', help='read actual normal Ninja graph after CPU configure; no target execution')
    args = parser.parse_args()
    report, overlay = build_plan(args.normal_root, args.output, inspect_link=args.inspect_link or args.build_private)
    if args.build_private and not report['build_commands_ready']:
        raise ValueError('normal CPU configure/build must finish before private compile')
    if args.write_overlay or args.build_private:
        args.output.mkdir(mode=0o700, exist_ok=False)
        (args.output / 'source').mkdir(mode=0o700)
        (args.output / 'code').mkdir(mode=0o700)
        for name, raw in overlay.items():
            (args.output / 'source' / name).write_bytes(raw)
        report['status'] = 'private-source-only-no-build-no-game'
        (args.output / 'plan.json').write_text(json.dumps(report, indent=2) + '\n')
    if args.build_private:
        report['private_build_result'] = build_private(report)
        report['compiled'] = True
        report['status'] = 'private-cpu-built-not-run'
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
