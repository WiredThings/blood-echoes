"""Existing CPU cache identity domains, bound to a parameterised private plan."""
from pathlib import Path
import argparse,hashlib,json,os,struct

def need(ok,why):
 if not ok:raise ValueError(why)
def combine(domain,rows):
 h=hashlib.sha256();h.update(domain.encode()+b'\0')
 for label,value in sorted(rows.items()):
  raw=label.encode();h.update(struct.pack('<Q',len(raw)));h.update(raw);h.update(bytes.fromhex(value))
 return h.hexdigest()
def digest_file(path,allowed):
 p=Path(path).absolute();need(not p.is_symlink()and p==p.resolve(strict=True),'canonical identity input');need(any(p.is_relative_to(root)for root in allowed),'identity input outside admitted roots');before=p.stat();need(p.is_file()and before.st_uid==os.getuid()and 0<before.st_size<=256*1024*1024,'bounded owned identity input')
 with p.open('rb')as f:h=hashlib.file_digest(f,'sha256').hexdigest()
 after=p.stat();need((before.st_ino,before.st_size,before.st_mtime_ns)==(after.st_ino,after.st_size,after.st_mtime_ns),'identity input changed');return h

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--plan',type=Path,required=True);ap.add_argument('--runtime-part',action='append',default=[]);ap.add_argument('--bridge-part',action='append',default=[]);ap.add_argument('--header',type=Path,required=True);ap.add_argument('--manifest',type=Path,required=True);a=ap.parse_args();plan=json.loads(a.plan.read_text());root=Path(plan['execution_root']);stage=Path(plan['stage']);need(a.plan.absolute()==root/'plan.json'and root.parent==Path(plan['project'])/'evidence'and root.name.startswith('resource-cpu-execution-'),'private identity plan');need(a.header.absolute()==stage/'generated/bb_cpu_cache_build.h'and a.manifest.absolute()==root/'cpu_cache_build.json','exact private identity outputs')
 allowed=[Path(plan['project']),Path(plan['source_snapshot']),Path(__file__).resolve().parent]
 def parts(rows):
  result={}
  for row in rows:
   label,path=row.split('=',1);need(label and label.isascii()and label not in result,'unique identity label');result[label]=digest_file(path,allowed)
  need(result,'identity parts required');result['builder-source']=digest_file(Path(__file__).resolve(),allowed);return result
 runtime,bridge=parts(a.runtime_part),parts(a.bridge_part);report=dict(schema=1,runtime_parts=runtime,bridge_parts=bridge,runtime_build=combine('BE-common-runtime-build-v1',runtime),bridge_build=combine('BE-common-bridge-build-v1',bridge))
 def literal(v):return ','.join('0x'+v[i:i+2]for i in range(0,len(v),2))
 header='// Generated from actual linked archives and private frontend/host inputs.\n#pragma once\n#include "bb_cpu_cache_identity.h"\nnamespace BbCacheBuild {\n'
 header+='inline constexpr BbCpuCache::Digest Runtime={'+literal(report['runtime_build'])+'};\ninline constexpr BbCpuCache::Digest Bridge={'+literal(report['bridge_build'])+'};\n}\n'
 for path,text in[(a.header,header),(a.manifest,json.dumps(report,sort_keys=True,indent=2)+'\n')]:
  need(not path.exists(),'identity outputs must be fresh');tmp=path.with_suffix(path.suffix+'.new')
  with tmp.open('x')as f:f.write(text)
  os.replace(tmp,path)
 print('CPU cache actual private build identity:',report['runtime_build'],report['bridge_build'])
if __name__=='__main__':main()
