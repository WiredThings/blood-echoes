#!/usr/bin/env python3
"""Two real generated units through the existing Producer.object, not a full rebuild."""
from pathlib import Path
import argparse,concurrent.futures,json,os,subprocess,sys,time,types
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from bbcpu_launch import component,private
from bbcpu_static_library import Producer,work_root,compile_flags,content_closure,key,VENDOR,digest
from native_observer import NativeObserver
from run_game import usage
from qa_build_guard import require_no_games

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--stage',type=Path,required=True);p.add_argument('--generated',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--units',nargs=2,default=['recomp_0012b','recomp_0003d']);a=p.parse_args();require_no_games()
    doc,_=component(a.stage);generated=private(a.generated);assert len(set(a.units))==2
    sources=[generated/(name+'.c')for name in a.units];assert all(f.is_file()and f.parent==generated for f in sources)
    a.output.mkdir(mode=0o700);private(a.output)
    compiler=Path(doc['toolchain']['compiler']).resolve(strict=True);resource=Path(subprocess.check_output([str(compiler),'-print-resource-dir'],text=True).strip()).resolve(strict=True)/'include'
    flags=compile_flags(resource,[VENDOR/'src',*[Path(x)for x in doc['toolchain']['includes']]])
    roots=[Path(x)for x in doc['toolchain']['dependency_roots']]
    rows=[];observer=NativeObserver()
    for jobs in(1,2):
        root=a.output/('jobs-'+str(jobs));root.mkdir(mode=0o700);work_root(root)
        producer=Producer(types.SimpleNamespace(work=root,compiler=compiler))
        compiler_key=key(content_closure(producer.closure(compiler,roots),compiler))
        objects=root/'objects';scratch=root/'preprocessed';objects.mkdir();scratch.mkdir()
        start=time.monotonic();peak=0;per_process={};samples=[]
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs)as pool:
            tasks=[pool.submit(producer.object,s,flags,compiler_key,objects,scratch)for s in sources]
            while not all(t.done()for t in tasks):
                pending=[os.getpid()];seen=set();processes={}
                while pending:
                    parent=pending.pop()
                    if parent in seen:continue
                    seen.add(parent)
                    for pid,ppid,path in observer.processes(parent):
                        processes[pid]=(pid,ppid,path)
                        if pid!=parent and pid not in seen:pending.append(pid)
                measured=usage([f'{pid} {ppid} 0 {path}'for pid,ppid,path in processes.values()],include_tools=True)
                clang=[r for r in measured if r['role']=='clang'];total=sum(r['footprint_bytes']for r in clang);peak=max(peak,total)
                for r in clang:per_process[r['pid']]=max(per_process.get(r['pid'],0),r['footprint_bytes'])
                system=observer.system();samples.append(dict(seconds=time.monotonic()-start,compiler_footprint_bytes=total,**system))
                time.sleep(.1)
            outputs=[t.result()for t in tasks]
        row=dict(jobs=jobs,seconds=time.monotonic()-start,compiler_peak_mib=peak/2**20,largest_process_mib=max(per_process.values(),default=0)/2**20,
                 objects=[dict(key=fp,sha256=digest(file))for file,fp in outputs],counters=producer.count,sampled_not_allocator_HWM=True,shared_pages_may_be_double_counted=True)
        rows.append(row);(root/'samples.json').write_text(json.dumps(samples)+'\n');print(json.dumps(row),flush=True)
    observer.close();assert rows[0]['objects']==rows[1]['objects'],'concurrency changed object bytes or semantic keys'
    result=dict(status='passed',units=a.units,runs=rows,object_bytes_identical=True,whole_game_recompiled=False,existing_cache_modified=False,game_started=False)
    (a.output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
if __name__=='__main__':main()
