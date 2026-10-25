"""Build a standalone canonical layout emitter; never load renderer or cached code."""
from pathlib import Path
import hashlib,json,re,subprocess,time
from qa_build_guard import require_no_games
P=Path(__file__).resolve().parents[1];require_no_games();e=P/'evidence'/('metadata-layout-'+str(time.time_ns()));e.mkdir();meta=json.loads((P/'evidence/texture-expansion-build.json').read_text());cmd=list(meta['compile']);obj=e/'layout.o';cmd[cmd.index('-o')+1]=str(obj);cmd[cmd.index('-MF')+1]=str(obj.with_suffix('.d'));cmd[-1]=str(P/'tests/metadata_layout.cpp');cmd.insert(1,'-Wno-invalid-offsetof');binary=e/'layout'
profile_source=(P/'vendor/metal_api/shadps4/shader_recompiler/profile.h').read_text()
profile_fields=re.findall(r'^\s*(u32|u64|bool)\s+(\w+)\s*(?:\{[^{}]*\})?;',profile_source,re.M);assert len(profile_fields)>40 and len({n for t,n in profile_fields})==len(profile_fields)
(e/'profile_fields.inc').write_text(''.join('PROFILE_FIELD('+n+')\n'for t,n in profile_fields));cmd.insert(1,'-I'+str(e))
with(e/'build.log').open('w')as log:
 subprocess.run(cmd,cwd='/private/tmp/blood-echoes-arm64-metal-20261023/build',stdout=log,stderr=subprocess.STDOUT,check=True,timeout=90)
 subprocess.run(['/usr/bin/clang++','-arch','arm64',str(obj),'-o',str(binary)],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=30)
deps=subprocess.check_output(['/usr/bin/otool','-L',str(binary)],text=True);assert all(line.strip().startswith(('/usr/lib/','/System/'))for line in deps.splitlines()[1:]),deps
text=subprocess.check_output([str(binary)],text=True,timeout=10,env=dict(PATH='/usr/bin:/bin',HOME=str(e)));layout=json.loads(text);assert layout['schema']==1 and layout['pointer_bytes']==8
source=P/'vendor/metal_api/shadps4';headers=['common/serdes.h','common/hash.h','shader_recompiler/backend/bindings.h','shader_recompiler/profile.h','shader_recompiler/specialization.h','shader_recompiler/info.h','shader_recompiler/resource.h','shader_recompiler/runtime_info.h','shader_recompiler/frontend/fetch_shader.h','shader_recompiler/ir/passes/srt.h','video_core/pipeline_cache_core_types.h','video_core/pipeline_cache_core_serialization.cpp'];layout['source_sha256']={x:hashlib.sha256((source/x).read_bytes()).hexdigest()for x in headers};layout['binary_sha256']=hashlib.sha256(binary.read_bytes()).hexdigest();layout['profile_fields']=[dict(type=t,name=n)for t,n in profile_fields];assert all('Profile.'+n in layout for t,n in profile_fields);layout['renderer_loaded']=False;layout['cached_code_registered']=False;fixtures=e/'fixtures';fixtures.mkdir();layout['authored_writer_fixtures']={}
for mode in ['plain','fetch','motion','geometry','graphics','compute','profile-padding']:
 path=fixtures/(mode+'.bin');result=json.loads(subprocess.check_output([str(binary),'--fixture',str(path),mode],text=True,timeout=10,env=dict(PATH='/usr/bin:/bin',HOME=str(e))));result.update(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest());layout['authored_writer_fixtures'][mode]=result
(e/'layout.json').write_text(json.dumps(layout,indent=2)+'\n');(P/'evidence/metadata-layout-current.json').write_text(json.dumps(dict(evidence=str(e),layout=str(e/'layout.json')))+'\n');print(e)
