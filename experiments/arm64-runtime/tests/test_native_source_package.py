"""Source/GUI admission controls; real assembly is separately exercised."""
from pathlib import Path
import json, os, plistlib, sys, tempfile, unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import package_native_from_source as package

class SourcePackage(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.a=self.root/'source-a';self.b=self.root/'source-b'
        self.revision='a'*40
        for root in (self.a,self.b):
            for name in ('src/runtime.cpp','vendor/net/messages.proto','vendor/net/build.py',
                         'config/gpu-adapter-headers.json','targets.cmake',
                         'tools/generate_native.py','tools/generate_gpu.py','tests/metadata_layout.cpp'):
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('authored '+name+'\n')
        self.app=self.root/'source-shell.app';(self.app/'Contents/MacOS').mkdir(parents=True)
        resources=self.app/'Contents/Resources';resources.mkdir();(resources/'BBLauncher_BBLauncher.bundle').mkdir()
        (self.app/'Contents/MacOS/bbport').write_bytes(b'author fixture, not a native image')
        (resources/'AppIcon.icns').write_bytes(b'author icon fixture')
        self.info=dict(CFBundleIdentifier=package.FORMAL_ID,BBSourceCommit=self.revision,CFBundleShortVersionString='0.2.51')
        self.save_info()
    def save_info(self):
        (self.app/'Contents/Info.plist').write_bytes(plistlib.dumps(self.info))
    def test_native_source_identity_is_independent_of_checkout_path(self):
        result=package.native_source_identity(self.a,self.b)
        self.assertGreater(result['files'],5);self.assertEqual(len(result['sha256']),64)
    def test_proto_generator_and_native_body_changes_are_not_hidden(self):
        for name in ('src/runtime.cpp','vendor/net/messages.proto','vendor/net/build.py','config/gpu-adapter-headers.json'):
            path=self.b/name;old=path.read_bytes();path.write_bytes(old+b'changed\n')
            with self.assertRaisesRegex(ValueError,'native source differs'):package.native_source_identity(self.a,self.b)
            path.write_bytes(old)
    def test_bytecode_cache_is_not_compiled_source(self):
        cache=self.b/'vendor/net/__pycache__';cache.mkdir();(cache/'build.cpython-311.pyc').write_bytes(b'local transient cache')
        package.native_source_identity(self.a,self.b)
    def test_native_source_link_cannot_escape_the_selected_tree(self):
        target=self.root/'external.cpp';target.write_text('external')
        path=self.b/'src/runtime.cpp';path.unlink();path.symlink_to(target)
        with self.assertRaisesRegex(ValueError,'link escaped'):package.native_source_identity(self.a,self.b)
    def test_original_bare_shell_is_read_only_and_has_no_native_side_effect(self):
        before=(self.app/'Contents/Info.plist').read_bytes()
        with patch('subprocess.Popen',side_effect=AssertionError('no native call in shell metadata admission')):
            package.shell_info(self.app,self.revision,'0.2.51')
        self.assertEqual((self.app/'Contents/Info.plist').read_bytes(),before)
    def test_qa_preferences_or_existing_engine_cannot_be_a_source_shell(self):
        for key in ('BBPreviewDataPath','BBPreviewPreferencesPath','BBQALaunchReceipt','BBRepoPath'):
            self.info[key]='/private/old-data';self.save_info()
            with self.assertRaisesRegex(ValueError,'private GUI'):package.shell_info(self.app,self.revision,'0.2.51')
            self.info.pop(key)
        self.save_info();(self.app/'Contents/Resources/bbport').mkdir()
        with self.assertRaisesRegex(ValueError,'bare source-built'):package.shell_info(self.app,self.revision,'0.2.51')
    def test_wrong_gui_revision_version_or_identifier_refused(self):
        for key,value in [('BBSourceCommit','b'*40),('CFBundleShortVersionString','0.2.50'),('CFBundleIdentifier','io.github.bbport.optimized.options-qa.example')]:
            old=self.info[key];self.info[key]=value;self.save_info()
            with self.assertRaises(ValueError):package.shell_info(self.app,self.revision,'0.2.51')
            self.info[key]=old
    def test_packaging_restores_caller_umask_on_failure(self):
        seen=[];previous=os.umask(0o002)
        def fail(**kwargs):
            current=os.umask(0o022);seen.append(current);raise RuntimeError('author stop')
        try:
            with patch.object(package,'_assemble',side_effect=fail):
                with self.assertRaises(RuntimeError):package.assemble(author=True)
            current=os.umask(0o002);self.assertEqual(current,0o002);self.assertEqual(seen,[0o022])
        finally:os.umask(previous)

if __name__=='__main__':unittest.main()
