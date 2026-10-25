from pathlib import Path
import hashlib,struct,sys,tempfile,unittest,zlib
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from game_shader_catalog import CatalogError,bnd4,dcx,orb_metadata,inventory,reverse,bounded_zlib,game_identity

def shader(hash_value=0x1234,fill=0):
 b=bytearray(60);struct.pack_into('<II',b,0,0xbeeb03ff,3);b[8:16]=bytes([fill])*8;b[32:39]=b'OrbShdr';b[39]=7;struct.pack_into('<I',b,40,(16<<8)|(1<<2));struct.pack_into('<Q',b,48,hash_value);struct.pack_into('<I',b,56,0xabcd);return bytes(b)
def binder(payload=None):
 payload=shader()if payload is None else payload;name='Authored.vpo'.encode('utf-16-le')+b'\0\0';end=100+len(name);offset=(end+15)&~15;b=bytearray(offset+len(payload));b[:4]=b'BND4';b[10]=1;struct.pack_into('<iQ',b,12,1,64);b[24:32]=b'26J25R1\0';struct.pack_into('<QQ',b,32,36,end);b[48]=1;b[49]=reverse(0x2a);b[64]=reverse(2);struct.pack_into('<iQQIII',b,68,-1,len(payload),len(payload),offset,7,100);b[100:end]=name;b[offset:]=payload;return bytes(b)
def wrap(data):
 z=zlib.compress(data);b=bytearray(76);b[:4]=b'DCX\0';struct.pack_into('>5I',b,4,0x10000,0x18,0x24,0x44,0x4c);b[24:28]=b'DCS\0';struct.pack_into('>II',b,28,len(data),len(z));b[36:44]=b'DCP\0DFLT';struct.pack_into('>I',b,44,0x20);b[68:72]=b'DCA\0';struct.pack_into('>I',b,72,8);return bytes(b)+z

def sfo(version='01.09'):
 keys=b'TITLE_ID\0APP_VER\0';values=b'CUSA03173\0'+version.encode()+b'\0';base=20+32;b=bytearray(base+len(keys)+len(values));struct.pack_into('<4sIIII',b,0,b'\0PSF',0x101,base,base+len(keys),2);struct.pack_into('<HHIII',b,20,0,0x204,10,10,0);struct.pack_into('<HHIII',b,36,9,0x204,6,6,10);b[base:base+len(keys)]=keys;b[base+len(keys):]=values;return bytes(b)

class CatalogControls(unittest.TestCase):
 def test_dcx_binder_real_offsets(self):
  a=bnd4(dcx(wrap(binder())));self.assertEqual(a['count'],1);self.assertEqual(a['entries'][0]['name'],'Authored.vpo');self.assertEqual(a['format'],0x2a)
 def test_bad_dcx_size(self):
  a=bytearray(wrap(binder()));struct.pack_into('>I',a,28,2)
  with self.assertRaises(CatalogError):dcx(a)
 def test_decompression_bomb_bound(self):
  with self.assertRaises(CatalogError):bounded_zlib(zlib.compress(b'x'*10000),4)
 def test_dcx_trailing_bytes(self):
  with self.assertRaises(CatalogError):dcx(wrap(binder())+b'X')
 def test_dcx_bad_method(self):
  a=bytearray(wrap(binder()));a[40:44]=b'KRAK'
  with self.assertRaises(CatalogError):dcx(a)
 def test_bad_entry_extent(self):
  a=bytearray(binder());struct.pack_into('<I',a,88,0xffffffff)
  with self.assertRaises(CatalogError):bnd4(a)
 def test_filename_points_into_entry_table(self):
  a=bytearray(binder());struct.pack_into('<I',a,96,64)
  with self.assertRaises(CatalogError):bnd4(a)
 def test_unterminated_filename(self):
  a=bytearray(binder());end=struct.unpack_from('<Q',a,40)[0];a[end-2:end]=b'XX'
  with self.assertRaises(CatalogError):bnd4(a)
 def test_negative_count(self):
  a=bytearray(binder());struct.pack_into('<i',a,12,-1)
  with self.assertRaises(CatalogError):bnd4(a)
 def test_unknown_big_endian(self):
  a=bytearray(binder());a[9]=1
  with self.assertRaises(CatalogError):bnd4(a)
 def test_unknown_header_size(self):
  a=bytearray(binder());struct.pack_into('<Q',a,32,44)
  with self.assertRaises(CatalogError):bnd4(a)
 def test_prefix_identity_and_code_digest(self):
  a=orb_metadata(shader())['records'][0];self.assertTrue(a['code_prefix_verified']);self.assertEqual(a['code_offset'],0);self.assertEqual(a['code_sha256'],hashlib.sha256(shader()[:16]).hexdigest())
 def test_signature_without_prefix_is_not_admitted(self):
  a=bytearray(shader());a[:4]=bytes(4);self.assertFalse(orb_metadata(a)['records'][0]['code_prefix_verified'])
 def test_unaligned_embedded_code(self):
  a=orb_metadata(b'abc'+shader())['records'][0];self.assertTrue(a['code_prefix_verified']);self.assertEqual(a['code_offset'],3)
 def test_hash_alias_keeps_distinct_code(self):
  a=orb_metadata(shader(fill=1))['records'][0];b=orb_metadata(shader(fill=2))['records'][0];self.assertEqual(a['program_hash'],b['program_hash']);self.assertNotEqual(a['code_sha256'],b['code_sha256'])
 def test_truncated_signature_reported(self):self.assertEqual(len(orb_metadata(b'OrbShdr')['rejected']),1)
 def test_sfo_fixed_version(self):self.assertEqual(game_identity(sfo())['APP_VER'],'01.09')
 def test_sfo_wrong_version_rejected(self):
  with self.assertRaises(CatalogError):game_identity(sfo('01.00'))
 def test_sfo_bad_pointer(self):
  a=bytearray(sfo());struct.pack_into('<I',a,48,0xffffffff)
  with self.assertRaises(CatalogError):game_identity(a)
 def test_inventory_read_only(self):
  with tempfile.TemporaryDirectory()as name:
   root=Path(name).resolve();(root/'sce_sys').mkdir();(root/'sce_sys/param.sfo').write_bytes(sfo());s=root/'dvdroot_ps4/shader';s.mkdir(parents=True);m=root/'dvdroot_ps4/mtd';m.mkdir();(root/'sce_module').mkdir();(s/'authored.shaderbnd.dcx').write_bytes(wrap(binder()));(m/'allmaterialbnd.mtdbnd.dcx').write_bytes(wrap(binder()));(root/'eboot.bin').write_bytes(b'O\x15=\x1d'+shader(fill=1));before={p:hashlib.sha256(p.read_bytes()).hexdigest()for p in root.rglob('*')if p.is_file()};a=inventory(root);self.assertEqual(a['summary']['unique_program_hashes'],1);self.assertEqual(a['summary']['hashes_with_distinct_verified_code'],1);self.assertEqual(before,{p:hashlib.sha256(p.read_bytes()).hexdigest()for p in root.rglob('*')if p.is_file()})

if __name__=='__main__':unittest.main()
