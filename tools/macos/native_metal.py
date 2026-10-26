"""Stage the separate ARM64 Metal host in an owned app; never installs or starts it."""
import json
import os
from pathlib import Path
import plistlib
import shutil
import stat
import struct
import subprocess

from package_optimized import commands, loads, minimum, sha, SYSTEM
from game_app import GAME_ICON


def native_available(info, major):
    return info.get('schema') == 1 and info.get('default_enabled') is True and major >= info.get('minimum_os_major', 27)


def create_native_gpu_bundle(engine, source_executable, *, bundle=None, version,
                             minimum_os_major=27,
                             bundle_id='io.github.bbport.optimized.metal-game'):
    """Wrap an admitted native target in a fresh owned staging bundle.

    Source/dependency bytes are never modified. The original package workflow
    owns dependency admission and outer frontend policy; this helper only relocates
    its copied main executable and applies the existing GPU application identity.
    """
    engine = Path(engine).absolute()
    if engine != engine.resolve(strict=True):
        raise ValueError('Canonical owned engine required')
    engine_stat = engine.stat()
    if not stat.S_ISDIR(engine_stat.st_mode) or engine_stat.st_uid != os.getuid():
        raise ValueError('Canonical owned engine required')
    source = Path(source_executable).absolute()
    if source != source.resolve(strict=True) or not source.is_relative_to(engine):
        raise ValueError('Native GPU executable must remain inside the engine')
    source_stat = source.lstat()
    if not stat.S_ISREG(source_stat.st_mode) or source_stat.st_uid != os.getuid() or not os.access(source, os.X_OK):
        raise ValueError('Owned regular native GPU executable required')
    with source.open('rb') as stream:
        header = stream.read(16)
    if len(header) != 16 or struct.unpack('<4I', header)[:2] != (0xfeedfacf, 0x100000c) or struct.unpack('<4I', header)[3] != 2:
        raise ValueError('Thin ARM64 native main executable required')
    if subprocess.check_output(['lipo', '-archs', str(source)], text=True).strip() != 'arm64':
        raise ValueError('Thin ARM64 native main executable required')
    bundle = Path(bundle) if bundle is not None else engine/'bin/Bloodborne Metal.app'
    if not bundle.is_absolute():
        bundle = engine/bundle
    if not bundle.is_relative_to(engine) or '..' in bundle.parts or os.path.lexists(bundle):
        raise ValueError('Fresh native GPU bundle inside the engine required')
    if bundle.parent != bundle.parent.resolve(strict=True):
        raise ValueError('Native GPU bundle parent must be canonical')
    parent_stat = bundle.parent.stat()
    if not stat.S_ISDIR(parent_stat.st_mode) or parent_stat.st_uid != os.getuid():
        raise ValueError('Owned native GPU bundle parent required')
    if type(minimum_os_major) is not int or minimum_os_major < 26 or minimum_os_major > 99:
        raise ValueError('Invalid native GPU minimum OS')
    if not isinstance(version, str) or not version or len(version) > 128 or '\0' in version:
        raise ValueError('Invalid native GPU version')
    if not isinstance(bundle_id, str) or not bundle_id or len(bundle_id) > 255 or '\0' in bundle_id:
        raise ValueError('Invalid native GPU bundle identity')
    relocations = []
    for ref in loads(source):
        if ref.startswith(SYSTEM):
            continue
        if not ref.startswith('@loader_path/'):
            raise ValueError('Native game bundle dependency is not relocated: '+ref)
        target = (source.parent/ref.removeprefix('@loader_path/')).resolve(strict=True)
        if not target.is_file() or not target.is_relative_to(engine):
            raise ValueError('Native game dependency leaves the package: '+ref)
        relocations.append((ref, target))
    # All admission checks precede creating the fresh bundle. No old bundle is removed.
    executable = bundle/'Contents/MacOS/bb-gpu'
    executable.parent.mkdir(parents=True, exist_ok=False)
    shutil.copy2(source, executable)
    for ref, target in relocations:
        subprocess.run(['install_name_tool', '-change', ref,
                        '@loader_path/'+os.path.relpath(target, executable.parent), str(executable)], check=True)
    resources = bundle/'Contents/Resources'
    resources.mkdir()
    shutil.copy2(GAME_ICON, resources/'AppIcon.icns')
    (bundle/'Contents/Info.plist').write_bytes(plistlib.dumps(dict(
        CFBundleIdentifier=bundle_id, CFBundleName='Bloodborne',
        CFBundleDisplayName='Bloodborne', CFBundleExecutable='bb-gpu', CFBundlePackageType='APPL',
        CFBundleInfoDictionaryVersion='6.0', CFBundleShortVersionString=version,
        CFBundleVersion=version.replace('.', ''), CFBundleIconFile='AppIcon',
        LSMinimumSystemVersion=f'{minimum_os_major}.0', NSHighResolutionCapable=True,
        NSPrincipalClass='NSApplication')))
    subprocess.run(['codesign', '--force', '--sign', '-', str(executable)], check=True)
    subprocess.run(['codesign', '--force', '--sign', '-', str(bundle)], check=True)
    return executable


def stage_arm64_metal(engine, build, prefix, version):
    engine, build, prefix = map(Path, (engine, build, prefix))
    host = engine / 'bin/gpu-metal-arm64'
    libraries = engine / 'deps-metal-arm64/lib'
    # engine is an owned staging copy, never the installed app or user data.
    # Rebuild its closure so an older staged dependency cannot survive an update.
    bundle = engine / 'bin/Bloodborne Metal.app'
    for old in (host, libraries.parent, bundle):
        if old.exists(): shutil.rmtree(old)
    host.mkdir(parents=True)
    libraries.mkdir(parents=True)
    inputs = {}
    dependency_sources = {}

    def copy_arm(source, target):
        source, target = Path(source), Path(target)
        archs = subprocess.check_output(['lipo', '-archs', str(source)], text=True).split()
        if 'arm64' not in archs:
            raise ValueError('Expected ARM64 input: ' + str(source))
        if archs == ['arm64']:
            shutil.copy2(source, target)
        else:
            subprocess.run(['lipo', str(source), '-thin', 'arm64', '-output', str(target)], check=True)
        target.chmod(target.stat().st_mode | 0o200)
        inputs[str(target.relative_to(engine))] = sha(source)
        # Native development builds may use Homebrew's dynamic closure. Preserve
        # its license texts and formula recipe rather than relying on the base app.
        for origin in source.resolve().parents:
            if not (origin/'INSTALL_RECEIPT.json').is_file(): continue
            key = origin.parent.name+'-'+origin.name
            dependency_sources[key] = dict(provider='Homebrew', formula=origin.parent.name, version=origin.name)
            dest = engine/'licenses/metal-arm64-dependencies'/key
            for item in list(origin.glob('LICENSE*'))+list(origin.glob('COPYING*'))+list(origin.glob('NOTICE*'))+list((origin/'.brew').glob('*.rb')):
                if item.is_file():
                    dest.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, dest/item.name)
            break

    queue = []
    for name in ['bb-gpu', 'libbbgpu.dylib']:
        copy_arm(build / name, host / name)
        queue.append(host / name)
    done = set()
    while queue:
        binary = queue.pop()
        if binary in done:
            continue
        done.add(binary)
        for ref in loads(binary):
            if ref.startswith(SYSTEM):
                continue
            name = Path(ref).name
            if name == 'libbbgpu.dylib':
                target = host / name
            else:
                target = libraries / name
                if not target.exists():
                    candidates = [Path(ref)] if ref.startswith('/') else []
                    candidates += [prefix/'lib'/name, Path('/opt/homebrew/lib')/name,
                                   Path('/opt/homebrew/opt/ffmpeg/lib')/name]
                    source = next((p for p in candidates if p.is_file()), None)
                    if source is None:
                        raise ValueError('Unresolved ARM dependency: ' + ref)
                    copy_arm(source, target)
                    queue.append(target)
            relocated = '@loader_path/' + os.path.relpath(target, binary.parent)
            subprocess.run(['install_name_tool', '-change', ref, relocated, str(binary)], check=True)
        for command, value in commands(binary):
            if command == 'LC_RPATH':
                subprocess.run(['install_name_tool', '-delete_rpath', value, str(binary)], check=True)
        if binary.suffix == '.dylib':
            subprocess.run(['install_name_tool', '-id', '@rpath/'+binary.name, str(binary)], check=True)
        subprocess.run(['codesign', '--force', '--sign', '-', str(binary)], check=True)

    # Use the original GPU bundle algorithm for a newly built native target.
    # The frontend policy and NATIVE-METAL manifest remain owned by this stage.
    min_major = max(27, max([26] + [minimum(p)[0] for p in done]))
    create_native_gpu_bundle(engine, host/'bb-gpu', version=version,
                             minimum_os_major=min_major)
    frontend = engine/'bin/Bloodborne Game.app'
    info_path = frontend/'Contents/Info.plist'
    if info_path.is_file():
        info = plistlib.loads(info_path.read_bytes())
        info['LSUIElement'] = True
        info_path.write_bytes(plistlib.dumps(info))
        subprocess.run(['codesign', '--force', '--sign', '-', str(frontend)], check=True)
    manifest = dict(schema=1, default_enabled=True, minimum_os_major=min_major,
                    renderer_architecture='arm64; game remains x86_64 Rosetta',
                    protocol=0x4d540001, lifetime_journal_bytes=262528,
                    storage_release_experiment_supported=False,
                    dependency_sources=dependency_sources, input_sha256=inputs, native_library_sha256=sha(host/'libbbgpu.dylib'))
    (engine/'NATIVE-METAL.json').write_text(json.dumps(manifest, indent=2)+'\n')
    validate_native_payload(engine, manifest)
    return manifest


def validate_native_payload(engine, manifest):
    """Reject non-native, escaping, unresolved or too-new dependencies before release."""
    engine = Path(engine).resolve()
    binaries = [engine/'bin/gpu-metal-arm64/bb-gpu',
                engine/'bin/gpu-metal-arm64/libbbgpu.dylib',
                engine/'bin/Bloodborne Metal.app/Contents/MacOS/bb-gpu']
    binaries += list((engine/'deps-metal-arm64/lib').glob('*'))
    for binary in binaries:
        if binary.is_symlink() or not binary.is_file():
            raise ValueError('Invalid native package file: '+str(binary))
        if subprocess.check_output(['lipo', '-archs', str(binary)], text=True).strip() != 'arm64':
            raise ValueError('Non-native package dependency: '+str(binary))
        if minimum(binary)[0] > manifest['minimum_os_major']:
            raise ValueError('Dependency exceeds advertised minimum OS: '+str(binary))
        for ref in loads(binary):
            if ref.startswith(SYSTEM): continue
            if not ref.startswith('@loader_path/'):
                raise ValueError('Non-local package dependency: '+ref)
            target = (binary.parent/ref.removeprefix('@loader_path/')).resolve()
            if not target.is_relative_to(engine) or not target.is_file():
                raise ValueError('Escaping or missing package dependency: '+ref)
    return len(binaries)
