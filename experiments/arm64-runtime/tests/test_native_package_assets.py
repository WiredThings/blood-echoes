import importlib.util
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

FILE = Path(__file__).resolve().parents[1] / 'tools/native_package_assets.py'
spec = importlib.util.spec_from_file_location('native_package_assets', FILE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class Assets(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.engine = self.root / 'engine'; self.engine.mkdir()
        self.src = self.root / 'source'; self.src.mkdir()
        self.edges, self.cmds, self.calls = {}, {}, []
        self.patches = [patch.object(m, 'loads', side_effect=lambda p: self.edges.get(Path(p), [])),
                        patch.object(m, 'commands', side_effect=lambda p: self.cmds.get(Path(p), [])),
                        patch.object(m, 'minimum', return_value=(27, 0)),
                        patch.object(m._original, 'run', return_value='arm64'),
                        patch.object(m, '_execute', side_effect=self.execute)]
        for p in self.patches: p.start()

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.tmp.cleanup()

    def execute(self, args):
        self.calls.append(list(map(str, args)))
        if '-change' in args or '-id' in args or '-delete_rpath' in args or '--force' in args:
            with Path(args[-1]).open('ab') as stream: stream.write(b'edited')

    def binary(self, name, executable=False):
        path = self.src / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(struct.pack('<4I', 0xfeedfacf, 0x100000c, 0, 2 if executable else 6) + name.encode())
        return path

    def relocator(self, **kw):
        return m.Relocator(self.engine, search_dirs=[self.src], **kw)

    def test_recursive_relocation_and_source_unchanged(self):
        app = self.binary('app', True); lib = self.binary('lib/a.dylib')
        self.edges[app] = ['@loader_path/lib/a.dylib', '/usr/lib/libSystem.B.dylib']
        self.cmds[app] = [('LC_RPATH', '@loader_path/lib')]
        self.cmds[lib] = [('LC_ID_DYLIB', str(lib))]
        before = m.sha(lib)
        r = self.relocator(); out = r.copy(app, self.engine / 'bin/app')
        self.assertEqual(before, m.sha(lib)); self.assertEqual(len(r.machos), 2)
        self.assertTrue(any(c[1] == '-delete_rpath' for c in self.calls))
        self.assertTrue(out.is_file())

    def test_cross_instance_shared_signed_dependency(self):
        lib = self.binary('a.dylib'); self.cmds[lib] = [('LC_ID_DYLIB', str(lib))]
        dest = self.engine / 'deps-metal-arm64/lib/a.dylib'
        a = self.relocator(); a.copy(lib, dest); a.sign_copied()
        digest = m.sha(dest); calls = len(self.calls)
        b = self.relocator(); b.copy(lib, dest)
        self.assertEqual(m.sha(dest), digest); self.assertEqual(len(self.calls), calls)
        self.assertEqual(b.machos, [dest])

    def test_different_source_same_bytes_collision(self):
        a = self.binary('a'); b = self.binary('b'); b.write_bytes(a.read_bytes())
        dest = self.engine / 'a'; self.relocator().copy(a, dest)
        with self.assertRaisesRegex(ValueError, 'collision'): self.relocator().copy(b, dest)

    def test_unknown_raw_identical_is_admitted(self):
        src = self.binary('a'); out = self.engine / 'a'; out.write_bytes(src.read_bytes())
        self.assertEqual(self.relocator().copy(src, out), out)

    def test_unknown_modified_rejected(self):
        src = self.binary('a'); out = self.engine / 'a'; out.write_bytes(b'other')
        with self.assertRaisesRegex(ValueError, 'differs'): self.relocator().copy(src, out)

    def test_external_edit_prevents_reuse_and_sign(self):
        src = self.binary('a'); r = self.relocator(); out = r.copy(src, self.engine / 'a')
        out.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'changed'): r.copy(src, out)
        with self.assertRaisesRegex(ValueError, 'changed'): r.sign_copied()

    def test_fat_and_newer_os_rejected(self):
        src = self.binary('a')
        with patch.object(m._original, 'run', return_value='arm64 x86_64'):
            with self.assertRaisesRegex(ValueError, 'ARM64'): self.relocator().copy(src, self.engine / 'a')
        with patch.object(m, 'minimum', return_value=(28, 0)):
            with self.assertRaisesRegex(ValueError, 'minimum'): self.relocator().copy(src, self.engine / 'a')

    def test_exact_absolute_missing_no_basename_fallback(self):
        app = self.binary('app', True); self.binary('missing.dylib')
        self.edges[app] = [str(self.src / 'absent/missing.dylib')]
        with self.assertRaisesRegex(ValueError, 'unresolved exact'): self.relocator().copy(app, self.engine / 'app')

    def test_rpath_full_tail_and_ambiguity(self):
        app = self.binary('app', True); self.binary('one/deep/lib.dylib'); self.binary('two/deep/lib.dylib')
        self.edges[app] = ['@rpath/deep/lib.dylib']
        self.cmds[app] = [('LC_RPATH', '@loader_path/one'), ('LC_RPATH', '@loader_path/two')]
        with self.assertRaisesRegex(ValueError, 'ambiguous'): self.relocator().copy(app, self.engine / 'app')
        self.cmds[app] = [('LC_RPATH', '@loader_path/one')]
        self.assertTrue(self.relocator().copy(app, self.engine / 'app').exists())

    def test_package_manager_file_symlink_resolves_only_into_admitted_root(self):
        app = self.binary('app', True); lib = self.binary('cellar/a.dylib')
        links = self.src / 'lib'; links.mkdir(); (links/'a.dylib').symlink_to(lib)
        self.edges[app] = ['@rpath/a.dylib']; self.cmds[app] = [('LC_RPATH','@loader_path/lib')]
        r = m.Relocator(self.engine, search_dirs=[self.src,links])
        r.copy(app,self.engine/'app')
        self.assertIn(self.engine/'deps-metal-arm64/lib/a.dylib',r.machos)

    def test_package_manager_symlink_outside_all_roots_still_refused(self):
        app = self.binary('app', True); outside = self.root/'external.dylib'
        outside.write_bytes(app.read_bytes())
        links = self.src/'lib'; links.mkdir(); (links/'a.dylib').symlink_to(outside)
        self.edges[app] = ['@rpath/a.dylib']; self.cmds[app] = [('LC_RPATH','@loader_path/lib')]
        with self.assertRaisesRegex(ValueError,'escapes'):
            m.Relocator(self.engine,search_dirs=[self.src,links]).copy(app,self.engine/'app')

    def test_executable_relative_dependency_and_escape(self):
        app = self.binary('bin/app', True); lib = self.binary('lib/a'); dep = self.binary('lib/b')
        self.edges[app] = ['@loader_path/../lib/a']; self.edges[lib] = ['@executable_path/../lib/b']
        self.assertTrue(self.relocator().copy(app, self.engine / 'bin/app').exists())
        other = self.binary('bad', True); self.edges[other] = ['@loader_path/../../escape']
        with self.assertRaisesRegex(ValueError, 'escapes'): self.relocator().copy(other, self.engine / 'bad')

    def test_no_executable_context_rejected(self):
        lib = self.binary('lib'); self.edges[lib] = ['@executable_path/other']
        with self.assertRaisesRegex(ValueError, 'context'): self.relocator().copy(lib, self.engine / 'lib')

    def test_inherited_runpath_stack(self):
        app = self.binary('app', True); a = self.binary('a'); b = self.binary('b'); c = self.binary('nested/c')
        self.edges[app] = [str(a)]; self.edges[a] = [str(b)]; self.edges[b] = ['@rpath/c']
        self.cmds[a] = [('LC_RPATH', '@loader_path/nested')]
        r = self.relocator(); r.copy(app, self.engine / 'app')
        self.assertIn(self.engine / 'deps-metal-arm64/lib/c', r.machos)

    def test_bare_loader_runpath_uses_actual_owner_directory(self):
        app = self.binary('app', True); lib = self.binary('lib/a'); other = self.binary('lib/b')
        self.edges[app] = [str(lib)]; self.edges[lib] = ['@rpath/b']
        self.cmds[lib] = [('LC_RPATH','@loader_path')]
        r = self.relocator(); r.copy(app,self.engine/'app')
        self.assertIn(self.engine/'deps-metal-arm64/lib/b',r.machos)

    def test_bare_executable_runpath_requires_and_uses_real_main(self):
        app = self.binary('app', True); lib = self.binary('lib/a'); other = self.binary('b')
        self.edges[app] = [str(lib)]; self.edges[lib] = ['@rpath/b']
        self.cmds[lib] = [('LC_RPATH','@executable_path')]
        r = self.relocator(); r.copy(app,self.engine/'app')
        self.assertIn(self.engine/'deps-metal-arm64/lib/b',r.machos)
        with self.assertRaisesRegex(ValueError,'context'):
            self.relocator()._expand('@executable_path',lib,None)

    def test_false_system_traversal_rejected(self):
        app = self.binary('app', True); self.edges[app] = ['/usr/lib/../../tmp/not-system']
        with self.assertRaisesRegex(ValueError, 'system'): self.relocator().copy(app, self.engine / 'app')

    def test_destination_link_and_outside_rejected(self):
        lib = self.binary('lib'); (self.engine / 'link').symlink_to(self.src, target_is_directory=True)
        with self.assertRaises(ValueError): self.relocator().copy(lib, self.engine / 'link/lib')
        with self.assertRaises(ValueError): self.relocator().copy(lib, self.root / 'outside')

    def test_explicit_binding_and_library_subdir(self):
        app = self.binary('app', True); lib = self.binary('libbbgpu.dylib'); dep = self.binary('net.dylib')
        self.edges[app] = [str(lib), str(dep)]
        r = self.relocator(bindings={lib: self.engine / 'bin/gpu-metal/libbbgpu.dylib'}, library_subdir='deps-net/lib')
        r.copy(app, self.engine / 'app')
        self.assertIn(self.engine / 'deps-net/lib/net.dylib', r.machos)
        self.assertIn(self.engine / 'bin/gpu-metal/libbbgpu.dylib', r.machos)

    def test_dependency_cycle_bounded(self):
        a = self.binary('a'); b = self.binary('b'); self.edges[a] = [str(b)]; self.edges[b] = [str(a)]
        r = self.relocator(bindings={a: self.engine / 'a'})
        r.copy(a, self.engine / 'a'); self.assertEqual(len(r.machos), 2)

    def test_ready_cycle_revisit_finishes_and_rechecks(self):
        a = self.binary('a'); b = self.binary('b')
        self.edges[a] = [str(b)]; self.edges[b] = [str(a)]
        r = self.relocator(bindings={a: self.engine / 'a'})
        r.copy(a, self.engine / 'a')
        with patch.object(r, '_copy', wraps=r._copy) as visits:
            r.copy(a, self.engine / 'a')
        self.assertEqual(visits.call_count, 3)
        (self.engine / 'deps-metal-arm64/lib/b').write_bytes(b'external')
        with self.assertRaisesRegex(ValueError, 'changed'): r.copy(a, self.engine / 'a')

    def test_diamond_traversal_and_metadata_are_linear(self):
        app = self.binary('app', True)
        levels = [[self.binary(f'level{level}-{side}') for side in range(2)] for level in range(8)]
        self.edges[app] = [str(p) for p in levels[0]]
        for previous, following in zip(levels, levels[1:]):
            for node in previous: self.edges[node] = [str(p) for p in following]
        count = 1 + sum(map(len, levels))
        r = self.relocator()
        with patch.object(r, '_copy', wraps=r._copy) as visits:
            r.copy(app, self.engine / 'app')
        self.assertLessEqual(visits.call_count, 2 * count)
        self.assertEqual(m.loads.call_count, count)
        self.assertEqual(m.commands.call_count, count)
        self.assertEqual(m._original.run.call_count, count)
        with patch.object(r, '_copy', wraps=r._copy) as visits:
            r.copy(app, self.engine / 'app')
        self.assertLessEqual(visits.call_count, 2 * count)
        self.assertEqual(m._original.run.call_count, count)

    def test_different_ancestor_context_not_hidden_by_memo(self):
        app = self.binary('app', True); a = self.binary('a'); b = self.binary('b')
        common = self.binary('common'); middle = self.binary('middle')
        self.binary('left/lib'); self.binary('right/lib')
        self.edges[app] = [str(a), str(b)]
        self.edges[a] = self.edges[b] = [str(common)]
        self.edges[common] = [str(middle)]; self.edges[middle] = ['@rpath/lib']
        self.cmds[a] = [('LC_RPATH', '@loader_path/left')]
        self.cmds[b] = [('LC_RPATH', '@loader_path/right')]
        with self.assertRaisesRegex(ValueError, 'collision'):
            self.relocator().copy(app, self.engine / 'app')

    def test_source_edit_after_metadata_cache_still_rejected(self):
        source = self.binary('app', True); r = self.relocator(); dest = self.engine / 'app'
        r.copy(source, dest)
        with source.open('ab') as stream: stream.write(b'external')
        with self.assertRaisesRegex(ValueError, 'collision'): r.copy(source, dest)

    def framework(self):
        root = self.root / 'prefix/Frameworks/Python.framework/Versions/3.11'
        (root / 'Resources/Python.app/Contents/MacOS').mkdir(parents=True)
        exe = root / 'Resources/Python.app/Contents/MacOS/Python'; exe.write_bytes(b'python')
        (root / 'bin').mkdir(); (root / 'bin/python3.11').symlink_to('../Resources/Python.app/Contents/MacOS/Python')
        (root / 'Python').write_bytes(b'library'); (root / 'lib/python3.11').mkdir(parents=True)
        (root.parents[3] / 'LICENSE').write_text('license')
        return root

    def test_provider_three_forms_and_wrong_version(self):
        root = self.framework()
        for provider in (root, root / 'bin/python3.11', root / 'Resources/Python.app/Contents/MacOS/Python'):
            self.assertEqual(m.python_provider(provider)['framework_root'], root)
        with self.assertRaises(ValueError): m.python_provider(root / 'Python')

    def wrapper(self):
        root = self.framework()
        wrapper = root / 'bin/python3.11'; wrapper.unlink(); wrapper.write_bytes(b'distinct CLI')
        interpreter = root / 'Resources/Python.app/Contents/MacOS/Python'
        library = root / 'Python'
        self.edges[wrapper] = [str(library), '/usr/lib/libSystem.B.dylib']
        self.edges[interpreter] = [str(library)]
        return root, wrapper, interpreter, library

    def test_different_wrapper_same_exact_framework(self):
        root, wrapper, interpreter, library = self.wrapper()
        for provider in (root, wrapper, interpreter):
            result = m.python_provider(provider)
            self.assertEqual(result['provider'], wrapper)
            self.assertEqual(result['interpreter'], interpreter)
            self.assertEqual(result['library'], library)

    def test_different_wrapper_wrong_framework_rejected(self):
        root, wrapper, interpreter, library = self.wrapper()
        wrong = self.src / 'other-framework/Python'; wrong.parent.mkdir(); wrong.write_bytes(library.read_bytes())
        for executable in (wrapper, interpreter):
            self.edges[executable] = [str(wrong)]
            with self.assertRaisesRegex(ValueError, 'library mismatch'): m.python_provider(root)
            self.edges[executable] = [str(library)]

    def test_different_wrapper_ambiguous_or_unproven_edge_rejected(self):
        root, wrapper, interpreter, library = self.wrapper()
        for edges in ([], ['@rpath/Python'], [str(library), str(library)]):
            self.edges[wrapper] = edges
            with self.assertRaisesRegex(ValueError, 'single exact'): m.python_provider(root)

    def test_stdlib_allowlist_no_links(self):
        root = self.framework() / 'lib/python3.11'
        for name in ('json/__init__.py', 'site-packages/no.py', 'test/no.py', '__pycache__/no.pyc', 'a.py', 'lib-dynload/a.so', 'data.txt'):
            path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text('x')
        self.assertEqual([str(x[1]) for x in m.stdlib_files(root)], ['a.py', 'json/__init__.py', 'lib-dynload/a.so'])
        (root / 'linked.py').symlink_to(root / 'a.py')
        with self.assertRaisesRegex(ValueError, 'links'): m.stdlib_files(root)

    def test_embed_reuses_completion_and_only_signs_python_closure(self):
        framework = self.framework(); selection = m.python_provider(framework)
        interpreter, library = selection['interpreter'], selection['library']
        interpreter.write_bytes(struct.pack('<4I', 0xfeedfacf, 0x100000c, 0, 2) + b'interpreter')
        library.write_bytes(struct.pack('<4I', 0xfeedfacf, 0x100000c, 0, 6) + b'framework')
        self.edges[interpreter] = ['@executable_path/../../../../Python']
        stdlib = selection['stdlib']; (stdlib / 'a.py').write_text('pass')
        (stdlib / 'lib-dynload').mkdir()
        safe = stdlib / 'lib-dynload/safe.so'; safe.write_bytes(struct.pack('<4I', 0xfeedfacf, 0x100000c, 0, 8))
        skipped = stdlib / 'lib-dynload/skipped.so'; skipped.write_bytes(b'not read as native')
        self.edges[skipped] = ['/unavailable/not-system']
        r = m.Relocator(self.engine, search_dirs=[self.src, framework],
                        bindings={library: self.engine / 'python/lib/Python'})
        app = self.binary('app', True); r.copy(app, self.engine / 'app')
        def complete(root, **kwargs):
            self.assertEqual(kwargs['writable_stage'], self.engine)
            self.assertIn(kwargs['module_name'], ('_sqlite3', '_hashlib'))
            return {'files': [dict(path='lib/python3.11/lib-dynload/safe.so',
                                   sha256=m.sha(root / 'lib/python3.11/lib-dynload/safe.so'))]}
        with patch.object(m._complete, 'complete', side_effect=complete) as call:
            result = m.embed_python(self.engine, framework / 'bin/python3.11', relocate=r.copy)
        self.assertEqual(call.call_count, 2)
        self.assertEqual(result['omitted_extensions'], ['lib-dynload/skipped.so'])
        signed = [Path(c[-1]) for c in self.calls if '--force' in c]
        self.assertNotIn(self.engine / 'app', signed)
        self.assertIn(self.engine / 'python/lib/Python', signed)
        self.assertTrue((self.engine / 'python/lib/python3.11/a.py').is_file())

    def test_embed_requires_binding_and_fresh_output(self):
        framework = self.framework()
        r = m.Relocator(self.engine, search_dirs=[framework])
        with self.assertRaisesRegex(ValueError, 'binding'): m.embed_python(self.engine, framework, relocate=r)
        (self.engine / 'python').mkdir()
        with self.assertRaisesRegex(ValueError, 'fresh'): m.embed_python(self.engine, framework, relocate=r)


if __name__ == '__main__': unittest.main()
