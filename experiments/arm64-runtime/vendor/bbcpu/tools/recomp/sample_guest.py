#!/usr/bin/env python3
"""tools/recomp/sample_guest.py SAMPLE.txt JIT_MAP [THREAD_SUBSTRING] [--base HEX] [--top N]: CPU samples of
translated guest code (macOS `sample`, the JIT's unnamed code) by guest block, from a BB_JIT_MAP
file of the same run. Self time per block, busiest first; --base: the image base, to print image
offsets (objdump of out/eboot.elf takes them)."""
import bisect
import re
import sys
from collections import Counter


def load_map(path):
    entries = []
    for line in open(path):
        parts = line.split()
        if len(parts) == 3:
            entries.append((int(parts[0], 16), int(parts[1], 16), int(parts[2], 16)))
    entries.sort()
    return entries, [e[0] for e in entries]


def guest_samples(sample, jit_map, want=None):
    """Self samples per guest block in translated code: (Counter guest rip -> samples, samples on
    the selected threads, samples in translated code, samples not in the map)."""
    entries, starts = load_map(jit_map)
    lines = open(sample, errors='replace').read().split('\n')
    start = next(i for i, l in enumerate(lines) if l.startswith('Call graph:'))
    selected = False
    stack = []  # [depth, count, child_sum, address or None]
    self_by_address = Counter()
    total = 0

    def close_to(depth):
        nonlocal total
        while stack and stack[-1][0] >= depth:
            d, count, child, address = stack.pop()
            if selected and address is not None and count - child > 0:
                self_by_address[address] += count - child
            if stack:
                stack[-1][2] += count

    for line in lines[start + 1:]:
        if line.startswith('Total number in stack') or line.startswith('Sort by top of stack'):
            break
        m = re.match(r'^(\s*)([+!:| ]*)(\d+)\s+(.*)$', line)
        if not m:
            continue
        depth = len(m.group(1)) + len(m.group(2))
        count = int(m.group(3))
        frame = m.group(4)
        if re.match(r'Thread_\d+', frame):
            close_to(0)
            selected = want is None or want in frame
            if selected:
                total += count
            continue
        close_to(depth)
        address = None
        if frame.startswith('???') and '<unknown binary>' in frame:
            a = re.search(r'\[0x([0-9a-f]+)\]', frame)
            if a:
                address = int(a.group(1), 16)
        stack.append([depth, count, 0, address])
    close_to(0)

    by_block = Counter()
    unmapped = 0
    for address, n in self_by_address.items():
        i = bisect.bisect_right(starts, address) - 1
        if i >= 0 and entries[i][0] <= address < entries[i][1]:
            by_block[entries[i][2]] += n
        else:
            unmapped += n
    return by_block, total, sum(self_by_address.values()), unmapped


def main():
    args = [a for a in sys.argv[1:]]
    base = 0
    top = 40
    if '--base' in args:
        i = args.index('--base')
        base = int(args[i + 1], 16)
        del args[i:i + 2]
    if '--top' in args:
        i = args.index('--top')
        top = int(args[i + 1])
        del args[i:i + 2]
    by_block, total, jit_total, unmapped = guest_samples(args[0], args[1], args[2] if len(args) > 2 else None)
    print(f'{total} samples on the selected threads, {jit_total} in translated code '
          f'({unmapped} not in the map), {len(by_block)} guest blocks')
    for guest, n in by_block.most_common(top):
        offset = f' +{guest - base:#x}' if base else ''
        print(f'{n:7d} {100.0 * n / max(jit_total, 1):5.1f}%  {guest:#x}{offset}')


if __name__ == '__main__':
    main()
