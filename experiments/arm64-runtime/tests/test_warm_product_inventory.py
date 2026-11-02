"""Disk-backed scheduling metadata never weakens native artifact admission."""
from pathlib import Path
import copy,hashlib,os,sys,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import warm_product_inventory as inventory
import quick_restart_preparation as quick
import startup_change_journal as journal
import test_warm_preparation as fixture

class LargeInventory(unittest.TestCase):
 def setUp(self):
  self.f=fixture.Warm();self.f.setUp();self.addCleanup(self.f.doCleanups)
 def disk(self):
  f=self.f;scope=quick._warm_products(f.f.gpu,quick.warm_binding(f.bound),f.f.context,f.local)
  writer=inventory.Writer(f.f.context,lambda:None)
  self.addCleanup(writer.close)
  for path,row in quick._inventory(scope,f.f.context,lambda:None).items():writer.add(path,row)
  return writer.finish(),scope
 def test_more_than_old_twenty_thousand_limit_streams_and_queries(self):
  f=self.f
  for i in range(20001):(f.cache/str(i)).write_bytes(b'authored')
  scope=quick._warm_products(f.f.gpu,quick.warm_binding(f.bound),f.f.context,f.local)
  result=quick._inventory(scope,f.f.context,lambda:None)
  self.assertEqual(result['storage'],'sqlite-v1');self.assertGreater(result['rows'],20000)
  self.assertLess(result['bytes'],8*1024*1024)
  with inventory.rows(result,f.f.context,lambda:None)as rows:
   self.assertEqual(rows.get(str(f.cache/'20000'))[1],hashlib.sha256(b'authored').hexdigest())
   self.assertIsNone(rows.get(str(f.cache/'absent')))
  self.assertFalse(list(Path(result['path']).parent.glob('.inventory-*')))
 def test_changed_real_product_keeps_original_delta_validation(self):
  f=self.f;descriptor,scope=self.disk();p=f.cache/'pso.bin'
  current=dict(unchanged=False,events=[[1,1,0,str(p).lstrip('/')]])
  products=dict(scope=scope,files=descriptor,journal=dict(pins={'root':[1,0,0,0,0,0,'/']},events=[]))
  with patch.object(journal,'query',return_value=current),patch.object(journal,'unchanged',return_value=True):
   self.assertTrue(quick._products_current(products,f.f.context,lambda:None))
   p.write_bytes(b'broken');self.assertFalse(quick._products_current(products,f.f.context,lambda:None))
 def test_corrupt_index_refuses_even_when_history_unchanged(self):
  f=self.f;descriptor,scope=self.disk();p=Path(descriptor['path']);p.write_bytes(b'bad database')
  with patch.object(journal,'query',return_value=dict(unchanged=True)):
   with self.assertRaises(ValueError):quick._products_current(dict(files=descriptor,scope=scope,journal={}),f.f.context,lambda:None)
  self.assertEqual(p.read_bytes(),b'bad database')
 def test_wrong_root_digest_count_and_alias_refuse(self):
  f=self.f;d,_=self.disk()
  for changes in(dict(sha256='0'*64),dict(rows=d['rows']+1),dict(bytes=d['bytes']+1),dict(path=str(f.cache/Path(d['path']).name))):
   with self.subTest(changes=changes),self.assertRaises((ValueError,OSError)):
    with inventory.rows(dict(d,**changes),f.f.context,lambda:None):pass
  p=Path(d['path']);alias=p.with_suffix('.alias');os.link(p,alias)
  with self.assertRaises(ValueError):
   with inventory.rows(d,f.f.context,lambda:None):pass
 def test_mid_read_mutation_and_cancel_refuse_without_deleting_old_index(self):
  f=self.f;d,_=self.disk();p=Path(d['path'])
  with inventory.rows(d,f.f.context,lambda:None)as rows:
   stamp=p.stat();os.utime(p,ns=(stamp.st_atime_ns,stamp.st_mtime_ns+1))
   with self.assertRaises(ValueError):rows.get(str(f.cache/'pso.bin'))
  self.assertTrue(p.exists())
  writer=inventory.Writer(f.f.context,lambda:None);scratch=writer.path
  writer.add('/authored',['file','a',1]);writer.close()
  self.assertFalse(scratch.exists());self.assertTrue(p.exists())
 def test_small_legacy_inventory_keeps_original_contract(self):
  f=self.f;scope=quick._warm_products(f.f.gpu,quick.warm_binding(f.bound),f.f.context,f.local)
  rows=quick._inventory(scope,f.f.context,lambda:None)
  self.assertIn(str(f.cache/'pso.bin'),rows);self.assertNotIn('storage',rows)

if __name__=='__main__':unittest.main()
