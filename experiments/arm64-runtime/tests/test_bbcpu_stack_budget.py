"""Check the actual ARM64 execution frame, not C source/inline assumptions."""
import argparse,re,subprocess
from pathlib import Path

def execution_frame(text):
    if '<_bbcpu_jit_run>:'not in text:raise ValueError('actual ARM64 JIT symbol absent')
    total=0
    for line in text.splitlines():
        match=re.search(r'\bsub\s+sp,\s*sp,\s*#(0x[0-9a-f]+|[0-9]+)(?:,\s*lsl\s*#(0x[0-9a-f]+|[0-9]+))?',line)
        if match:total+=int(match[1],0)<<(int(match[2],0)if match[2]else 0)
        match=re.search(r'\bstp\s+[^\[]+\[sp,\s*#-(0x[0-9a-f]+|[0-9]+)\]!',line)
        if match:total+=int(match[1],0)
    if not 0<total<=1024:raise ValueError(f'JIT execution frame {total} exceeds 1 KiB; keep compiler/report scratch outlined')
    return total

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--probe',type=Path,required=True);p.add_argument('--objdump',type=Path,required=True);a=p.parse_args()
    r=subprocess.run([str(a.objdump),'--disassemble-symbols=_bbcpu_jit_run','--no-show-raw-insn',str(a.probe)],capture_output=True,text=True,timeout=15,check=True)
    print('actual ARM64 JIT execution frame:',execution_frame(r.stdout),'bytes; cold compiler scratch excluded')
if __name__=='__main__':main()
