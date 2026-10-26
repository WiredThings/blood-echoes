"""Build the ordinary same-source renderer/helper targets in isolated development.

Uses current CMake targets, never prepared objects or historical compile commands.
Dependency construction is separate and is not asserted by a successful build.
"""
from pathlib import Path
import argparse, json, os, subprocess, time
from configure_native_build import PRIVATE_RUNTIME, directory, renderer_inputs, need, sha

def inspect(source,build):
 source=directory(source);build=directory(build)
 selected=renderer_inputs(source,build)
 cache={}
 for line in (build/'CMakeCache.txt').read_text().splitlines():
  if line.startswith('CMAKE_COMMAND:INTERNAL='):cache['cmake']=line.split('=',1)[1]
 need('cmake'in cache,'configured CMake executable missing')
 cmake=Path(cache['cmake']).resolve(strict=True)
 need(cmake.is_file(),'canonical configured CMake executable required')
 units=json.loads((build/'compile_commands.json').read_text())
 sources={}
 for unit in units:
  if 'CMakeFiles/bbgpu.dir/'not in unit.get('command',''):continue
  path=Path(unit['file']).absolute()
  need(path==path.resolve(strict=True)and path.is_relative_to(source),'renderer source outside configured source tree')
  need(str(path)not in sources,'duplicate renderer translation unit')
  sources[str(path)]=sha(path)
 need(sources,'normal renderer has no translation units')
 return dict(selected=selected,cmake=str(cmake),cmake_sha256=sha(cmake),source_sha256=sources,
             command=[str(cmake),'--build',str(build),'--parallel','1','--target','bbgpu','game-pso-preparer','game-module-preparer'])

def build(*,source_root,renderer_build,output):
 from qa_build_guard import require_no_games
 from native_observer import NativeObserver
 from qa_memory_policy import MemoryPolicy
 from local_metal_builder import FixedRunner
 project=directory(PRIVATE_RUNTIME);out=Path(output).absolute()
 need(out.parent==project/'evidence'and out==out.resolve(strict=False)and not out.exists(),'fresh isolated build evidence required')
 directory(out.parent);plan=inspect(source_root,renderer_build);require_no_games()
 out.mkdir(mode=0o700);(out/'plan.json').write_text(json.dumps(plan,indent=2)+'\n')
 observer=NativeObserver();before=observer.system();policy=MemoryPolicy(before);start=time.monotonic();samples=[]
 class Guard:
  def check(self,**kw):
   if kw.get('preflight'):require_no_games()
   now=observer.system();elapsed=time.monotonic()-start;reason=policy.observe(elapsed,now)
   if not samples or elapsed-samples[-1]['seconds']>=1:samples.append(dict(seconds=elapsed,**now))
   need(not reason,reason or'build protection')
   need(elapsed<=900,'normal target build timeout')
 report=dict(schema=1,status='running',source_root=str(source_root),renderer_build=str(renderer_build),
             game_started=False,GPU_started=False,formal_data_modified=False,
             clean_dependency_build_proven=False,dependency_provenance='configured external prefixes; construction not verified')
 try:
  raw=FixedRunner(out).run(plan['command'],880,Guard(),cwd=source_root)
  (out/'build.log').write_bytes(raw)
  need(inspect(source_root,renderer_build)==plan,'normal build inputs changed')
  artifacts={name:Path(renderer_build)/name for name in('libbbgpu.dylib','game-pso-preparer','game-module-preparer')}
  result={}
  for name,path in artifacts.items():
   need(path==path.resolve(strict=True)and path.is_file()and path.stat().st_uid==os.getuid(),'owned normal artifact required')
   need(subprocess.check_output(['/usr/bin/lipo','-archs',str(path)],text=True).strip()=='arm64','normal artifact is not thin ARM64')
   result[name]=dict(path=str(path),sha256=sha(path))
  report.update(status='normal-targets-built',artifacts=result,renderer_translation_units=len(plan['source_sha256']))
  return report
 except BaseException as error:
  report.update(status='failed',error=str(error));raise
 finally:
  report.update(before=before,after=observer.system(),seconds=time.monotonic()-start,samples=samples)
  (out/'result.json').write_text(json.dumps(report,indent=2)+'\n');observer.close()

def main():
 p=argparse.ArgumentParser(description=__doc__)
 for name in('source-root','renderer-build','output'):p.add_argument('--'+name,type=Path,required=True)
 a=p.parse_args();result=build(source_root=a.source_root,renderer_build=a.renderer_build,output=a.output)
 print(json.dumps({k:v for k,v in result.items()if k!='samples'},indent=2))
if __name__=='__main__':main()
