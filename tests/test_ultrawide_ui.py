from paths import ROOT
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

sys.path.insert(0, str(ROOT / 'scripts'))
import ultrawide_ui as ui


def gfx(payload=b''):
    # Minimal real container: RECT, frame rate/count, optional metadata tag, End.
    body = b'\x08\x00' + b'\x00\x18\x01\x00'
    if payload:
        body += struct.pack('<HI', (77 << 6) | 63, len(payload)) + payload
    body += b'\x00\x00'
    return b'GFX\x0b' + struct.pack('<I', len(body) + 8) + body


def sfo():
    keys = b'TITLE_ID\x00APP_VER\x00'
    data = b'CUSA03173\x0001.09\x00'
    return (struct.pack('<5I', 0x46535000, 0x101, 52, 52 + len(keys), 2)
            + struct.pack('<HHIII', 0, 0x204, 10, 10, 0)
            + struct.pack('<HHIII', 9, 0x204, 6, 6, 10) + keys + data)


class UIFilesTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.game = self.root / 'Game'
        self.menu = self.game / 'dvdroot_ps4/menu'
        self.menu.mkdir(parents=True)
        (self.game / 'sce_sys').mkdir()
        (self.game / 'sce_sys/param.sfo').write_bytes(sfo())
        (self.game / 'eboot.bin').write_bytes(b'fixture')
        self.old = gfx(b'previous UI, possibly another mod')
        self.new = gfx(b'new UI, any aspect ratio')
        (self.menu / 'fe.gfx').write_bytes(self.old)
        (self.menu / 'untouched.gfx').write_bytes(gfx(b'untouched'))
        self.mod = self.root / '21x9 UI Fixes/menu'
        self.mod.mkdir(parents=True)
        (self.mod / 'fe.gfx').write_bytes(self.new)
        self.data = self.root / 'data'
        self.install = ui.Installation(self.game, self.data)

    def enable(self):
        _, files = ui.source_folder(self.mod)
        self.install.swap(True, files)

    def test_accepts_partial_packs_any_name_and_compressed_gfx(self):
        for folder in (self.mod, self.mod.parent, self.root):
            self.assertEqual(ui.source_folder(folder)[1], {'fe.gfx': self.new})
        compressed = b'CFX' + self.new[3:8] + zlib.compress(self.new[8:])
        ui.valid_gfx(compressed, 'fe.gfx')
        (self.mod / 'fe.gfx').write_bytes(compressed)
        self.enable()
        self.assertEqual((self.menu / 'fe.gfx').read_bytes(), compressed)

    def test_enable_disable_reenable_preserves_exact_previous_files(self):
        self.enable()
        backup, manifest = self.install.load()
        self.assertEqual((self.menu / 'fe.gfx').read_bytes(), self.new)
        self.assertEqual((backup / 'original/fe.gfx').read_bytes(), self.old)
        self.assertTrue(self.install.inspect(str(self.mod))['enabled'])
        self.install.swap(False)
        self.assertEqual((self.menu / 'fe.gfx').read_bytes(), self.old)
        self.assertFalse(self.install.inspect(str(self.mod))['enabled'])
        self.enable()
        self.assertEqual((backup / 'original/fe.gfx').read_bytes(), self.old)
        self.assertNotEqual(self.install.load()[0], backup)
        self.install.swap(False)
        self.assertEqual((self.menu / 'fe.gfx').read_bytes(), self.old)
        self.assertEqual((self.menu / 'untouched.gfx').read_bytes(), gfx(b'untouched'))

    def test_added_files_removed_on_disable_and_non_gfx_ignored(self):
        (self.mod / 'new.gfx').write_bytes(gfx(b'new'))
        (self.mod / 'eboot.bin').write_bytes(b'do not install')
        self.enable()
        self.assertTrue((self.menu / 'new.gfx').exists())
        self.assertFalse((self.menu / 'eboot.bin').exists())
        self.install.swap(False)
        self.assertFalse((self.menu / 'new.gfx').exists())

    def test_download_missing_does_not_prevent_disable(self):
        self.enable()
        (self.mod / 'fe.gfx').unlink()
        status = self.install.inspect(str(self.mod))
        self.assertTrue(status['canDisable'])
        self.assertFalse(status['blocksPlay'])
        self.install.swap(False)
        self.assertEqual((self.menu / 'fe.gfx').read_bytes(), self.old)

    def test_bad_or_empty_files_rejected_before_any_change(self):
        for data in (b'', b'not gfx', self.new[:-1], self.new[:15] + b'corrupt'):
            (self.mod / 'fe.gfx').write_bytes(data)
            with self.assertRaises(ValueError):
                self.enable()
            self.assertEqual((self.menu / 'fe.gfx').read_bytes(), self.old)
        self.assertFalse((self.install.state / 'current.json').exists())

    def test_source_and_destination_symlinks_rejected(self):
        original = self.mod / 'fe.gfx'
        original.unlink()
        original.symlink_to(self.menu / 'fe.gfx')
        with self.assertRaises(ValueError): self.enable()
        original.unlink()
        original.write_bytes(self.new)
        (self.menu / 'fe.gfx').unlink()
        target = self.root / 'outside'
        target.write_bytes(self.old)
        (self.menu / 'fe.gfx').symlink_to(target)
        with self.assertRaises(ValueError): self.enable()
        self.assertEqual(target.read_bytes(), self.old)

    def test_other_mod_changes_are_not_overwritten_on_restore(self):
        self.enable()
        third = gfx(b'third party changes')
        (self.menu / 'fe.gfx').write_bytes(third)
        with self.assertRaises(ValueError): self.install.swap(False)
        self.assertEqual((self.menu / 'fe.gfx').read_bytes(), third)
        self.assertTrue(self.install.inspect(str(self.mod))['blocksPlay'])

    def test_corrupt_backup_prevents_restore(self):
        self.enable()
        backup, _ = self.install.load()
        (backup / 'original/fe.gfx').write_bytes(b'broken')
        with self.assertRaises(ValueError): self.install.swap(False)
        self.assertEqual((self.menu / 'fe.gfx').read_bytes(), self.new)

    def test_io_failure_rolls_back_all_files(self):
        (self.mod / 'new.gfx').write_bytes(gfx(b'new'))
        replace = self.install.replace
        calls = 0
        def failing(*args):
            nonlocal calls
            calls += 1
            if calls == 2: raise OSError('disk full')
            return replace(*args)
        with patch.object(self.install, 'replace', failing):
            with self.assertRaises(OSError): self.enable()
        self.assertEqual((self.menu / 'fe.gfx').read_bytes(), self.old)
        self.assertFalse((self.menu / 'new.gfx').exists())
        self.assertFalse(self.install.inspect(str(self.mod))['needsRecovery'])

    def test_interrupted_swap_recovers_on_disable(self):
        (self.mod / 'new.gfx').write_bytes(gfx(b'new'))
        replace = self.install.replace
        calls = 0
        def interrupted(*args):
            nonlocal calls
            calls += 1
            if calls == 2: raise SystemExit('simulated interruption')
            return replace(*args)
        with patch.object(self.install, 'replace', interrupted):
            with self.assertRaises(SystemExit): self.enable()
        recovered = ui.Installation(self.game, self.data)
        self.assertTrue(recovered.inspect(str(self.mod))['needsRecovery'])
        recovered.swap(False)
        self.assertEqual((self.menu / 'fe.gfx').read_bytes(), self.old)
        self.assertFalse((self.menu / 'new.gfx').exists())

    def test_identical_original_and_mod_still_toggle_correctly(self):
        (self.mod / 'fe.gfx').write_bytes(self.old)
        self.enable()
        self.assertTrue(self.install.inspect(str(self.mod))['enabled'])
        self.assertIn('already installed', self.install.inspect(str(self.mod))['message'])
        self.install.swap(False)
        self.assertFalse(self.install.inspect(str(self.mod))['enabled'])
        self.assertIn('already contained', self.install.inspect(str(self.mod))['message'])

    def test_disabling_again_does_not_overwrite_later_manual_restoration(self):
        (self.mod / 'fe.gfx').write_bytes(self.old)
        self.enable()
        self.install.swap(False)
        restored = gfx(b'original UI restored from an earlier backup')
        (self.menu / 'fe.gfx').write_bytes(restored)
        self.install.swap(False)
        self.assertEqual((self.menu / 'fe.gfx').read_bytes(), restored)
        self.assertNotIn('already contained', self.install.inspect(str(self.mod))['message'])

    def test_backup_is_per_game_and_originals_can_change_when_disabled(self):
        self.enable()
        self.install.swap(False)
        updated = gfx(b'user changes after disable')
        (self.menu / 'fe.gfx').write_bytes(updated)
        self.enable()
        self.install.swap(False)
        self.assertEqual((self.menu / 'fe.gfx').read_bytes(), updated)
        other = self.root / 'Other Game'
        import shutil
        shutil.copytree(self.game, other)
        self.assertNotEqual(ui.Installation(other, self.data).state, self.install.state)

    def test_an_existing_operation_holds_the_lock(self):
        with self.install.locked():
            with self.assertRaisesRegex(ValueError, 'Another UI operation'):
                self.enable()


class OptionalUIStartupTests(unittest.TestCase):
    setUp = UIFilesTests.setUp
    enable = UIFilesTests.enable
    # Use the same synthetic game/UI data; exercise helper startup, not game code.
    def unavailable_menu(self, kind):
        target = self.root / 'original-menu'
        self.menu.rename(target)
        if kind == 'linked':
            self.menu.symlink_to(target, target_is_directory=True)
        elif kind == 'file':
            self.menu.write_bytes(b'not a directory')
        elif kind == 'linked-parent':
            parent = self.game / 'dvdroot_ps4'
            parent.rmdir()
            parent.symlink_to(self.root / 'outside-dvdroot', target_is_directory=True)
        return target

    def test_cli_inspection_without_mod_does_not_block_missing_menu(self):
        self.unavailable_menu('missing')
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/ultrawide_ui.py'),
                                 'inspect', '--game', str(self.game), '--data', str(self.data)],
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(json.loads(result.stdout)['blocksPlay'])
        self.assertFalse(self.data.exists())

    def test_linked_menu_without_managed_install_does_not_block_play(self):
        target = self.unavailable_menu('linked')
        fresh = ui.Installation(self.game, self.data)
        self.assertFalse(fresh.inspect('')['blocksPlay'])
        self.assertEqual((target / 'fe.gfx').read_bytes(), self.old)

    def test_inactive_backup_does_not_block_later_linked_menu(self):
        self.enable()
        self.install.swap(False)
        self.unavailable_menu('linked')
        self.assertFalse(ui.Installation(self.game, self.data).inspect('')['blocksPlay'])

    def test_missing_menu_with_active_mod_still_blocks_play(self):
        self.enable()
        target = self.unavailable_menu('missing')
        status = ui.Installation(self.game, self.data).inspect('')
        self.assertTrue(status['blocksPlay'])
        self.assertTrue(status['canDisable'])
        self.assertIn(str(self.menu), status['message'])
        with self.assertRaisesRegex(ValueError, 'Missing folder'):
            self.install.swap(False)
        self.assertEqual((target / 'fe.gfx').read_bytes(), self.new)
        target.rename(self.menu)
        self.install.swap(False)
        self.assertEqual((self.menu / 'fe.gfx').read_bytes(), self.old)

    def test_linked_menu_with_active_mod_cannot_restore_through_link(self):
        self.enable()
        target = self.unavailable_menu('linked')
        self.assertTrue(ui.Installation(self.game, self.data).inspect('')['blocksPlay'])
        with self.assertRaisesRegex(ValueError, 'Symbolic links'):
            self.install.swap(False)
        self.assertEqual((target / 'fe.gfx').read_bytes(), self.new)

    def test_unusable_menu_prevents_install_but_not_ordinary_play(self):
        for kind, message in [('missing', 'Missing folder'), ('linked', 'Symbolic links'),
                              ('file', 'Not a folder'), ('linked-parent', 'Symbolic links')]:
            with self.subTest(kind=kind):
                # Independent fixture for each destination shape.
                fixture = UIFilesTests()
                fixture.setUp()
                try:
                    OptionalUIStartupTests.unavailable_menu(fixture, kind)
                    install = ui.Installation(fixture.game, fixture.data)
                    status = install.inspect(str(fixture.mod))
                    self.assertFalse(status['blocksPlay'])
                    self.assertTrue(status['folderValid'])
                    self.assertFalse(status['canEnable'])
                    self.assertIn(message, status['message'])
                    self.assertIn(str(fixture.game / 'dvdroot_ps4'), status['message'])
                    with self.assertRaisesRegex(ValueError, message):
                        _, files = ui.source_folder(fixture.mod)
                        install.swap(True, files)
                    self.assertFalse((install.state / 'current.json').exists())
                    self.assertEqual((fixture.root / 'original-menu/fe.gfx').read_bytes(), fixture.old)
                finally:
                    fixture.doCleanups()
