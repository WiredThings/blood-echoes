"""Source-only ARM64 dependency closure for a fresh, owned package engine.

No default provider/search paths and no QA payloads.  Provenance is deliberately
process-local: an unknown already-relocated destination is never trusted.
"""
from __future__ import annotations
import importlib.util
import os
from pathlib import Path
import stat
import struct
import subprocess


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_original = _load('_native_assets_original', Path(__file__).resolve().parents[3] / 'tools/macos/package_optimized.py')
_complete = _load('_native_assets_complete', Path(__file__).with_name('complete_packaged_python.py'))
copy_file, loads, commands, minimum, sha = (_original.copy, _original.loads,
                                          _original.commands, _original.minimum, _original.sha)
SYSTEM = _original.SYSTEM
_ENGINES = {}
EXCLUDED = frozenset({'site-packages', '__pycache__', 'test', 'tests', 'idlelib',
                     'tkinter', 'turtledemo', 'ensurepip', 'config-3.11-darwin'})
SAFE_ENV = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'PYTHONDONTWRITEBYTECODE': '1'}


def need(value, message):
    if not value:
        raise ValueError(message)


def _system(ref):
    need('..' not in Path(ref).parts if ref.startswith(SYSTEM) else True,
         'noncanonical system dependency')
    return ref.startswith(SYSTEM)


def _file(path):
    path = Path(path).resolve(strict=True)
    need(path.is_file(), 'regular source file required')
    return path


def _root(path):
    path = Path(path).absolute()
    need(path == path.resolve(strict=True) and path.is_dir(), 'canonical directory required')
    return path


def _execute(args):
    subprocess.run(list(map(str, args)), check=True, env=SAFE_ENV,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30)


class Relocator:
    def __init__(self, engine, *, search_dirs=(), bindings=None, minimum_os=(27, 0),
                 library_subdir='deps-metal-arm64/lib'):
        self.engine = _root(engine)
        need(self.engine.stat().st_uid == os.getuid(), 'owned engine required')
        rel = Path(library_subdir)
        need(not rel.is_absolute() and rel.parts and not set(rel.parts) & {'..', '.'}, 'invalid library subdirectory')
        self.libdir = self.engine / rel
        self.search_dirs = tuple(_root(p) for p in search_dirs)
        self.bindings = {}
        for source, destination in (bindings or {}).items():
            source = Path(source).absolute()
            need(source == _file(source), 'binding source must be canonical')
            self.bindings[source] = self._destination(destination)
        self.minimum_os = tuple(minimum_os)
        self.machos, self.inputs = [], []
        self._records = _ENGINES.setdefault(self.engine, {})
        self._active = set()
        self._executable = None

    def _destination(self, path):
        path = Path(path).absolute()
        need(path.is_relative_to(self.engine) and path != self.engine and '..' not in path.parts,
             'destination outside engine')
        need(path == path.resolve(strict=False), 'destination contains a link')
        if path.exists():
            s = path.lstat()
            need(stat.S_ISREG(s.st_mode) and s.st_uid == os.getuid() and s.st_nlink == 1,
                 'owned single-link destination required')
        return path

    def _remember(self, record):
        path = record['destination']
        if path not in self.machos:
            self.machos.append(path)
            self.inputs.append(record['input'])

    def _allowed(self, candidate, loader):
        return (candidate in self.bindings or
                any(candidate.is_relative_to(root) for root in (loader.parent, *self.search_dirs)))

    def _expand(self, value, loader, executable):
        if value.startswith('@loader_path/'):
            path = loader.parent / value[len('@loader_path/'):]
        elif value.startswith('@executable_path/'):
            need(executable is not None, 'executable context required')
            path = executable.parent / value[len('@executable_path/'):]
        elif value.startswith('/'):
            path = Path(value)
        else:
            raise ValueError('unsupported load path')
        path = path.resolve(strict=False)
        need(self._allowed(path, loader), 'dependency escapes declared source roots')
        return path

    def _resolve(self, ref, loader, executable, ancestors=()):
        if ref.startswith('@rpath/'):
            tail = Path(ref[len('@rpath/'):])
            need(tail.parts and not tail.is_absolute() and '..' not in tail.parts, 'invalid rpath tail')
            roots = list(self.search_dirs)
            # Dyld also searches the run-path stack of the loading chain.
            owners = list(dict.fromkeys([loader, *reversed(ancestors),
                                         *([executable] if executable is not None else [])]))
            for owner in owners:
                roots.extend(self._expand(value, owner, executable)
                             for command, value in commands(owner) if command == 'LC_RPATH')
            candidates = set()
            for root in roots:
                candidate = (root / tail).resolve(strict=False)
                # The lexical tail cannot traverse upwards. A package-manager
                # file symlink may resolve into another explicitly admitted
                # source root (e.g. Homebrew lib -> Cellar), never outside all.
                need(self._allowed(candidate, loader), 'rpath escapes declared roots')
                if candidate.is_file():
                    candidates.add(candidate.resolve(strict=True))
            need(len(candidates) == 1, 'unresolved or ambiguous rpath dependency')
            return next(iter(candidates))
        path = self._expand(ref, loader, executable)
        need(path.is_file(), 'unresolved exact dependency')
        return path.resolve(strict=True)

    @staticmethod
    def _is_executable(source):
        with source.open('rb') as stream:
            head = stream.read(16)
        need(len(head) == 16 and struct.unpack('<I', head[:4])[0] == 0xfeedfacf, 'thin Mach-O required')
        return struct.unpack('<4I', head)[3] == 2

    def copy(self, source, destination):
        source = _file(source)
        executable = source if self._is_executable(source) else self._executable
        if executable == source:
            self._executable = source
        return self._copy(source, self._destination(destination), executable)

    def _copy(self, source, destination, executable, ancestors=()):
        source = _file(source)
        digest = sha(source)
        need(_original.run('/usr/bin/lipo', '-archs', source).strip() == 'arm64', 'thin ARM64 required')
        need(minimum(source) <= self.minimum_os, 'minimum macOS exceeds package target')
        edges = []
        for ref in loads(source):
            if _system(ref):
                continue
            dep = self._resolve(ref, source, executable, ancestors)
            target = self._destination(self.bindings.get(dep, self.libdir / dep.name))
            edges.append((ref, dep, target))
        plan = tuple(edges)
        record = self._records.get(destination)
        if record:
            need(record['source'] == source and record['source_sha256'] == digest and record['plan'] == plan,
                 'destination source or dependency-plan collision')
            self._remember(record)
            if destination in self._active:
                return destination
            need(record['ready'] and sha(destination) == record['after'], 'staged bytes changed')
            for _, dep, target in edges:
                self._copy(dep, target, executable, (*ancestors, source))
            need(sha(source) == digest, 'source changed during reuse')
            return destination
        if destination.exists():
            need(sha(destination) == digest, 'unknown destination differs from source')
        else:
            copy_file(source, destination)
        need(sha(destination) == digest and sha(source) == digest, 'source changed while copying')
        record = dict(source=source, destination=destination, source_sha256=digest, plan=plan,
                      ready=False, after=None,
                      input={'path': str(destination.relative_to(self.engine)), 'source': str(source),
                             'sha256_before_relocation': digest})
        self._records[destination] = record
        self._remember(record)
        self._active.add(destination)
        try:
            for ref, dep, target in edges:
                self._copy(dep, target, executable, (*ancestors, source))
                _execute(['/usr/bin/install_name_tool', '-change', ref,
                          '@loader_path/' + os.path.relpath(target, destination.parent), destination])
            for command, value in commands(source):
                if command == 'LC_RPATH':
                    _execute(['/usr/bin/install_name_tool', '-delete_rpath', value, destination])
                elif command == 'LC_ID_DYLIB':
                    _execute(['/usr/bin/install_name_tool', '-id', '@rpath/' + destination.name, destination])
            need(sha(source) == digest, 'source changed during relocation')
            record['after'] = sha(destination)
            record['input']['sha256_after_relocation'] = record['after']
            record['ready'] = True
            return destination
        finally:
            self._active.remove(destination)

    def sign_copied(self, paths=None):
        """Sign only tracked owned copies, updating shared byte provenance."""
        selected = list(self.machos if paths is None else paths)
        for path in selected:
            path = self._destination(path)
            record = self._records.get(path)
            need(path in self.machos and record and record['ready'] and sha(path) == record['after'],
                 'cannot sign untracked or changed bytes')
            _execute(['/usr/bin/codesign', '--force', '--sign', '-', path])
            _execute(['/usr/bin/codesign', '--verify', '--strict', path])
            record['after'] = sha(path)
            record['input']['sha256_after_relocation'] = record['after']
        return selected

    def closure(self, destination):
        result = set()
        def visit(path):
            need(path in self._records and self._records[path]['ready'], 'untracked closure')
            if path in result:
                return
            result.add(path)
            for _, _, target in self._records[path]['plan']:
                visit(target)
        visit(Path(destination))
        return sorted(result)


def python_provider(provider):
    """Resolve only the three declared Framework Python 3.11 provider forms."""
    path = Path(provider).resolve(strict=True)
    if path.is_dir():
        root = path
    elif path.parts[-5:] == ('Resources', 'Python.app', 'Contents', 'MacOS', 'Python'):
        root = path.parents[4]
    elif path.parts[-2:] == ('bin', 'python3.11'):
        root = path.parents[1]
    else:
        raise ValueError('unsupported Python provider layout')
    need(root.name == '3.11' and root.parent.name == 'Versions' and root.parent.parent.name == 'Python.framework',
         'Python 3.11 Framework required')
    interpreter = _file(root / 'Resources/Python.app/Contents/MacOS/Python')
    supplied = _file(root / 'bin/python3.11')
    library = _file(root / 'Python')
    if supplied != interpreter:
        # Homebrew may install a distinct command-line wrapper.  Its basename
        # or version string does not prove that it uses this Framework.
        for executable in (supplied, interpreter):
            refs = [ref for ref in loads(executable) if Path(ref).name == 'Python']
            need(len(refs) == 1 and refs[0].startswith('/'),
                 'single exact Framework Python load edge required')
            need(_file(refs[0]) == library, 'Framework interpreter library mismatch')
    return dict(framework_root=root, provider=supplied, interpreter=interpreter,
                library=library, stdlib=_root(root / 'lib/python3.11'),
                license=_file(root.parents[3] / 'LICENSE'))


def stdlib_files(stdlib):
    """Original package allowlist; never follow directory/file symlinks."""
    stdlib = _root(stdlib)
    selected = []
    for source in sorted(stdlib.rglob('*')):
        relative = source.relative_to(stdlib)
        if set(relative.parts) & EXCLUDED:
            continue
        need(not source.is_symlink(), 'stdlib links are not admitted')
        if source.is_file() and source.suffix in ('.py', '.so'):
            selected.append((source, relative))
    return selected


def embed_python(engine, provider, *, relocate):
    engine = _root(engine)
    relocator = relocate if isinstance(relocate, Relocator) else getattr(relocate, '__self__', None)
    need(isinstance(relocator, Relocator) and relocator.engine == engine, 'tracked Relocator required')
    selection = python_provider(provider)
    root = selection['framework_root']
    need(root in relocator.search_dirs, 'declare Framework root as a source search root')
    output = engine / 'python'
    need(not output.exists(), 'fresh Python destination required')
    need(relocator.bindings.get(selection['library']) == output / 'lib/Python', 'explicit Python library binding required')
    target = relocator.copy(selection['interpreter'], output / 'bin/python3')
    tracked = set(relocator.closure(target))
    omitted, records = [], []
    for source, relative in stdlib_files(selection['stdlib']):
        dest = output / 'lib/python3.11' / relative
        if source.suffix == '.so':
            if any(not _system(ref) for ref in loads(source)):
                omitted.append(str(relative))
                continue
            relocator.copy(source, dest)
            tracked.update(relocator.closure(dest))
        else:
            digest = sha(source)
            copy_file(source, dest)
            need(sha(source) == digest and sha(dest) == digest, 'stdlib source changed')
            records.append(dict(path=str(dest.relative_to(engine)), source=str(source), sha256=digest))
    license_digest = sha(selection['license'])
    copy_file(selection['license'], output / 'LICENSE')
    need(sha(selection['license']) == license_digest and sha(output / 'LICENSE') == license_digest,
         'license source changed')
    records.append(dict(path='python/LICENSE', source=str(selection['license']), sha256=license_digest))
    # Needed before complete() executes the copied interpreter; never sign sources.
    relocator.sign_copied(sorted(tracked))
    receipts = [_complete.complete(output, writable_stage=engine, provider=selection['provider'], module_name=name)
                for name in ('_sqlite3', '_hashlib')]
    completed = []
    for receipt in receipts:
        for item in receipt['files']:
            path = relocator._destination(output / item['path'])
            need(sha(path) == item['sha256'], 'completed Python bytes changed')
            if path not in relocator.machos:
                relocator.machos.append(path)
                # Complete owns these copies and already signs/verifies its closure.
                need('source' in item and 'source_sha256' in item, 'missing completed dependency provenance')
                source = _file(item['source'])
                need(sha(source) == item['source_sha256'], 'completion source changed')
                record = dict(source=source, destination=path, source_sha256=item['source_sha256'],
                              plan=(), ready=True, after=item['sha256'],
                              input=dict(path=str(path.relative_to(engine)), source=str(source),
                                         sha256_before_relocation=item['source_sha256'],
                                         sha256_after_relocation=item['sha256']))
                need(path not in relocator._records, 'completion destination collision')
                relocator._records[path] = record
                relocator.inputs.append(record['input'])
            completed.append(path)
    return dict(framework_root=root, provider=selection['provider'], python_root=output,
                omitted_extensions=omitted, files=records, completion=receipts,
                machos=sorted(tracked | set(completed)))
