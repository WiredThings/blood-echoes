"""Prepare only an explicit isolated producer identity receipt, not a game launch.

Existing static metadata supplies exact code identities. No game bytes, original
cache, shader binary, source or formal settings are written into this store.
"""
from pathlib import Path
import argparse,hashlib,json,os,stat,struct,tempfile
from static_program_links import rows,read_json,regular,need,runtime_stages
import gnm_shader_metadata as gnm
from game_shader_catalog import game_identity

MARKER=b'Blood Echoes isolated fetch input storage v1\n'
def sha(path):
 path=regular(path,512*1024*1024);fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
 with os.fdopen(fd,'rb')as stream:
  before=os.fstat(stream.fileno());need(before.st_uid==os.getuid()and before.st_nlink==1,'owned single-link producer input');value=hashlib.file_digest(stream,'sha256').digest();after=os.fstat(stream.fileno());stable=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns)
  need(stable(before)==stable(after),'producer source changed during hashing');return value
def receipt(*,game_root,catalog,index,backend,runtime_source_root=None):
 game=Path(game_root);need(game.is_absolute()and game.resolve(strict=True)==game,'canonical fixed game root')
 catalog_path=regular(catalog);index=regular(index);backend=regular(backend,512*1024*1024)
 initial={p:sha(p)for p in(catalog_path,index,backend,regular(game/'sce_sys/param.sfo',65536))}
 source_catalog=read_json(catalog_path);sfo=regular(game/'sce_sys/param.sfo',65536).read_bytes();identity=game_identity(sfo)
 need(identity==source_catalog['game_identity']and identity['APP_VER']=='01.09','fixed 1.09 game/catalog SFO identity')
 title=identity['TITLE_ID'].encode('ascii');version=identity['APP_VER'].encode('ascii');need(len(title)==9 and title.startswith(b'CUSA')and title[4:].isdigit()and len(version)==5,'title/version fixed receipt shape')
 canonical=set()
 for package in source_catalog['packages']:
  if package['kind']not in('shader','embedded')or package['debug']:continue
  records=package.get('binary_info',[])if package['kind']=='embedded'else[r for entry in package['entries']for r in entry['binary_info']]
  for r in records:
   if r.get('code_prefix_verified'):canonical.add((r['program_hash'],r.get('code_sha256'),r['binary_type_raw'],r['code_bytes']))
 source=Path(runtime_source_root)if runtime_source_root is not None else Path(__file__).resolve().parents[1]/'vendor/metal_api/shadps4'
 need(source.is_absolute()and source.resolve(strict=True)==source,'canonical selected runtime source root')
 runtime_header=regular(source/'shader_recompiler/runtime_info.h');native=runtime_stages(runtime_header)
 programs=[]
 for row in rows(index):
  need(row['schema']==1 and row['complete_pso']is False,'static program identity index')
  ident=row['identity'];lengths={v['code_bytes']for v in row['variants']};need(len(lengths)==1,'exact same code identity has contradictory lengths');length=lengths.pop()
  need(type(length)is int and 8<=length<=512*1024 and length%4==0,'owned code byte budget')
  stage=ident['hardware_stage_value'];need(type(stage)is int and 0<=stage<=6,'native hardware stage enum')
  need(native[ident['hardware_stage']]==stage,'native stage name/value mismatch')
  for variant in row['variants']:
   fact=variant['facts'];need(gnm.STAGES[fact['file_stage']]==ident['hardware_stage']and fact['binary_type']in gnm.BINARY_TYPES[fact['file_stage']],'verified static file/BinaryInfo/native stage mapping')
   need((ident['program_hash'],ident['code_sha256'],fact['binary_type'],length)in canonical,'static code not backed by independent catalog')
  programs.append((stage,int(ident['program_hash'],16),bytes.fromhex(ident['code_sha256']),length))
 programs.sort();need(0<len(programs)<=32768 and len({p[:3]for p in programs})==len(programs),'bounded unambiguous static program proofs')
 body=b''.join(struct.pack('<IQ32sI',*p)for p in programs)
 need(all(sha(p)==value for p,value in initial.items()),'producer source changed during receipt preparation')
 header=struct.pack('<8sII',b'BEFPROD1',1,len(programs))+hashlib.sha256(sfo).digest()+initial[backend]+initial[index]+initial[catalog_path]+title+version+b'\0\0'
 need(len(header)==160,'receipt header extent');return header+body,dict(title_id=identity['TITLE_ID'],app_ver=identity['APP_VER'],programs=len(programs),static_index_sha256=initial[index].hex(),catalog_sha256=initial[catalog_path].hex(),backend_sha256=initial[backend].hex(),SFO_sha256=hashlib.sha256(sfo).hexdigest(),runtime_source_root=str(source),runtime_info_sha256=sha(runtime_header).hex())
def publish(root,data):
 root=Path(root);need(root.is_absolute()and root.resolve(strict=True)==root,'canonical existing QA root');s=root.lstat();need(stat.S_ISDIR(s.st_mode)and s.st_uid==os.getuid()and stat.S_IMODE(s.st_mode)==0o700,'owned 0700 QA root')
 marker=root/'fetch-register-development.marker'
 if not marker.exists():
  fd=os.open(marker,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
  with os.fdopen(fd,'wb')as f:f.write(MARKER);f.flush();os.fsync(f.fileno())
 else:
  s=marker.lstat();need(stat.S_ISREG(s.st_mode)and s.st_uid==os.getuid()and s.st_nlink==1 and stat.S_IMODE(s.st_mode)==0o600 and marker.read_bytes()==MARKER,'exact private QA marker')
 target=root/'fetch-producer.receipt'
 if target.exists()or target.is_symlink():
  s=target.lstat();need(stat.S_ISREG(s.st_mode)and s.st_uid==os.getuid()and s.st_nlink==1 and stat.S_IMODE(s.st_mode)==0o600,'owned regular receipt replacement')
 fd,name=tempfile.mkstemp(prefix='.fetch-receipt-',dir=root)
 try:
  with os.fdopen(fd,'wb')as f:f.write(data);f.flush();os.fsync(f.fileno())
  os.replace(name,target)
 finally:
  if os.path.exists(name):os.unlink(name)
 return target
def configure_capture(*,root,game_root,catalog,index,backend,runtime_source_root=None):
 """Original launcher/prepare stage entrypoint; no separate launch or user option.

 The caller supplies its already-selected game, final DSO and owned cohort root.
 Only a verified receipt is published; the returned map is passed to that same
 existing runtime process. Native configure_v1 is an equivalent direct hook.
 """
 root=Path(root);data,proof=receipt(game_root=game_root,catalog=catalog,index=index,backend=backend,runtime_source_root=runtime_source_root)
 path=publish(root,data)
 return dict(schema=1,status='configured',capture_environment={'BB_FETCH_CAPTURE_ROOT':str(root),'BB_FETCH_CAPTURE_GAME_ROOT':str(Path(game_root))},receipt=dict(path=str(path),sha256=hashlib.sha256(data).hexdigest(),bytes=len(data)),proof=proof,game_started=False,formal_data_modified=False)
def main():
 p=argparse.ArgumentParser();p.add_argument('--qa-root',type=Path,required=True);p.add_argument('--game-root',type=Path,required=True);p.add_argument('--catalog',type=Path,required=True);p.add_argument('--index',type=Path,required=True);p.add_argument('--backend',type=Path,required=True);p.add_argument('--runtime-source-root',type=Path);a=p.parse_args();print(json.dumps(configure_capture(root=a.qa_root,game_root=a.game_root,catalog=a.catalog,index=a.index,backend=a.backend,runtime_source_root=a.runtime_source_root),indent=2))
if __name__=='__main__':main()
