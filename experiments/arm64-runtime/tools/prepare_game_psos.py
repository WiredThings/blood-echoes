#!/usr/bin/env python3
"""Serial candidate-only game PSO worker supervisor, preserving old caches.

Phases: record exact successful effective-source requests -> existing local
builder -> prepare using the resulting local libraries. Never starts a game.
"""
from pathlib import Path
import argparse,hashlib,json,os,re,signal,stat,subprocess,sys,time

MAX_BLOB=1024*1024;MAX_TOTAL=8*MAX_BLOB;MAX_LINE=256*1024
HEX=re.compile(r'[0-9a-f]{64}')
class Refused(ValueError):pass
class Stopped(RuntimeError):pass
def need(v,why):
 if not v:raise Refused(why)
def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def sha(v):return hashlib.sha256(v).hexdigest()
def directory(p):
 p=Path(p);s=p.lstat();need(p.is_absolute()and p==p.resolve(strict=True)and stat.S_ISDIR(s.st_mode)and s.st_uid==os.getuid(),'owned canonical directory');return p
def owned(p,maximum,expected=None):
 p=Path(p);need(p.is_absolute()and p==p.resolve(strict=True),'canonical nonlinked file')
 fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
 try:
  a=os.fstat(fd);need(stat.S_ISREG(a.st_mode)and a.st_uid==os.getuid()and 0<a.st_size<=maximum,'owned bounded regular input')
  data=bytearray()
  while len(data)<a.st_size:
   b=os.read(fd,a.st_size-len(data));need(bool(b),'short owned input');data.extend(b)
  b=os.fstat(fd);identity=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns)
  need(identity(a)==identity(b),'input changed while reading');data=bytes(data)
  if expected is not None:need(HEX.fullmatch(expected)and sha(data)==expected,'owned input SHA')
  return data
 finally:os.close(fd)
def fresh(parent,name):
 parent=directory(parent);p=parent/name;p.mkdir(mode=0o700);return directory(p)
def new_bytes(root,name,data):
 p=root/name;fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
 try:
  at=0
  while at<len(data):n=os.write(fd,data[at:]);need(n>0,'short snapshot write');at+=n
  os.fsync(fd)
 finally:os.close(fd)
 return dict(path=str(p),name=name,bytes=len(data),sha256=sha(data))

def freeze(unit,layout,root,cohort,allow_compile,*,cache_root,artifact_cache_dir,audit_stage,audit_key_profile):
 """One owned read-set; caller retains returned immutable bytes until child exits."""
 need(unit['schema']==2 and unit['record_type']=='offline-pso-input','v2 full recipe')
 policy=unit['execution_policy'];need(policy['runtime_cache_import_allowed']is False and
  policy['actual_final_read_admission']['allowed']is False and
  policy['candidate_preparation']['policy_eligible']is True,'candidate-only policy')
 need(unit['source_provenance']['enabled']is False and unit['profile']['compiler_compatibility_proof']is False,'old compatibility not upgraded')
 cache=directory(unit['namespace']['cache']);need(cache.is_relative_to(cache_root),'source outside private Shared state')
 refs=[unit['profile']['reference'],unit['key']['reference']]
 for s in unit['stages']:refs.extend((s['metadata'],s['spirv']))
 need(len(refs)<=14 and sum(r['bytes']for r in refs)<=MAX_TOTAL,'whole read-set budget')
 held={};canonical=[]
 for r in refs:
  p=Path(r['path']);need(p.parent==cache and p.name==r['name'],'same cache-local filename')
  need(p not in held,'duplicate source path in whole recipe')
  b=owned(p,MAX_BLOB,r['sha256']);need(len(b)==r['bytes'],'source byte count');held[p]=b
  canonical.append({k:r[k]for k in('path','name','bytes','sha256')})
 need(digest(canonical)==policy['candidate_preparation']['frozen_input_set_identity'],'frozen read-set identity')
 profile=held[Path(unit['profile']['reference']['path'])];key=held[Path(unit['key']['reference']['path'])]
 audit_key_profile(unit,key,profile,layout)
 packet=dict(packet_schema=1,manifest_schema=2,candidate_only=True,runtime_cache_import_allowed=False,
  native_info_srt_allowed=False,walker_bytes_passed=0,current_cohort=cohort,
  cache_id=unit['namespace']['cache_id'],recipe_blob_identity=unit['recipe_blob_identity'],
  original_cache=str(cache),frozen_input_set_identity=policy['candidate_preparation']['frozen_input_set_identity'],
  allow_compile=bool(allow_compile),artifact_cache_dir=str(artifact_cache_dir),key=new_bytes(root,'key.bin',key),stages=[],
  layout=dict(bindings_size=layout['Backend::Bindings.size'],runtime_size=layout['RuntimeInfo.size'],graphics_key_size=layout['GraphicsPipelineKey.size']))
 for i,s in enumerate(unit['stages']):
  meta=held[Path(s['metadata']['path'])];spv=held[Path(s['spirv']['path'])]
  audit=audit_stage(s,meta,spv,layout);region=audit['sections']['specialization']
  need(region['offset']==24 and region['native_spec_only_reader_allowed']is True,'audited exact spec interval')
  spec=meta[24:24+region['bytes']];need(sha(spec)==region['sha256'],'frozen exact spec SHA')
  row=dict(logical_stage=s['logical_stage'],hardware_stage=s['hardware_stage'],program_hash=s['program_hash'],
   permutation=s['cache_local_permutation'],permutation_hash=s['permutation_hash'],
   spec=new_bytes(root,f'spec-{i}.bin',spec),spirv=new_bytes(root,f'spirv-{i}.bin',spv))
  row['spec'].update(source_offset=24,native_spec_only_reader_allowed=True)
  packet['stages'].append(row)
 encoded=json.dumps(packet,sort_keys=True,separators=(',',':')).encode()+b'\n';need(len(encoded)<=MAX_LINE,'native packet budget')
 new_bytes(root,'packet.json',encoded)
 return packet,held

def rows(path):
 with Path(path).open('rb')as f:
  for _ in range(100000):
   b=f.readline(MAX_LINE+1)
   if not b:return
   need(len(b)<=MAX_LINE and b.endswith(b'\n'),'manifest line budget')
   yield json.loads(b)
  need(not f.read(1),'manifest record budget')

def preparation_ready(row):
 """A finished worker is insufficient: require actual native ready outcomes/events."""
 w=row['worker'];c=w.get('artifact_counts',{});events=row['artifact_events'];n=c.get('attempts',0)
 return (w.get('native_pipeline_ready')is True and w.get('artifact_publication_observed')is True
  and n>0 and len(events)==n and c.get('hits',0)+c.get('built',0)==n
  and c.get('deferred',0)+c.get('failed',0)+c.get('critical',0)+c.get('unavailable',0)==0
  and all(e.get('state')in(1,2)and HEX.fullmatch(e.get('archive_sha256',''))for e in events))

def main(argv=None):
 ap=argparse.ArgumentParser();ap.add_argument('--project',required=True,type=Path)
 ap.add_argument('--worker',required=True,type=Path);ap.add_argument('--library-sha256',required=True)
 ap.add_argument('--manifest',required=True,type=Path);ap.add_argument('--layout',required=True,type=Path)
 ap.add_argument('--layout-sha256',required=True)
 ap.add_argument('--source-root',required=True,type=Path);ap.add_argument('--cohort',required=True)
 ap.add_argument('--gpu-root',required=True,type=Path);ap.add_argument('--run-root',required=True,type=Path)
 ap.add_argument('--artifact-cache-dir',required=True,type=Path)
 ap.add_argument('--phase',choices=['record','prepare','all'],default='all')
 ap.add_argument('--max-recipes',type=int,default=16);ap.add_argument('--seconds',type=int,default=600)
 ap.add_argument('--allow-compile',action=argparse.BooleanOptionalAction,default=True)
 a=ap.parse_args(argv);P=directory(a.project);sys.path.insert(0,str(P/'tools'))
 from game_pso_owned_audit import audit_owned_stage,audit_owned_key_profile,load_canonical_layout
 from qa_build_guard import require_no_games
 from native_observer import NativeObserver
 from qa_memory_policy import MemoryPolicy
 from warm_archive_manifest import resolve_gpu_identity
 from local_aot_policy import prepare
 need(HEX.fullmatch(a.cohort)and HEX.fullmatch(a.library_sha256)and HEX.fullmatch(a.layout_sha256),'current identities required')
 need(0<a.max_recipes<=4096 and 0<a.seconds<=7200,'explicit bounded work budget')
 run=directory(a.run_root);gpu=directory(a.gpu_root);artifact=directory(a.artifact_cache_dir)
 need(artifact.is_relative_to(gpu)and artifact!=gpu,'exact original QA PsoCache artifact/cache_dir required')
 need(run.parent==P/'state'and gpu.is_relative_to(P/'state')and run!=gpu and not any(run.iterdir()),'fresh Shared run and selected QA artifact roots')
 need(a.manifest.resolve(strict=True).is_relative_to(P)and a.layout.resolve(strict=True).is_relative_to(P),'actual runtime inputs must be Shared')
 require_no_games();identity=resolve_gpu_identity(a.worker,P);need(identity['library_sha256']==a.library_sha256,'actual selected worker library SHA')
 layout=load_canonical_layout(a.layout,a.source_root,a.layout_sha256)
 observer=NativeObserver();before=observer.system();need(not before['locked']and before['pressure']!=4,'protection preflight')
 policy=MemoryPolicy(before);start=time.monotonic();samples=[];outcomes=[];selected=[];seen=set();result={};selected_bytes=0;queue_budget_deferred=False
 try:
  for r in rows(a.manifest):
   if r.get('record_type')!='offline-pso-input' or not r['execution_policy']['candidate_preparation']['policy_eligible']:continue
   rid=r['recipe_blob_identity']
   if rid in seen:continue
   length=len(json.dumps(r,separators=(',',':')).encode())
   if selected_bytes+length>16*1024*1024:queue_budget_deferred=True;break
   seen.add(rid);selected.append(r);selected_bytes+=length
   if len(selected)==a.max_recipes:break
  need(bool(selected),'no eligible complete candidate recipes')
  def check():
   elapsed=time.monotonic()-start;s=observer.system();samples.append(dict(seconds=elapsed,**s))
   stop=policy.observe(elapsed,s)
   if stop or elapsed>a.seconds:raise Stopped(stop or 'bounded preparation budget reached')
  phases=('record','prepare')if a.phase=='all'else(a.phase,)
  for phase in phases:
   check();updates,local=prepare(mode='record'if phase=='record'else'load',gpu_root=gpu,cohort=a.cohort,engine='arm64',graphics=True)
   if local['status']=='stopped':raise Stopped(local.get('error','local builder safety stop'))
   if phase=='prepare':need(local['status']=='prepared','verified local library builder completion required')
   for ordinal,unit in enumerate(selected):
    check();require_no_games();case=fresh(run,f'{phase}-{ordinal:04d}');packetdir=fresh(case,'packet');scratch=fresh(case,'source-cache');tmp=fresh(case,'tmp')
    packet,held=freeze(unit,layout,packetdir,a.cohort,a.allow_compile,cache_root=P/'state',artifact_cache_dir=artifact,audit_stage=audit_owned_stage,audit_key_profile=audit_owned_key_profile)
    env={k:v for k,v in os.environ.items()if not k.startswith(('BB_','DYLD_'))}
    env.update(updates);env.update(BB_GPU_USER_DIR=str(gpu),BB_MTL_ARCHIVE_COMPATIBILITY_ID=a.cohort,
     BB_MTL_PERSIST_WARM_ARCHIVE='0',BB_MTL_RELEASE_WARM_ARCHIVE='1',HOME=str(case),TMPDIR=str(tmp),
     DYLD_PRINT_LIBRARIES='1',MTL_DEBUG_LAYER='1')
    # Keep actual original optimization selection; caller must provide current
    # options. Only source/library preparation controls above are replaced.
    for k,v in os.environ.items():
     if k.startswith(('BB_MTL_','BB_LAYER_','BB_PC_','BB_GUEST_'))and not k.startswith('BB_MTL_LOCAL_AOT_')and k not in env:env[k]=v
    log=case/'console.log';command=[str(a.worker.resolve()),phase,str(packetdir/'packet.json'),str(scratch)]
    with log.open('w')as stream:
     proc=subprocess.Popen(command,env=env,cwd=case,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
     try:
      while proc.poll()is None:check();time.sleep(.1)
     finally:
      if proc.poll()is None:
       os.killpg(proc.pid,signal.SIGTERM)
       try:proc.wait(timeout=3)
       except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
    # held bytes remain live through the entire compile; never reopen original cache.
    need(sum(map(len,held.values()))<=MAX_TOTAL,'retained original read set budget');del held
    text=log.read_text();events=[];worker=[];loaded=[]
    for line in text.splitlines():
     if line.startswith('GAME_PSO_RESULT '):worker.append(json.loads(line.split(' ',1)[1]))
     if line.startswith('Metal game PSO artifact event: '):events.append(json.loads(line.split(': ',1)[1]))
     if 'dyld'in line and'libbbgpu'in line and line.split()[-1].startswith('/'):loaded.append(str(Path(line.split()[-1]).resolve()))
    need(sorted(set(loaded))==[identity['library']],'actual runtime GPU load edge changed')
    row=dict(phase=phase,ordinal=ordinal,pid=proc.pid,exit=proc.returncode,recipe=unit['recipe_blob_identity'],
     worker=worker[-1]if len(worker)==1 else None,artifact_events=events,log=str(log),
     runtime_cache_import_allowed=False,actual_final_read_admission=False)
    outcomes.append(row);(case/'result.json').write_text(json.dumps(row,indent=2)+'\n')
    if proc.returncode in(70,79):raise Stopped('native worker protection/interruption; see actual log')
    need(proc.returncode==0 and len(worker)==1,'worker rejected; see actual log')
  incomplete=[row['ordinal']for row in outcomes if row['phase']=='prepare'and not preparation_ready(row)]
  result['incomplete_preparations']=incomplete
  result['status']='partial-worker-phases'if incomplete else'completed-worker-phases'
 except Stopped as e:result.update(status='stopped',error=str(e))
 except Exception as e:result.update(status='failed',error=str(e))
 finally:
  result.update(before=before,after=observer.system(),samples=samples,memory_policy=MemoryPolicy.MANIFEST,
   seconds=time.monotonic()-start,outcomes=outcomes,worker_identity=identity,selected_recipes=len(selected),
   selected_manifest_bytes=selected_bytes,queue_budget_deferred=queue_budget_deferred,full_catalog_coverage_proven=False,
   runtime_cache_import_allowed=False,actual_final_read_admission=False,game_started=False)
  (run/'result.json').write_text(json.dumps(result,indent=2)+'\n');observer.close()
 print(json.dumps({k:v for k,v in result.items()if k not in('samples','outcomes')},indent=2))
 return 0 if result['status']=='completed-worker-phases'else 70 if result['status']=='stopped'else 65
if __name__=='__main__':raise SystemExit(main())
