"""Bind a fresh private GPU app without replacing an existing app or build."""
from pathlib import Path
import argparse,json,shutil,subprocess,time,os,stat,sys
from qa_build_guard import require_no_games
from gpu_candidate_identity import verify,sha
PRIVATE_PROJECT=Path('/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype')

def normal_source(receipt,component,project):
 """Admit the ordinary CMake target and its exact same-byte renderer."""
 def regular(path):
  path=Path(path).absolute();s=path.lstat()
  if path!=path.resolve(strict=True)or not path.is_relative_to(project/'evidence')or not stat.S_ISREG(s.st_mode)or s.st_uid!=os.getuid()or s.st_nlink!=1:
   raise ValueError('owned canonical normal build input required')
  return path
 def read(path):
  path=regular(path)
  if path.stat().st_size>32*1024*1024:raise ValueError('normal receipt budget')
  return json.loads(path.read_text())
 receipt=regular(receipt);record=read(receipt);plan=read(receipt.parent/'plan.json')
 if record.get('status')!='normal-targets-built':raise ValueError('completed normal target receipt required')
 source=Path(record['source_root']);build=Path(record['renderer_build'])
 if plan['selected']['source']!=str(source/'vendor/metal_api')or plan['selected']['build']!=str(build):raise ValueError('normal source/build identity')
 for name in('bb-gpu','libbbgpu.dylib'):
  row=record['artifacts'][name];path=regular(row['path'])
  if path!=build/name or sha(path)!=row['sha256']:raise ValueError('normal target artifact changed')
 main=plan.get('gpu_executable_source')
 if not main or Path(main['path'])!=source/'vendor/metal_api/bb_gpu/bb_gpu_main.cpp'or sha(regular(main['path']))!=main['sha256']:
  raise ValueError('normal GPU main source identity')
 provenance=component.get('normal_provenance',{})
 original=read(provenance['normal_gpu_result']);original_plan=read(Path(provenance['normal_gpu_result']).parent/'plan.json')
 if sha(regular(provenance['normal_gpu_result']))!=provenance['normal_gpu_result_sha256']or sha(regular(Path(provenance['normal_gpu_result']).parent/'plan.json'))!=provenance['normal_gpu_plan_sha256']or original['source_root']!=str(source)or original_plan['source_sha256']!=plan['source_sha256']:
  raise ValueError('component and wrapper source mismatch')
 library=build/'libbbgpu.dylib';target=build/'bb-gpu'
 if record['artifacts']['libbbgpu.dylib']['sha256']!=component['library_sha256']:raise ValueError('component and normal renderer differ')
 from warm_archive_manifest import resolve_gpu_identity
 actual=resolve_gpu_identity(target,project/'evidence')
 if actual['library']!=str(library)or actual['library_sha256']!=component['library_sha256']:raise ValueError('normal executable actual renderer mismatch')
 return target,dict(receipt=str(receipt),receipt_sha256=sha(receipt),source_main=main,actual_gpu_identity=actual)

def prepare(candidate,*,project=PRIVATE_PROJECT,normal_build_result=None,repository=None):
 require_no_games();project=Path(project).resolve(strict=True);source=Path(candidate).absolute()
 if source!=source.resolve(strict=True)or not source.is_relative_to(project/'evidence'):
  raise ValueError('canonical isolated candidate manifest required')
 m=json.loads(source.read_text())
 if m.get('kind')=='blood-echoes-game-pso-component':
  if not(m.get('schema')==1 and m.get('production')is False and
         m.get('coherent_renderer')is True and m.get('diagnostic_only')is False):
   raise ValueError('coherent preparation component required')
  library=Path(m['library']);want=m['library_sha256']
 else:library=Path(m['candidate']);want=m['sha256']
 from game_pso_component import sha as artifact_sha
 if not library.is_relative_to(project)or artifact_sha(library)!=want:
  raise ValueError('candidate library identity')
 if bool(normal_build_result)!=bool(repository):raise ValueError('normal build and source repository must be supplied together')
 normal,normal_provenance=normal_source(normal_build_result,m,project)if normal_build_result else(None,None)
 if normal is None:
  base=json.loads((project/'evidence/first-upload-gpu-app.json').read_text());verify(base,project/'backend')
 stamp=str(time.time_ns());app=project/'backend/bbport/bin'/('Bloodborne Metal Candidate QA '+stamp+'.app')
 if app.exists():raise ValueError('fresh private GPU app required')
 # Preserve the existing admission boundary: a DSO built in evidence is
 # copied byte-for-byte into the private backend, never admitted outside it.
 if normal is not None or not library.is_relative_to(project/'backend'):
  target_dir=project/'backend/bbport/bin'/('gpu-preparation-'+stamp);target_dir.mkdir(mode=0o700)
  copied=target_dir/library.name;shutil.copy2(library,copied)
  if artifact_sha(copied)!=want:raise ValueError('copied preparation DSO changed')
  library=copied
 if normal is not None:
  # Preserve raw DSO bytes. Only copies of the main executable are rewritten.
  from gpu_candidate_identity import resolve_gpu_edge
  host=library.parent
  if not host.is_relative_to(project/'backend')or host==project/'backend':raise ValueError('fresh owned normal host directory required')
  executable=host/'bb-gpu'
  if executable.exists():raise ValueError('fresh normal executable copy required')
  links=[line.strip().split(' (compatibility')[0]for line in subprocess.check_output(['/usr/bin/otool','-L',str(normal)],text=True).splitlines()[1:]]
  staged={};bindings=[]
  for edge in links:
   if edge.startswith(('/usr/lib/','/System/')):continue
   origin=resolve_gpu_edge(normal,edge);name=Path(edge).name
   if name.startswith('libbbgpu'):
    if sha(origin)!=want:raise ValueError('normal GPU source library mismatch')
    copied=library
   else:
    copied=host/name
    if name in staged or copied.exists():raise ValueError('ambiguous native dependency name')
    if subprocess.check_output(['/usr/bin/lipo','-archs',str(origin)],text=True).strip()!='arm64':raise ValueError('non-native normal dependency')
    shutil.copy2(origin,copied)
    if sha(copied)!=sha(origin):raise ValueError('normal dependency copy changed')
    staged[name]=dict(source=str(origin),sha256=sha(origin))
   bindings.append((edge,copied))
  shutil.copy2(normal,executable)
  for edge,copied in bindings:subprocess.run(['/usr/bin/install_name_tool','-change',edge,'@loader_path/'+os.path.relpath(copied,executable.parent),str(executable)],check=True)
  subprocess.run(['/usr/bin/codesign','--force','--sign','-',str(executable)],check=True)
  repository=Path(repository).resolve(strict=True);sys.path.insert(0,str(repository/'tools/macos'))
  import native_metal
  if Path(native_metal.__file__).resolve()!=repository/'tools/macos/native_metal.py':raise ValueError('original GPU bundle implementation identity')
  target=native_metal.create_native_gpu_bundle(project/'backend/bbport',executable,bundle=app,version='0.2.51',bundle_id='io.github.bbport.optimized.metal-game.qa.normal.'+stamp)
  normal_provenance['direct_dependency_copies']=staged
 else:
  shutil.copytree(Path(base['app']),app);target=app/'Contents/MacOS/bb-gpu'
 deps=[line.strip().split(' (compatibility')[0]for line in subprocess.check_output(
  ['/usr/bin/otool','-L',str(target)],text=True).splitlines()[1:]]
 hits=[d for d in deps if Path(d).name.startswith('libbbgpu')]
 if len(hits)!=1:raise ValueError('exactly one original renderer load edge required')
 subprocess.run(['/usr/bin/install_name_tool','-change',hits[0],str(library),str(target)],check=True)
 subprocess.run(['/usr/bin/codesign','--force','--deep','--sign','-',str(app)],check=True)
 meta=dict(target=str(target),library=str(library),library_sha256=want,app=str(app),
  source_candidate=str(source),source_candidate_sha256=artifact_sha(source),
  default_enabled=False,formal_app_modified=False)
 if normal_provenance:meta['normal_build']=normal_provenance;meta['historical_app_template_used']=False
 meta['load_identity']=verify(meta,project/'backend');out=project/'evidence'/('gpu-candidate-app-'+stamp+'.json')
 with out.open('x')as stream:json.dump(meta,stream,indent=2);stream.write('\n')
 return out

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--candidate',type=Path,required=True)
 ap.add_argument('--project',type=Path,default=PRIVATE_PROJECT)
 ap.add_argument('--normal-build-result',type=Path);ap.add_argument('--source-repository',type=Path);a=ap.parse_args()
 print(prepare(a.candidate,project=a.project,normal_build_result=a.normal_build_result,repository=a.source_repository))
if __name__=='__main__':main()
