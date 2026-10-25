#!/usr/bin/env python3
"""Read-only local boot contract audit; emits metadata, never code bytes."""
import argparse,hashlib,json,re,struct
from pathlib import Path
import capstone

def audit(boot,source):
 b=boot.read_bytes();assert b[:8]==b'BBPROBE5'
 size,entry,ns,nr,ni,caps=struct.unpack_from('<6Q',b,8);assert caps==1
 image=b[-size:];proc=struct.unpack_from('<Q',b,56)[0];nm=struct.unpack_from('<Q',b,96)[0]
 off=104+56*nm;nb=struct.unpack_from('<Q',b,off)[0];off+=8+nb*24
 segs=[struct.unpack_from('<3Q',b,off+24*i)for i in range(ns)];off+=ns*24
 names=[b[off+128*i:off+128*(i+1)].split(b'\0')[0].decode()for i in range(ni)];off+=128*ni
 relocs=[struct.unpack_from('<4Q',b,off+32*i)for i in range(nr)]
 assert off+32*nr+size==len(b)
 direct={target:value for target,kind,value,addend in relocs if kind==0}
 imp={target:names[value]for target,kind,value,addend in relocs if kind==1}
 def word(at):return direct.get(at,struct.unpack_from('<Q',image,at)[0])
 symbols=dict(re.findall(r'\{"([^"]+)","([^"]+)"\}',source.read_text()))
 decoder=capstone.Cs(capstone.CS_ARCH_X86,capstone.CS_MODE_64);decoder.detail=True;decoder.skipdata=True
 target_flag=0x579fcbc;refs=[]
 for ins in decoder.disasm(image[0x56e0000:0x5797924],0x56e0000):
  if not ins.id:continue
  for operand in ins.operands:
   if operand.type==capstone.x86.X86_OP_MEM and operand.mem.base==capstone.x86.X86_REG_RIP and ins.address+ins.size+operand.mem.disp==target_flag:
    refs.append(dict(address=hex(ins.address),mnemonic=ins.mnemonic,access=operand.access))
 def relative_candidates(target):
  found=[]
  for byte in [b'\xe8',b'\xe9']:
   at=image.find(byte)
   while at>=0 and at+5<=len(image):
    if at+5+struct.unpack_from('<i',image,at+1)[0]==target:found.append(hex(at))
    at=image.find(byte,at+1)
  return sorted(found)
 def plt(at):
  ins=next(decoder.disasm(image[at:at+16],at));op=ins.operands[0]
  assert ins.mnemonic=='jmp'and op.type==capstone.x86.X86_OP_MEM
  slot=at+ins.size+op.mem.disp;nid=imp[slot]
  return dict(plt=hex(at),got=hex(slot),nid=nid,name=symbols.get(nid))
 libc_param=word(proc+0x38)
 result=dict(boot_sha256=hashlib.sha256(b).hexdigest(),image_bytes=size,procparam=hex(proc),libcparam=hex(libc_param),libcparam_size=word(libc_param),libcparam_version=struct.unpack_from('<I',image,libc_param+8)[0],libcparam_type=struct.unpack_from('<I',image,libc_param+12)[0],mlock_config_pointer=hex(word(libc_param+0x50)),mlock_flag=hex(target_flag),mlock_flag_initial=struct.unpack_from('<I',image,target_flag)[0],mlock_flag_static_refs=refs,imports=[plt(x)for x in[0x56e0310,0x56e0340,0x56e0350,0x56e0360]],wrapper_direct_branch_candidates=relative_candidates(0x5703e70),mlock_plt_direct_branch_candidates=relative_candidates(0x56e0360),munmap_plt_direct_branch_candidates=relative_candidates(0x56e0340),mlock_import_slots=[hex(t)for t,n in imp.items()if n=='3k6kx-zOOSQ#p#J'],munlock_imports=[name for name in names if 'munlock'in symbols.get(name,'').lower()],qualification='Fixed boot static read only; relocations applied only as scalar metadata. Relative candidates verified at known libc instruction boundaries. Indirect/self-modifying guest calls remain unproved; no dynamic lock count or requested-size measurement.')
 assert result['mlock_config_pointer']=='0x0'and result['mlock_flag_initial']==0
 assert result['wrapper_direct_branch_candidates']==['0x5703d44','0x5704047']
 assert result['mlock_plt_direct_branch_candidates']==['0x5703ed9']
 assert result['mlock_import_slots']==['0x5798748']
 return result
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--boot',type=Path,required=True);p.add_argument('--import-names',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();r=audit(a.boot,a.import_names);a.output.write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r,indent=2))
