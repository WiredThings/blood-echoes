"""Author-only bundle inputs; mocked Mach-O tools, no compiler/native/GPU execution."""
from pathlib import Path
import os
import plistlib
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools/macos'))
import native_metal as native


class NativeGpuBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='authored-native-gpu-bundle-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.engine = self.root/'engine'
        self.host = self.engine/'bin/gpu-metal-arm64'
        self.host.mkdir(parents=True)
        self.source = self.host/'bb-gpu'
        self.source.write_bytes(struct.pack('<4I',0xfeedfacf,0x100000c,0,2)+b'authored main payload')
        self.source.chmod(0o755)
        self.library = self.host/'libbbgpu.dylib'
        self.library.write_bytes(b'authored same-build library')
        self.cover = self.root/'author-cover.icns'
        self.cover.write_bytes(b'authored cover asset')
        self.addCleanup(patch.stopall)
        self.arch = patch.object(native.subprocess,'check_output',return_value='arm64\n').start()
        self.commands = patch.object(native.subprocess,'run').start()
        self.loads = patch.object(native,'loads',return_value=['/usr/lib/libSystem.B.dylib','@loader_path/libbbgpu.dylib']).start()
        patch.object(native,'GAME_ICON',self.cover).start()

    def build(self, **kwargs):
        return native.create_native_gpu_bundle(self.engine,self.source,version='0.2.99',**kwargs)

    def test_default_bundle_preserves_original_gpu_identity_icon_and_source(self):
        original = self.source.read_bytes()
        library = self.library.read_bytes()
        executable = self.build()
        bundle = self.engine/'bin/Bloodborne Metal.app'
        self.assertEqual(executable,bundle/'Contents/MacOS/bb-gpu')
        info = plistlib.loads((bundle/'Contents/Info.plist').read_bytes())
        self.assertEqual(info['CFBundleIdentifier'],'io.github.bbport.optimized.metal-game')
        self.assertEqual(info['CFBundleExecutable'],'bb-gpu')
        self.assertEqual(info['CFBundleName'],'Bloodborne')
        self.assertEqual(info['CFBundleIconFile'],'AppIcon')
        self.assertEqual(info['LSMinimumSystemVersion'],'27.0')
        self.assertEqual(info['NSPrincipalClass'],'NSApplication')
        self.assertTrue(info['NSHighResolutionCapable'])
        self.assertNotIn('LSUIElement',info)
        self.assertEqual((bundle/'Contents/Resources/AppIcon.icns').read_bytes(),self.cover.read_bytes())
        self.assertEqual(self.source.read_bytes(),original)
        self.assertEqual(self.library.read_bytes(),library)
        expected='@loader_path/'+os.path.relpath(self.library,executable.parent)
        calls=[call.args[0]for call in self.commands.call_args_list]
        self.assertEqual(calls[0],['install_name_tool','-change','@loader_path/libbbgpu.dylib',expected,str(executable)])
        self.assertEqual(calls[-2:], [['codesign','--force','--sign','-',str(executable)],['codesign','--force','--sign','-',str(bundle)]])
        self.loads.assert_called_once_with(self.source)

    def test_fresh_custom_qa_identity_does_not_edit_frontend_or_outer_metadata(self):
        frontend=self.engine/'bin/Bloodborne Game.app/Contents'
        frontend.mkdir(parents=True)
        info=frontend/'Info.plist';info.write_bytes(b'authored existing frontend metadata')
        target=self.engine/'bin/Author QA.app'
        executable=self.build(bundle=target,bundle_id='io.example.author.qa',minimum_os_major=28)
        gpu=plistlib.loads((target/'Contents/Info.plist').read_bytes())
        self.assertEqual(gpu['CFBundleIdentifier'],'io.example.author.qa')
        self.assertEqual(gpu['CFBundleShortVersionString'],'0.2.99')
        self.assertEqual(gpu['CFBundleVersion'],'0299')
        self.assertEqual(gpu['LSMinimumSystemVersion'],'28.0')
        self.assertEqual(info.read_bytes(),b'authored existing frontend metadata')
        self.assertEqual(executable.name,'bb-gpu')

    def test_original_stage_uses_helper_and_keeps_frontend_policy(self):
        build=self.root/'normal-build';build.mkdir()
        (build/'bb-gpu').write_bytes(self.source.read_bytes());(build/'bb-gpu').chmod(0o755)
        (build/'libbbgpu.dylib').write_bytes(b'authored library')
        frontend=self.engine/'bin/Bloodborne Game.app/Contents';frontend.mkdir(parents=True)
        (frontend/'Info.plist').write_bytes(plistlib.dumps({'LSUIElement': False, 'authored': 'keep'}))
        with patch.object(native,'loads',return_value=['/usr/lib/libSystem.B.dylib']), \
             patch.object(native,'commands',return_value=[]), \
             patch.object(native,'minimum',return_value=(26,0)), \
             patch.object(native,'validate_native_payload',return_value=3), \
             patch.object(native,'create_native_gpu_bundle',wraps=native.create_native_gpu_bundle) as helper:
            result=native.stage_arm64_metal(self.engine,build,self.root/'empty-prefix','0.2.99')
        helper.assert_called_once_with(self.engine,self.engine/'bin/gpu-metal-arm64/bb-gpu',version='0.2.99',minimum_os_major=27)
        self.assertTrue((self.engine/'bin/Bloodborne Metal.app/Contents/MacOS/bb-gpu').is_file())
        info=plistlib.loads((frontend/'Info.plist').read_bytes())
        self.assertTrue(info['LSUIElement']);self.assertEqual(info['authored'],'keep')
        self.assertEqual(result['minimum_os_major'],27)

    def test_existing_bundle_is_never_removed_or_overwritten(self):
        target=self.engine/'bin/Bloodborne Metal.app';target.mkdir()
        sentinel=target/'keep';sentinel.write_bytes(b'authored old app')
        with self.assertRaisesRegex(ValueError,'Fresh'):
            self.build()
        self.assertEqual(sentinel.read_bytes(),b'authored old app')
        self.commands.assert_not_called()

    def test_existing_symlink_and_escaping_bundle_are_rejected(self):
        target=self.engine/'bin/Bloodborne Metal.app';target.symlink_to(self.root/'missing')
        with self.assertRaisesRegex(ValueError,'Fresh'):
            self.build()
        self.assertTrue(target.is_symlink())
        with self.assertRaisesRegex(ValueError,'Fresh'):
            self.build(bundle=self.root/'outside.app')
        self.assertFalse((self.root/'outside.app').exists())

    def test_external_and_symlinked_source_are_rejected(self):
        external=self.root/'external';external.write_bytes(self.source.read_bytes());external.chmod(0o755)
        with self.assertRaisesRegex(ValueError,'inside the engine'):
            native.create_native_gpu_bundle(self.engine,external,version='0.2.99')
        link=self.host/'linked';link.symlink_to(self.source)
        with self.assertRaisesRegex(ValueError,'inside the engine'):
            native.create_native_gpu_bundle(self.engine,link,version='0.2.99')
        self.arch.assert_not_called()

    def test_architecture_and_dylib_disguised_as_main_are_rejected(self):
        for cpu,kind in [(0x1000007,2),(0x100000c,6)]:
            self.source.write_bytes(struct.pack('<4I',0xfeedfacf,cpu,0,kind))
            with self.assertRaisesRegex(ValueError,'ARM64 native main'):
                self.build()
        self.assertFalse((self.engine/'bin/Bloodborne Metal.app').exists())
        self.commands.assert_not_called()

    def test_fat_or_wrong_lipo_result_is_rejected(self):
        self.arch.return_value='x86_64 arm64\n'
        with self.assertRaisesRegex(ValueError,'ARM64 native main'):
            self.build()
        self.commands.assert_not_called()

    def test_unadmitted_and_escaping_dependencies_fail_before_creation(self):
        outside=self.root/'outside.dylib';outside.write_bytes(b'authored external')
        for ref in ['@rpath/libbbgpu.dylib',str(outside),'@loader_path/missing.dylib',
                    '@loader_path/'+os.path.relpath(outside,self.host)]:
            with self.subTest(ref=ref):
                self.loads.return_value=[ref]
                with self.assertRaises((ValueError,FileNotFoundError)):
                    self.build()
                self.assertFalse((self.engine/'bin/Bloodborne Metal.app').exists())
        self.commands.assert_not_called()

    def test_source_dependency_escaping_by_symlink_is_rejected(self):
        self.library.unlink();outside=self.root/'external.dylib';outside.write_bytes(b'authored')
        self.library.symlink_to(outside)
        with self.assertRaisesRegex(ValueError,'leaves the package'):
            self.build()
        self.assertFalse((self.engine/'bin/Bloodborne Metal.app').exists())

    def test_bundle_parent_alias_and_invalid_metadata_are_rejected(self):
        alias=self.engine/'alias';alias.symlink_to(self.engine/'bin',target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'canonical'):
            self.build(bundle=alias/'QA.app')
        for args in [dict(minimum_os_major=True),dict(minimum_os_major=0),dict(bundle_id='')]:
            with self.subTest(args=args):
                with self.assertRaises(ValueError):self.build(**args)
        self.assertFalse((self.engine/'bin/Bloodborne Metal.app').exists())


if __name__=='__main__':
    unittest.main()
