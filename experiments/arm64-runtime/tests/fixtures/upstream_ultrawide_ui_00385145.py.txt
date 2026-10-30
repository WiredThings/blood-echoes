#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Install a user's own .gfx UI files, with immutable backups and recoverable swaps.

No aspect-ratio restrictions, downloads, or game assets. The user chooses the layout.
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import uuid
import zlib

MAX_FILE = 16 * 1024 * 1024


def directory(path):
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f'Missing folder or unsupported link: {path.name}')
    return path


def read_file(path, expected=None):
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError(f'Not a regular file: {path.name}')
    if path.stat().st_size > MAX_FILE:
        raise ValueError(f'UI file is too large: {path.name}')
    data = path.read_bytes()
    if expected is not None and fingerprint(data) != expected:
        raise ValueError(f'File changed or backup damaged: {path.name}')
    return data


def fingerprint(data):
    return [len(data), hashlib.sha256(data).hexdigest()]


def valid_gfx(data, name):
    """Validate the GFx/SWF container, lengths and tag bounds, without executing it."""
    if len(data) < 12 or data[:3] not in (b'GFX', b'CFX', b'FWS', b'CWS'):
        raise ValueError(f'{name} is not a valid .gfx UI file.')
    length = int.from_bytes(data[4:8], 'little')
    if not 12 <= length <= MAX_FILE:
        raise ValueError(f'{name} has an invalid UI file length.')
    body = data[8:]
    if data[:1] == b'C':
        decoder = zlib.decompressobj()
        body = decoder.decompress(body, length - 8 + 1)
        if not decoder.eof or decoder.unused_data:
            raise ValueError(f'{name} has damaged compressed UI data.')
    if len(body) != length - 8:
        raise ValueError(f'{name} is incomplete or damaged.')
    bits = body[0] >> 3
    if bits == 0:
        raise ValueError(f'{name} has an invalid UI header.')
    offset = (5 + 4 * bits + 7) // 8 + 4  # RECT, frame rate and frame count
    while offset + 2 <= len(body):
        tag = int.from_bytes(body[offset:offset + 2], 'little')
        offset += 2
        size = tag & 63
        if size == 63:
            if offset + 4 > len(body):
                break
            size = int.from_bytes(body[offset:offset + 4], 'little')
            offset += 4
        offset += size
        if offset > len(body):
            break
        if tag >> 6 == 0 and size == 0 and offset == len(body):
            return
    raise ValueError(f'{name} has incomplete UI records.')


def source_folder(path):
    if not path:
        raise ValueError('Choose the extracted UI mod folder (optional).')
    root = directory(Path(path).expanduser().resolve(strict=True))
    candidates = [root, root / 'menu', root / 'dvdroot_ps4/menu']
    children = list(root.iterdir())
    if len(children) <= 100:
        candidates += [p / 'menu' for p in children if p.is_dir() and not p.is_symlink() and not p.name.startswith('.')]
    matches = list(dict.fromkeys(p for p in candidates if p.is_dir() and not p.is_symlink()
                                and any(f.suffix.lower() == '.gfx' for f in p.iterdir())))
    if len(matches) != 1:
        raise ValueError('Choose one extracted UI mod folder (or its menu folder) containing .gfx files.')
    menu = matches[0]
    files = {}
    names = set()
    for path in sorted(menu.iterdir()):
        if path.suffix.lower() != '.gfx':
            continue
        if path.name.casefold() in names:
            raise ValueError(f'Duplicate UI filename: {path.name}')
        names.add(path.name.casefold())
        data = read_file(path)
        valid_gfx(data, path.name)
        files[path.name] = data
    if sum(len(data) for data in files.values()) > 64 * 1024 * 1024:
        raise ValueError('The selected UI pack is too large.')
    return menu, files


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path, data, mode=0o600):
    fd, tmp = tempfile.mkstemp(prefix='.bb-ui-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as file:
            os.fchmod(file.fileno(), mode)
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp, path)
        sync_directory(path.parent)
    finally:
        Path(tmp).unlink(missing_ok=True)


def write_json(path, obj):
    atomic_write(path, (json.dumps(obj, indent=2) + '\n').encode())


def game_folder(path):
    game = Path(path).expanduser().resolve(strict=True)
    from prepare import sfo
    if not (game / 'eboot.bin').is_file():
        raise ValueError('Select the game folder containing eboot.bin.')
    info = sfo((game / 'sce_sys/param.sfo').read_bytes())
    if info.get('TITLE_ID') not in ('CUSA00207', 'CUSA00208', 'CUSA00900', 'CUSA01363', 'CUSA03173', 'CUSA03023') or info.get('APP_VER') != '01.09':
        raise ValueError('UI installation requires a verified Bloodborne 1.09 game folder.')
    directory(game / 'dvdroot_ps4')
    directory(game / 'dvdroot_ps4/menu')
    return game


class Installation:
    def __init__(self, game, data):
        self.game = game_folder(game)
        self.menu = self.game / 'dvdroot_ps4/menu'
        key = hashlib.sha256(os.fsencode(str(self.game))).hexdigest()
        self.state = Path(data).expanduser().resolve() / 'ui-mod-backups' / key

    @contextlib.contextmanager
    def locked(self):
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory(self.state.parent)
        directory(self.state)
        fd = os.open(self.state / '.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('Another UI operation is using this game folder. Try again when it finishes.')
            yield
        finally:
            os.close(fd)

    def current(self, name):
        directory(self.game / 'dvdroot_ps4')
        directory(self.menu)
        path = self.menu / name
        return fingerprint(read_file(path)) if path.exists() or path.is_symlink() else None

    def load(self):
        pointer = self.state / 'current.json'
        if not pointer.exists():
            return None, None
        directory(self.state)
        generation = json.loads(read_file(pointer)).get('generation', '')
        if not re.fullmatch('[0-9a-f]{32}', generation):
            raise ValueError('Invalid UI backup record.')
        backup = directory(self.state / generation)
        manifest = json.loads(read_file(backup / 'manifest.json'))
        if manifest.get('version') != 1 or manifest.get('game') != str(self.game) or not isinstance(manifest.get('files'), dict) or not manifest['files']:
            raise ValueError('The UI backup record does not match this game folder.')
        names = set()
        for name, kinds in manifest['files'].items():
            if Path(name).name != name or '/' in name or '\\' in name or not name.lower().endswith('.gfx') or name.casefold() in names:
                raise ValueError('Invalid filename in UI backup record.')
            names.add(name.casefold())
            for kind in ('original', 'mod'):
                directory(backup / kind)
                if kinds[kind] is not None:
                    read_file(backup / kind / name, kinds[kind])
        return backup, manifest

    def check_current(self, manifest):
        current = {}
        for name, kinds in manifest['files'].items():
            value = self.current(name)
            if value not in (kinds['original'], kinds['mod']):
                raise ValueError(f'{name} changed after the UI mod was installed. It was left untouched; restore that file or resolve the other mod first.')
            current[name] = 'original' if value == kinds['original'] else 'mod'
        return current

    def backup(self, files):
        existing = {p.name.casefold(): p.name for p in self.menu.iterdir()}
        generation = uuid.uuid4().hex
        backup = self.state / generation
        backup.mkdir(mode=0o700)
        manifest = {'version': 1, 'game': str(self.game), 'files': {}, 'enabled': False, 'pending': None}
        try:
            for kind in ('original', 'mod'):
                (backup / kind).mkdir(mode=0o700)
            for source_name, data in files.items():
                name = existing.get(source_name.casefold(), source_name)
                path = self.menu / name
                original = read_file(path) if path.exists() or path.is_symlink() else None
                if original is not None:
                    atomic_write(backup / 'original' / name, original)
                atomic_write(backup / 'mod' / name, data)
                manifest['files'][name] = {'original': fingerprint(original) if original is not None else None,
                                           'mod': fingerprint(data),
                                           'mode': stat.S_IMODE(path.stat().st_mode) if original is not None else 0o644}
            write_json(backup / 'manifest.json', manifest)
            write_json(self.state / 'current.json', {'generation': generation})
        except BaseException:
            # If the pointer was not published this is an incomplete backup, never active.
            # Keep it for diagnosis; no game files have been changed yet.
            raise
        return backup, manifest

    def replace(self, backup, manifest, name, kind):
        value = self.current(name)
        kinds = manifest['files'][name]
        if value not in (kinds['original'], kinds['mod']):
            raise ValueError(f'{name} changed during the UI swap; it was left untouched.')
        if kinds[kind] is None:
            (self.menu / name).unlink(missing_ok=True)
            sync_directory(self.menu)
        else:
            data = read_file(backup / kind / name, kinds[kind])
            atomic_write(self.menu / name, data, kinds['mode'])

    def swap(self, enabled, files=None):
        with self.locked():
            backup, manifest = self.load()
            if enabled:
                if manifest and (manifest['enabled'] or manifest.get('pending')):
                    raise ValueError('Disable the current UI fix before enabling another one.')
                # A fresh, immutable snapshot of exactly what is installed before this enable.
                backup, manifest = self.backup(files)
            elif manifest is None or not (manifest['enabled'] or manifest.get('pending')):
                # Already disabled: do not replay an old snapshot over later user changes.
                return
            before = self.check_current(manifest)
            previous = dict(manifest)
            manifest['pending'] = {'before': before, 'target': 'mod' if enabled else 'original'}
            write_json(backup / 'manifest.json', manifest)
            try:
                for name in manifest['files']:
                    self.replace(backup, manifest, name, 'mod' if enabled else 'original')
                manifest.update(enabled=enabled, pending=None)
                write_json(backup / 'manifest.json', manifest)
            except Exception:
                try:
                    for name, kind in before.items():
                        self.replace(backup, manifest, name, kind)
                    write_json(backup / 'manifest.json', previous)
                except Exception:
                    pass  # durable journal remains; Disable retries restoration
                raise

    def inspect(self, folder):
        result = dict(folderValid=False, enabled=False, managed=False, needsRecovery=False,
                      canEnable=False, canDisable=False, blocksPlay=False, message='')
        try:
            _, manifest = self.load()
            if manifest:
                result['managed'] = True
                result['enabled'] = manifest['enabled']
                result['needsRecovery'] = manifest.get('pending') is not None
                result['canDisable'] = result['enabled'] or result['needsRecovery']
                # An inactive backup must not prevent unrelated changes made after disabling.
                if result['canDisable']:
                    self.check_current(manifest)
                    result['blocksPlay'] = result['needsRecovery']
        except (OSError, ValueError, KeyError, TypeError) as error:
            result.update(message=str(error), blocksPlay=True)
            return result
        try:
            _, files = source_folder(folder)
            result['folderValid'] = True
            result['canEnable'] = not result['needsRecovery'] and not result['enabled']
            result['message'] = (f'UI fix enabled · {len(manifest["files"])} files; previous files backed up.' if result['enabled'] else
                                 f'{len(files)} valid .gfx files · ready to enable.')
        except (OSError, ValueError, KeyError, TypeError, zlib.error) as error:
            result['message'] = str(error)
            if result['enabled']:
                result['message'] = 'UI fix enabled. Download folder unavailable; you can still disable and restore.'
        identical = manifest and all(v['original'] == v['mod'] for v in manifest['files'].values())
        if identical and not result['canDisable']:
            try:
                identical = all(self.current(name) == v['original'] for name, v in manifest['files'].items())
            except (OSError, ValueError):
                identical = False  # Later user changes are not ours to restore or diagnose.
        if result['needsRecovery']:
            result['message'] = 'An interrupted UI swap needs recovery. Disable the UI fix to restore the backups.'
        elif identical:
            result['message'] = ('UI fix enabled. These files were already installed; disabling will keep that layout.'
                                 if result['enabled'] else
                                 'UI fix disabled. The restored files already contained this UI layout.')
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('inspect', 'enable', 'disable'))
    parser.add_argument('--game', required=True)
    parser.add_argument('--data', required=True)
    parser.add_argument('--folder', default='')
    args = parser.parse_args()
    install = Installation(args.game, args.data)
    if args.action == 'enable':
        _, files = source_folder(args.folder)
        install.swap(True, files)
    elif args.action == 'disable':
        install.swap(False)
    print(json.dumps(install.inspect(args.folder)))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, zlib.error) as error:
        print(json.dumps({'error': str(error)}))
        sys.exit(1)
