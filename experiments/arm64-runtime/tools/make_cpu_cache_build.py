#!/usr/bin/env python3
"""Bind the shared compiled translator/bridge into both CPU-cache executables."""
from pathlib import Path
import argparse,hashlib,json,os,stat,struct,tempfile
P=Path(__file__).resolve().parents[1]
def need(value,why):
 if not value:raise ValueError(why)
def directory(path):
 path=Path(path).absolute();info=path.lstat()
 need(path==path.resolve(strict=True)and stat.S_ISDIR(info.st_mode)and info.st_uid==os.getuid(),'owned canonical build/source directory required')
 return path
def stamp(info):return(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_uid)
def digest_file(path,roots=None):
 path=Path(path).absolute();roots=roots or(P,)
 need(path==path.resolve(strict=True)and any(path.is_relative_to(root)for root in roots),'build identity input escaped declared roots or is linked')
 before=path.lstat();need(stat.S_ISREG(before.st_mode)and before.st_uid==os.getuid()and 0<before.st_size<=64*1024*1024,'owned bounded regular build identity input required')
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
 with os.fdopen(fd,'rb')as stream:
  need(stamp(os.fstat(stream.fileno()))==stamp(before),'build identity input changed before open')
  digest=hashlib.file_digest(stream,'sha256').hexdigest()
  need(stamp(os.fstat(stream.fileno()))==stamp(before),'build identity input changed while reading')
 need(path==path.resolve(strict=True)and stamp(path.lstat())==stamp(before),'build identity path changed while reading')
 return digest
def output_path(path,build):
 path=Path(path).absolute()
 need(path.is_relative_to(build)and path!=build and path.parent==path.parent.resolve(),'identity output must stay inside the independent build root')
 if path.exists()or path.is_symlink():
  need(path==path.resolve(strict=True)and stat.S_ISREG(path.lstat().st_mode)and path.stat().st_uid==os.getuid(),'identity output is linked or unowned')
 return path
def write_output(path,text,build):
 path=output_path(path,build);path.parent.mkdir(parents=True,exist_ok=True);directory(path.parent)
 if path.exists():
  if path.read_text()==text:return
 fd,temporary=tempfile.mkstemp(prefix=path.name+'.',suffix='.new',dir=path.parent)
 try:
  with os.fdopen(fd,'w')as stream:stream.write(text);stream.flush();os.fsync(stream.fileno())
  need(path.parent==path.parent.resolve(strict=True),'identity output parent changed')
  os.replace(temporary,path)
 finally:
  if os.path.exists(temporary):os.unlink(temporary)

def combine(domain,rows):
 h=hashlib.sha256();h.update(domain.encode()+b'\0')
 for label,digest in sorted(rows.items()):
  raw=label.encode();h.update(struct.pack('<Q',len(raw)));h.update(raw);h.update(bytes.fromhex(digest))
 return h.hexdigest()

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--runtime-part',action='append',default=[]);ap.add_argument('--bridge-part',action='append',default=[]);ap.add_argument('--provenance-part',action='append',default=[]);ap.add_argument('--header',type=Path,required=True);ap.add_argument('--manifest',type=Path,required=True)
 ap.add_argument('--source-root',type=Path,default=P);ap.add_argument('--build-root',type=Path);ap.add_argument('--renderer-root',type=Path);a=ap.parse_args()
 source=directory(a.source_root);build=directory(a.build_root or source);renderer=directory(a.renderer_root or source);roots=tuple(dict.fromkeys((source,build,renderer)))
 output_path(a.header,build);output_path(a.manifest,build)
 def parts(args,required=True):
  out={}
  for row in args:
   label,path=row.split('=',1);need(label and label.isascii()and label not in out,'unique ASCII build identity label required')
   out[label]=digest_file(Path(path),roots)
  if required:
   need(out,'build identity parts required');out['builder-source']=digest_file(Path(__file__),roots)
  return out
 runtime,bridge=parts(a.runtime_part),parts(a.bridge_part)
 report=dict(schema=2,runtime_parts=runtime,bridge_parts=bridge,provenance_parts=parts(a.provenance_part,False),runtime_build=combine('BE-common-runtime-build-v1',runtime),bridge_build=combine('BE-common-bridge-build-v1',bridge),source_root=str(source),build_root=str(build),renderer_root=str(renderer))
 def literal(hex):return ','.join('0x'+hex[i:i+2]for i in range(0,len(hex),2))
 header='// Generated from actual linked archives and frontend/bridge inputs; do not edit.\n#pragma once\n#include "bb_cpu_cache_identity.h"\nnamespace BbCacheBuild {\n'
 header+='inline constexpr BbCpuCache::Digest Runtime={'+literal(report['runtime_build'])+'};\n'
 header+='inline constexpr BbCpuCache::Digest Bridge={'+literal(report['bridge_build'])+'};\n}\n'
 for path,text in [(a.header,header),(a.manifest,json.dumps(report,indent=2)+'\n')]:
  write_output(path,text,build)
 print('CPU cache common build identity:',report['runtime_build'])
if __name__=='__main__':main()
