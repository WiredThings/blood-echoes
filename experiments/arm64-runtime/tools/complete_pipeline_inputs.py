"""Complete candidate-only PSO inputs from explicitly selected private QA caches.

Existing canonical readers/owned packet auditing remain authoritative. Static
facts supply provenance; they never invent draw state, mutate cache keys, or turn
an old producer into trusted native input. No GPU or game is executed.
"""
from pathlib import Path
import argparse,collections,hashlib,json,os,re,sqlite3,tempfile,time
from game_pso_candidates import cache_records,directory
from game_pso_owned_audit import checked_bytes,audit_owned_stage,audit_owned_key_profile,metadata_sections,load_canonical_layout
from game_pso_progress_identity import layout_abi_identity
from prepare_game_psos import freeze
from static_program_links import rows as static_rows,lookup_table,resolve,geometry_copy_provenance
from game_module_inputs import module_constraints

MAX_CACHES=128;MAX_RECORDS=100000;MAX_OUTPUT=256*1024*1024
TITLE=re.compile(r'CUSA[0-9]{5}')
def need(value,message):
 if not value:raise ValueError(message)
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def file_sha(path):
 with Path(path).open('rb')as f:return hashlib.file_digest(f,'sha256').hexdigest()
def exact_payload(unit):
 """Deduplicate only the entire exact raw input set, never a program/spec hash."""
 stages=[dict(logical_stage=s['logical_stage'],hardware_stage=s['hardware_stage'],program_hash=s['program_hash'],permutation=s['cache_local_permutation'],permutation_hash=s['permutation_hash'],spec_sha256=s['serialized_spec_sha256'],metadata_sha256=s['metadata']['sha256'],metadata_bytes=s['metadata']['bytes'],spirv_sha256=s['spirv']['sha256'],spirv_bytes=s['spirv']['bytes'])for s in unit['stages']]
 return digest(dict(profile_sha256=unit['profile']['reference']['sha256'],profile_bytes=unit['profile']['reference']['bytes'],key_sha256=unit['key']['reference']['sha256'],key_bytes=unit['key']['reference']['bytes'],stages=sorted(stages,key=lambda s:s['logical_stage'])))
def worker_payload(packet,profile_reference,layout):
 """Exact bytes and scalars consumed by the existing native worker.
 Opaque Info/flat/walker bytes are audited as provenance but not sent to it.
 This identity is for this worker contract/current execution, not a global key.
 """
 return digest(dict(consumer='game-pso-preparer-v1/spec-only',layout_abi=layout_abi_identity(layout),
  profile=dict(sha256=profile_reference['sha256'],bytes=profile_reference['bytes']),
  key=dict(sha256=packet['key']['sha256'],bytes=packet['key']['bytes']),
  stages=[dict(logical_stage=s['logical_stage'],hardware_stage=s['hardware_stage'],program_hash=s['program_hash'],permutation=s['permutation'],permutation_hash=s['permutation_hash'],spec=dict(sha256=s['spec']['sha256'],bytes=s['spec']['bytes']),spirv=dict(sha256=s['spirv']['sha256'],bytes=s['spirv']['bytes']))for s in packet['stages']]))
def static_link(stage,table):
 candidates=table.get((stage['program_hash'],stage['hardware_stage']),[])
 if len(candidates)!=1:return dict(status='missing-static-code'if not candidates else'ambiguous-static-code',candidates=[x['identity']for x in candidates])
 item=candidates[0]
 return dict(status='unique-static-code',identity=item['identity'],variant_ids=[v['id']for v in item['variants']],source_reference_count=sum(len(v['evidence_refs'])for v in item['variants']))
def source_profile_key(unit,layout):
 blobs={}
 for r in [unit['profile']['reference'],unit['key']['reference']]:
  b,_=checked_bytes(Path(r['path']),expected=r['sha256']);need(len(b)==r['bytes'],'frozen input byte count');blobs[r['path']]=b
 audit_owned_key_profile(unit,blobs[unit['key']['reference']['path']],blobs[unit['profile']['reference']['path']],layout)
 return blobs

def complete(*,project,caches,layout,static_index,output,title='CUSA03173',check=lambda:None):
 project=directory(Path(project));state=directory(project/'state');need(TITLE.fullmatch(title),'title identity');need(0<len(caches)<=MAX_CACHES,'explicit bounded QA cache list')
 output=Path(output).absolute();need(not output.exists()and output.is_relative_to(project/'evidence'),'new private evidence output required');output.mkdir(mode=0o700);directory(output)
 inputs=Path(static_index).absolute();input_sha=file_sha(inputs);records=list(static_rows(inputs));need(len(records)<=MAX_RECORDS,'static identity budget');table=lookup_table(records)
 for row in records:
  i=row['identity'];need(i['hardware_stage']in('fragment','vertex','geometry','export','hull','local','compute')and len(i['code_sha256'])==64,'static identity shape')
 counters=collections.Counter();gaps=collections.Counter();modules=collections.Counter();inventory_receipts=[];cache_errors=[];started=time.monotonic();seen_caches=set();output_bytes=0
 db=sqlite3.connect(output/'exact-inputs.sqlite');db.execute('PRAGMA temp_store=FILE');db.execute('PRAGMA mmap_size=0');db.execute('PRAGMA cache_size=-2048');db.execute('CREATE TABLE payloads(id TEXT PRIMARY KEY, recipe TEXT, cache TEXT)');db.execute('CREATE TABLE modules(id TEXT PRIMARY KEY)');db.execute('CREATE TABLE raw_inputs(id TEXT PRIMARY KEY,recipe TEXT,cache TEXT)')
 cohort=digest(dict(domain='CPU-owned-packet-audit-only',layout_abi=layout_abi_identity(layout),static_index=input_sha));artifact=output/'unpublished-artifacts';artifact.mkdir();scratch=output/'packet-audit';scratch.mkdir()
 with(output/'completed-inputs.jsonl').open('x')as manifests,(output/'input-links.jsonl').open('x')as links,(output/'module-inputs.jsonl').open('x')as stage_out,(output/'gaps.jsonl').open('x')as gap_out:
  def emit(stream,row):
   nonlocal output_bytes
   value=json.dumps(row,sort_keys=True,separators=(',',':')).encode()+b'\n';need(len(value)<=256*1024,'output row byte budget');output_bytes+=len(value);need(output_bytes<=MAX_OUTPUT,'total completion output budget');stream.write(value.decode())
  for path in caches:
   check();cache=directory(Path(path));need(cache.is_relative_to(state)and cache!=state and cache.name.startswith(title+'-metal-'),'private title QA shader cache required')
   if str(cache)in seen_caches:continue
   seen_caches.add(str(cache));counters['cache_directories']+=1
   try:
    for unit in cache_records(cache,layout,check,snapshot_sink=inventory_receipts.append):
     check();counters['records']+=1;need(counters['records']<=MAX_RECORDS,'completion record budget')
     if unit['record_type']!='offline-pso-input':
      counters['rejected_inputs']+=1;gaps[unit['reason']]+=1;emit(gap_out,unit);continue
     associations=[];static_complete=True
     for stage in unit['stages']:
      link=static_link(stage,table);associations.append(link);static_complete&=link['status']=='unique-static-code'
      mraw,_=checked_bytes(Path(stage['metadata']['path']),expected=stage['metadata']['sha256']);meta,sections=metadata_sections(mraw,layout)
      module_id=digest(dict(profile=unit['profile']['reference']['sha256'],stage=stage['hardware_stage'],metadata=stage['metadata']['sha256'],spirv=stage['spirv']['sha256'],static_link=link))
      if db.execute('INSERT OR IGNORE INTO modules VALUES(?)',(module_id,)).rowcount:
       reasons=module_constraints(meta)
       if link['status']!='unique-static-code':reasons.append(link['status'])
       module=dict(schema=1,module_input_identity=module_id,program_hash=stage['program_hash'],hardware_stage=stage['hardware_stage'],logical_stage=stage['logical_stage'],static=link,profile=unit['profile'],stage=stage,sections=sections,owned_gcn_consumer_template_complete=not reasons,native_module_ready=False,missing_dependencies=reasons,required_native_gates=['current Profile compatibility','regenerated SRT data and flat count equality','no uncaptured register reads','no OffsetByProgramBase without known base','current resource specialization equivalence','successful complete original translation'],old_walker_execution_allowed=False,native_Info_SRT_deserialization_allowed=False)
       if stage['hardware_stage']=='geometry'and link['status']=='unique-static-code':module['owned_static_copy']=geometry_copy_provenance(table[stage['program_hash'],stage['hardware_stage']][0],meta['context']['geometry_copy'],table)
       modules['observed_unique_modules']+=1;modules['owned_gcn_consumer_templates'if not reasons else'needs_additional_dependencies']+=1;emit(stage_out,module)
     if not unit['execution_policy']['candidate_preparation']['policy_eligible']:
      counters['existing_native_exclusions']+=1
      for reason in unit['session_rejections']:gaps[reason]+=1
      emit(gap_out,dict(schema=1,recipe_blob_identity=unit['recipe_blob_identity'],cache=str(cache),reasons=unit['session_rejections'],static_links=associations,native_ready=False));continue
     if not static_complete:
      counters['static_identity_gaps']+=1;emit(gap_out,dict(schema=1,recipe_blob_identity=unit['recipe_blob_identity'],cache=str(cache),reasons=[x['status']for x in associations if x['status']!='unique-static-code'],native_ready=False));continue
     payload=exact_payload(unit);existing=db.execute('SELECT recipe,cache FROM raw_inputs WHERE id=?',(payload,)).fetchone()
     if existing:
      counters['exact_raw_input_duplicates']+=1;emit(links,dict(schema=1,exact_payload_identity=payload,recipe_blob_identity=unit['recipe_blob_identity'],cache=str(cache),same_raw_input_as=dict(recipe_blob_identity=existing[0],cache=existing[1]),static_links=associations));continue
     # Exercise the actual immutable read-set and packet path; temporary bytes
     # are owned by this new audit only, and never enter any original cache.
     with tempfile.TemporaryDirectory(dir=scratch)as name:
      packet,held=freeze(unit,layout,Path(name),cohort,False,cache_root=state,artifact_cache_dir=artifact,audit_stage=audit_owned_stage,audit_key_profile=audit_owned_key_profile)
      need(packet['native_info_srt_allowed']is False and packet['walker_bytes_passed']==0 and packet['allow_compile']is False,'candidate-only packet invariants')
      counters['owned_packet_audits']+=1;effective=worker_payload(packet,unit['profile']['reference'],layout)
     db.execute('INSERT INTO raw_inputs VALUES(?,?,?)',(payload,unit['recipe_blob_identity'],str(cache)))
     counters['unique_raw_input_sets']+=1
     previous=db.execute('SELECT recipe,cache FROM payloads WHERE id=?',(effective,)).fetchone()
     audit=dict(schema=1,exact_raw_payload_identity=payload,exact_worker_payload_identity=effective,recipe_blob_identity=unit['recipe_blob_identity'],cache=str(cache),static_links=associations,owned_packet_audit_passed=True,native_ready=False,original_worker_schema=2,runtime_cache_import_allowed=False)
     if previous:
      counters['exact_worker_payload_duplicates']+=1;audit['same_worker_payload_as']=dict(recipe_blob_identity=previous[0],cache=previous[1]);emit(links,audit);continue
     db.execute('INSERT INTO payloads VALUES(?,?,?)',(effective,unit['recipe_blob_identity'],str(cache)))
     counters['packet_ready_inputs']+=1;counters['packet_ready_'+unit['classification']['family']]+=1
     emit(manifests,unit);emit(links,audit)
   except(ValueError,KeyError,OSError)as error:
    cache_errors.append(dict(cache=str(cache),error=str(error)));gaps['cache-error']+=1
   db.commit()
 db.close();need(file_sha(inputs)==input_sha,'static index changed during completion')
 result=dict(schema=1,status='passed-input-audit'if not cache_errors else'partial-input-audit',counts=dict(counters),module_counts=dict(modules),gap_reasons=dict(gaps),cache_errors=cache_errors,complete_scan=not cache_errors,static_index_sha256=input_sha,layout_abi_sha256=layout_abi_identity(layout),input_manifest_sha256=file_sha(output/'completed-inputs.jsonl'),source_inventories=inventory_receipts,seconds=time.monotonic()-started,output_bytes=output_bytes,game_started=False,GPU_started=False,native_deserializer_called=False,source_cache_modified=False,runtime_cache_import_allowed=False,formal_data_modified=False,qualification='Complete canonical existing worker packets audited on CPU, not native GPU readiness. Static defaults alone do not invent full PSOs. Raw snapshots and exact native-worker consumed payloads have separate identities. No program-hash or guessed semantic merges; Info/flat/walker provenance never becomes native input.')
 (output/'summary.json').write_text(json.dumps(result,indent=2)+'\n');return result

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--project',type=Path,required=True);ap.add_argument('--cache-list',type=Path,required=True);ap.add_argument('--layout',type=Path,required=True);ap.add_argument('--layout-source',type=Path,required=True);ap.add_argument('--static-index',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
 layout_raw,_=checked_bytes(a.layout,limit=4*1024*1024);layout=load_canonical_layout(a.layout,a.layout_source,hashlib.sha256(layout_raw).hexdigest());selected=json.loads(a.cache_list.read_text());need(isinstance(selected,list)and all(isinstance(p,str)for p in selected),'explicit cache path array')
 from native_observer import NativeObserver
 from qa_memory_policy import MemoryPolicy
 from qa_build_guard import require_no_games
 require_no_games();observer=NativeObserver();before=observer.system();need(before['pressure']!=4,'severe pressure before input audit');policy=MemoryPolicy(before);start=time.monotonic()
 def check():
  reason=policy.observe(time.monotonic()-start,observer.system())
  if reason:raise RuntimeError(reason)
 try:r=complete(project=a.project,caches=selected,layout=layout,static_index=a.static_index,output=a.output,check=check);r.update(before=before,after=observer.system(),memory_policy=MemoryPolicy.MANIFEST);(a.output/'summary.json').write_text(json.dumps(r,indent=2)+'\n')
 finally:observer.close()
 print(json.dumps({k:v for k,v in r.items()if k not in('source_inventories',)},indent=2))
if __name__=='__main__':main()
