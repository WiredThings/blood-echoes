#!/usr/bin/env python3
"""tools/recomp/profile.py SAMPLE.txt JIT_MAP LOG [THREAD_SUBSTRING] [--out OUTDIR] [--top N]: CPU time of
the game's own functions (docs/RECOMPILATION.md). Translated-code self samples from a macOS `sample`
file (tools/recomp/sample_guest.py) are given to the functions of OUTDIR/functions.tsv
(tools/recomp/scan.c) and their modules (tools/recomp/label.py); LOG is the run's log, for the image
base ("Image: game image at"). A run: SAMPLE=1 tools/mac_bench.sh NAME BB_JIT_MAP=FILE.

Writes OUTDIR/profile.tsv (address, samples, share of translated code, module, source) and prints the
time per module and the busiest functions."""
import bisect
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sample_guest import guest_samples  # noqa: E402


def option(args, name, default):
    if name in args:
        i = args.index(name)
        value = args[i + 1]
        del args[i:i + 2]
        return value
    return default


def main():
    args = sys.argv[1:]
    out = option(args, '--out', 'out/recomp')
    top = int(option(args, '--top', '40'))
    sample, jit_map, log = args[:3]
    thread = args[3] if len(args) > 3 else None
    base = None
    for line in open(log, errors='replace'):
        m = re.match(r'Image: game image at (0x[0-9a-f]+)', line)
        if m:
            base = int(m.group(1), 16)
            break
    if base is None:
        sys.exit(f'{log}: no "Image: game image at" line')

    starts, sizes, modules, sources = [], [], [], []
    for line in open(f'{out}/modules.tsv'):
        if line.startswith('address'):
            continue
        address, size, module, source, _ = line.rstrip('\n').split('\t')
        starts.append(int(address, 16))
        sizes.append(int(size))
        modules.append(module)
        sources.append(source)

    by_block, total, jit_total, unmapped = guest_samples(sample, jit_map, thread)
    by_function = collections.Counter()
    by_module = collections.Counter()
    outside = 0
    for rip, n in by_block.items():
        offset = rip - base
        i = bisect.bisect_right(starts, offset) - 1
        if i >= 0 and offset < starts[i] + sizes[i]:
            by_function[i] += n
            by_module[modules[i]] += n
        else:
            outside += n
    share = lambda n: 100.0 * n / max(jit_total, 1)
    print(f'{total} samples on the selected threads, {jit_total} in translated code; '
          f'{outside} outside the function table, {unmapped} not in the JIT map')
    for module, n in by_module.most_common():
        print(f'  {module:12} {n:7} {share(n):5.1f}%')
    ranked = by_function.most_common()
    covered = 0
    for count, (_, n) in enumerate(ranked, 1):
        covered += n
        if covered * 2 >= jit_total:
            print(f'{len(by_function)} functions ran; the busiest {count} take half the time')
            break
    print(f'{"samples":>8} {"share":>6}  {"offset":>9} {"size":>6}  module')
    for i, n in ranked[:top]:
        print(f'{n:8} {share(n):5.1f}%  {starts[i]:#9x} {sizes[i]:6}  {modules[i]} {sources[i]}')
    with open(f'{out}/profile.tsv', 'w') as f:
        f.write('address\tsamples\tshare\tmodule\tsource\n')
        for i, n in ranked:
            f.write(f'{starts[i]:#x}\t{n}\t{share(n):.3f}\t{modules[i]}\t{sources[i]}\n')


if __name__ == '__main__':
    main()
