"""Hierarchical BBAUDIT1 shader-module phases; never sum nested driver scopes."""
import collections
PHASES={79:'gcn_to_ir',80:'ir_to_spirv',83:'module_backend'}
def analyze_shader_phases(rows):
 modules=[x for x in rows if x['kind']==78];anchors=collections.Counter((x['pid'],x['tid'],x['start'])for x in modules)
 assigned=collections.defaultdict(list);orphan_phases=[]
 for row in rows:
  if row['kind']not in PHASES:continue
  candidates=[m for m in modules if (m['pid'],m['tid'])==(row['pid'],row['tid'])and m['start']<=row['start']and row['end']<=m['end']]
  if candidates:
   shortest=min(x['wall']for x in candidates);candidates=[x for x in candidates if x['wall']==shortest]
  if len(candidates)!=1:orphan_phases.append(row['seq']);continue
  m=candidates[0];assigned[m['pid'],m['seq']].append(row)
 sequences=collections.defaultdict(set)
 for row in rows:sequences[row['pid']].add(row['seq'])
 result=[]
 for m in modules:
  anchor=(m['pid'],m['tid'],m['start']);metadata=[x for x in rows if (x['pid'],x['tid'],x['start'])==anchor and x['kind']in(81,82)]
  identity=[x for x in metadata if x['kind']==81];returned=[x for x in metadata if x['kind']==82]
  unique=anchors[anchor]==1;identity_ok=unique and len(identity)==1 and identity[0]['arg']>>32<=6
  return_ok=unique and len(returned)==1 and returned[0]['arg']in(0,1)
  phases=assigned[m['pid'],m['seq']];bykind={k:[x for x in phases if x['kind']==k]for k in PHASES}
  first=min((x['seq']for x in identity),default=m['seq']);last=m['seq'];span=last-first+1
  publication=unique and 0<span<=16384 and sum(first<=x<=last for x in sequences[m['pid']])==span
  complete=identity_ok and return_ok and publication and all(len(bykind[k])==1 for k in PHASES)
  phase_rows={name:[dict(start_ns=x['start'],end_ns=x['end'],wall_ms=x['wall']/1e6,thread_cpu_ms=x['cpu']/1e6)for x in bykind[k]]for k,name in PHASES.items()}
  disjoint=all(a['end']<=b['start']for a,b in zip(sorted(phases,key=lambda x:x['start']),sorted(phases,key=lambda x:x['start'])[1:]))
  entry=dict(pid=m['pid'],tid=m['tid'],seq=m['seq'],program_hash=f"{m['arg']:016x}",start_ns=m['start'],end_ns=m['end'],wall_ms=m['wall']/1e6,thread_cpu_ms=m['cpu']/1e6,identity_complete=identity_ok,returned_marker=return_ok,nonzero_module_handle=bool(returned[0]['arg'])if return_ok else None,stage=identity[0]['arg']>>32 if identity_ok else None,permutation=identity[0]['arg']&0xffffffff if identity_ok else None,anchor_unique=unique,publication_interval_complete=publication,phase_intervals_disjoint=disjoint,complete=bool(complete and disjoint),phases=phase_rows)
  if entry['complete']:
   entry['other_module_wall_ms']=max(0,m['wall']-sum(x['wall']for x in phases))/1e6
  entry['backend_nested_scopes']={k:[dict(wall_ms=x['wall']/1e6,thread_cpu_ms=x['cpu']/1e6,arg=x['arg'])for x in rows if x['kind']==kind and (x['pid'],x['tid'])==(m['pid'],m['tid'])and m['start']<=x['start']and x['end']<=m['end']]for kind,k in[(16,'spirv_to_msl_total'),(10,'library_total'),(11,'driver_library_build')]}
  result.append(entry)
 backend_only=[dict(pid=x['pid'],tid=x['tid'],start_ns=x['start'],wall_ms=x['wall']/1e6,arg=x['arg'])for x in rows if x['kind']==16 and not any(m['pid']==x['pid']and m['tid']==x['tid']and m['start']<=x['start']and x['end']<=m['end']for m in modules)]
 return dict(modules=result,orphan_phase_sequences=orphan_phases,backend_calls_without_gcn_module=backend_only,qualification='Returned means function returned a handle, not valid shading. Child phases latch at admitted module entry. Nested backend probes may be gated or below threshold; absence is not zero. Permutation indices are run-local, not stable cross-run specialization keys. Thread CPU excludes compiler helper threads. No shader payloads recorded.')
