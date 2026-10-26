"""Stage the coherent ARM engine in the original app; never install or launch it.

The existing QA packager owns native relocation/ABI/tool closure. This final
staging step reuses its code payload, not its GUI identity, preferences or data.
"""
from pathlib import Path
import argparse,hashlib,json,os,plistlib,re,shutil,subprocess,sys

SOURCE=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(SOURCE/'tools/macos'))
from package_optimized import inspect_payload,loads,SYSTEM
from game_app import create_game_app,sign_game_app

FORMAL_ID='io.github.bbport.optimized'
QA_ID=re.compile(r'io\.github\.bbport\.optimized\.options-qa\.arm64\.gui\.[0-9]+')
ROLE_PATHS=('bin/bb-probe-metal','bin/Bloodborne Game.app/Contents/MacOS/bb-probe-metal',
 'bin/gpu-metal/libbbgpu.dylib','bin/Bloodborne Metal.app/Contents/MacOS/bb-gpu',
 'bin/gpu-metal-arm64/libbbgpu.dylib','bin/gpu-net/libbbnet.dylib',
 'bin/game-pso-preparer','bin/game-module-preparer')

def need(value,message):
 if not value:raise ValueError(message)
def sha(path):
 with Path(path).open('rb')as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def info(app):return plistlib.loads((app/'Contents/Info.plist').read_bytes())
def write(path,value):path.write_text(json.dumps(value,indent=2)+'\n')
def owned(path):
 path=Path(path).absolute();need(path==path.resolve(strict=True)and path.stat().st_uid==os.getuid(),'canonical owned staging input');return path

def check_relative_payload(root):
 """Keep internal executable aliases; never package external QA dependencies."""
 root=owned(root)
 for path in root.rglob('*'):
  if path.is_symlink():
   target=os.readlink(path)
   need(not os.path.isabs(target)and path.resolve(strict=True).is_relative_to(root),'external/absolute packaged link')
 # The existing public payload scan checks game/save/development data. It
 # accepts safe relative links in source mode; we checked them independently.
 rows=inspect_payload(root,source=True)
 from package_optimized import development_tool
 need(not any(development_tool(Path(row['path']))for row in rows),'development automation in staged application')
 need(not any(Path(row['path']).name in('ARM64-QA-MANIFEST.json','DEVELOPMENT-WORLD-WARPER.json')for row in rows),'QA/development manifest in application')
 return rows

def stage(*,qa_app,baseline,output,launcher_products,revision):
 from qa_build_guard import require_no_games
 require_no_games()
 qa=owned(qa_app);base=owned(baseline);products=owned(launcher_products)
 need(QA_ID.fullmatch(info(qa).get('CFBundleIdentifier','')),'declared isolated QA code source required')
 old_info=info(base);need(old_info.get('CFBundleIdentifier')==FORMAL_ID,'original formal app baseline required')
 need(re.fullmatch(r'[0-9a-f]{40}',revision or''),'exact source revision required')
 qa_engine=owned(qa/'Contents/Resources/bbport')
 native=json.loads((qa_engine/'NATIVE-METAL.json').read_text())
 need(native.get('game_runtime')=='fex-arm64'and native.get('production')is False,'isolated coherent FEX code required')
 for name,digest in native['frozen_runtime'].items():
  path=qa_engine/name;need(path==path.resolve(strict=True)and sha(path)==digest,'QA runtime identity changed')
 output=Path(output).absolute();parent=owned(output.parent)
 need(output.parent==parent and not os.path.lexists(output)and output.suffix=='.app','fresh canonical staging app required')
 need(not output.is_relative_to(Path('/Applications'))and not base.is_relative_to(output),'this tool never writes an installed app')
 launcher=products/'BBLauncher';resource=products/'BBLauncher_BBLauncher.bundle'
 need(launcher.is_file()and not launcher.is_symlink()and resource.is_dir()and not resource.is_symlink(),'current original GUI product required')
 need(subprocess.check_output(['/usr/bin/lipo','-archs',str(launcher)],text=True).strip()=='arm64','native GUI required')
 subprocess.run(['/usr/bin/codesign','--verify','--strict',str(base)],check=True,capture_output=True)
 subprocess.run(['/usr/bin/ditto','--norsrc','--noextattr',str(base),str(output)],check=True)
 engine=output/'Contents/Resources/bbport'
 # This directory exists only in our new app copy. No installed tree is erased.
 need(engine.is_relative_to(output)and not engine.is_symlink(),'fresh staging engine required')
 old_licenses=engine/'licenses';license_copy=output/'Contents/Resources/.prior-component-licenses'
 if old_licenses.is_dir():shutil.copytree(old_licenses,license_copy,symlinks=False)
 shutil.rmtree(engine)
 subprocess.run(['/usr/bin/ditto','--norsrc','--noextattr',str(qa_engine),str(engine)],check=True)
 if license_copy.exists():
  shutil.copytree(license_copy,engine/'licenses',dirs_exist_ok=True,symlinks=False);shutil.rmtree(license_copy)
 shutil.copy2(launcher,output/'Contents/MacOS/bbport')
 target=output/'Contents/Resources/BBLauncher_BBLauncher.bundle'
 if target.exists():shutil.rmtree(target)
 subprocess.run(['/usr/bin/ditto','--norsrc','--noextattr',str(resource),str(target)],check=True)
 final_info=dict(old_info)
 for key in('BBPreviewDataPath','BBPreviewPreferencesPath','BBQALaunchReceipt','BBRepoPath'):final_info.pop(key,None)
 final_info.update(BBSourceCommit=revision,LSMinimumSystemVersion='27.0')
 (output/'Contents/Info.plist').write_bytes(plistlib.dumps(final_info))
 # Reuse the original game wrapper/cover algorithm. Both executable-relative
 # spellings are equivalent for this MH_EXECUTE; normalize only this copied CPU
 # so the existing helper can relocate all dependencies to its nested location.
 cpu=engine/'bin/bb-probe-metal'
 for edge in loads(cpu):
  if edge.startswith('@executable_path/'):
   subprocess.run(['/usr/bin/install_name_tool','-change',edge,'@loader_path/'+edge[len('@executable_path/'):],str(cpu)],check=True)
 subprocess.run(['/usr/bin/codesign','--force','--sign','-',str(cpu)],check=True,capture_output=True)
 nested=create_game_app(output)
 nested_app=nested.parents[2];nested_info=info(nested_app);nested_info['LSUIElement']=True
 (nested_app/'Contents/Info.plist').write_bytes(plistlib.dumps(nested_info));sign_game_app(nested_app)
 gpu_app=engine/'bin/Bloodborne Metal.app';gpu_info=info(gpu_app)
 gpu_info.update(CFBundleIdentifier=FORMAL_ID+'.metal-game',CFBundleShortVersionString=final_info['CFBundleShortVersionString'],CFBundleVersion=final_info['CFBundleVersion'])
 (gpu_app/'Contents/Info.plist').write_bytes(plistlib.dumps(gpu_info))
 subprocess.run(['/usr/bin/codesign','--force','--sign','-',str(gpu_app)],check=True,capture_output=True)
 # No QA preference file, QA save/cache, absolute source-tree pointer or private
 # application manifest is adopted. Runtime code keeps its isolated branch.
 local=json.loads((engine/'ARM64-LOCAL-PREPARATION.json').read_text())
 for key in('runtime_project','source_repository'):local.pop(key,None)
 local.update(host_local_only=False,portable_installer=True,production=True)
 write(engine/'ARM64-LOCAL-PREPARATION.json',local)
 parity=json.loads((engine/'SOURCE-PARITY.json').read_text());parity.pop('previous_stage',None);write(engine/'SOURCE-PARITY.json',parity)
 component=json.loads((engine/'GAME-PSO-COMPONENT.json').read_text());component['production']=True;write(engine/'GAME-PSO-COMPONENT.json',component)
 native.update(production=True,source_revision=revision,
  renderer_architecture='native arm64 FEX game runtime and independent arm64 Metal host',
  frozen_runtime={name:sha(engine/name)for name in ROLE_PATHS})
 write(engine/'NATIVE-METAL.json',native)
 for name in ROLE_PATHS:
  binary=engine/name;need(subprocess.check_output(['/usr/bin/lipo','-archs',str(binary)],text=True).strip()=='arm64','non-native runtime role')
  for edge in loads(binary):
   if edge.startswith(SYSTEM):continue
   prefix=next((p for p in('@loader_path/','@executable_path/')if edge.startswith(p)),None)
   need(prefix is not None,'non-relative native runtime load edge')
   resolved=(binary.parent/edge[len(prefix):]).resolve(strict=True)
   need(resolved.is_file()and resolved.is_relative_to(engine),'runtime dependency escapes package')
  subprocess.run(['/usr/bin/codesign','--verify','--strict',str(binary)],check=True,capture_output=True)
 # Freeze after all relocation/signing operations, and before signing outer app.
 need(all(sha(engine/n)==h for n,h in native['frozen_runtime'].items()),'runtime changed during staging')
 rows=check_relative_payload(output)
 subprocess.run(['/usr/bin/codesign','--force','--sign','-',str(output)],check=True,capture_output=True)
 subprocess.run(['/usr/bin/codesign','--verify','--deep','--strict',str(output)],check=True,capture_output=True)
 need(info(output)['CFBundleIdentifier']==FORMAL_ID,'formal identity changed')
 return dict(schema=1,status='staged-not-installed',app=str(output),source_revision=revision,
  original_bundle_id_preserved=True,formal_data_modified=False,QA_progress_adopted=False,
  runtime_sha256=native['frozen_runtime'],GUI_sha256=sha(output/'Contents/MacOS/bbport'),
  payload_files=len(rows),game_started=False,acceptance_complete=False)

def main():
 ap=argparse.ArgumentParser(description=__doc__)
 for name in('qa-app','baseline','output','launcher-products'):ap.add_argument('--'+name,type=Path,required=True)
 ap.add_argument('--source-revision',required=True);args=ap.parse_args()
 result=stage(qa_app=args.qa_app,baseline=args.baseline,output=args.output,launcher_products=args.launcher_products,revision=args.source_revision)
 write(args.output.parent/'local-install-stage.json',result);print(json.dumps(result,indent=2))
if __name__=='__main__':main()
