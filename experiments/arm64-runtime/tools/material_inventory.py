"""Independent bounded MTD metadata reader; no extraction, JIT or GPU initialization.
Format reference: https://github.com/soulsmods/SoulsFormatsNEXT/blob/master/SoulsFormats/Formats/MTD.cs
"""
import argparse, collections, hashlib, json, struct, sys
from pathlib import Path
P=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(P/'tools'))
from game_shader_catalog import CatalogError,require,span,dcx,bnd4,entry_data
class Reader:
 def __init__(self,data):require(len(data)<=1024*1024,'MTD byte budget');self.data=data;self.at=0;self.limits=[len(data)]
 def take(self,n):require(0<=n<=self.limits[-1]-self.at,'nested MTD span');out=bytes(span(self.data,self.at,n));self.at+=n;return out
 def u32(self):return struct.unpack('<I',self.take(4))[0]
 def marker(self,n):require(self.take(1)==bytes([n]),'MTD marker');self.take((-self.at)%4)
 def block(self,t=None,v=None,m=None):
  require(self.u32()==0,'MTD block prefix');length=self.u32();end=self.at+length;require(self.at+12<=end<=self.limits[-1],'MTD block length');kind=self.u32();version=self.u32();require(t is None or kind==t,'MTD block type');require(v is None or version==v,'MTD block version');marker=self.take(1)[0];self.take((-self.at)%4);require(m is None or marker==m,'MTD block marker');self.limits.append(end);return kind,version
 def end(self):require(self.at==self.limits[-1],'MTD block exact end');self.limits.pop()
 def string(self,m):
  n=self.u32();require(n<=8192,'MTD string budget');raw=self.take(n)
  try:s=raw.decode('shift_jis',errors='strict')
  except UnicodeError as e:raise CatalogError('MTD Shift-JIS')from e
  self.marker(m);return s
 def count(self,limit=1024):n=self.u32();require(n<=limit,'MTD count budget');return n

def parse_mtd(data):
 r=Reader(data);r.block(0,3,1);r.block(1,2,0xb0);require(r.string(0x34)=='MTD ','MTD magic');require(r.u32()==1000,'MTD version');r.end();r.marker(1);r.block(2,4,0xa3);shader=r.string(0xa3);description=r.string(3);require(r.u32()==1,'MTD data version');r.block(3,4,0xa3);require(r.u32()==0,'MTD list prefix');r.marker(3);params=[]
 for _ in range(r.count()):
  r.block(4,4,0xa3);name=r.string(0xa3);kind=r.string(4);require(r.u32()==1,'MTD param shape');r.block(v=1);count=r.count(4);types={'int':('i',1),'int2':('i',2),'bool':('B',1),'float':('f',1),'float2':('f',2),'float3':('f',3),'float4':('f',4)};require(kind in types,'MTD param type');fmt,n=types[kind];require(count==n,'MTD value count');values=struct.unpack('<'+fmt*n,r.take(struct.calcsize(fmt)*n));require(kind!='bool' or values[0]in(0,1),'MTD bool');r.end();r.marker(4);require(r.u32()==0,'MTD param trailer');r.end();params.append(dict(name=name,type=kind,value=list(values)))
 r.marker(3);textures=[]
 for _ in range(r.count()):
  _,v=r.block(0x2000,m=0xa3);require(v in(3,5),'MTD texture version');name=r.string(0x35);uv=r.u32();r.marker(0x35);index=r.u32();path='';floats=[]
  if v==5:
   require(r.u32()==0xa3,'MTD extended texture');path=r.string(0xba);n=r.count(128);floats=list(struct.unpack('<'+'f'*n,r.take(4*n)))
  r.end();textures.append(dict(type=name,uv=uv,shader_data_index=index,path=path,unknown_float_count=len(floats)))
 for _ in range(3):r.marker(4);require(r.u32()==0,'MTD trailer');r.end()
 require(r.at==len(data)and len(r.limits)==1,'MTD EOF')
 return dict(shader_path=shader,description=description,params=params,textures=textures)

def stem(name):return name.replace('\\','/').rsplit('/',1)[-1].rsplit('.',1)[0]
def candidates(shader,names):
 base=stem(shader).casefold();return[n for n in names if stem(n).casefold()==base or stem(n).casefold().startswith(base+'_')or stem(n).casefold().startswith(base+'@')]

def run(catalog_path,out):
 cat=json.loads(catalog_path.read_text());root=Path(cat['game_root']);package=[x for x in cat['packages']if x['kind']=='material'];require(len(package)==1,'single fixed material bundle');package=package[0];path=root/package['path'];raw=path.read_bytes();require(hashlib.sha256(raw).hexdigest()==package['sha256'],'material input changed');data=dcx(raw);binder=bnd4(data);rows=[];shader_entries=[dict(package=p['path'],**e)for p in cat['packages']if p['kind']=='shader'and not p['debug']for e in p['entries']];names=sorted({x['name']for x in shader_entries});lookup=collections.defaultdict(list);linked={}
 for index,e in enumerate(shader_entries):lookup[e['name']].append((index,e))
 for entry in binder['entries']:
  payload=entry_data(data,entry);m=parse_mtd(payload);m.update(material=entry['name'],material_sha256=hashlib.sha256(payload).hexdigest());links=[]
  for name in candidates(m['shader_path'],names):
   for index,e in lookup[name]:
    linked[index]=dict(id=index,package=e['package'],entry=e['name'],entry_sha256=e['sha256'],programs=[dict(program_hash=p['program_hash'],binary_type_raw=p['binary_type_raw'],code_sha256=p.get('code_sha256'),code_prefix_verified=p['code_prefix_verified'])for p in e['binary_info']]);links.append(index)
  m['shader_entry_name_candidates']=links;rows.append(m);del payload
 require(hashlib.sha256(path.read_bytes()).hexdigest()==package['sha256'],'material changed during read');flat=[l for m in rows for l in m['shader_entry_name_candidates']];unique=set(flat);result=dict(schema=1,source_catalog=str(catalog_path),source_material_sha256=package['sha256'],materials=rows,shader_entry_candidates=list(linked.values()),summary=dict(materials=len(rows),parsed_all=True,distinct_shader_paths=len({m['shader_path']for m in rows}),materials_with_name_candidates=sum(bool(m['shader_entry_name_candidates'])for m in rows),materials_without_name_candidates=[m['material']for m in rows if not m['shader_entry_name_candidates']],unique_candidate_shader_entries=len(unique),candidate_program_hashes=len({p['program_hash']for l in linked.values()for p in l['programs']}),parameter_records=sum(len(m['params'])for m in rows),texture_slot_records=sum(len(m['textures'])for m in rows)),qualification='MTD fields structurally decoded. Shader entries are name-family candidates, not proven executable/material binding or full specialization coverage. No game content extracted, compiled, modified, or eligible for source export.')
 out.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n');print(json.dumps(result['summary'],indent=2,ensure_ascii=False))
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--catalog',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args();run(a.catalog,a.out)
