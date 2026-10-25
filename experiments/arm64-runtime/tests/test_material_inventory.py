import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import struct,unittest
from material_inventory import parse_mtd,candidates,CatalogError

def marker(b,n):b.append(n);b.extend(b'\xfe'*((-len(b))%4))
def u32(b,n):b.extend(struct.pack('<I',n))
def string(b,s,m):raw=s.encode('shift_jis');u32(b,len(raw));b.extend(raw);marker(b,m)
def block(b,t,v,m):
 u32(b,0);slot=len(b);u32(b,0);start=len(b);u32(b,t);u32(b,v);marker(b,m);return slot,start
def end(b,token):slot,start=token;struct.pack_into('<I',b,slot,len(b)-start)
def fixture(extended=False):
 b=bytearray();f=block(b,0,3,1);h=block(b,1,2,0xb0);string(b,'MTD ',0x34);u32(b,1000);end(b,h);marker(b,1);d=block(b,2,4,0xa3);string(b,'X:\\Unit\\Surface.spx',0xa3);string(b,'test',3);u32(b,1);l=block(b,3,4,0xa3);u32(b,0);marker(b,3);u32(b,1);p=block(b,4,4,0xa3);string(b,'g_Unit',0xa3);string(b,'float3',4);u32(b,1);v=block(b,0x1002,1,0xa2);u32(b,3);b.extend(struct.pack('<3f',.25,.5,1));end(b,v);marker(b,4);u32(b,0);end(b,p);marker(b,3);u32(b,1);t=block(b,0x2000,5 if extended else 3,0xa3);string(b,'g_UnitTexture',0x35);u32(b,2);marker(b,0x35);u32(b,7)
 if extended:u32(b,0xa3);string(b,'unit.dds',0xba);u32(b,2);b.extend(struct.pack('<2f',1,2))
 end(b,t)
 for token in [l,d,f]:marker(b,4);u32(b,0);end(b,token)
 return b
class Tests(unittest.TestCase):
 def test_regular(self):
  r=parse_mtd(fixture());self.assertEqual(r['shader_path'],'X:\\Unit\\Surface.spx');self.assertEqual(r['params'][0]['value'],[.25,.5,1]);self.assertEqual(r['textures'][0]['shader_data_index'],7);self.assertEqual(r['textures'][0]['path'],'')
 def test_extended(self):
  r=parse_mtd(fixture(True));self.assertEqual(r['textures'][0]['path'],'unit.dds');self.assertEqual(r['textures'][0]['unknown_float_count'],2)
 def test_all_truncations(self):
  b=fixture(True)
  for n in range(len(b)):
   with self.subTest(n=n),self.assertRaises(CatalogError):parse_mtd(b[:n])
 def test_append(self):
  with self.assertRaises(CatalogError):parse_mtd(fixture()+b'x')
 def test_nested_escape(self):
  b=fixture();struct.pack_into('<I',b,24,0x40000000)
  with self.assertRaises(CatalogError):parse_mtd(b)
 def test_bad_marker(self):
  b=fixture();b[16]=9
  with self.assertRaises(CatalogError):parse_mtd(b)
 def test_string_budget(self):
  b=fixture();struct.pack_into('<I',b,80,0x40000000)
  with self.assertRaises(CatalogError):parse_mtd(b)
 def test_semantic_version(self):
  b=fixture();struct.pack_into('<I',b,52,1001)
  with self.assertRaises(CatalogError):parse_mtd(b)
 def test_prefix_boundary(self):
  self.assertEqual(candidates('x\\Surface.spx',['Surface.vpo','Surface_Gbuf.ppo','Surface@[cl].vpo','SurfaceExtra.vpo','SubSurface.vpo']),['Surface.vpo','Surface_Gbuf.ppo','Surface@[cl].vpo'])
 def test_case_path(self):self.assertEqual(candidates('x/Surface.spx',['dir\\surface_Gbuf.vpo']),['dir\\surface_Gbuf.vpo'])
if __name__=='__main__':unittest.main()
