#!/usr/bin/env python3
"""Real FEX authored-byte controls; never launches a game or clears a cache."""
from pathlib import Path
import argparse,hashlib,json,os,stat,subprocess,sys,time

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--binary',type=Path,required=True)
    args=ap.parse_args()
    tools=Path(__file__).resolve().parents[1]/'tools';sys.path.insert(0,str(tools))
    from qa_build_guard import require_no_games
    project=Path('/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype')
    binary=args.binary
    if not binary.is_absolute()or binary!=binary.resolve(strict=True)or binary.name!='test_fex_prepared_disk':
        raise ValueError('canonical actual test binary required')
    s=binary.stat()
    if not stat.S_ISREG(s.st_mode)or s.st_uid!=os.getuid()or s.st_mode&0o022:
        raise ValueError('owned test binary required')
    digest=hashlib.sha256(binary.read_bytes()).hexdigest()
    require_no_games()
    tag=f'unit-catalog-controls-{time.time_ns()}'
    root=project/'state'/tag;evidence=project/'evidence'/tag
    root.mkdir(mode=0o700);evidence.mkdir(mode=0o700)
    rows=[]
    def run(mode,state,address):
        require_no_games()
        if hashlib.sha256(binary.read_bytes()).hexdigest()!=digest:raise ValueError('test binary changed')
        log=evidence/f'{len(rows)}-{mode}.log'
        start=time.monotonic()
        with log.open('x')as stream:
            result=subprocess.run([str(binary),mode,str(state),address],stdout=stream,stderr=subprocess.STDOUT,timeout=40)
        rows.append(dict(mode=mode,returncode=result.returncode,seconds=time.monotonic()-start,log=str(log)))
        report=dict(status='running',binary=str(binary),binary_sha256=digest,runs=rows,game_started=False,formal_data_modified=False)
        (evidence/'result.json').write_text(json.dumps(report,indent=2)+'\n')
        print(mode,result.returncode,flush=True)
        if result.returncode:raise RuntimeError(f'control failed; retained {log}')
    # Hints inside the Darwin embedding allocator's guest region, no MAP_FIXED.
    for name,consumer in [('normal','load'),('changed','load-file-changed'),('bad-catalog','load-bad-catalog'),('missing-catalog','load-missing-catalog')]:
        state=root/name;state.mkdir(mode=0o700)
        run('generate',state,'0x7000000000');run(consumer,state,'0x7100000000')
    state=root/'legacy';state.mkdir(mode=0o700)
    run('generate-legacy',state,'0x7000000000');run('load-legacy',state,'0x7100000000')
    run('upgrade',state,'0x7000000000');run('load',state,'0x7100000000')
    report=json.loads((evidence/'result.json').read_text());report['status']='passed'
    (evidence/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    print('Evidence:',evidence)

if __name__=='__main__':main()
