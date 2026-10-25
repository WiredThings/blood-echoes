"""Metadata-only speculative discovery lead; never asserts full specialization equivalence."""
import collections
from shader_phase_analysis import analyze_shader_phases,HW_STAGE_NAMES

def analyze(rows):
 groups=collections.defaultdict(list)
 for x in rows:
  if x['kind']in(84,85):groups[x['pid'],x['tid']].append(x)
 probes=[];invalid=[]
 for thread,items in groups.items():
  # macOS can give multiple calls the same microsecond timestamp. Pair in
  # per-thread publication order; do not treat a timestamp as a unique token.
  items.sort(key=lambda x:x['seq']);index=0
  while index<len(items):
   h=items[index];m=items[index+1]if index+1<len(items)else None
   paired=h['kind']==84 and m is not None and m['kind']==85 and h['start']==m['start']
   if not paired:
    invalid.append(dict(thread=thread,sequences=[h['seq']]));index+=1;continue
   index+=2;first,last=h['seq'],m['seq'];sequences={x['seq']for x in rows if x['pid']==thread[0]}
   metadata_valid=m['arg']>>32 in(1,2,3)and m['arg']&0xffffffff<=6
   published=0<last-first<=16384 and len([q for q in sequences if first<=q<=last])==last-first+1
   if not metadata_valid or not published:
    invalid.append(dict(thread=thread,sequences=[h['seq'],m['seq']]));continue
   reason=m['arg']>>32;stage=m['arg']&0xffffffff
   probes.append(dict(pid=thread[0],tid=thread[1],start_ns=h['start'],sequence=h['seq'],program_hash=f"{h['arg']:016x}",stage=stage,stage_name=HW_STAGE_NAMES[stage],reason={1:'program_missing',2:'permutation_missing',3:'template_missing'}[reason]))
 modules=analyze_shader_phases(rows)['modules'];leads=[]
 for module in modules:
  if not module['complete']:continue
  matching=[p for p in probes if p['pid']==module['pid']and p['tid']!=module['tid']and p['stage']==module['stage']and p['program_hash']==module['program_hash']and p['start_ns']<=module['start_ns']]
  for reason in ('program_missing','permutation_missing','template_missing'):
   candidates=[p for p in matching if p['reason']==reason]
   if not candidates:continue
   first=min(candidates,key=lambda x:x['start_ns']);lead=(module['start_ns']-first['start_ns'])/1e6
   leads.append(dict(module_sequence=module['seq'],program_hash=module['program_hash'],stage_name=module['stage_name'],permutation=module['permutation'],reason=reason,first_discovery_lead_ms=lead,module_wall_ms=module['wall_ms'],full_specialization_equivalence_proven=False))
 return dict(probes=probes,invalid_probe_pairs=invalid,module_leads=leads,overflow_markers=sum(x['kind']==86 for x in rows),qualification='Earliest observed hash/stage discovery only; no full specialization or immutable resource snapshot proof. Program may already exist without a template. Worker budgets are 128 unique tuples per thread/process, not reset per gate; absence is not zero opportunity. No compilation is scheduled and no draw is skipped.')

if __name__=='__main__':
 import json,sys
 from analyze_stutter import read
 trace=read(__import__('pathlib').Path(sys.argv[1]));out=analyze(trace['rows']);out['trace']={k:v for k,v in trace.items()if k!='rows'};print(json.dumps(out,indent=2))
