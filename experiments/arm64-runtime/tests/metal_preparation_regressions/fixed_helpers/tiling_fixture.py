"""Authored schema2 response data; no renderer or cache access."""
def libraries(local=14,processed=14):
 rows=[dict(bits=b,macro=m,texture=False)for b in(8,16,32,64,96,128)for m in(False,True)]+[dict(bits=b,macro=True,texture=True)for b in(32,64)]
 for i,r in enumerate(rows):r.update(entry_ready=True,actual_route=2 if i<local else 1,source_sha256='d'*64,request_key='e'*64,library_sha256='f'*64)
 return dict(schema=1,known=14,processed=processed,local_ready=min(local,processed),complete=local==processed==14,final_psos_prepared=False,items=rows[:processed])
