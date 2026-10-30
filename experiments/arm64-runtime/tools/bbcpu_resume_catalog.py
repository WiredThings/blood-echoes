"""Feed real installed hook continuations to the unchanged upstream generator."""
from bisect import bisect_right

def continuations(rows,sites,exclusions=(),blocked=()):
    def need(ok,why):
        if not ok:raise ValueError(why)
    need(isinstance(sites,list)and len(sites)<=64,'invalid runtime continuation catalog')
    entries=sorted(rows);starts=[start for start,size in entries]
    need(len(set(starts))==len(starts),'ambiguous original function starts')
    result=[];seen=set();blocked=set(blocked)
    for site in sites:
        need(isinstance(site,dict)and set(site)=={'site','covered','resume','phase'},'invalid installed hook entry')
        at,n,resume=site['site'],site['covered'],site['resume']
        need(all(type(v)is int for v in(at,n,resume))and 0<=at<2**64 and 0<n<2**64-at and resume==at+n,'invalid installed hook range')
        need(site['phase']in('resource-hooks','extra-heaps','game-menu'),'unrecognized installed hook phase')
        i=bisect_right(starts,at)-1
        need(i>=0,'installed hook has no original function')
        start,size=entries[i];end=start+size
        need(start<=at<resume<end and all(other>=end or other+length<=at for other,length in entries[:i]),'continuation outside unambiguous original function')
        need(resume not in seen and resume not in starts,'duplicate/conflicting continuation')
        need(not any(resume<lo+length and lo<end for lo,length in exclusions),'continuation overlaps excluded code slot')
        seen.add(resume)
        # The original generator deliberately retains setjmp callers in its
        # translator. A continuation may not bypass that same restriction.
        if start not in blocked:result.append((resume,end-resume))
    return sorted(result)

def blocked_callers(scan):
    imports=scan/'imports.tsv'
    if not imports.exists():return set()
    jumps={int(a,16)for a,name in(line.split('\t')for line in imports.read_text().splitlines()[1:]if line)if name in('setjmp','_setjmp','sigsetjmp')}
    return {int(a,16)for a,b in(line.split('\t')for line in(scan/'calls.tsv').read_text().splitlines()[1:]if line)if int(b,16)in jumps}

def compilation_catalog(scan,sites,exclusions):
    rows=[]
    for line in(scan/'functions.tsv').read_text().splitlines()[1:]:
        if not line:continue
        fields=line.split('\t');start,size=int(fields[0],16),int(fields[1])
        if not any(start<lo+length and lo<start+size for lo,length in exclusions):rows.append((start,size))
    fragments=continuations(rows,sites,exclusions,blocked_callers(scan))
    starts=[start for start,size in sorted(rows)]
    (scan/'continuation-prefixes.tsv').write_text('start\tresume\n'+''.join(f'{starts[bisect_right(starts,resume)-1]:#x}\t{resume:#x}\n'for resume,size in fragments))
    output=scan/'compilation-functions.tsv'
    # Only real address/length fields are needed by bbrecomp. Do not invent
    # scanner instruction counts for fragments the scanner did not enumerate.
    output.write_text('address\tsize\n'+''.join(f'{start:#x}\t{size}\n'for start,size in sorted(rows+fragments)))
    return output,[f'{start:#x}'for start,size in sorted(rows+fragments)],len(fragments)
