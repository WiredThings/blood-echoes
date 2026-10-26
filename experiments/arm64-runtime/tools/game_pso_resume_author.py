"""Standalone authored process. Its ASCII fixtures are not Metal/game assets."""
from pathlib import Path
import json,os,sys
E=Path(__file__).resolve().parent
sys.path.insert(0,str(E))
import game_pso_queue as q
from test_game_pso_queue import NativeAuthor
from prepare_game_psos import preparation_ready
root=Path(sys.argv[1]).resolve();budget=None if sys.argv[2]=='none'else int(sys.argv[2]);count=int(sys.argv[3]);state=root/'state.json'
for name in('requests','libraries','artifacts'):(root/name).mkdir(exist_ok=True)
class DiskAuthor(NativeAuthor):
 def __init__(self):
  super().__init__();self.sources={p.name for p in(root/'requests').iterdir()};self.libraries={p.name for p in(root/'libraries').iterdir()};self.artifacts={p.name for p in(root/'artifacts').iterdir()if p.read_text()=='LOCAL'}
  self.environment_epoch=json.loads(state.read_text()).get('epoch',0)if state.exists()else 0
 def local(self,p,g):
  value=super().local(p,g)
  if p=='prepare':
   for rid in self.libraries:(root/'libraries'/rid).write_text('AUTHOR-LIBRARY')
  return value
 def execute(self,u,p,a,o):
  value=super().execute(u,p,a,o);rid=u['recipe_blob_identity']
  if p=='record':
   (root/'requests'/rid).write_text('AUTHOR-SOURCE')
   with (root/'record-log.jsonl').open('a')as f:f.write(json.dumps(rid)+'\n')
  elif value['worker'].get('native_pipeline_ready'):
   local=not value['worker']['source_fallback'];(root/'artifacts'/rid).write_text('LOCAL'if local else'SOURCE')
   if value['worker']['artifact_counts']['built']:
    with(root/'compile-log.jsonl').open('a')as f:f.write(json.dumps(rid)+'\n')
   if not local:
    (root/'requests'/rid).write_text('AUTHOR-LATE-SOURCE');self.sources.add(rid);self.environment_epoch+=1
  return value
native=DiskAuthor();context=dict(worker='a'*64,dso='b'*64,cohort='c'*64,layout='d'*64,options='e'*64)
r=q.run_manifest(root/'manifest.jsonl',progress_root=root/'progress',context=context,execute=native.execute,prepare_phase=native.local,check=native.check,ready=preparation_ready,seconds=100000,page_size=4,max_recipes=budget,clock=native.clock)
state.write_text(json.dumps(dict(epoch=native.environment_epoch)));print(json.dumps(r,separators=(',',':')))
