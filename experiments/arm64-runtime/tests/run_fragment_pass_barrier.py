#!/usr/bin/env python3
"""Short, windowless Metal pass-boundary correctness controls. Never starts a game.

Uses existing configured authored targets and the existing capability_environment.
The chosen QA state must pass the renderer's unchanged directory admission.
--mutations compiles two private scheduler variants; configured objects/caches stay intact.
"""
from pathlib import Path
import argparse, hashlib, json, shlex, subprocess, sys, tempfile, time
D=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(D/'tools'))
from launcher_runtime_profile import capability_environment, owned_dir

def need(ok,why):
    if not ok: raise ValueError(why)

def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def mutated_binaries(build,out):
    lines=subprocess.check_output(['ninja','-C',str(build),'-t','commands','mtl-scheduler-test'],text=True).splitlines()
    rows=[shlex.split(line)for line in lines if ' -c 'in line and any(t.endswith('/mtl_scheduler.mm')for t in shlex.split(line))]
    need(len(rows)==1,'one configured real scheduler test compile role required');row=rows[0]
    source=next(Path(t)for t in row if t.endswith('/mtl_scheduler.mm'));body=source.read_text()
    link=shlex.split(lines[-1]);link=link[link.index('/usr/bin/clang++'):];link=link[:link.index('&&')]if'&&'in link else link
    original_object=row[row.index('-o')+1];result={}
    for name,old,new in (
      ('reset-query','descriptor.visibilityResultType = MTLVisibilityResultTypeAccumulate','descriptor.visibilityResultType = MTLVisibilityResultTypeReset'),
      ('repeat-clear','loadAction=MTLLoadActionLoad','loadAction=MTLLoadActionClear')):
        need(old in body,'exact mutation anchor absent: '+name)
        cpp=out/(name+'.mm');cpp.write_text(body.replace(old,new));args=[];i=0
        while i<len(row):
            item=row[i];i+=1
            if item in('-MT','-MF'):i+=1;continue
            if item=='-MD':continue
            args.append(item)
        obj=out/(name+'.o');args[args.index('-o')+1]=str(obj);args[args.index(str(source))]=str(cpp)
        run=subprocess.run(args,cwd=build,capture_output=True,text=True,timeout=60)
        (out/(name+'-compile.log')).write_text(run.stdout+run.stderr);need(run.returncode==0,'mutation compile failed')
        binary=out/name;args=list(link);args[args.index(original_object)]=str(obj);args[args.index('-o')+1]=str(binary)
        run=subprocess.run(args,cwd=build,capture_output=True,text=True,timeout=60)
        (out/(name+'-link.log')).write_text(run.stdout+run.stderr);need(run.returncode==0,'mutation link failed');result[name]=binary
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--renderer-build',type=Path,required=True);p.add_argument('--qa-state',type=Path,required=True)
    p.add_argument('--full',action='store_true');p.add_argument('--mutations',action='store_true');a=p.parse_args()
    build=a.renderer_build.absolute();need(build==build.resolve(strict=True),'canonical configured build required');qa=owned_dir(a.qa_state)
    out=Path(tempfile.mkdtemp(prefix='blood-echoes-fragment-barrier-',dir='/private/tmp'));rows=[]
    cases=[]
    for coalesce in('0','1'):
        cases.append(('render-list-'+coalesce,build/'mtl-render-list-test',dict(BB_MTL_COALESCE_BARRIERS=coalesce),0))
        for hold in('0','1'):
            cases.append(('scheduler-'+hold+'-'+coalesce,build/'mtl-scheduler-test',dict(BB_TEST_FRAGMENT_BARRIER_ONLY='1',BB_MTL_HOLD=hold,BB_MTL_COALESCE_BARRIERS=coalesce),0))
    cases += [
        ('replay-thread',build/'mtl-scheduler-test',dict(BB_TEST_FRAGMENT_BARRIER_ONLY='1',BB_TEST_FRAGMENT_POST_REPLAY='1',BB_MTL_HOLD='1'),0),
        ('required-fence',build/'mtl-scheduler-test',dict(BB_TEST_FRAGMENT_BARRIER_ONLY='1',BB_MTL_FENCES='0'),0),
        ('reject-unknown-raw-state',build/'mtl-scheduler-test',dict(BB_TEST_REJECT_RAW_BARRIER='1'),-6),
    ]
    for hold in('0','1'):
        cases.append(('upgraded-residency-'+hold,build/'mtl-scheduler-test',dict(BB_TEST_FRAGMENT_BARRIER_ONLY='1',BB_TEST_FRAGMENT_UPGRADE='1',BB_MTL_PRECISE_RESIDENCY='1',BB_MTL_HOLD=hold),0))
    if a.full:
        # The legacy TestHeldPass/TestGpuQueue contract explicitly requires HOLD=1.
        # Direct mode is covered by the dedicated matrix above, not a misconfigured legacy suite.
        cases += [('full-scheduler-held',build/'mtl-scheduler-test',dict(BB_MTL_HOLD='1'),0)]
    if a.mutations:
        for name,binary in mutated_binaries(build,out).items():
            cases.append(('mutation-'+name,binary,dict(BB_TEST_FRAGMENT_BARRIER_ONLY='1',BB_MTL_HOLD='1'),1))
    identities={str(binary):digest(binary)for _,binary,_,_ in cases}
    try:
        for name,binary,values,want in cases:
            with capability_environment(D,qa_root=qa,prefix='metal-fragment-controls-')as env:
                env.update(MTL_DEBUG_LAYER='1',**values);start=time.monotonic()
                r=subprocess.run([str(binary)],env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=45)
                log=out/(name+'.log');log.write_text(r.stdout)
                row=dict(case=name,status=r.returncode,expected=want,passed=r.returncode==want,seconds=time.monotonic()-start,log=str(log));rows.append(row);print(json.dumps(row),flush=True)
                need(r.returncode==want,'control failed: '+str(log))
                if name.startswith('mutation-'):need('FAIL 'in r.stdout,'mutation must fail a real correctness assertion')
        need(all(digest(Path(path))==sha for path,sha in identities.items()),'test executable changed during run')
    finally:
        (out/'result.json').write_text(json.dumps(dict(cases=rows,binaries=identities,game_started=False,GUI_or_capture_used=False,API_validation=True,
          qualification='Short authored correctness tests only; no full-scene GPU time, FPS or memory acceptance.'),indent=2)+'\n')
        print(out,flush=True)
if __name__=='__main__':main()
