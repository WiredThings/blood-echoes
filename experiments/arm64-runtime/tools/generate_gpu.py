#!/usr/bin/env python3
"""Use exact declarations of the existing private Metal frontend, never guessed ABI sizes."""
from pathlib import Path
import argparse,json,re
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source-root',type=Path,default=Path(__file__).resolve().parents[1])
parser.add_argument('--output-dir',type=Path)
parser.add_argument('--header-manifest',type=Path)
options=parser.parse_args()
P=options.source_root.resolve(strict=True);R=P/'vendor/metal_api'
G=(options.output_dir or P/'generated').absolute();G.mkdir(parents=True,exist_ok=True)
if G!=G.resolve(strict=True):raise ValueError('generated output must not be linked')
meta=json.loads((options.header_manifest or P/'config/gpu-adapter-headers.json').read_text())
for header,names in meta.items():
 path=R/header
 if Path(header).is_absolute()or '..'in Path(header).parts or not path.resolve(strict=True).is_relative_to(R.resolve(strict=True)):
  raise ValueError('GPU adapter header must belong to selected renderer')
 if header not in ('shadps4/core/libraries/gnmdriver/gnmdriver.h','shadps4/core/libraries/videoout/video_out.h')or not isinstance(names,list)or any(not isinstance(n,str)or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*',n)for n in names):
  raise ValueError('invalid declared GPU adapter header surface')

headers=['#include "core/guest_cpu/hle_call_adapter.h"'];cases=[]
for header,names in meta.items():
 headers.append('#include "'+str(R/header)+'"')
 ns='Libraries::GnmDriver' if 'gnmdriver' in header else 'Libraries::VideoOut'
 for n in names:cases.append((n,ns))
headers.append('#include "'+str(R/'shadps4/core/libraries/kernel/equeue.h')+'"')
headers.append('#include "'+str(R/'shadps4/core/libraries/kernel/time.h')+'"')
text=(R/'shadps4/core/libraries/kernel/equeue.cpp').read_text();decl=[]
for m in re.finditer(r'([\w:*]+\s+PS4_SYSV_ABI\s+(\w+)\([^{};]*?\))\s*\{',text,re.S):
 decl.append(m[1]+';');cases.append((m[2],'Libraries::Kernel'))
headers+=['namespace Libraries::Kernel {','\n'.join(decl),'}','using namespace Core::GuestCpu;','std::shared_ptr<HleCallAdapter> bb_arm_gpu_adapter(std::string_view name,uintptr_t address) {']
for n,ns in cases:headers.append(f'if(name=="{n}") return MakeHleCallAdapter(reinterpret_cast<decltype(&{ns}::{n})>(address));')
headers+=['return {};','}'];(G/'gpu_adapters.cpp').write_text('\n'.join(headers)+'\n');print('Typed GPU adapters:',len(cases))
