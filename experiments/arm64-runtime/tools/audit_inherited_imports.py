"""Read-only source parity, not execution of the original CPU runtime."""
from pathlib import Path
import argparse,hashlib,json,re,time

ITEMS=[('sceKernelMlock','3k6kx-zOOSQ#p#J'),('sceKernelGetModuleInfoFromAddr','f7KBOafysXo#p#J'),('__elf_phdr_match_addr','Fjc4-n1+y2g#p#J'),('__pthread_cxa_finalize','kbw4UHHSYy0#p#J'),('msync','tZY4+SZNFhA#p#J'),('_is_signal_return','crb5j7mkk1c#p#J'),('sigreturn','mo0bFmWppIw#p#J'),('__Ux86_64_setcontext','OjWstbIRPUo#p#J'),('__progname','djxxOmW6-aw#p#J')]
def resolver(text):
 start=text.index('uintptr_t runtime_resolve(');end=text.index('\nstatic const struct',start);return text[start:end]
def audit(original,prototype,imports):
 original=Path(original);prototype=Path(prototype);a=resolver((original/'runtime.c').read_text());b=resolver((prototype/'runtime.c').read_text());rows=[];sources=list(original.glob('*.c'));observed={fields[1]:fields[2]for line in Path(imports).read_text().splitlines()if len(fields:=line.split('\t'))>=3}
 for name,nid in ITEMS:
  hits=[]
  for source in sources:
   text=source.read_text()
   if nid in text or re.search(r'(?<![A-Za-z0-9_])'+re.escape(name)+r'(?![A-Za-z0-9_])',text):hits.append(source.name)
  rows.append(dict(name=name,nid=nid,original_c_source_hits=hits,prototype_observed_route=observed.get(name)))
 return dict(resolver_body_byte_identical=a==b,original_resolver_sha256=hashlib.sha256(a.encode()).hexdigest(),prototype_resolver_sha256=hashlib.sha256(b.encode()).hexdigest(),original_c_sources_checked=len(sources),rows=rows,qualification='Static source parity plus an existing prototype import inventory. Does not execute original CPU resolver or establish PS4 ABI semantics. Separate native GPU control verifies its fallback exports. No calls/success stubs installed.')
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--original-src',type=Path,required=True);ap.add_argument('--imports',type=Path,required=True);a=ap.parse_args();p=Path(__file__).resolve().parents[1];e=p/'evidence'/('inherited-import-source-audit-'+str(time.time_ns()));e.mkdir();r=audit(a.original_src,p/'vendor/bb_runtime/src',a.imports);(e/'result.json').write_text(json.dumps(r,indent=2)+'\n');print(e);print(json.dumps(r,indent=2))
