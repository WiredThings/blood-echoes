"""Small native rejection controls before lazy Device creation; no game."""
from pathlib import Path
import argparse,hashlib,json,os,time
import game_pso_batch as batch
from prepare_game_psos import wait_owned_worker,dyld_gpu_image,verify_loaded_gpu
from warm_archive_manifest import resolve_gpu_identity
from native_observer import NativeObserver
from qa_memory_policy import MemoryPolicy
from qa_build_guard import require_no_games

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--project',type=Path,required=True);ap.add_argument('--worker',type=Path,required=True);ap.add_argument('--evidence',type=Path,required=True);a=ap.parse_args();P=a.project.resolve(strict=True);E=a.evidence.absolute();assert E.parent==P/'evidence'and not E.exists();E.mkdir(mode=0o700);root=P/'state'/('native-batch-protocol-'+str(time.time_ns()));root.mkdir(mode=0o700);tmp=root/'tmp';tmp.mkdir();observer=NativeObserver();before=observer.system();policy=MemoryPolicy(before);start=time.monotonic();w=None;result=dict(status='started',game_started=False,formal_data_modified=False,root=str(root),before=before)
 def check():
  require_no_games();reason=policy.observe(time.monotonic()-start,observer.system())
  if reason:raise RuntimeError(reason)
 try:
  check();identity=resolve_gpu_identity(a.worker,P);env=dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin',HOME=str(root),TMPDIR=str(tmp),BB_GPU_USER_DIR=str(root),DYLD_PRINT_LIBRARIES='1');w=batch.Worker(executable=a.worker,phase='prepare',allow_compile=False,environment=env,root=root,check=check,clock=time.monotonic,start=start,seconds=20,wait=wait_owned_worker);rows=[];loaded=set()
  for n,(label,data,wrong_sha)in enumerate([('wrong-packet-sha',b'{}\n',True),('wrong-packet-policy',b'{"allow_compile":true}\n',False),('malformed-json',b'{not-json}\n',False)],1):
   packet=root/f'packet-{n}.json';packet.write_bytes(data);scratch=root/f'source-{n}';scratch.mkdir();sha='0'*64 if wrong_sha else hashlib.sha256(data).hexdigest();r=w.request(packet=packet,packet_sha256=sha,scratch=scratch,ordinal=n);assert r['exit']==65
   values=[json.loads(line.split(' ',1)[1])for line in r['lines']if line.startswith('GAME_PSO_RESULT ')];assert len(values)==1 and values[0]['status']=='rejected'and values[0]['artifact_publication_observed']is False
   for line in r['lines']:
    image=dyld_gpu_image(line,r['pid'])
    if image is not None:loaded.add(image)
   rows.append(dict(label=label,exit=r['exit'],result=values[0],sequence=r['sequence'],request_id=r['request_id']))
  w.close();assert w.failed==3 and w.proc.returncode==65;log=w.log.read_text();assert 'GPU: Metal device 'not in log;assert 'Metal game PSO artifact event:'not in log;result.update(status='passed-no-device-native-rejections',rows=rows,batch_exit=65,processed=3,failed=3,partial_not_success=True,no_device_log=True,no_artifact_events=True,actual_loaded_gpu=verify_loaded_gpu(loaded,identity))
 except BaseException as error:
  result.update(status='failed',error=str(error))
  if w is not None:w.close(abort=True)
 finally:
  result.update(seconds=time.monotonic()-start,after=observer.system());observer.close();(E/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(E/'result.json',result['status'])
 return 0 if result['status']=='passed-no-device-native-rejections'else 65
if __name__=='__main__':raise SystemExit(main())
