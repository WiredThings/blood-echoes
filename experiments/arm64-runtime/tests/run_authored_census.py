"""Full scanner dispatch control using only authored fixture bytes."""
from pathlib import Path
import json,struct,tempfile,sys,zlib,argparse
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from full_game_catalog import Scanner,sha
from test_full_game_catalog import model

def dcx(data):
 c=zlib.compress(data);d=bytearray(76)+c;d[:4]=b'DCX\0';struct.pack_into('>5I',d,4,0x10000,0x18,0x24,0x24,0x2c);d[24:28]=b'DCS\0';struct.pack_into('>II',d,28,len(data),len(c));d[36:44]=b'DCP\0DFLT';struct.pack_into('>I',d,44,32);d[68:72]=b'DCA\0';struct.pack_into('>I',d,72,8);return d

def shader():
 d=bytearray(60);d[:4]=b'\xff\x03\xeb\xbe';struct.pack_into('<I',d,4,3);d[32:39]=b'OrbShdr';d[39]=7;struct.pack_into('<I',d,40,(16<<8)|(4<<2));struct.pack_into('<Q',d,48,0x12345);return d

def binder(payload):
 d=bytearray(128)+payload;d[:4]=b'BND4';d[10]=1;struct.pack_into('<i',d,12,1);struct.pack_into('<Q',d,16,64);struct.pack_into('<QQ',d,32,36,128);d[48]=1;d[49]=0x74;d[64]=0x40;struct.pack_into('<iqqIiI',d,68,-1,len(payload),len(payload),128,1,100);d[100:122]='fixture.vpo'.encode('utf-16-le');return d

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--prototype',type=Path);ap.add_argument('--out',type=Path);a=ap.parse_args();p=a.prototype
 if a.out:assert a.out.is_absolute()and not a.out.exists(),'new absolute report path required'
 with tempfile.TemporaryDirectory(prefix='author-census-')as tmp:
  root=Path(tmp).resolve()/'game';root.mkdir();out=Path(tmp)/'out';out.mkdir()
  (root/'model.flver.dcx').write_bytes(dcx(model()));(root/'program.bnd.dcx').write_bytes(dcx(binder(shader())))
  bh=bytearray(128);bh[:4]=b'BHF4';bh[10]=1;struct.pack_into('<i',bh,12,1);struct.pack_into('<Q',bh,16,64);struct.pack_into('<Q',bh,32,36);bh[48]=1;bh[49]=0x74;bh[64]=0x40;payload=dcx(shader());struct.pack_into('<iqqIiI',bh,68,-1,len(payload),len(payload),48,1,100);bh[100:122]='fixture.cpo'.encode('utf-16-le');(root/'a.tpfbhd').write_bytes(bh);body=bytearray(48);body[:4]=b'BDF4';(root/'a.tpfbdt').write_bytes(body+payload)
  prior=Path(tmp)/'prior.json';prior.write_text(json.dumps(dict(game_root=str(root),packages=[],programs=[])))
  before={str(f.name):sha(f.read_bytes())for f in root.iterdir()};s=Scanner(root,prior,out,p);r=s.run();assert r['errors']==0;assert r['counts']['flver_parsed']==1;assert r['counts']['binders_parsed']==1;assert r['counts']['split_entries']==1;assert r['counts']['binary_info_records']==2;assert r['unique_nondebug_program_hashes']==1;assert before=={str(f.name):sha(f.read_bytes())for f in root.iterdir()}
  if a.out:a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(r,indent=2)+'\n')
 print('AUTHORED CENSUS full dispatch: DCX + FLVER links + BND4 shader + BHF/BDF inner DCX + unchanged inputs passed')
if __name__=='__main__':main()
