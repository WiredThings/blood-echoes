#!/usr/bin/env python3
"""Read bounded diagnostic metadata after the private processes have stopped."""
import argparse,collections,json,re,struct,csv
from pathlib import Path
KINDS={1:'fex_block',2:'fex_invalidation_lock',3:'fex_ir',4:'fex_codegen',5:'fex_publish',6:'fex_code_buffer_lock',7:'fex_mapping_lock',8:'fex_code_pages_lock',10:'metal_library_total',11:'metal_library_build',12:'metal_pso',13:'metal_replay_wait',14:'metal_gpu_wait',15:'metal_submit_wait',16:'metal_spirv_to_msl_total',17:'metal_slow_packet_wall',18:'metal_packet_cpu_window',20:'ipc_total',21:'ipc_send',22:'ipc_reply',23:'ipc_handler',24:'ipc_writer_lock',25:'ipc_space_wait',26:'ipc_queue_write',30:'hle_wait',31:'guest_usleep',32:'hle_registry_lookup',33:'resource_hook',40:'frame_interval',41:'released_image_collection',42:'memory_report',46:'guest_gpu_submit',49:'guest_frame_cpu_window',50:'selected_condition_wait',54:'matching_condition_notification',58:'producer_thread_cpu_window',60:'remote_inline_retention',61:'submission_snapshot',62:'submission_preparation',63:'submission_queue',64:'submission_notify',65:'producer_condition_wait',70:'producer_condition_notification',73:'fex_unaligned_handler',74:'fex_unaligned_repair',75:'fex_unaligned_log_write',76:'fex_unaligned_publish',77:'fex_unaligned_rejected'}
def read(path):
 data=path.read_bytes()
 if len(data)<64:raise ValueError('short audit header')
 magic,version,pid,mono,utc,cap,issued,dropped=struct.unpack_from('<8s7Q',data)
 if magic!=b'BBAUDIT1' or version!=1 or cap!=16384 or len(data)!=64+cap*64:raise ValueError('unsupported audit format')
 rows=[];clock_rejections=[];clock_rejection_count=0
 for i in range(cap):
  seq,busy,kind,tid,start,wall,cpu,arg,epoch=struct.unpack_from('<QII6Q',data,64+i*64)
  if not seq or busy or seq>issued or (seq-1)%cap!=i:continue
  if wall>((1<<64)-1)-start:
   clock_rejection_count+=1
   if len(clock_rejections)<8:clock_rejections.append(dict(seq=seq,kind=kind,start_mono_ns=start,wall_raw=wall,reason='end timestamp overflow; probable unsigned duration underflow'))
   continue
  rows.append(dict(pid=pid,seq=seq,kind=kind,tid=tid,start=start,end=start+wall,wall=wall,cpu=cpu,arg=arg))
 return dict(pid=pid,issued=issued,dropped=dropped,overwritten=max(0,issued-cap),valid_rows=len(rows),clock_rejected_rows=clock_rejection_count,clock_rejection_examples=clock_rejections,all_published=(issued<=cap and dropped==0 and clock_rejection_count==0 and len(rows)==issued),rows=sorted(rows,key=lambda x:x['start']))
def publication_range(trace,first,last):
 """Prove a retained publication interval, never completeness of overwritten history."""
 low=max(1,trace['issued']-16384+1)
 valid_bounds=isinstance(first,int)and isinstance(last,int)and 0<first<=last<=trace['issued']and first>=low
 present={r['seq']for r in trace['rows']if valid_bounds and first<=r['seq']<=last}
 expected=last-first+1 if valid_bounds else 0
 return dict(first=first,last=last,retained_low=low,expected=expected,published=len(present),valid=bool(valid_bounds and len(present)==expected),whole_trace_complete=trace['all_published'],scope='only this retained sequence interval; earlier overwrite or drops remain reported')

def union(intervals):
 end=0;total=0
 for a,b in sorted(intervals):
  if b>end:total+=b-max(a,end);end=b
 return total

def summarize(rows,start,end,names):
 kinds={}
 for k,name in KINDS.items():
  selected=[r for r in rows if r['kind']==k and r['start']<end and r['end']>start]
  if not selected:continue
  bytid=collections.defaultdict(list)
  for r in selected:bytid[(r['pid'],r['tid'])].append((max(start,r['start']),min(end,r['end'])))
  kinds[name]=dict(events=len(selected),wall_sum_ms=round(sum(r['wall'] for r in selected)/1e6,3),thread_cpu_sum_ms=round(sum(r['cpu'] for r in selected)/1e6,3),wall_union_ms=round(union([(max(start,r['start']),min(end,r['end'])) for r in selected])/1e6,3),max_ms=round(max(r['wall'] for r in selected)/1e6,3),longest=[dict(pid=r['pid'],tid=r['tid'],wall_ms=round(r['wall']/1e6,3),cpu_ms=round(r['cpu']/1e6,3),start_mono_ns=r['start'],arg=names.get(r['arg'],str(r['arg'])) if k in (30,46,65) else r['arg']) for r in sorted(selected,key=lambda x:x['wall'],reverse=True)[:5]])
 if 'producer_thread_cpu_window'in kinds:kinds['producer_thread_cpu_window']['cpu_semantics']='whole same-producer notification interval; not exact CPU in clipped frame or FEX-only work'
 if 'guest_frame_cpu_window'in kinds:kinds['guest_frame_cpu_window']['cpu_semantics']='whole same-thread marker interval; not selected call CPU or exact clipped-frame CPU'
 if 'metal_packet_cpu_window'in kinds:kinds['metal_packet_cpu_window']['cpu_semantics']='CPU across broader sample window; packet lower=max(0,cpu-arg), upper=cpu; arg is bounded extra window in ns; not exact packet CPU'
 return kinds

def producer_condition_waits(rows,names):
 groups=collections.defaultdict(list)
 for r in rows:
  if r['kind']in(65,66,67,68,69):groups[(r['pid'],r['tid'],r['start'])].append(r)
 result=[]
 for key,group in groups.items():
  bykind=collections.defaultdict(list)
  for r in group:bykind[r['kind']].append(r)
  if not bykind[65]:continue
  complete=all(len(bykind[k])==1 for k in(65,66,67,68))
  for primary in bykind[65]:
   result.append(dict(pid=key[0],tid=key[1],start_mono_ns=key[2],end_mono_ns=primary['end'],wall_ms=primary['wall']/1e6,cpu_ms=primary['cpu']/1e6,operation=primary['arg'],name=names.get(primary['arg'],str(primary['arg'])),condition=bykind[66][0]['arg']if complete else None,mutex=bykind[67][0]['arg']if complete else None,result=bykind[68][0]['arg']if complete else None,metadata_complete=complete,token=bykind[69][0]['arg']if len(bykind[69])==1 and bykind[69][0]['arg']else None,token_complete=len(bykind[69])==1 and bool(bykind[69][0]['arg']),scope='argument-slot identities only; producer previously notified an admitted render wait; no guest dereference'))
 return sorted(result,key=lambda r:r['start_mono_ns'])

def producer_notifications(rows,names,waits):
 groups=collections.defaultdict(list)
 for r in rows:
  if r['kind']in(70,71,72):groups[(r['pid'],r['tid'],r['start'])].append(r)
 result=[]
 for key,group in groups.items():
  bykind=collections.defaultdict(list)
  for r in group:bykind[r['kind']].append(r)
  for primary in bykind[70]:
   complete=all(len(bykind[k])==1 for k in(70,71,72));token=primary['arg']&((1<<30)-1);condition=bykind[71][0]['arg']if complete else None;op=bykind[72][0]['arg']if complete else None
   matches=[w for w in waits if complete and w['metadata_complete']and w.get('token_complete')and w['pid']==key[0]and w['token']==token and w['condition']==condition and w['start_mono_ns']+20000000<=key[2]<=w['end_mono_ns']]
   result.append(dict(pid=key[0],tid=key[1],start_mono_ns=key[2],end_mono_ns=primary['end'],wall_ms=primary['wall']/1e6,cpu_ms=primary['cpu']/1e6,token=token,stage=(primary['arg']>>30)&3,result=primary['arg']>>32,condition=condition,operation=op,name=names.get(op,str(op)),metadata_complete=complete,matching_waits=len(matches),waiter_tids=[w['tid']for w in matches],scope='candidate notification of an outstanding >=20ms argument-slot wait; not proof this call uniquely woke the waiter'))
 return sorted(result,key=lambda r:r['start_mono_ns'])

def main():
 ap=argparse.ArgumentParser();ap.add_argument('evidence',type=Path);a=ap.parse_args();e=a.evidence
 traces=[read(p) for p in sorted(p for p in e.glob('stutter-*.bin') if re.fullmatch(r'stutter-\d+\.bin',p.name))];rows=[r for t in traces for r in t.pop('rows')]
 log=(e/'runtime.log').read_text(errors='replace');names={int(n):name for n,name in re.findall(r'BACHATA_FEX_HLE_BEGIN operation=(\d+) name=(\S+)',log)}
 events=[json.loads(l) for l in (e/'operator-events.jsonl').read_text().splitlines()] if (e/'operator-events.jsonl').exists() else []
 conditions=json.loads((e/'stutter-case-conditions.json').read_text())if(e/'stutter-case-conditions.json').exists()else{}
 report=dict(generic_condition_wait_scopes_suppressed=conditions.get('generic_condition_wait_scopes_suppressed',False),condition_wait_scope='explicit mode omits duplicate generic condition timing; admitted render waits and notifications are selected; optional producer waits and candidate worker notifications are reported separately'if conditions.get('generic_condition_wait_scopes_suppressed',False)else'legacy generic HLE scopes retained',slow_packet_audit=conditions.get('slow_packet_audit',False),coarse_audit=conditions.get('coarse_audit',False),traces=traces,events=events,warning='Nested categories and threads overlap; do not add them as frame latency. CPU-time excludes blocked/descheduled time; wall minus CPU is not uniquely lock wait. Sub-threshold operations are omitted. Ring overwrite is explicit.',windows=[])
 for event in events:
  if event.get('event')!='cycle_start':continue
  t=event['mono_ns'];report['windows'].append(dict(event=event,start_mono_ns=t-2000000000,end_mono_ns=t+12000000000,stages=summarize(rows,t-2000000000,t+12000000000,names)))
 # Frame intervals are now directly in the same monotonic trace; no CSV-origin inference.
 frames=[r for r in rows if r['kind']==40 and r['wall']>=40000000]
 csv_rows=list(csv.DictReader((e/'frames.csv').open())) if (e/'frames.csv').exists() else []
 observations=json.loads((e/'result.json').read_text()).get('samples',[])if(e/'result.json').exists()else[]
 report['producer_condition_waits']=producer_condition_waits(rows,names)
 report['producer_condition_metadata_complete']=all(x['metadata_complete']for x in report['producer_condition_waits'])
 report['producer_notifications']=producer_notifications(rows,names,report['producer_condition_waits'])
 report['packet_probe_status']=[dict(pid=x['pid'],tid=x['tid'],mono_ns=x['start'],anchors=x['arg']&0xffffffff,slow=(x['arg']>>32)&0xffff,rejected=(x['arg']>>48)&0xffff,saturated=(x['arg']&0xffffffff)==0xffffffff or ((x['arg']>>32)&0xffff)==0xffff or (x['arg']>>48)==0xffff)for x in rows if x['kind']==19]
 report['packet_identities']=[dict(pid=x['pid'],tid=x['tid'],start_mono_ns=x['start'],wall_ms=x['wall']/1e6,kind=(x['arg']>>32)-1 if x['arg']>>32 else None,payload_bytes=x['arg']&0xffffffff)for x in rows if x['kind']==43]
 report['task_targets']=[dict(pid=x['pid'],tid=x['tid'],start_mono_ns=x['start'],wall_ms=x['wall']/1e6,image_offset=x['arg'])for x in rows if x['kind']==44]
 report['guest_frame_markers']=[dict(pid=x['pid'],tid=x['tid'],seq=x['seq'],mono_ns=x['start'],operation=x['arg'],name=names.get(x['arg'],str(x['arg'])))for x in rows if x['kind']==45]
 report['guest_frame_status']=[dict(pid=x['pid'],tid=x['tid'],seq=x['seq'],mono_ns=x['start'],calls=x['arg']&0xffffffff,invalid=(x['arg']>>32)&0xffff,markers=x['arg']>>48)for x in rows if x['kind']==47]
 report['guest_frame_accounting']=[dict(pid=x['pid'],tid=x['tid'],seq=x['seq'],mono_ns=x['start'],calls=x['arg']&0xffffffff,completed=x['arg']>>32)for x in rows if x['kind']==48]
 guest_groups=collections.defaultdict(list)
 for x in report['guest_frame_markers']:guest_groups[x['pid'],x['tid']].append(x)
 report['long_frames']=[]
 for r in sorted(frames,key=lambda x:x['wall'],reverse=True)[:12]:
  item=dict(pid=r['pid'],tid=r['tid'],serial=r['arg'],start_mono_ns=r['start'],end_mono_ns=r['end'],frame_ms=round(r['wall']/1e6,3),stages=summarize([a for a in rows if a['kind']!=40],r['start'],r['end'],names))
  item['guest_frame_boundaries']=[]
  for key,points in guest_groups.items():
   points.sort(key=lambda x:x['mono_ns']);before=[x for x in points if x['mono_ns']<=r['start']];after=[x for x in points if x['mono_ns']>=r['end']];inside=[x for x in points if r['start']<x['mono_ns']<r['end']]
   adjacent=before[-1:]+inside+after[:1];item['guest_frame_boundaries'].append(dict(pid=key[0],tid=key[1],last_before=before[-1]if before else None,inside=inside,first_after=after[0]if after else None,max_adjacent_marker_gap_ms=max((b['mono_ns']-a['mono_ns'])/1e6 for a,b in zip(adjacent,adjacent[1:]))if len(adjacent)>1 else None,warning='End-of-call markers are rate-limited>=40ms; timestamp association only, not frame-ID mapping'))
  item['packet_cpu_bounds']=[dict(pid=a['pid'],tid=a['tid'],window_start_mono_ns=a['start'],window_end_mono_ns=a['end'],cpu_upper_ms=a['cpu']/1e6,cpu_slack_ms=a['arg']/1e6,cpu_lower_ms=max(0,a['cpu']-a['arg'])/1e6)for a in rows if a['kind']==18 and a['start']<r['end']and a['end']>r['start']]
  item['overlapping_supervisor_observations']=[dict(seconds=x['seconds'],pressure=x['pressure'],swap_mib=x['swap_mib'],**x['observation'])for x in observations if 'observation'in x and x['observation']['start_mono_ns']<r['end']and x['observation']['end_mono_ns']>r['start']]
  if 0<r['arg']<=len(csv_rows):
   csv_row=csv_rows[r['arg']-1]
   try:
    matched=abs(float(csv_row['frame_ms'])-r['wall']/1e6)<0.02
    item['csv_interval_matches']=matched
    if matched:item['frame_csv']=csv_row
   except (ValueError,TypeError,KeyError):item['csv_interval_matches']=False
  report['long_frames'].append(item)
 if rows:report['all_retained']=summarize(rows,min(r['start'] for r in rows),max(r['end'] for r in rows),names)
 (e/'stutter-summary.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({k:v for k,v in report.items() if k not in ('all_retained','windows','long_frames','events','packet_probe_status','packet_identities','task_targets','guest_frame_markers','guest_frame_status','guest_frame_accounting')},indent=2));print('windows',len(report['windows']),'long frames',len(report['long_frames']))
if __name__=='__main__':main()
