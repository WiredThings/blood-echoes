#!/usr/bin/env python3
"""Real local compiler/cache controls, authored code only; no game or Cua."""
from pathlib import Path
import argparse, hashlib, json, os, shutil, struct, subprocess, sys
from run_bbcpu_authored_static import fixture

SOURCE=Path(__file__).resolve().parents[1]
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser(description=__doc__)
 for name in('build','compiler','linker','sdk','work'):p.add_argument('--'+name,type=Path,required=True)
 p.add_argument('--dependency-root',type=Path,action='append',required=True);p.add_argument('--include',type=Path,action='append',default=[]);a=p.parse_args()
 root=a.work.absolute();assert not root.exists()and root.parent.resolve()==Path('/private/tmp');root.mkdir(mode=0o700)
 source=root/'source.elf';source.write_bytes(fixture());data=source.read_bytes()
 image=bytearray(24576);image[:8192]=data[:8192];image[16384:]=data[8192:]
 boot=root/'authored.boot';boot.write_bytes(struct.pack('<8s6Q',b'BBPROBE2',len(image),4096,2,1,0,0)+struct.pack('<6Q',0,8192,5,16384,8192,4)+struct.pack('<4Q',16768,0,4096,0)+image)
 originals=(sha(boot),sha(source));logs=root/'logs';logs.mkdir();count=0
 env={k:v for k,v in os.environ.items()if not k.startswith(('BB_','BBCPU_','DYLD_'))}
 def run(argv,name,expected=0):
  nonlocal count
  count+=1;r=subprocess.run(list(map(str,argv)),env=env,capture_output=True,text=True,timeout=120)
  (logs/f'{count:02}-{name}.log').write_text(r.stdout+r.stderr)
  assert r.returncode==expected,(name,r.returncode,r.stdout,r.stderr)
  return r.stdout
 def effective(name,patch=None):
  out=root/name;argv=[a.build/'bbcpu-code-preparer','--boot',boot,'--elf',source,'--out',out]
  if patch:argv+=['--patches',patch]
  run(argv,name);return out
 base=effective('effective-a');cache=root/'cpu-cache';cache.mkdir(mode=0o700)
 def produce(effective,name):
  argv=[sys.executable,SOURCE/'tools/bbcpu_static_library.py','--effective',effective,'--work',cache,'--tools',a.build,'--abi',a.build/'bbcpu-abi-identity.json','--compiler',a.compiler,'--linker',a.linker,'--sdk',a.sdk]
  for path in a.dependency_root:argv+=['--dependency-root',path]
  for path in a.include:argv+=['--include',path]
  output=run(argv,name);events=[json.loads(line)for line in output.splitlines()if line.startswith('{')]
  assert events[-1]['event']=='done';return events[-1]
 cold=produce(base,'cold');assert cold['counters']['object_compiles']>0 and not cold['reused_ready']
 warm=produce(base,'warm');assert warm['reused_ready']and all(value==0 for value in warm['counters'].values())
 artifact=Path(cold['artifact']);run([a.build/'bbcpu-static-admission-probe',boot,artifact],'actual-admission')
 run([a.build/'bbcpu-contract-probe',artifact/'librecomp.dylib','static'],'actual-static-execution')
 # Fault injection touches only this freshly generated test library. Preserve
 # both its good bytes and the damaged generation when the new one is linked.
 good=root/'good-library-before-injection.dylib';shutil.copyfile(artifact/'librecomp.dylib',good)
 damaged=bytearray(good.read_bytes());damaged[-1]^=1;(artifact/'librecomp.dylib').write_bytes(damaged);damaged_sha=sha(artifact/'librecomp.dylib')
 run([a.build/'bbcpu-static-admission-probe',boot,artifact],'damaged-refusal',expected=2)
 repaired=produce(base,'local-relink');c=repaired['counters']
 assert c['generator_processes']==c['preprocessor_processes']==c['object_compiles']==0 and c['object_reuses']>0 and c['link_processes']==1,c
 assert Path(repaired['artifact'])!=artifact and sha(artifact/'librecomp.dylib')==damaged_sha and good.exists()
 run([a.build/'bbcpu-static-admission-probe',boot,repaired['artifact']],'repaired-admission')
 # CPU-code Mod -> original uses separate final entries and the shared object
 # content cache; unrelated resource text is never a compilation input.
 patch=root/'code-mod.bin';patch.write_bytes(b'BBPATCH2'+struct.pack('<2Q',0x400000,1)+struct.pack('<2Q',4100,1)+b'\x29')
 changed=effective('effective-b',patch);mod=produce(changed,'code-mod')
 assert mod['effective_identity']!=cold['effective_identity']and mod['artifact_key']!=cold['artifact_key']
 run([a.build/'bbcpu-static-admission-probe',boot,mod['artifact'],'--mutate-code'],'mod-actual-admission')
 back=produce(base,'back-to-original');assert back['reused_ready']and back['artifact_key']==repaired['artifact_key']and all(v==0 for v in back['counters'].values())
 (root/'unrelated-resource.txt').write_text('resource-only change')
 unrelated=produce(base,'unrelated-resource');assert unrelated['reused_ready']and all(v==0 for v in unrelated['counters'].values())
 assert originals==(sha(boot),sha(source))
 result=dict(status='passed',authored_only=True,game_started=False,formal_data_accessed=False,cold=cold,warm=warm,repaired=repaired,mod=mod,back_to_original=back,unrelated_resource=unrelated,damaged_generation_preserved=True,actual_producer_library_executed=True,sources_unchanged=True)
 (root/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
if __name__=='__main__':main()
