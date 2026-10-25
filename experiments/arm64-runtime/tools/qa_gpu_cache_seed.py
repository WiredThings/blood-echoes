"""Explicit cache-only seed from an admitted local QA app; never select saves."""
from pathlib import Path
import hashlib,json,os,re,stat,shutil,subprocess
from game_pso_owned_audit import checked_bytes
from game_pso_component import sha,relative
HEX=re.compile('[0-9a-f]{64}')
CACHE_PARTS=('cache','local-metal')
def need(v,m):
 if not v:raise ValueError(m)
def directory(path):
 p=Path(path).absolute();s=p.lstat();need(p==p.resolve(strict=True)and stat.S_ISDIR(s.st_mode)and s.st_uid==os.getuid(),'owned canonical QA directory');return p
def json_file(p):return json.loads(checked_bytes(Path(p),limit=4*1024*1024)[0])
def validate(manifest,*,project,expected_upstream_gpu_sha256,gpu_verifier=None,component_loader=None,app_verifier=None):
 p=directory(project);manifest=Path(manifest).absolute()
 need(HEX.fullmatch(expected_upstream_gpu_sha256),'current CPU-paired upstream GPU identity required')
 need(manifest.name=='ARM64-QA-MANIFEST.json'and manifest.parent.name=='Resources'and manifest.parent.parent.name=='Contents','QA resource manifest required')
 app=directory(manifest.parents[2]);need(app.parent==p/'apps'and app.name.startswith('Blood Echoes ARM64 GUI QA ')and app.suffix=='.app','only admitted local QA app under project/apps')
 m=json_file(manifest);need(m.get('schema')==2 and m.get('host_local_only')is True and m.get('formal_data_modified')is False and m.get('portable_installer')is False,'host-local non-formal QA manifest required')
 need(m.get('app')==str(app),'manifest/app identity mismatch')
 if app_verifier is None:
  subprocess.run(['/usr/bin/codesign','--verify','--strict',str(app)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
 else:need(app_verifier(app)is True,'source QA app signature rejected')
 # Compare the original coherent build, not relocated final app bytes.
 upstream=m['upstream_inputs']['gpu_library'];need(upstream==expected_upstream_gpu_sha256,'cache seed upstream renderer differs from current CPU-paired renderer')
 cpu_ref=m['cpu_runtime_manifest'];cpu_path=Path(cpu_ref['path']);need(cpu_path.is_relative_to(p/'evidence')and HEX.fullmatch(cpu_ref['sha256']),'QA CPU provenance required')
 cpu_raw,_=checked_bytes(cpu_path,expected=cpu_ref['sha256']);cpu=json.loads(cpu_raw)
 need(cpu.get('schema')==1 and cpu.get('kind')=='blood-echoes-isolated-cpu-runtime'and cpu.get('production')is False and cpu['gpu_library_sha256']==upstream,'QA source CPU/GPU provenance mismatch')
 stage=directory(app/'Contents/Resources/bbport');frozen=m['frozen_runtime']
 required={'bin/bb-probe-metal','bin/gpu-metal/libbbgpu.dylib','bin/Bloodborne Metal.app/Contents/MacOS/bb-gpu','bin/gpu-metal-arm64/libbbgpu.dylib','bin/gpu-net/libbbnet.dylib','bin/game-pso-preparer'}
 need(required<=set(frozen)and len(frozen)<=32,'complete QA frozen runtime required')
 for rel,want in frozen.items():need(HEX.fullmatch(want)and sha(relative(stage,rel))==want,'source package frozen runtime changed: '+rel)
 prep=m['local_metal_preparation'];need(prep.get('runtime_project')==str(p)and prep.get('host_local_only')is True,'QA local preparation provenance')
 need(1<=len(prep['runtime_tools'])<=64,'bounded QA tool closure')
 for rel,want in prep['runtime_tools'].items():
  need(Path(rel).name==rel and HEX.fullmatch(want)and sha(stage/'native-runtime-tools'/rel)==want,'source QA tool closure changed')
 need(len(m['effective_scripts'])==16,'complete original QA script closure required')
 for rel,want in m['effective_scripts'].items():need(HEX.fullmatch(want)and sha(relative(stage,rel))==want,'source QA script closure changed')
 if gpu_verifier is None:
  from warm_archive_manifest import resolve_gpu_identity
  gpu_verifier=resolve_gpu_identity
 exe=stage/'bin/Bloodborne Metal.app/Contents/MacOS/bb-gpu';lib=stage/'bin/gpu-metal-arm64/libbbgpu.dylib'
 actual=gpu_verifier(exe,stage)
 need(actual['library']==str(lib)and actual['library_sha256']==frozen['bin/gpu-metal-arm64/libbbgpu.dylib'],'source QA actual renderer edge/hash mismatch')
 if component_loader is None:
  from game_pso_component import load
  component_loader=load
 component=component_loader(stage);need(component is not None and component['library_sha256']==actual['library_sha256'],'source preparation component/renderer mismatch')
 source=directory(m['private_gpu_cache']);need(source.parent==p/'state'and re.fullmatch(r'gui-gpu-local-aot-[0-9]+',source.name),'only private QA GPU cache root; formal paths forbidden')
 return dict(source=str(source),source_manifest=str(manifest),source_manifest_sha256=sha(manifest),
  upstream_gpu_library_sha256=upstream,source_packaged_gpu_library_sha256=actual['library_sha256'],
  cache_parts=list(CACHE_PARTS),cache_only=True,save_config_seed_changed=False,formal_data_modified=False)

def snapshot(source):
 source=directory(source);result={}
 for part in CACHE_PARTS:
  top=source/part
  if not top.exists():continue
  for base,dirs,files in os.walk(top,followlinks=False):
   for child in [Path(base),*(Path(base)/n for n in dirs+files)]:
    if child in result:continue
    info=child.lstat();need(child==child.resolve(strict=True)and info.st_uid==os.getuid()and (stat.S_ISREG(info.st_mode)or stat.S_ISDIR(info.st_mode)),'cache clone owned regular tree; no links or special files')
    result[child]=(info.st_dev,info.st_ino,info.st_mode,info.st_size,info.st_mtime_ns,info.st_uid)
    need(len(result)<=100000,'cache-only clone file budget')
 need(bool(result),'source QA shader cache is empty');return result

def clone(admission,destination,*,project,baseline_root):
 p=directory(project);source=directory(admission['source']);dest=Path(destination).absolute()
 need(source.parent==p/'state'and re.fullmatch(r'gui-gpu-local-aot-[0-9]+',source.name),'isolated QA source required')
 need(dest.parent==p/'state'and re.fullmatch(r'gui-gpu-local-aot-[0-9]+',dest.name)and not os.path.lexists(dest),'fresh isolated QA destination required')
 baseline=directory(baseline_root);need(baseline.parent==p/'state','original QA baseline GPU root required')
 before=snapshot(source)
 # Preserve the original QA configuration seed. Replace only cache roots as a
 # unit; never merge old/default per-cache permutation files.
 excluded={'cache','local-metal','game-pso-progress'}
 shutil.copytree(baseline,dest,symlinks=False,ignore=lambda path,names:excluded&set(names)if Path(path)==baseline else set())
 count=0;total=0;fingerprint=hashlib.sha256()
 for old,identity in sorted(before.items()):
  new=dest/old.relative_to(source)
  if stat.S_ISDIR(identity[2]):new.mkdir(mode=0o700,parents=True,exist_ok=True);continue
  new.parent.mkdir(parents=True,exist_ok=True);fd=os.open(old,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
  try:
   start=os.fstat(fd);values=lambda v:(v.st_dev,v.st_ino,v.st_mode,v.st_size,v.st_mtime_ns,v.st_uid)
   need(values(start)==identity,'source cache changed before copy');h=hashlib.sha256()
   with new.open('xb')as out:
    while True:
     b=os.read(fd,1024*1024)
     if not b:break
     out.write(b);h.update(b);total+=len(b)
    out.flush();os.fsync(out.fileno())
   need(values(os.fstat(fd))==identity,'source cache changed during copy')
  finally:os.close(fd)
  fingerprint.update(str(old.relative_to(source)).encode()+b'\0'+h.digest());count+=1
 need(snapshot(source)==before,'source cache tree changed during clone')
 return dict(admission,destination=str(dest),copied_files=count,copied_bytes=total,
             copied_content_identity=fingerprint.hexdigest(),source_cache_content_sha256=fingerprint.hexdigest(),source_cache_modified=False,baseline_gpu_root=str(baseline),baseline_cache_excluded=True,
             excluded_non_cache_state=True,game_pso_progress_hint_copied=False)
