"""Bounded metadata creation and atomic refusal using authored ELF-like data."""
from pathlib import Path
import unittest,sys,struct,copy,tempfile
from unittest.mock import patch
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'));sys.path.insert(0,str(P/'vendor/blood_echoes/loader'))
from prepare_module_metadata import record,boot_header,prepare
import prepare_module_metadata as metadata

def model():
 elf=bytearray(8192);elf[0x800:0x814]=bytes([1,0x1b,3,0x3b])+struct.pack('<iI',-0x104,1)+bytes(8)
 def ph(kind,addr,files,mem,flags,align=16):return dict(type=kind,vaddr=addr,offset=addr,filesz=files,memsz=mem,flags=flags,align=align)
 return dict(elf=elf,ph=[ph(1,0,4096,4096,5),ph(1,4096,256,4096,6),ph(7,4096,16,32,4),ph(0x6474e550,0x800,20,20,4)],tags={12:0x10,13:0x20})
class Controls(unittest.TestCase):
 def test_native_layout_and_relocated_module(self):
  b,segs=record(model(),'module',0x8000,1,2);self.assertEqual(len(b),424);self.assertEqual(struct.unpack_from('<iI',b,264),(1,2));self.assertEqual(struct.unpack_from('<Q4I',b,272),(0x9000,16,32,0,16));self.assertEqual(struct.unpack_from('<QQII',b,328),(0x8800,0x8700,20,256));self.assertEqual(segs,[(0x8000,4096,5),(0x9000,4096,6)])
 def test_main_static_tls_offset(self):
  b,_=record(model(),'main',0,0,1,True);self.assertEqual(struct.unpack_from('<I',b,288)[0],32)
 def test_no_unwind_is_explicit(self):
  m=model();m['ph'].pop();b,_=record(m,'main',0,0,1,True);self.assertEqual(b[328:352],bytes(24))
 def test_unwind_encoding_table_range_controls(self):
  for change in ['encoding','table','frame-above','unmapped','short-file','span']:
   with self.subTest(change=change):
    m=model()
    if change=='encoding':m['elf'][0x801]=0
    if change=='table':struct.pack_into('<I',m['elf'],0x808,10000)
    if change=='frame-above':struct.pack_into('<i',m['elf'],0x804,4)
    if change=='unmapped':m['ph'][0]['filesz']=0x700
    if change=='short-file':m['ph'][-1]['filesz']=19
    if change=='span':m['ph'][-1]['offset']=9000
    with self.assertRaises(ValueError):record(m,'module',0,0,1)
 def test_tls_name_segment_controls(self):
  for change in ['align','memory','flags','empty-name','long-name','nul-name','unicode-name','segments']:
   with self.subTest(change=change):
    m=model();name='module'
    if change=='align':m['ph'][2]['align']=3
    if change=='memory':m['ph'][2]['filesz']=33
    if change=='flags':m['ph'][0]['flags']=8
    if change=='empty-name':name=''
    if change=='long-name':name='a'*256
    if change=='nul-name':name='a\0b'
    if change=='unicode-name':name='测试'
    if change=='segments':m['ph']+=m['ph'][:2]*2
    with self.assertRaises((ValueError,UnicodeError)):record(m,name,0,0,1)
 def test_original_boot_header_and_atomic_refusal(self):
  # Authored files stay in this test's fresh tree, without a pre-existing QA
  # state directory. Only the CPU fixture's policy root changes, not runtime.
  with tempfile.TemporaryDirectory(dir=P,prefix='module-metadata-unit-')as directory,patch.object(metadata,'PRIVATE_ROOT',P):
   root=Path(directory);boot=root/'boot';out=root/'out';out.write_bytes(b'keep-existing-metadata')
   raw=b'BBPROBE5'+struct.pack('<6Q',16384,0,1,0,0,1)+struct.pack('<6Q',64,0,0,0,0,2)+bytes(112)+struct.pack('<Q',0)+struct.pack('<3Q',0,16384,5)+bytes(16384);boot.write_bytes(raw);self.assertEqual(boot_header(boot)['size'],16384)
   for mutation in [b'',raw[:55],b'X'+raw[1:],raw+b'X',raw[:-1]]:
    boot.write_bytes(mutation)
    with self.assertRaises(ValueError):boot_header(boot)
   boot.write_bytes(raw)
   with self.assertRaisesRegex(ValueError,'source module size'):prepare(boot,root,out,P/'vendor/blood_echoes/loader')
   self.assertEqual(out.read_bytes(),b'keep-existing-metadata')
if __name__=='__main__':unittest.main()
