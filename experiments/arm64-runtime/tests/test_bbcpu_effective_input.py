#!/usr/bin/env python3
"""Original guarded patch semantics and canonical effective CPU input, no game."""
import argparse, hashlib, json, struct, subprocess, tempfile
from pathlib import Path
from run_bbcpu_authored_static import fixture

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--preparer',type=Path,required=True);p.add_argument('--scan',type=Path,required=True);a=p.parse_args()
 root=Path(tempfile.mkdtemp(prefix='blood-echoes-bbcpu-input-')).resolve()
 elf=root/'source.elf';elf.write_bytes(fixture());data=elf.read_bytes()
 image=bytearray(24576);image[:8192]=data[:8192];image[16384:24576]=data[8192:16384]
 boot=root/'linked.bin'
 header=struct.pack('<8s6Q',b'BBPROBE2',len(image),4096,2,1,0,0)
 segments=struct.pack('<6Q',0,8192,5,16384,8192,4)
 reloc=struct.pack('<4Q',16768,0,4096,0)
 boot.write_bytes(header+segments+reloc+image)
 original=(sha(boot),sha(elf));sequence=0
 def run(*extra,good=True,source=boot,source_elf=elf):
  nonlocal sequence
  sequence+=1;out=root/f'output-{sequence:02}'
  result=subprocess.run([a.preparer,'--boot',source,'--elf',source_elf,'--out',out,*extra],capture_output=True,text=True,timeout=20)
  (root/f'run-{sequence:02}.log').write_text(result.stdout+result.stderr)
  assert (result.returncode==0)==good,result.stdout+result.stderr
  assert (out/'effective-input.json').exists()==good
  return out,json.loads((out/'effective-input.json').read_text())if good else None
 def patch(path,edits):
  path.write_bytes(b'BBPATCH2'+struct.pack('<2Q',0x400000,len(edits))+b''.join(struct.pack('<2Q',at,len(value))+value for at,value in edits));return path
 one,base=run();two,again=run()
 assert base['effective_identity']==again['effective_identity']
 assert sha(one/'effective.elf')==sha(two/'effective.elf')==base['effective_elf_sha256']
 assert (one/'pointers.txt').read_text()=='0x1000\n'
 # Stable code Mod changes the code identity; removing it recovers the old
 # identity even when a different patch file/path expresses the original code.
 changed=patch(root/'change.bin',[(4100,b'\x29')]);_,mod=run('--patches',changed)
 assert mod['effective_identity']!=base['effective_identity']
 restored=patch(root/'restore.bin',[(4100,b'\x01')]);_,back=run('--patches',restored)
 assert back['effective_identity']==base['effective_identity']
 # Complete pointer writes use the original launcher's rebase semantics. Its
 # actual relative value is part of the identity, not the worker's ASLR base.
 pointer=patch(root/'pointer.bin',[(16768,struct.pack('<Q',0x401010))]);pointer_out,pointer_id=run('--patches',pointer)
 assert pointer_id['effective_identity']!=base['effective_identity']
 assert (pointer_out/'pointers.txt').read_text()=='0x1010\n'
 # Incomplete pointer writes and incorrect guarded originals fail without
 # publishing a usable descriptor or changing the source.
 partial=patch(root/'partial.bin',[(16769,b'\x01')]);run('--patches',partial,good=False)
 guarded=root/'startup.bin';guarded.write_bytes(b'BBSTART1'+struct.pack('<I',1)+struct.pack('<QI',4100,1)+b'\x01\x29')
 _,start=run('--startup',guarded);assert start['effective_identity']==mod['effective_identity']
 bad=root/'bad-startup.bin';bad.write_bytes(guarded.read_bytes()[:-2]+b'\x77\x29');run('--startup',bad,good=False)
 # ELF metadata is checked against all main boot segments, including flags.
 wrong=root/'wrong.elf';raw=bytearray(data);struct.pack_into('<I',raw,68,4);wrong.write_bytes(raw);run(source_elf=wrong,good=False)
 linked=root/'linked-symlink.bin';linked.symlink_to(boot);run(source=linked,good=False)
 scan=root/'scan';scan.mkdir()
 result=subprocess.run([a.scan,one/'effective.elf',scan,one/'pointers.txt'],capture_output=True,text=True,timeout=20)
 (root/'scan.log').write_text(result.stdout+result.stderr);assert result.returncode==0,result.stdout+result.stderr
 offsets=[row.split('\t')[0]for row in(scan/'functions.tsv').read_text().splitlines()[1:]if row.strip()]
 assert offsets==['0x1000','0x1010','0x1040'],offsets
 assert original==(sha(boot),sha(elf))
 summary=dict(status='passed',authored_only=True,original_patch_algorithms=True,aslr_and_path_independent=True,code_mod_and_restore=True,rebased_pointer_identity=True,guard_and_partial_relocation_refusal=True,complete_layout_and_no_symlink=True,upstream_scan_functions=len(offsets),sources_unchanged=True,evidence=str(root))
 (root/'result.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
