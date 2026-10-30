#!/usr/bin/env python3
"""tools/recomp/pointers.py EBOOT_BIN OUT: the image offsets base-relative relocations put into the
game's data (vtables, function pointer tables), one per line, for tools/recomp/scan.c: code they
point to is code something calls, functions the unwind tables may not list. Also writes
pointer_slots.tsv next to OUT: where each of them is stored (slot, target), for
tools/recomp/label.py: consecutive slots are a vtable or a table of functions."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ps4elf  # noqa: E402

R_X86_64_RELATIVE = 8


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    m = ps4elf.module(pathlib.Path(sys.argv[1]))
    targets = sorted({addend for _, kind, _, addend in m['relocs'] if kind == R_X86_64_RELATIVE})
    pathlib.Path(sys.argv[2]).write_text(''.join(f'{t:#x}\n' for t in targets))
    slots = sorted((slot, addend) for slot, kind, _, addend in m['relocs'] if kind == R_X86_64_RELATIVE)
    pathlib.Path(sys.argv[2]).with_name('pointer_slots.tsv').write_text(
        'slot\ttarget\n' + ''.join(f'{s:#x}\t{t:#x}\n' for s, t in slots))
    print(f'{len(targets)} distinct pointer targets')


if __name__ == '__main__':
    main()
