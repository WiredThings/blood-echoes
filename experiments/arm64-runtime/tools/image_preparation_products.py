"""Reuse original image/link algorithms and their exact unchanged products.

No patch/save/config processing is replaced. Actual source bytes and parser
rules identify images; Mod selection/path information remains provenance.
"""
from pathlib import Path
import hashlib,json,os,stat,sys,tempfile,time
from incremental_file_hashes import Hashes,digest,stat_stamp,replaceable
from launcher_runtime_profile import owned_dir,code_bytes,bundle,WORK
from launcher_source_view import clone
from launcher_game_catalog import file_sha,write_json
from game_pso_owned_audit import checked_bytes
import startup_change_journal as journal

INPUTS=('eboot.bin','sce_module/libc.prx','sce_module/libSceFios2.prx','sce_sys/param.sfo')
OUTPUTS=('boot.bin','boot-libc.bin','boot-linked.bin','eboot.elf','entry.bin','analysis.json','libc-link.json','link.json')
RULES=('scripts/prepare.py','scripts/game_check.py','scripts/link_libc.py','scripts/link_modules.py')

class OriginalFailure(Exception):
 def __init__(self,code,message):super().__init__(message);self.code=code

def original_algorithms(game,target,prepare,link_libc,link_modules):
 # Keep the original fatal statuses. Neither failure can become supervisor
 # menu restart (75), quickload (76), or an invented continuation protocol.
 try:prepare.prepare(game,target);link_libc.link(game,target);link_modules.link(game,target)
 except prepare.GameCheckError as error:raise OriginalFailure(2,'Unsupported game files: '+str(error))from error
 except Exception as error:raise OriginalFailure(1,'Image/link preparation failed: '+str(error))from error

def need(value,why):
 if not value:raise ValueError(why)

def read(path,limit=65536):
 s=Path(path).lstat();need(s.st_nlink==1 and stat.S_IMODE(s.st_mode)==0o600,'private image scheduling receipt')
 doc=json.loads(checked_bytes(path,limit=limit)[0]);need(set(doc)=={'body','sha256'}and digest(doc['body'])==doc['sha256'],'image scheduling receipt checksum');return doc['body']

def write(path,body):write_json(path,dict(body=body,sha256=digest(body)))

def prepare_images(*,stage,game,out,work,environment,build=None,check=lambda:None):
 start=time.monotonic();stage=Path(stage).resolve(strict=True);game=Path(game).absolute();out=owned_dir(out);work=owned_dir(work)
 root=owned_dir(work/'image-products',create=True);registrations=owned_dir(root/'registrations',create=True)
 rules={name:code_bytes(stage/name,limit=4*1024*1024,content=False)[1]['sha256']for name in RULES}
 # Scheduling/logging code is package-validated, but it does not change the
 # bytes emitted by these original image/link algorithms or their ABI.
 # The original merger forbids eboot/sce_module/sce_sys overrides. Resolve the
 # actual selected paths nevertheless; never assume the frontend enforced it.
 sources={name:(game/name).resolve(strict=True)for name in INPUTS}
 origins=sorted({str(p.parent)for p in sources.values()})
 binding=dict(schema=1,role='original-image-inputs',sources={k:str(v)for k,v in sources.items()},rules=rules)
 hashes=Hashes(registrations/('source-'+digest(binding)+'.json'),binding,hasher=file_sha,check=check,watch_roots=origins)
 inputs={name:dict(zip(('sha256','bytes'),hashes.get(path)))for name,path in sources.items()}
 semantic=dict(schema=1,abi='original-BBPROBE2-4-5',rules=rules,inputs=inputs,skip_game_check=environment.get('BB_SKIP_GAME_CHECK')=='1')
 identity=digest(semantic);record_path=registrations/(identity+'.json');generation=None;product_rows=None;output_hashes=None
 try:
  saved=read(record_path);need(saved['semantic']==semantic,'exact image compilation identity')
  generation=owned_dir(root/saved['generation']);need(generation.parent==root,'image product generation escaped')
  need(stat.S_IMODE(generation.stat().st_mode)==0o500,'sealed image product generation')
  products=saved['products'];need(set(products)==set(OUTPUTS),'complete original image outputs')
  output_hashes=Hashes(registrations/('products-'+generation.name+'.json'),dict(role='sealed-original-images',identity=identity),hasher=file_sha,check=check,watch_roots=[generation])
  if not output_hashes.journal_valid:output_hashes.previous={}
  for name in OUTPUTS:need(output_hashes.get(generation/name)==(products[name]['sha256'],products[name]['bytes']),'original image product damaged')
  output_hashes.verify()
  if not output_hashes.journal_valid:output_hashes.publish()
  product_rows=products
 except(OSError,ValueError,KeyError,TypeError):generation=None
 if generation is None:
  if build is None:
   # Original functions, original game checks, imports and relocation logic.
   sys.path.insert(0,str(stage/'scripts'))
   import prepare,link_libc,link_modules
   for module in(prepare,link_libc,link_modules):need(Path(module.__file__).resolve()==stage/'scripts'/Path(module.__file__).name,'image parser imported outside selected stage')
   def build(game,target):original_algorithms(game,target,prepare,link_libc,link_modules)
  with tempfile.TemporaryDirectory(prefix='image-build-',dir=root)as temporary:
   scratch=Path(temporary);check();build(game,scratch);hashes.verify()
   generation=root/(identity+'-'+str(time.time_ns()));generation.mkdir(mode=0o700)
   product_rows={}
   for name in OUTPUTS:
    source=scratch/name;sha,size=file_sha(source,check);need(0<size<=512*1024*1024,'bounded original output');clone(source,generation/name);(generation/name).chmod(0o400)
    need(file_sha(generation/name,check)==(sha,size),'exact cloned image output');product_rows[name]=dict(sha256=sha,bytes=size)
   generation.chmod(0o500)
   output_hashes=Hashes(registrations/('products-'+generation.name+'.json'),dict(role='sealed-original-images',identity=identity),hasher=file_sha,check=check,watch_roots=[generation])
   for name in OUTPUTS:need(output_hashes.get(generation/name)==(product_rows[name]['sha256'],product_rows[name]['bytes']),'sealed original output')
   output_hashes.publish();write(record_path,dict(semantic=semantic,generation=generation.name,products=product_rows))
  built=True
 else:built=False
 # The original shader integrity check depends on selected shader files, not
 # image compilation. A shader Mod change validates this range without relink.
 shader=(game/'dvdroot_ps4/shader').resolve(strict=False);watch=shader if shader.is_dir()else shader.parent
 watch=watch.resolve(strict=True);compatibility=dict(schema=1,identity=identity,shader=str(shader),skip=semantic['skip_game_check'])
 validation_path=registrations/('game-check-'+digest(compatibility)+'.json');valid=False
 try:
  value=read(validation_path);valid=value['binding']==compatibility and journal.unchanged(value['journal'],[watch])
 except(OSError,ValueError,KeyError,TypeError):pass
 if not valid:
  token=journal.query([watch]);check()
  if not semantic['skip_game_check']:
   sys.path.insert(0,str(stage/'scripts'));import game_check
   need(Path(game_check.__file__).resolve()==stage/'scripts/game_check.py','original game validator path')
   problem=game_check.problem(game)
   if problem is not None:raise OriginalFailure(2,'Unsupported game files: '+game_check.explain(*problem))
  if token is not None and journal.unchanged(token,[watch]):write(validation_path,dict(binding=compatibility,journal=token))
 # Retain the actual published inode stamps; unchanged out files need no copy.
 destination_path=registrations/'current-output.json';previous={}
 try:
  previous=read(destination_path)
  if previous['identity']!=identity or previous['out']!=str(out):previous={}
 except(OSError,ValueError,KeyError,TypeError):pass
 destination={};restored=0
 for name in OUTPUTS:
  check();target=out/name
  if previous and target.exists()and stat_stamp(target.lstat())==previous.get('files',{}).get(name):destination[name]=previous['files'][name];continue
  need(replaceable(target),'unsafe generated image destination')
  temporary=out/('.image-'+str(time.time_ns())+'-'+name)
  clone(generation/name,temporary);temporary.chmod(0o600);os.replace(temporary,target);destination[name]=stat_stamp(target.lstat());restored+=1
 need({name:(game/name).resolve(strict=True)for name in INPUTS}==sources,'selected image input mapping changed')
 hashes.verify();output_hashes.verify();hashes.publish()
 write(destination_path,dict(identity=identity,out=str(out),files=destination))
 report=dict(status='built'if built else'reused',identity=identity,seconds=time.monotonic()-start,original_algorithms_invoked=built,restored_outputs=restored,source_hashes=hashes.report(),product_hashes=output_hashes.report(),shader_validation_reused=valid)
 print('Image/link preparation: '+json.dumps(report,sort_keys=True),flush=True);return report

def main():
 import argparse
 parser=argparse.ArgumentParser();parser.add_argument('game',type=Path);parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
 stage=Path(__file__).resolve().parents[1];data=owned_dir(os.environ['BB_DATA_DIR']);work=owned_dir(data/WORK,create=True)
 admitted=bundle(stage,registration_root=work/'code-registration')
 if admitted is None:
  # Preserve the existing explicit Shared QA boundary; a failed formal bundle
  # raises above and can never silently become this development fallback.
  project=Path('/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype')
  need(stage.is_relative_to(project)and data.is_relative_to(project/'state'),'original Shared QA image scope')
  local=json.loads(code_bytes(stage/'ARM64-LOCAL-PREPARATION.json')[0]);need(local.get('host_local_only')is True and local.get('runtime_project')==str(project),'original QA preparation identity')
  parity=json.loads(code_bytes(stage/'SOURCE-PARITY.json')[0])['effective_sources']
  for name in RULES:code_bytes(stage/name,limit=4*1024*1024,expected=parity[name],content=False)
 need(args.out.absolute()==data/'out','original output role')
 try:prepare_images(stage=stage,game=args.game,out=args.out,work=work,environment=os.environ)
 except OriginalFailure as error:
  print(str(error),file=sys.stderr,flush=True);raise SystemExit(error.code)
 except Exception as error:
  print('Image/link preparation failed: '+str(error),file=sys.stderr,flush=True);raise SystemExit(1)

if __name__=='__main__':main()
