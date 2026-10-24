from pathlib import Path
import sys
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'))
from analyze_stutter import producer_condition_waits,producer_notifications
rows=[]
for k,arg in [(65,123),(66,0x4000),(67,0x5000),(68,0x8002003c)]:rows.append(dict(kind=k,arg=arg,pid=1,tid=2,start=1000000000,end=1020000000 if k==65 else 1000000000,wall=20000000 if k==65 else 0,cpu=10000 if k==65 else 0))
x=producer_condition_waits(rows,{123:'runtime.cond_timedwait'})[0]
assert x['metadata_complete']and x['condition']==0x4000 and x['mutex']==0x5000 and x['result']==0x8002003c and x['name']=='runtime.cond_timedwait'and x['wall_ms']==20
for bad in [rows[:2],rows+[rows[-1]]]:
 x=producer_condition_waits(bad,{})[0];assert not x['metadata_complete']and x['condition']is None and x['result']is None
assert producer_condition_waits(rows[1:],{})==[]
print('PASS exact producer span metadata join; missing/duplicate rows stay incomplete')

rows.append(dict(kind=69,arg=7,pid=1,tid=2,start=1000000000,end=1000000000,wall=0,cpu=0))
waits=producer_condition_waits(rows,{123:'runtime.cond_wait'});assert waits[0]['token_complete']and waits[0]['token']==7
wake=[]
for k,arg in [(70,7|(2<<30)),(71,0x4000),(72,124)]:wake.append(dict(kind=k,arg=arg,pid=1,tid=3,start=1020000000,end=1020001000 if k==70 else 1020000000,wall=1000 if k==70 else 0,cpu=100 if k==70 else 0))
x=producer_notifications(wake,{124:'runtime.cond_signal'},waits)[0];assert x['metadata_complete']and x['matching_waits']==1 and x['waiter_tids']==[2]and x['result']==0 and x['stage']==2
assert producer_notifications(wake[:2],{},waits)[0]['matching_waits']==0
assert producer_notifications([r|{'pid':2}for r in wake],{},waits)[0]['matching_waits']==0
print('PASS notification token/process/slot association; incomplete and cross-process matches rejected')

late=[r|{'start':1030000000,'end':1030001000 if r['kind']==70 else 1030000000}for r in wake]
assert producer_notifications(late,{},waits)[0]['matching_waits']==0
print('PASS notifications outside the actual wait span do not become causal matches')
