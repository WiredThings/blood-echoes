"""Read-only per-thread self/ancestor accounting of a Darwin sample call tree.

Tree indentation follows the upstream bbcpu tools/recomp/sample_guest.py format.
Counts are wall-clock thread observations, NOT CPU time or additive frame costs.
Recursive ancestors are counted once per leaf to avoid inclusive double counts.
"""
import collections,json,re,sys
from pathlib import Path

def categories(frames):
    result=set()
    for f in frames:
        if any(n in f for n in('BbcpuBackend::Query','QueryGuestExecutableRange','HleVeneerAllocator::QueryExecutableRange','(anonymous namespace)::executable(')):result.add('code_query')
        if 'ArmResourceRuntime::'in f or 'ArmExtraHeaps::'in f:result.add('resource_hooks')
        if 'BbPayload::Gate::Mutation'in f:result.add('payload_gate_mutation')
    return result

def parse(text):
    rows={};stack=[];current=None;active=False
    def close(depth):
        while stack and stack[-1][0]>=depth:
            d,count,children,frame=stack.pop();own=count-children
            if own<0:raise ValueError('sample child counts exceed parent')
            if own and current is not None:
                fn=frame.split('  (in ',1)[0]
                current['self'][fn]+=own
                for cat in categories([x[3]for x in stack]+[frame]):current['ancestor'][cat]+=own
            if stack:stack[-1][2]+=count
    for line in text.splitlines():
        if line.startswith('Call graph:'):active=True;continue
        if not active:continue
        if line.startswith(('Total number in stack','Sort by top of stack','Binary Images:')):break
        match=re.match(r'^(\s*)([+!:| ]*)(\d+)\s+(.*)$',line)
        if not match:continue
        depth=len(match[1])+len(match[2]);count=int(match[3]);frame=match[4]
        thread=re.match(r'Thread_(\d+)(.*)',frame)
        if thread:
            close(0);key=thread[1]
            current=rows.setdefault(key,dict(thread=key,labels=set(),observations=0,self=collections.Counter(),ancestor=collections.Counter()))
            current['observations']+=count;current['labels'].add(thread[2].strip());continue
        if current is None:raise ValueError('frame before thread')
        close(depth);stack.append([depth,count,0,frame])
    close(0)
    if not rows:raise ValueError('missing Darwin sample call tree')
    result=[]
    for r in rows.values():
        total=r['observations'];self_count=sum(r['self'].values())
        if self_count!=total:raise ValueError('thread self counts do not cover observations')
        result.append(dict(thread=r['thread'],labels=sorted(r['labels']),observations=total,self_samples=self_count,
            ancestor_samples=dict(r['ancestor']),ancestor_percent={k:round(100*v/total,3)for k,v in r['ancestor'].items()},
            self_top=[dict(function=k,samples=v,percent=round(100*v/total,3))for k,v in r['self'].most_common(15)]))
    return dict(schema=1,qualification='Per-thread wall observations; ancestors overlap and are not CPU-time or frame-time percentages.',threads=result)

if __name__=='__main__':
    file=Path(sys.argv[1])
    if not file.is_file()or file.stat().st_size>64*1024*1024:raise ValueError('bounded sample file required')
    print(json.dumps(parse(file.read_text(errors='replace')),indent=2))
