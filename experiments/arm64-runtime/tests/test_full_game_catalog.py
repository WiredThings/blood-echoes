import unittest,struct,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from full_game_catalog import flver_metadata,split_bhf,dcx_bounded,dispatch_payload,ScanError,MetadataBudget,MetadataBudgetError,MODEL_LIMITS,BHF_LIMITS,Scanner,project_root,LIMIT

def model():
 d=bytearray(530);d[:8]=b'FLVER\0L\0';struct.pack_into('<8I',d,8,0x20014,512,18,0,1,0,1,1);d[73]=1;struct.pack_into('<3I',d,80,1,1,0)
 struct.pack_into('<8i',d,128,316,340,0,0,0,0,0,0)
 struct.pack_into('<12i',d,160,0,0,0,0,-1,0,0,0,1,308,1,312)
 struct.pack_into('<IBBh6I',d,208,0,0,1,0,3,12,6,0,16,0)
 struct.pack_into('<8I',d,240,0,0,12,1,0,0,12,0)
 struct.pack_into('<4i',d,272,1,0,0,288);struct.pack_into('<5I',d,288,0,0,2,0,0)
 struct.pack_into('<ii',d,308,0,0);d[316:324]='mesh'.encode('utf-16-le');d[340:350]='a.mtd'.encode('utf-16-le');return d

def bhf():
 d=bytearray(128);d[:4]=b'BHF4';d[10]=1;struct.pack_into('<i',d,12,1);struct.pack_into('<Q',d,16,64);struct.pack_into('<Q',d,32,36);d[48]=1;d[49]=0x74;d[64]=0x40
 struct.pack_into('<iqqIiI',d,68,-1,8,8,48,1,100);d[100:110]='a.tpf'.encode('utf-16-le');return d

def repeated_model(layouts=1,members=1,materials=1,name_chars=4):
 mats=128;mesh=mats+materials*32;faces=mesh+48;vb=faces+32;lo=vb+32;members_at=lo+layouts*16;fs=members_at+members*20;vis=fs+4;name=vis+4;encoded=('x'*name_chars).encode('utf-16-le')+b'\0\0';off=(name+len(encoded)+15)&~15;length=max(18,members*4+6);d=bytearray(off+length)
 d[:8]=b'FLVER\0L\0';struct.pack_into('<8I',d,8,0x20014,off,length,0,materials,0,1,1);d[73]=1;struct.pack_into('<3I',d,80,1,layouts,0)
 for i in range(materials):struct.pack_into('<8i',d,mats+i*32,name,name,0,0,0,0,i,0)
 struct.pack_into('<12i',d,mesh,0,0,0,0,-1,0,0,0,1,fs,1,vis);struct.pack_into('<IBBh6I',d,faces,0,0,1,0,3,members*4,6,0,16,0);struct.pack_into('<8I',d,vb,0,0,members*4,1,0,0,members*4,0)
 for i in range(layouts):struct.pack_into('<4i',d,lo+i*16,members,0,0,members_at)
 for i in range(members):struct.pack_into('<5I',d,members_at+i*20,0,i*4,0,i,0)
 struct.pack_into('<ii',d,fs,0,0);d[name:name+len(encoded)]=encoded;return d

def repeated_bhf(count,name_chars=4):
 at=64+count*36;encoded=('n'*name_chars).encode('utf-16-le')+b'\0\0';d=bytearray(at+len(encoded));d[:4]=b'BHF4';d[10]=1;struct.pack_into('<i',d,12,count);struct.pack_into('<Q',d,16,64);struct.pack_into('<Q',d,32,36);d[48]=1;d[49]=0x74
 for i in range(count):
  start=64+i*36;d[start]=0x40;struct.pack_into('<iqqIiI',d,start+4,-1,8,8,48,i,at)
 d[at:]=encoded;return d

class Tests(unittest.TestCase):
 def test_retry_preserves_split_dispatch(self):
  from pathlib import Path
  class Fixture:
   seen=[]
   def split(self,path,where):self.seen.append((str(path),where));return 19
   def payload(self,*args):raise AssertionError('split body would be skipped')
  f=Fixture();self.assertEqual(dispatch_payload(f,Path('/author/no-file.tpfbhd'),'resource'),19);self.assertEqual(f.seen,[('/author/no-file.tpfbhd','resource')])

 def test_dcx_profiles(self):
  import zlib
  payload=b'authored resource marker'*20;compressed=zlib.compress(payload)
  for profile in[(0x10000,0x18,0x24,0x44,0x4c),(0x10000,0x18,0x24,0x24,0x2c)]:
   d=bytearray(76)+compressed;d[:4]=b'DCX\0';struct.pack_into('>5I',d,4,*profile);d[24:28]=b'DCS\0';struct.pack_into('>II',d,28,len(payload),len(compressed));d[36:44]=b'DCP\0DFLT';struct.pack_into('>I',d,44,32);d[68:72]=b'DCA\0';struct.pack_into('>I',d,72,8)
   self.assertEqual(dcx_bounded(d),payload)
   for n in range(len(d)):
    with self.assertRaises(ValueError):dcx_bounded(d[:n])
   with self.assertRaises(ValueError):dcx_bounded(d+b'extra')
   struct.pack_into('>I',d,28,128*1024*1024+1)
   with self.assertRaises(ValueError):dcx_bounded(d)

 def test_flver_links(self):
  r=flver_metadata(model());self.assertEqual(r['materials'][0]['mtd'],'a.mtd');self.assertEqual(r['meshes'][0]['layouts'][0]['stride'],12);self.assertTrue(r['meshes'][0]['faces'][0]['cull_backfaces'])
 def test_model_all_truncations(self):
  d=model()
  for n in range(len(d)):
   with self.assertRaises((ValueError,struct.error),msg=str(n)):flver_metadata(d[:n])
 def test_bad_layout_link(self):
  d=model();struct.pack_into('<i',d,244,2)
  with self.assertRaises(ValueError):flver_metadata(d)
 def test_bad_mesh_link(self):
  d=model();struct.pack_into('<i',d,308,2)
  with self.assertRaises(ValueError):flver_metadata(d)
 def test_bad_strings(self):
  d=model();struct.pack_into('<i',d,132,531)
  with self.assertRaises(ValueError):flver_metadata(d)
 def test_bad_stride(self):
  d=model();struct.pack_into('<i',d,248,16)
  with self.assertRaises(ValueError):flver_metadata(d)
 def test_bhf(self):
  r=split_bhf(bhf(),56);self.assertEqual(r[0]['name'],'a.tpf');self.assertEqual(r[0]['offset'],48)
 def test_bhf_bad_body(self):
  with self.assertRaises(ValueError):split_bhf(bhf(),55)
 def test_bhf_name_escape(self):
  d=bhf();struct.pack_into('<I',d,96,64)
  with self.assertRaises(ValueError):split_bhf(d,56)
 def test_bhf_all_truncations(self):
  d=bhf()
  # Actual name terminates at110; unrelated trailing bytes aren't required.
  for n in range(112):
   with self.assertRaises((ValueError,struct.error),msg=str(n)):split_bhf(d[:n],56)

 def test_default_project_resolves_vendored_D_loader(self):
  root=project_root();self.assertTrue((root/'vendor/blood_echoes/loader/prepare.py').is_file());self.assertNotIn('/Users/Shared/',str(root))
 def test_shared_layout_charged_each_reference(self):
  b=MetadataBudget(dict(MODEL_LIMITS,layout_members=3))
  with self.assertRaises(MetadataBudgetError)as cm:flver_metadata(repeated_model(layouts=2,members=2),b)
  self.assertEqual(cm.exception.details['field'],'layout_members');self.assertEqual(b.used['layout_members'],2)
 def test_high_repeated_layout_bounded_before_expansion(self):
  with self.assertRaises(MetadataBudgetError)as cm:flver_metadata(repeated_model(layouts=129,members=128))
  self.assertEqual(cm.exception.details['field'],'layout_members')
 def test_shared_material_names_charged_even_when_interned(self):
  with self.assertRaises(MetadataBudgetError)as cm:flver_metadata(repeated_model(materials=129,name_chars=4096))
  self.assertEqual(cm.exception.details['field'],'string_bytes')
 def test_shared_bhf_names_charged_each_reference(self):
  b=MetadataBudget(dict(BHF_LIMITS,string_bytes=5))
  with self.assertRaises(MetadataBudgetError)as cm:split_bhf(repeated_bhf(2,4),56,b)
  self.assertEqual(cm.exception.details['field'],'string_bytes');self.assertEqual(b.used['string_bytes'],4)
 def test_high_repeated_bhf_name_table_bounded(self):
  with self.assertRaises(MetadataBudgetError)as cm:split_bhf(repeated_bhf(4097,4096),56)
  self.assertEqual(cm.exception.details['field'],'string_bytes')
 def test_budget_does_not_change_valid_metadata(self):
  r=flver_metadata(model());self.assertEqual(r['materials'][0]['mtd'],'a.mtd');self.assertEqual(r['metadata_usage']['used']['mesh_vertex_refs'],1)
 def test_full_dispatch_budget_is_explicit_coverage_gap(self):
  import tempfile,json,hashlib
  with tempfile.TemporaryDirectory()as name:
   base=Path(name);root=base/'game';out=base/'out';root.mkdir();out.mkdir();path=root/'repeated.flver';path.write_bytes(repeated_model(layouts=129,members=128));before=hashlib.sha256(path.read_bytes()).hexdigest();prior=base/'prior.json';prior.write_text(json.dumps(dict(game_root=str(root),packages=[])))
   scanner=Scanner(root,prior,out);r=scanner.run();self.assertEqual(r['errors'],1);self.assertEqual(r['gaps'],1);self.assertEqual(r['counts']['metadata_budget_exceeded'],1);self.assertEqual(r['counts'].get('flver_parsed',0),0);self.assertEqual(before,hashlib.sha256(path.read_bytes()).hexdigest());e=json.loads((out/'errors.jsonl').read_text());self.assertEqual(e['coverage_status'],'budget-limited');self.assertEqual(e['budget']['field'],'layout_members')


 def test_all_cli_defaults_and_separate_outputs(self):
  import tempfile,json,subprocess,os,hashlib
  tools=Path(__file__).resolve().parents[1]/'tools';env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
  def run(name,*args):
   r=subprocess.run([sys.executable,str(tools/name),*map(str,args)],env=env,capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr);return r
  with tempfile.TemporaryDirectory()as name:
   base=Path(name).resolve();game=base/'game';game.mkdir();(game/'fixture.flver').write_bytes(model());prior=base/'prior.json';prior.write_text(json.dumps(dict(game_root=str(game),packages=[],programs=[],summary=dict(shader_entries=0,debug_entries=0))))
   scan=base/'scan';run('full_game_catalog.py','--game-root',game,'--prior-catalog',prior,'--out',scan)
   before={p.name:hashlib.sha256(p.read_bytes()).hexdigest()for p in scan.iterdir()};materials=base/'materials.json';materials.write_text(json.dumps(dict(materials=[dict(material='a.mtd',material_sha256='a'*64,shader_path='a.spx',params=[],textures=[],shader_entry_name_candidates=[])],shader_entry_candidates=[])));derived=base/'derived';run('derive_bindings.py','--scan',scan,'--catalog',prior,'--materials',materials,'--out',derived)
   retry=base/'retry';run('retry_scan_errors.py','--scan',scan,'--game-root',game,'--prior-catalog',prior,'--out',retry)
   final=base/'final';run('summarize_scan.py','--scan',scan,'--recovery',retry,'--prior-catalog',prior,'--bindings',derived/'binding-summary.json','--out',final)
   self.assertEqual(before,{p.name:hashlib.sha256(p.read_bytes()).hexdigest()for p in scan.iterdir()});self.assertEqual(json.loads((derived/'binding-summary.json').read_text())['full_pso_recipes_from_static_scan'],0);self.assertEqual(json.loads((final/'final-summary.json').read_text())['structurally_parsed_flver'],1)
   bad=game/'bad-output';r=subprocess.run([sys.executable,str(tools/'full_game_catalog.py'),'--game-root',str(game),'--prior-catalog',str(prior),'--out',str(bad)],env=env,capture_output=True,text=True);self.assertNotEqual(r.returncode,0);self.assertFalse(bad.exists())


 def test_estimated_metadata_bytes_fail_before_rows(self):
  b=MetadataBudget(dict(MODEL_LIMITS,estimated_bytes=1))
  with self.assertRaises(MetadataBudgetError)as cm:flver_metadata(model(),b)
  self.assertEqual(cm.exception.details['field'],'estimated_bytes');self.assertEqual(b.used['estimated_bytes'],0)
 def test_oversized_bhf_file_refused_before_read(self):
  from types import SimpleNamespace
  body=SimpleNamespace(is_file=lambda:True,is_symlink=lambda:False,stat=lambda:SimpleNamespace(st_size=56))
  def forbidden():self.fail('oversized header payload read')
  path=SimpleNamespace(name='a.tpfbhd',with_name=lambda name:body,stat=lambda:SimpleNamespace(st_size=LIMIT+1),read_bytes=forbidden)
  with self.assertRaises(MetadataBudgetError)as cm:Scanner.split(SimpleNamespace(),path,'a.tpfbhd')
  self.assertEqual(cm.exception.details['field'],'bhf_header_bytes')

if __name__=='__main__':unittest.main()
