# SPDX-License-Identifier: GPL-2.0-or-later
"""Compile the exact early production scalar branch without Device/Storage code."""
from pathlib import Path
import argparse
p=argparse.ArgumentParser();p.add_argument('--source-root',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
s=(a.source_root/'src/game_pso_preparer.mm').read_text();main=s[s.index('int main('):];branch=main[main.index(' if(argc==2'):main.index(' // This executable')]
assert 'Metal::Device'not in branch and'Storage::'not in branch
out='#include <cstdio>\n#include <string_view>\n#include "video_core/renderer_metal/mtl_fixed_helper_seeds.h"\nint main(int argc,char**argv){\n'+branch+'return 64;\n}\n';a.output.write_text(out)
