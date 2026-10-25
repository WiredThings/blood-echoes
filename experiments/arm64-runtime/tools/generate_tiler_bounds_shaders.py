from pathlib import Path
import subprocess,time,json,sys,shutil,hashlib
p=Path(__file__).resolve().parents[1];b=Path('/private/tmp/blood-echoes-arm64-metal-20261023/build');e=p/'evidence'/('tiler-bounds-generated-'+str(time.time_ns()));e.mkdir();inc=e/'include/video_core/host_shaders_msl';inc.mkdir(parents=True)
sys.path.insert(0,str(p/'tools'));from qa_build_guard import require_no_games
require_no_games();runs=[]
shader=e/"tiling.comp";shutil.copy2(p/"vendor/metal_api/shadps4/video_core/host_shaders/tiling.comp",shader)
for mode in ('micro','macro'):
 for bits in (8,16,32,64,96,128):
  name=f'tiling_{mode}_{bits}_comp';spv=e/(name+'.spv');out=inc/(name+'_msl.h')
  for cmd in [['/opt/homebrew/bin/glslang','--target-env','vulkan1.3','-o',str(spv),f'-DBITS_PER_PIXEL={bits}',f'-DIS_MACRO_TILED={int(mode=="macro")}',str(shader)],[str(b/'spirv_to_msl_header'),str(spv),str(out),name,'comp']]:
   r=subprocess.run(cmd,capture_output=True,text=True,timeout=30);runs.append(dict(command=cmd,exit=r.returncode,output=r.stdout+r.stderr));assert r.returncode==0,r.stderr
(e/'generate.json').write_text(json.dumps(runs,indent=2));(p/'evidence/tiler-bounds-generated-current.json').write_text(json.dumps(dict(evidence=str(e),include=str(e/'include'),shader_sha256=hashlib.sha256(shader.read_bytes()).hexdigest()),indent=2));print(e)
