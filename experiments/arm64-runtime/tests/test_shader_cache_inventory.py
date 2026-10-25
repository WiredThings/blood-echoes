from pathlib import Path
import tempfile,struct,sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from inventory_shader_cache import inventory,combine
from game_shader_catalog import CatalogError

def make(root,index=2):
 h=0x12345678;k=combine(h,index);(root/'profile.bin').write_bytes(struct.pack('<4I',0x42425043,10,7,5));(root/f'0x{h:016x}_{index}.spv').write_bytes(struct.pack('<5I',0x07230203,0x10000,0,1,0));(root/f'0x{k:016x}.meta').write_bytes(struct.pack('<IIQQ',7,10,k,index)+b'authored spec');(root/'g_authored.key').write_bytes(struct.pack('<II',5,0)+b'authored pipeline');return h,k

class CacheControls(unittest.TestCase):
 def test_pair_complete_and_read_only(self):
  with tempfile.TemporaryDirectory()as name:
   p=Path(name).resolve();h,k=make(p);before={f.name:f.read_bytes()for f in p.iterdir()};r=inventory(p,dict(programs=[dict(program_hash=f'{h:016x}',debug=False)]));self.assertEqual(r['summary']['paired_specializations'],1);self.assertEqual(r['summary']['paired_programs_absent_from_static_catalog'],0);self.assertEqual(before,{f.name:f.read_bytes()for f in p.iterdir()})
 def test_missing_metadata_not_complete(self):
  with tempfile.TemporaryDirectory()as name:
   p=Path(name).resolve();h,k=make(p);next(p.glob('*.meta')).unlink();self.assertEqual(inventory(p)['summary']['unpaired_shader_binaries'],1)
 def test_wrong_permutation_not_complete(self):
  with tempfile.TemporaryDirectory()as name:
   p=Path(name).resolve();h,k=make(p);next(p.glob('*.meta')).write_bytes(struct.pack('<IIQQ',7,10,k,999));self.assertEqual(inventory(p)['summary']['paired_specializations'],0)
 def test_unknown_versions_rejected(self):
  with tempfile.TemporaryDirectory()as name:
   p=Path(name).resolve();make(p);(p/'profile.bin').write_bytes(bytes(16))
   with self.assertRaises(CatalogError):inventory(p)
 def test_symlink_rejected(self):
  with tempfile.TemporaryDirectory()as name:
   p=Path(name).resolve();make(p);(p/'bad.spv').symlink_to(p/'profile.bin')
   with self.assertRaises(CatalogError):inventory(p)
 def test_corrupt_spirv_reported(self):
  with tempfile.TemporaryDirectory()as name:
   p=Path(name).resolve();make(p);next(p.glob('*.spv')).write_bytes(bytes(20));self.assertEqual(inventory(p)['summary']['invalid_files'],1)

if __name__=='__main__':unittest.main()
