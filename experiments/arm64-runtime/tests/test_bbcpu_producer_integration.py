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
 # An authored real entry detour executes its original prefix + HLE callback
 # in the translator and returns into a statically compiled middle-of-function
 # suffix. This verifies actual execution, guest RBP/RSP preservation, and a
 # changed suffix rejecting BOTH affected table entries without fake hashes.
 code=bytearray(fixture());code[4096:4108]=bytes.fromhex('554889e54889f84801f05dc3');struct.pack_into('<I',code,8300,12)
 resume_source=root/'resume-source.elf';resume_source.write_bytes(code)
 def resume_boot(path,raw):
  img=bytearray(24576);img[:8192]=raw[:8192];img[16384:]=raw[8192:]
  path.write_bytes(boot.read_bytes()[:-24576]+img)
 before=root/'resume-before.boot';resume_boot(before,code)
 analysis=root/'resume-analysis';run([a.build/'bbcpu-code-preparer','--boot',before,'--elf',resume_source,'--out',analysis],'resume-analysis')
 stub=bytes.fromhex('554889e54889f850b8260000000f0558e9')+struct.pack('<i',4103-(512+21))
 code[512:512+len(stub)]=stub;code[4096:4103]=b'\xe9'+struct.pack('<i',512-4101)+b'\x90\x90'
 after=root/'resume-after.boot';resume_boot(after,code)
 selected=root/'resume-effective';run([a.build/'bbcpu-code-preparer','--boot',after,'--elf',resume_source,'--out',selected],'resume-effective')
 (selected/'analysis').mkdir();shutil.copyfile(analysis/'effective.elf',selected/'analysis/effective.elf')
 descriptor=selected/'effective-input.json';meta=json.loads(descriptor.read_bytes());meta.update(resume_entries=[dict(site=4096,covered=7,resume=4103,phase='resource-hooks')],analysis_elf_sha256=sha(selected/'analysis/effective.elf'));descriptor.write_text(json.dumps(meta))
 resumed=produce(selected,'resume-produce');run([a.build/'bbcpu-static-admission-probe',after,resumed['artifact']],'resume-final-image-admission')
 run([a.build/'bbcpu-contract-probe',Path(resumed['artifact'])/'librecomp.dylib','resume'],'resume-actual-static-body')
 run([a.build/'bbcpu-contract-probe',Path(resumed['artifact'])/'librecomp.dylib','resume-changed'],'resume-changed-body-refused')
 # A suffix can jump to an out-of-range shared epilogue while its RSP still
 # contains the original prologue. Its translator boundary must refer to the
 # ORIGINAL function's return slot, not the suffix's current stack pointer.
 code=bytearray(fixture());code[4096:4112]=bytes.fromhex('554889e54889f8534801f0e9')+struct.pack('<i',4208-4112);code[4208:4211]=b'\x5b\x5d\xc3';struct.pack_into('<I',code,8300,16)
 branch_source=root/'branch-source.elf';branch_source.write_bytes(code);branch_before=root/'branch-before.boot';resume_boot(branch_before,code)
 branch_analysis=root/'branch-analysis';run([a.build/'bbcpu-code-preparer','--boot',branch_before,'--elf',branch_source,'--out',branch_analysis],'branch-analysis')
 code[512:512+len(stub)]=stub;code[4096:4103]=b'\xe9'+struct.pack('<i',512-4101)+b'\x90\x90';branch_after=root/'branch-after.boot';resume_boot(branch_after,code)
 branch_effective=root/'branch-effective';run([a.build/'bbcpu-code-preparer','--boot',branch_after,'--elf',branch_source,'--out',branch_effective],'branch-effective')
 (branch_effective/'analysis').mkdir();shutil.copyfile(branch_analysis/'effective.elf',branch_effective/'analysis/effective.elf');descriptor=branch_effective/'effective-input.json';meta=json.loads(descriptor.read_bytes());meta.update(resume_entries=[dict(site=4096,covered=7,resume=4103,phase='resource-hooks')],analysis_elf_sha256=sha(branch_effective/'analysis/effective.elf'));descriptor.write_text(json.dumps(meta))
 branch=produce(branch_effective,'branch-produce');run([a.build/'bbcpu-contract-probe',Path(branch['artifact'])/'librecomp.dylib','resume-branch'],'branch-actual-original-stack-boundary')
 assert originals==(sha(boot),sha(source))
 result=dict(status='passed',authored_only=True,game_started=False,formal_data_accessed=False,cold=cold,warm=warm,repaired=repaired,mod=mod,back_to_original=back,unrelated_resource=unrelated,resume_fragment_actual_execution=True,resume_changed_body_refused=True,resumed=resumed,branch_translator_boundary=True,branch=branch,damaged_generation_preserved=True,actual_producer_library_executed=True,sources_unchanged=True)
 (root/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
if __name__=='__main__':main()
