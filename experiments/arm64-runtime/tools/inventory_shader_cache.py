"""Read-only flat-cache identity catalog. Never invokes WarmUp, Clear or Save."""
from pathlib import Path
import argparse,collections,hashlib,json,re,struct,time
from game_shader_catalog import CatalogError,require

def combine(seed,index):return(seed^(index+0x9e3779b9+(seed<<12)+(seed>>4)))&0xffffffffffffffff
def inventory(cache,catalog=None):
 cache=Path(cache);require(cache.is_dir()and cache==cache.resolve(),'canonical cache directory required')
 paths=sorted(p for p in cache.iterdir()if p.suffix in('.meta','.spv','.key','.bin'));require(len(paths)<=20000,'file count budget')
 require(all(p.is_file()and not p.is_symlink()for p in paths),'regular cache files required');require(sum(p.stat().st_size for p in paths)<=512*1024*1024,'cache byte budget')
 files={};errors=[];metas={};shaders=[];pipelines=[];profile_header=None
 for path in paths:
  before=path.stat();data=path.read_bytes();after=path.stat();require((before.st_ino,before.st_size,before.st_mtime_ns)==(after.st_ino,after.st_size,after.st_mtime_ns),'cache input changed during read')
  row=dict(name=path.name,bytes=len(data),sha256=hashlib.sha256(data).hexdigest());files[path.name]=row
  if path.name=='profile.bin':profile_header=bytes(data[:16])
  if path.suffix=='.meta':
   if len(data)<24:errors.append(dict(name=path.name,reason='short metadata'));continue
   version,binary_version,perm_hash,index=struct.unpack_from('<IIQQ',data);row.update(meta_version=version,binary_version=binary_version,permutation=index,permutation_hash=f'{perm_hash:016x}')
   if(version,binary_version)!=(7,10)or path.name!=f'0x{perm_hash:016x}.meta':errors.append(dict(name=path.name,reason='metadata version or filename mismatch'));continue
   metas[perm_hash]=row
  elif path.suffix=='.spv':
   match=re.fullmatch(r'0x([0-9a-f]{16})_(\d+)\.spv',path.name)
   if match is None or len(data)<20 or len(data)%4 or struct.unpack_from('<I',data)[0]!=0x07230203:errors.append(dict(name=path.name,reason='bad SPIR-V header/name'));continue
   program=int(match[1],16);index=int(match[2]);row.update(program_hash=f'{program:016x}',permutation=index,permutation_hash=f'{combine(program,index):016x}');shaders.append(row)
  elif path.suffix=='.key':
   if len(data)<8:errors.append(dict(name=path.name,reason='short pipeline key'));continue
   version,kind=struct.unpack_from('<II',data);row.update(key_version=version,kind='graphics'if kind==0 else'compute'if kind==1 else'unknown')
   if version!=5 or kind not in(0,1):errors.append(dict(name=path.name,reason='pipeline key version/type mismatch'));continue
   pipelines.append(row)
 profile=files.get('profile.bin');require(profile is not None,'profile.bin required');header=profile_header;require(len(header)==16 and struct.unpack('<4I',header)==(0x42425043,10,7,5),'incompatible profile versions')
 candidates=collections.defaultdict(list)
 for row in shaders:candidates[int(row['permutation_hash'],16)].append(row)
 pairs=[];unpaired=[];ambiguous=[]
 known={p['program_hash']for p in catalog['programs']if not p['debug']}if catalog else None
 for key,items in candidates.items():
  meta=metas.get(key)
  if len(items)!=1:ambiguous.append(dict(permutation_hash=f'{key:016x}',names=[x['name']for x in items]));continue
  row=items[0]
  if not meta or meta['permutation']!=row['permutation']:unpaired.append(row['name']);continue
  pairs.append(dict(program_hash=row['program_hash'],permutation=row['permutation'],meta=meta,spirv=row,known_static_program=(row['program_hash']in known)if known is not None else None))
 orphan=[r['name']for k,r in metas.items()if k not in candidates]
 return dict(schema=1,cache=str(cache),profile=profile,summary=dict(files=len(files),bytes=sum(r['bytes']for r in files.values()),shader_binaries=len(shaders),shader_metadata=len(metas),paired_specializations=len(pairs),unique_paired_program_hashes=len({p['program_hash']for p in pairs}),graphics_pipeline_keys=sum(p['kind']=='graphics'for p in pipelines),compute_pipeline_keys=sum(p['kind']=='compute'for p in pipelines),unpaired_shader_binaries=len(unpaired),orphan_metadata=len(orphan),ambiguous_permutation_hashes=len(ambiguous),invalid_files=len(errors),paired_programs_absent_from_static_catalog=sum(p['known_static_program']is False for p in pairs)),paired_specializations=pairs,pipeline_keys=pipelines,unpaired_binaries=unpaired,orphan_metadata=orphan,ambiguous_hashes=ambiguous,errors=errors,qualification='Exact stored blob/profile digests and canonical HashCombine joins only. Full serialization is preserved by digest, not semantically decoded; no live-key equivalence, PSO success or complete-variant coverage claim. Permutation indices are cache-local. No reads invoke renderer initialization; no cache writes/deletions.')

if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--cache',type=Path,required=True);ap.add_argument('--catalog',type=Path);a=ap.parse_args();p=Path(__file__).resolve().parents[1];from qa_build_guard import require_no_games;require_no_games();require(a.cache.resolve().is_relative_to(p/'state'),'only private QA cache accepted');c=json.loads(a.catalog.read_text())if a.catalog else None;e=p/'evidence'/('shader-cache-inventory-'+str(time.time_ns()));e.mkdir();r=inventory(a.cache.absolute(),c);(e/'inventory.json').write_text(json.dumps(r,indent=2)+'\n');print(e);print(json.dumps(r['summary'],indent=2))
