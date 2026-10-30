#!/usr/bin/env python3
"""tools/recomp/ps4elf.py: reading the game's executable, eboot.bin (a PS4 SELF, or the plain ELF some
dumpers write): its ELF image, and its dynamic tables (symbols, imports, relocations).

  ps4elf.py EBOOT_BIN OUT_ELF    writes the ELF image (out/eboot.elf, what the recompiler reads)

From bbport (https://github.com/deadinside28/bloodborne_pc, GPL-2.0+): scripts/prepare.py
(parse_self, parse_elf, span, unpack), scripts/link_libc.py (encode_id) and scripts/link_modules.py
(module), copied unchanged so that the recompiler stands alone."""
import hashlib
import pathlib
import struct
import sys


def span(data, offset, size):
    if offset < 0 or size < 0 or offset + size > len(data):
        raise ValueError(f"out of bounds: {offset:#x}+{size:#x} / {len(data):#x}")
    return data[offset:offset + size]

def unpack(fmt, data, offset):
    return struct.unpack(fmt, span(data, offset, struct.calcsize(fmt)))

def parse_elf(data):
    """A plain ELF (some dumpers write the executable without the SELF wrapper)."""
    header = unpack('<16sHHIQQQIHHHHHH', data, 0)
    if header[0][:7] != b'\x7fELF\x02\x01\x01' or header[2] != 62:
        raise ValueError("expected little-endian x86-64 ELF")
    if header[9] != 56 or not 0 < header[10] < 256:
        raise ValueError("unsupported program headers")
    ph = [dict(zip(('type', 'flags', 'offset', 'vaddr', 'paddr', 'filesz', 'memsz', 'align'),
                   unpack('<IIQQQQQQ', data, header[5] + i * 56)))
          for i in range(header[10])]
    end = max(header[5] + header[10] * 56, max(p['offset'] + p['filesz'] for p in ph))
    if end > 512 * 1024 * 1024:
        raise ValueError("probe image exceeds 512 MiB limit")
    return bytearray(span(data, 0, end)), header, ph, [], []

def parse_self(data):
    if span(data, 0, 4) == b'\x7fELF':
        return parse_elf(data)
    if span(data, 0, 4) != b'O\x15=\x1d':
        raise ValueError("expected PS4 SELF or ELF (eboot.bin is neither: an encrypted or damaged dump?)")
    count, = unpack('<H', data, 24)
    base = 32 + count * 32
    header = unpack('<16sHHIQQQIHHHHHH', data, base)
    if header[0][:7] != b'\x7fELF\x02\x01\x01' or header[2] != 62:
        raise ValueError("expected little-endian x86-64 ELF")
    if header[9] != 56 or not 0 < header[10] < 256:
        raise ValueError("unsupported program headers")
    ph = [dict(zip(('type', 'flags', 'offset', 'vaddr', 'paddr', 'filesz', 'memsz', 'align'),
                   unpack('<IIQQQQQQ', data, base + header[5] + i * 56)))
          for i in range(header[10])]
    end = max(p['offset'] + p['filesz'] for p in ph)
    if end > 512 * 1024 * 1024:
        raise ValueError("probe image exceeds 512 MiB limit")
    elf = bytearray(end)
    header_end = header[5] + header[10] * 56
    elf[:header_end] = span(data, base, header_end)
    covered = [(0, header_end)]
    segments = []
    for i in range(count):
        flags, off, size, mem = unpack('<QQQQ', data, 32 + i * 32)
        segments.append(dict(flags=hex(flags), offset=off, size=size))
        if not flags & 0x800:
            continue
        if flags & 10:
            raise ValueError("encrypted/compressed SELF segment is unsupported")
        index = (flags >> 20) & 4095
        if index >= len(ph):
            raise ValueError("invalid segment index")
        p = ph[index]
        if size != p['filesz'] or size != mem:
            raise ValueError("unsupported blocked segment layout")
        elf[p['offset']:p['offset'] + size] = span(data, off, size)
        covered.append((p['offset'], p['offset'] + size))
    missing = []
    for i, p in enumerate(ph):
        if p['filesz'] and not any(a <= p['offset'] and p['offset'] + p['filesz'] <= z
                                  for a, z in covered):
            missing.append(i)
            if p['type'] in (1, 2, 0x61000000, 0x61000010):
                raise ValueError(f"required segment {i} is unavailable")
    return elf, header, ph, segments, missing

def encode_id(value):
    alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+-'
    result = alphabet[value & 63]
    value >>= 6
    while value:
        result = alphabet[value & 63] + result
        value >>= 6
    return result

def module(path):
    source = path.read_bytes()
    elf, header, ph, _, missing = parse_self(source)
    dp = next(p for p in ph if p['type'] == 2)
    dynamic = []
    for pos in range(dp['offset'], dp['offset'] + dp['filesz'], 16):
        tag, value = unpack('<QQ', elf, pos)
        if not tag:
            break
        dynamic.append((tag, value))
    tags = dict(dynamic)
    lp = next(p for p in ph if p['type'] == 0x61000000)
    blob = span(elf, lp['offset'], lp['filesz'])
    strings = span(blob, tags[0x61000035], tags[0x61000037])

    def string(offset):
        span(strings, offset, 1)
        return strings[offset:strings.index(0, offset)].decode('ascii')
    libraries, modules = {}, {}
    for tag, value in dynamic:
        if tag in (0x61000013, 0x61000015, 0x6100000d, 0x6100000f):
            table = libraries if tag in (0x61000013, 0x61000015) else modules
            key = encode_id(value >> 48)
            identity = (string(value & 0xffffffff), (value >> 32) & 65535)
            if key in table and table[key] != identity:
                raise ValueError('conflicting module/library IDs')
            table[key] = identity

    def identity(name):
        parts = name.split('#')
        if len(parts) != 3:
            return None  # plain names such as module_start are not linkable
        n, lib, mod = parts
        return (n, libraries[lib], modules[mod])
    symbols = []
    table = span(blob, tags[0x61000039], tags[0x6100003f])
    for pos in range(0, len(table), 24):
        n, info, other, section, value, size = unpack('<IBBHQQ', table, pos)
        name = string(n)
        symbols.append(dict(name=name, type=info & 15, binding=info >> 4, section=section,
                            value=value, size=size, identity=identity(name) if name else None))
    relocations = []
    for ot, st in ((0x61000029, 0x6100002d), (0x6100002f, 0x61000031)):
        table = span(blob, tags[ot], tags[st])
        for pos in range(0, len(table), 24):
            target, info, addend = unpack('<QQq', table, pos)
            relocations.append((target, info & 0xffffffff, info >> 32, addend))
    return dict(elf=elf, header=header, ph=ph, tags=tags, symbols=symbols, relocs=relocations,
                identity=identity, libraries=libraries, modules=modules,
                sha256=hashlib.sha256(source).hexdigest(), missing=missing)


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    elf, *_ = parse_self(pathlib.Path(sys.argv[1]).read_bytes())
    pathlib.Path(sys.argv[2]).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(sys.argv[2]).write_bytes(elf)
    print(f'{sys.argv[2]}: {len(elf)} bytes')


if __name__ == '__main__':
    main()
