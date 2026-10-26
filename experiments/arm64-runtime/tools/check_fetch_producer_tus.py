"""Bounded original-TU compile controls; no renderer link or game/GPU start.

Reuses a recorded native build's actual include/ABI flags, substitutes current
source root, and compiles only the two edited TUs. Private temporary objects are
deleted after nm checks. The published result contains commands/SHA/counts only.
"""
from pathlib import Path
import argparse,hashlib,json,os,re,select,subprocess,tempfile,time

ROOT=Path(__file__).resolve().parents[1]
EDITED=['vendor/metal_api/shadps4/shader_recompiler/frontend/fetch_shader.cpp','vendor/metal_api/shadps4/video_core/pipeline_cache_core.cpp','vendor/metal_api/shadps4/video_core/pipeline_cache_core_serialization.cpp','vendor/metal_api/shadps4/video_core/cache_storage.cpp']
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser();p.add_argument('--baseline',type=Path,default=ROOT/'evidence/tiling-library-integration-20261026/native/build.json');p.add_argument('--output',type=Path);a=p.parse_args();baseline=a.baseline.resolve(strict=True);manifest=json.loads(baseline.read_text());unit=next(u for u in manifest['units']if'game_artifact_native'in u['source'])
 base=[v.replace(manifest['source_root'],str(ROOT))for v in unit['command']][:-1];base.remove('-c');at=base.index('-o');del base[at:at+2];base=[v for v in base if not re.fullmatch(r'-O[0-3szg]|-DBB_DEV_FETCH_CAPTURE=.*',v)]
 base+=['-I'+str(ROOT/'vendor/metal_api'/name)for name in ('layer','games','cp')]+['-O0']
 out=(a.output or ROOT/'evidence'/('fetch-producer-tu-controls-'+str(time.time_ns()))).absolute();assert not out.exists();out.mkdir(parents=True)
 sources=EDITED+['vendor/metal_api/shadps4/shader_recompiler/frontend/fetch_shader.h','vendor/metal_api/shadps4/shader_recompiler/compile_snapshot.h','vendor/metal_api/shadps4/shader_recompiler/runtime_info.h','vendor/metal_api/shadps4/shader_recompiler/profile.h','src/module_profile_bool_fields.inc','src/fetch_compile_observer.h','src/fetch_compile_producer.h','src/fetch_register_snapshot.h','src/fetch_register_store.h','src/fetch_stage_association.h','src/fetch_module_proof.h','src/fetch_companion_store.h','src/fetch_companion_mailbox.h','src/fetch_compile_canonical.h']
 projection='vendor/metal_api/shadps4/shader_recompiler/fetch_compile_projection.h'
 if(ROOT/projection).is_file():sources.append(projection)
 before={name:sha(ROOT/name)for name in sources};results=[];started=time.monotonic()
 with tempfile.TemporaryDirectory(prefix='fetch-producer-tu-',dir='/private/tmp')as temp:
  for flag in(0,1):
   for i,relative in enumerate(EDITED):
    target=Path(temp)/f'tu-{flag}-{i}.o';command=base+['-DBB_DEV_FETCH_CAPTURE='+str(flag),'-c','-o',str(target),str(ROOT/relative)]
    run=subprocess.run(command,text=True,capture_output=True,timeout=90);record=dict(source=relative,source_sha256=before[relative],macro=flag,command=command,exit=run.returncode,diagnostics=run.stderr[-12000:]);results.append(record)
    if run.returncode:raise RuntimeError(json.dumps(record,indent=2))
    names=subprocess.check_output(['/usr/bin/nm','-u',str(target)],text=True);hooks=[line.strip()for line in names.splitlines()if any(v in line for v in('BbFetchCapture','BbFetchStore','ObserveCapturedSharp','ObserveCapturedFetch'))];record['collector_symbol_refs']=hooks;record['object_sha256']=sha(target)
    if flag==0:assert not hooks,'disabled TU references collector helpers'
    elif i<2:assert hooks,'enabled TU did not reach collector hooks'
    if flag==0:
     # Stream/discard preprocess output; never save generated shader/include bytes.
     pp_command=base+['-DBB_DEV_FETCH_CAPTURE=0','-E','-P',str(ROOT/relative)];proc=subprocess.Popen(pp_command,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL);forbidden=0;output_bytes=0;tail=b''
     deadline=time.monotonic()+90
     try:
      while True:
       remaining=deadline-time.monotonic();assert remaining>0,'preprocessor timeout'
       ready,_,_=select.select([proc.stdout],[],[],min(remaining,1))
       if not ready:continue
       part=os.read(proc.stdout.fileno(),65536)
       if not part:break
       data=tail+part;forbidden+=sum(data.count(word)for word in(b'BbFetchCapture',b'BbFetchStore',b'ObserveCapturedSharp',b'ObserveCapturedFetch',b'fetch_record'));tail=data[-64:];output_bytes+=len(part);assert output_bytes<=64*1024*1024,'preprocessor byte budget'
      assert proc.wait(timeout=max(.1,deadline-time.monotonic()))==0 and forbidden==0,'disabled preprocessing retained collector'
     finally:
      if proc.poll()is None:proc.kill();proc.wait(timeout=5)
     record['disabled_preprocess']=dict(command=pp_command,bytes=output_bytes,collector_identifiers=forbidden,output_retained=False)
 assert before=={name:sha(ROOT/name)for name in sources},'sources changed during TU controls'
 summary=dict(schema=1,status='passed',baseline=str(baseline),baseline_sha256=sha(baseline),source_sha256=before,controls=results,seconds=time.monotonic()-started,objects_retained=False,game_started=False,GPU_started=False,renderer_linked=False,formal_data_modified=False,qualification='Two actual changed translation units compiled with current sources and recorded native ABI flags at O0, macro OFF/ON; not a coherent complete renderer build or gameplay test.')
 (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(out/'summary.json');print(json.dumps(dict(status=summary['status'],controls=len(results),seconds=summary['seconds'],macro_off_collector_symbols=0,game_started=False,GPU_started=False),indent=2))
if __name__=='__main__':main()
