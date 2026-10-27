"""Real existing parsers + authored files only; no game/Metal/GUI processes."""
from pathlib import Path
import contextlib,copy,hashlib,io,json,os,shutil,sqlite3,struct,sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from resource_parse_products import Products,encoded
from full_game_catalog import Scanner
from launcher_game_catalog import source_inventory
from test_full_game_catalog import model,bhf
from test_game_shader_catalog import binder,wrap,sfo,shader
import game_shader_catalog as packages

class ResourceProducts(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.base=Path(self.temp.name).resolve();self.root=self.base/'game';self.root.mkdir()
  (self.root/'one.flver').write_bytes(model());(self.root/'same-model.flver').write_bytes(model())
  (self.root/'unknown.bin').write_bytes(b'opaque data with rejected OrbShdr signature')
  (self.root/'texture.tpfbhd').write_bytes(bhf());body=bytearray(56);body[:4]=b'BDF4';body[48:]=b'original';(self.root/'texture.tpfbdt').write_bytes(body)
  self.prior=self.base/'prior.json';self.prior.write_text(json.dumps(dict(game_root=str(self.root),packages=[])))
  self.products=Products(self.base/'products #1','a'*64);self.addCleanup(self.products.close);self.n=0
 def run_scan(self,*,cache=True,root=None):
  root=root or self.root;self.n+=1;out=self.base/('scan'+str(self.n));out.mkdir()
  prior=self.base/('prior'+str(self.n)+'.json');prior.write_text(json.dumps(dict(game_root=str(root),packages=[])))
  scanner=Scanner(root,prior,out,products=self.products if cache else None,source_files=source_inventory(root))
  with contextlib.redirect_stdout(io.StringIO()):summary=scanner.run()
  streams={p.name:p.read_bytes()for p in out.glob('*.jsonl')}
  return summary,streams
 def stable_summary(self,s):return{k:v for k,v in s.items()if k not in('elapsed_seconds','peak_rss_native_units')}
 def test_original_reader_and_cached_output_are_identical_including_duplicate_partial_keys(self):
  baseline,rows=self.run_scan(cache=False);first,fresh=self.run_scan();self.assertEqual(rows,fresh)
  self.assertEqual(self.stable_summary(baseline),self.stable_summary(first))
  with patch.object(Scanner,'parse_file',side_effect=AssertionError('unchanged resource parsed again')):
   second,reused=self.run_scan()
  self.assertEqual(rows,reused);self.assertEqual(self.stable_summary(first),self.stable_summary(second))
  self.assertEqual(len(reused['partial-pipelines.jsonl'].splitlines()),1)
  self.assertEqual(second['partial_pipeline_occurrences'],2)
 def test_new_mod_file_and_restored_original_only_parse_changed_bytes(self):
  _,original=self.run_scan();path=self.root/'unknown.bin';old=path.read_bytes();path.write_bytes(b'new selected Mod payload')
  calls=[];parse=Scanner.parse_file
  def track(scanner,path,*args):calls.append(path.name);return parse(scanner,path,*args)
  with patch.object(Scanner,'parse_file',track):self.run_scan()
  self.assertEqual(calls,['unknown.bin']);path.write_bytes(old)
  with patch.object(Scanner,'parse_file',side_effect=AssertionError('cached vanilla parsed again')):_,restored=self.run_scan()
  self.assertEqual(original,restored)
 def test_split_body_change_invalidates_header_dependency_and_only_affected_resources(self):
  self.run_scan();path=self.root/'texture.tpfbdt';data=bytearray(path.read_bytes());data[48:]=b'changed!';path.write_bytes(data)
  calls=[];parse=Scanner.parse_file
  def track(scanner,path,*args):calls.append(path.name);return parse(scanner,path,*args)
  with patch.object(Scanner,'parse_file',track):cached,rows=self.run_scan()
  self.assertEqual(set(calls),{'texture.tpfbhd','texture.tpfbdt'})
  baseline,expected=self.run_scan(cache=False);self.assertEqual(rows,expected);self.assertEqual(self.stable_summary(cached),self.stable_summary(baseline))
 def test_identical_registered_resources_in_new_view_reuse_without_parsing(self):
  _,old=self.run_scan();other=self.base/'another-view';shutil.copytree(self.root,other)
  with patch.object(Scanner,'parse_file',side_effect=AssertionError('directory name became parser identity')):_,new=self.run_scan(root=other)
  self.assertEqual(old,new)
 def test_single_bad_row_is_retained_and_only_its_resource_reparsed(self):
  self.run_scan();db=self.products.db
  row=next((i,key)for i,key,raw in db.execute('SELECT id,key,payload FROM products')if json.loads(raw)['inputs']['path']=='unknown.bin')
  db.execute('UPDATE products SET payload=? WHERE id=?',(b'corrupted original row',row[0]));db.commit()
  calls=[];parse=Scanner.parse_file
  def track(scanner,path,*args):calls.append(path.name);return parse(scanner,path,*args)
  with patch.object(Scanner,'parse_file',track):self.run_scan()
  self.assertEqual(calls,['unknown.bin']);self.assertEqual(db.execute('SELECT payload FROM products WHERE id=?',(row[0],)).fetchone()[0],b'corrupted original row')
  self.assertEqual(db.execute('SELECT count(*) FROM products WHERE key=?',(row[1],)).fetchone()[0],2)
 def test_parser_abi_change_cannot_reuse_old_results(self):
  self.run_scan();different=Products(self.products.path.parent,'b'*64);self.addCleanup(different.close)
  old=self.products;self.products=different
  calls=[];parse=Scanner.parse_file
  def track(scanner,path,*args):calls.append(path.name);return parse(scanner,path,*args)
  with patch.object(Scanner,'parse_file',track):self.run_scan()
  self.assertEqual(len(calls),5);self.products=old
 def test_new_member_is_scanned_and_unselected_metadata_never_promoted(self):
  self.run_scan();(self.root/'new.bin').write_bytes(shader())
  calls=[];parse=Scanner.parse_file
  def track(scanner,path,*args):calls.append(path.name);return parse(scanner,path,*args)
  with patch.object(Scanner,'parse_file',track):_,rows=self.run_scan()
  self.assertEqual(calls,['new.bin']);self.assertIn(b'code_prefix_verified',rows['programs.jsonl'])
 def test_wrong_registered_digest_fails_before_caching_new_parse(self):
  sources=source_inventory(self.root);sources[0]['sha256']='f'*64;out=self.base/'wrong';out.mkdir()
  scanner=Scanner(self.root,self.prior,out,products=self.products,source_files=sources)
  try:
   with self.assertRaisesRegex(ValueError,'content changed before parse'),contextlib.redirect_stdout(io.StringIO()):scanner.run()
  finally:
   for stream in scanner.streams.values():stream.close()
  self.assertEqual(self.products.published,0)
 def test_budget_fallback_does_not_skip_parsing_or_change_output(self):
  with patch('resource_parse_products.MAX_ROW',16):first,rows=self.run_scan()
  self.assertGreater(self.products.oversized,0)
  second,other=self.run_scan(cache=False);self.assertEqual(rows,other);self.assertEqual(self.stable_summary(first),self.stable_summary(second))
 def test_database_symlink_and_hardlink_are_not_followed(self):
  for kind in('symlink','hardlink'):
   root=self.base/kind;root.mkdir();target=self.base/('outside-'+kind);target.write_bytes(b'outside')
   path=root/'metadata-v1.sqlite'
   path.symlink_to(target)if kind=='symlink'else os.link(target,path)
   with self.assertRaises(ValueError):Products(root,'a'*64)
   self.assertEqual(target.read_bytes(),b'outside')
 def test_late_split_body_mutation_cannot_publish_a_false_product(self):
  path=self.root/'texture.tpfbdt';original=Scanner.payload;out=self.base/'late';out.mkdir()
  scanner=Scanner(self.root,self.prior,out,products=self.products,source_files=source_inventory(self.root))
  def mutate(local,where,data,*args):
   if where.startswith('texture.tpfbhd::'):
    old=path.stat();raw=bytearray(path.read_bytes());raw[-1]^=1;path.write_bytes(raw);os.utime(path,ns=(old.st_atime_ns,old.st_mtime_ns))
   return original(local,where,data,*args)
  try:
   with patch.object(Scanner,'payload',mutate),contextlib.redirect_stdout(io.StringIO()),self.assertRaisesRegex(ValueError,'dependency changed during scan'):scanner.run()
  finally:
   for stream in scanner.streams.values():stream.close()
  published=[json.loads(row[0])['inputs']['path']for row in self.products.db.execute('SELECT payload FROM products')]
  self.assertNotIn('texture.tpfbhd',published)

class PackageProducts(unittest.TestCase):
 def test_existing_binder_and_embedded_reader_reuse_original_metadata_and_local_invalidation(self):
  with tempfile.TemporaryDirectory()as name:
   root=Path(name).resolve()/'game';(root/'sce_sys').mkdir(parents=True);(root/'sce_sys/param.sfo').write_bytes(sfo())
   shader_dir=root/'dvdroot_ps4/shader';shader_dir.mkdir(parents=True);mtd=root/'dvdroot_ps4/mtd';mtd.mkdir()
   shader_path=shader_dir/'authored.shaderbnd.dcx';shader_path.write_bytes(wrap(binder()));(mtd/'allmaterialbnd.mtdbnd.dcx').write_bytes(wrap(binder()))
   (root/'eboot.bin').write_bytes(b'O\x15=\x1d'+shader(fill=1))
   products=Products(root.parent/'products','a'*64)
   try:
    expected=packages.inventory(root);first=packages.inventory(root,products=products,source_files=source_inventory(root));self.assertEqual(expected,first)
    with patch.object(packages,'read_package',side_effect=AssertionError('unchanged binder reparsed')):
     self.assertEqual(first,packages.inventory(root,products=products,source_files=source_inventory(root)))
    old=shader_path.read_bytes();shader_path.write_bytes(wrap(binder(shader(fill=2))));read=packages.read_package;calls=[]
    def track(root,path,embedded=False):calls.append(path.name);return read(root,path,embedded)
    with patch.object(packages,'read_package',track):changed=packages.inventory(root,products=products,source_files=source_inventory(root))
    self.assertEqual(calls,['authored.shaderbnd.dcx']);self.assertEqual(changed,packages.inventory(root))
    shader_path.write_bytes(old)
    with patch.object(packages,'read_package',side_effect=AssertionError('cached original reparsed')):self.assertEqual(first,packages.inventory(root,products=products,source_files=source_inventory(root)))
   finally:products.close()

 def test_complete_existing_static_pipeline_reuses_all_reader_layers_and_preserves_index(self):
  import launcher_game_catalog as controller
  import gnm_shader_metadata as gnm
  from test_gnm_shader_metadata import fixture
  with tempfile.TemporaryDirectory()as name:
   base=Path(name).resolve();root=base/'game';(root/'sce_sys').mkdir(parents=True);(root/'sce_sys/param.sfo').write_bytes(sfo())
   shader_dir=root/'dvdroot_ps4/shader';shader_dir.mkdir(parents=True);mtd=root/'dvdroot_ps4/mtd';mtd.mkdir()
   (shader_dir/'authored.shaderbnd.dcx').write_bytes(wrap(binder(fixture())))
   (mtd/'allmaterialbnd.mtdbnd.dcx').write_bytes(wrap(binder()))
   elf=bytearray(128)+fixture(wrapper=38);ident=b'\x7fELF\x02\x01\x01'+bytes(9)
   struct.pack_into('<16sHHIQQQIHHHHHH',elf,0,ident,2,62,1,0,64,0,0,64,56,1,0,0,0)
   struct.pack_into('<IIQQQQQQ',elf,64,1,4,0,0,0,len(elf),len(elf),1);(root/'eboot.bin').write_bytes(elf)
   source=Path(__file__).resolve().parents[1]/'vendor/metal_api/shadps4'
   sources=source_inventory(root);outputs=[];results=[]
   import startup_change_journal as journal
   with patch.object(journal,'query',return_value=None),patch.object(journal,'unchanged',return_value=False),contextlib.redirect_stdout(io.StringIO()):
    first=base/'first';first.mkdir();results.append(controller.build_static(game_root=root,source_root=source,output=first,identity='1'*64,source_files=sources))
    with patch.object(packages,'read_package',side_effect=AssertionError('package reparsed')),patch.object(gnm,'checked_elf',side_effect=AssertionError('embedded ELF redecoded')),patch.object(gnm,'shader_headers',side_effect=AssertionError('GNM headers redecoded')),patch.object(Scanner,'parse_file',side_effect=AssertionError('full resource reparsed')):
     second=base/'second';second.mkdir();results.append(controller.build_static(game_root=root,source_root=source,output=second,identity='2'*64,source_files=sources))
   for out in(first,second):outputs.append({str(p.relative_to(out)):p.read_bytes()for p in out.rglob('*')if p.is_file()and p.name not in('READY.json','summary.json')})
   self.assertEqual(outputs[0],outputs[1]);self.assertEqual(results[1]['resource_products']['published'],0)
   self.assertGreater(results[1]['resource_products']['hits'],5);self.assertFalse(results[1]['resource_products']['artifact_authority'])

if __name__=='__main__':unittest.main(verbosity=2)
