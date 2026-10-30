#!/usr/bin/env python3
"""Actual scanner -> generated C -> ARM64 library -> bbcpu, authored bytes only."""
from pathlib import Path
import argparse,hashlib,json,os,shutil,struct,subprocess,time

SOURCE=Path(__file__).resolve().parents[1]/'vendor/bbcpu'
def run(argv,root,name,env=None,expected=0,pass_fds=()):
 result=subprocess.run(list(map(str,argv)),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,env=env,timeout=120,pass_fds=pass_fds)
 (root/(name+'.log')).write_text(result.stdout)
 if result.returncode!=expected:raise RuntimeError(f'{name}: {result.returncode}: {result.stdout[-2000:]}')
 return result.stdout
def fixture():
 data=bytearray(16384)
 ident=b'\x7fELF\x02\x01\x01'+bytes(9)
 data[:64]=struct.pack('<16sHHIQQQIHHHHHH',ident,3,62,1,4096,64,0,0,64,56,3,0,0,0)
 ph=[(1,5,0,0,0,8192,8192,4096),(1,4,8192,16384,0,8192,8192,4096),(0x6474e550,4,8192,16384,0,36,36,4)]
 for i,row in enumerate(ph):data[64+i*56:120+i*56]=struct.pack('<IIQQQQQQ',*row)
 data[4096:4103]=bytes.fromhex('4889f84801f0c3')
 data[4112:4122]=bytes.fromhex('0f3148c1e2204809d0c3')
 # A real indirect call to a runtime-supplied exit veneer; UD2 must never run.
 data[4160:4176]=bytes.fromhex('4889f84883ec08bf25000000ffd00f0b')
 data[8192:8228]=struct.pack('<BBBBiIiiiiii',1,0x1b,3,0x3b,0x3c,3,-0x3000,0x60,-0x2ff0,0x80,-0x2fc0,0xa0)
 cie=struct.pack('<II',16,0)+b'\x01zR\x00\x01\x78\x10\x01\x1b\x0c\x07\x08'
 data[8256:8256+len(cie)]=cie
 data[8288:8305]=struct.pack('<IIiIB',13,0x24,4096-(0x4060+8),7,0)
 data[8320:8337]=struct.pack('<IIiIB',13,0x44,4112-(0x4080+8),10,0)
 data[8352:8369]=struct.pack('<IIiIB',13,0x64,4160-(0x40a0+8),16,0)
 return data
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--build-dir',type=Path,required=True);p.add_argument('--work-dir',type=Path,required=True);a=p.parse_args()
 root=a.work_dir.absolute()
 if root.exists()or not root.parent.resolve().is_relative_to(Path('/private/tmp')):p.error('fresh private tmp work directory required')
 root.mkdir(mode=0o700);scan=root/'scan';scan.mkdir();gen=root/'gen';gen.mkdir()
 elf=root/'authored.elf';elf.write_bytes(fixture());start=time.monotonic()
 build=a.build_dir.resolve(strict=True)
 abi=json.loads((build/'bbcpu-abi-identity.json').read_text())
 assert abi['abi']==5 and abi['architecture']=='arm64' and abi['native_tsc']is True
 def check_headers():
  assert all(hashlib.sha256((SOURCE/name).read_bytes()).hexdigest()==digest for name,digest in abi['headers'].items())
 check_headers()
 image=bytearray(24576);data=elf.read_bytes();image[:8192]=data[:8192];image[16384:24576]=data[8192:16384]
 boot=root/'authored.boot';boot.write_bytes(struct.pack('<8s6Q',b'BBPROBE2',len(image),4096,2,1,0,0)+struct.pack('<6Q',0,8192,5,16384,8192,4)+struct.pack('<4Q',16768,0,4096,0)+image)
 artifact=root/'artifact'
 run([build/'bbcpu-code-preparer','--boot',boot,'--elf',elf,'--out',artifact],root,'effective-code')
 effective=json.loads((artifact/'effective-input.json').read_text());elf=artifact/'effective.elf'
 run([build/'bbcpu-scan',elf,scan,artifact/'pointers.txt'],root,'scan')
 functions=(scan/'functions.tsv').read_text().splitlines()
 offsets=[line.split('\t')[0]for line in functions[1:]if line.strip()]
 assert offsets==['0x1000','0x1010','0x1040'],offsets
 env={k:v for k,v in os.environ.items()if not k.startswith(('BB_','BBCPU_','DYLD_'))};env['BB_JIT']='0'
 run([build/'bbrecomp',elf,scan/'functions.tsv',gen/'recomp.c',*offsets],root,'generate',env)
 objects=[]
 for c in sorted(gen.glob('recomp*.c')):
  out=c.with_suffix('.o');run(['/usr/bin/clang','-arch','arm64','-std=gnu11','-O2','-DBBCPU_NATIVE_TSC=1','-ffp-contract=off','-fno-strict-aliasing','-fvisibility=hidden','-I'+str(SOURCE/'src'),'-I/opt/homebrew/include','-c',c,'-o',out],root,'compile-'+c.stem);objects.append(out)
 library=artifact/'librecomp.dylib'
 run(['/usr/bin/clang','-arch','arm64','-dynamiclib',*objects,'-o',library],root,'link')
 run(['/usr/bin/codesign','--force','--sign','-',library],root,'sign')
 library_sha=hashlib.sha256(library.read_bytes()).hexdigest()
 receipt=struct.pack('<8sQQ32s32s32s32s',b'BBAOT005',5,0x0100000c,bytes.fromhex(abi['header_contract_sha256']),bytes.fromhex(effective['effective_identity']),bytes.fromhex(effective['effective_elf_sha256']),bytes.fromhex(library_sha))
 (artifact/'bbcpu.aot-receipt').write_bytes(receipt)
 assert run([build/'bbcpu-static-admission-probe','--abi'],root,'compiled-abi').strip()==abi['header_contract_sha256']
 run([build/'bbcpu-static-admission-probe',boot,artifact],root,'admission-good')
 run([build/'bbcpu-static-admission-probe',boot,artifact,'--mutate-code'],root,'admission-changed-code',expected=2)
 def clone(name):
  out=root/name;out.mkdir(mode=0o700)
  for filename in('effective.elf','librecomp.dylib','bbcpu.aot-receipt'):shutil.copyfile(artifact/filename,out/filename)
  return out
 bad=clone('receipt-bad-contract');raw=bytearray(receipt);raw[24]^=1;(bad/'bbcpu.aot-receipt').write_bytes(raw)
 run([build/'bbcpu-static-admission-probe',boot,bad],root,'admission-bad-contract',expected=2)
 bad=clone('receipt-bad-library');raw=bytearray(library.read_bytes());raw[-1]^=1;(bad/'librecomp.dylib').write_bytes(raw)
 run([build/'bbcpu-static-admission-probe',boot,bad],root,'admission-bad-library',expected=2)
 bad=clone('receipt-wrong-architecture');raw=bytearray(library.read_bytes());struct.pack_into('<I',raw,4,0x01000007);(bad/'librecomp.dylib').write_bytes(raw)
 forged=bytearray(receipt);forged[120:152]=hashlib.sha256(raw).digest();(bad/'bbcpu.aot-receipt').write_bytes(forged)
 run([build/'bbcpu-static-admission-probe',boot,bad],root,'admission-wrong-architecture',expected=2)
 env['BB_JIT']='1'
 hit=run([build/'bbcpu-contract-probe',library,'static'],root,'static-hit',env)
 with library.open('rb')as pinned:
  run([build/'bbcpu-contract-probe',f'/dev/fd/{pinned.fileno()}','static'],root,'static-pinned-fd',env,pass_fds=(pinned.fileno(),))
 mismatch=run([build/'bbcpu-contract-probe',library,'changed'],root,'changed-fallback',env)
 assert 'at least 8192 calls'in hit and 'own translator returned changed result'in mismatch
 wrong=[]
 for c in sorted(gen.glob('recomp*.c')):
  obj=c.with_suffix('.wrong.o');run(['/usr/bin/clang','-arch','arm64','-std=gnu11','-O2','-I'+str(SOURCE/'src'),'-I/opt/homebrew/include','-c',c,'-o',obj],root,'wrong-abi-compile-'+c.stem);wrong.append(obj)
 wrong_library=root/'librecomp-wrong-abi.dylib'
 run(['/usr/bin/clang','-arch','arm64','-dynamiclib',*wrong,'-o',wrong_library],root,'wrong-abi-link')
 run([build/'bbcpu-contract-probe',wrong_library,'wrong-abi'],root,'wrong-abi-fallback',env)
 bad_source=gen/'bad-span.c'
 main_source=(gen/'recomp.c').read_text()
 malformed=main_source.replace('{0x1000, 7,','{0xffffffffffffffff, 7,').replace('{0x1010, 10,','{0x1010, 0xffffffffffffffff,')
 assert malformed!=main_source
 bad_source.write_text(malformed);bad_object=gen/'bad-span.o'
 run(['/usr/bin/clang','-arch','arm64','-std=gnu11','-O2','-DBBCPU_NATIVE_TSC=1','-I'+str(SOURCE/'src'),'-I/opt/homebrew/include','-c',bad_source,'-o',bad_object],root,'bad-span-compile')
 bad_library=root/'librecomp-bad-span.dylib'
 bad_objects=[bad_object if obj.name=='recomp.o'else obj for obj in objects]
 run(['/usr/bin/clang','-arch','arm64','-dynamiclib',*bad_objects,'-o',bad_library],root,'bad-span-link')
 run([build/'bbcpu-contract-probe',bad_library,'bad-span'],root,'bad-span-fallback',env)
 links=run(['/usr/bin/otool','-L',build/'bbcpu-contract-probe'],root,'host-dependencies')
 symbols=run(['/usr/bin/nm','-g',build/'bbcpu-contract-probe'],root,'host-symbols')
 assert not any(s in links+symbols for s in('FEXCore','FexGuestCpuBackend','fex_embedding','AetherPS4'))
 architecture=run(['/usr/bin/lipo','-archs',build/'bbcpu-contract-probe'],root,'host-architecture').strip()
 assert architecture=='arm64'
 check_headers()
 result=dict(status='passed',authored_only=True,game_started=False,fex_loaded=False,no_fex_dependency_or_global_symbol=True,architecture=architecture,hardware_tsc_domain_verified=True,wrong_abi_rejected=True,overflow_and_unmapped_spans_rejected=True,static_translator_owned_stop_verified=True,effective_input_and_receipt_admitted=True,code_contract_digest_and_architecture_controls=True,pinned_fd_actually_loaded=True,header_contract_sha256=abi['header_contract_sha256'],functions=len(offsets),static_hit_calls_lower_bound=8192,changed_code_fell_back_to='bbcpu',seconds=time.monotonic()-start,library_bytes=library.stat().st_size,library_sha256=library_sha)
 (root/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
if __name__=='__main__':main()
