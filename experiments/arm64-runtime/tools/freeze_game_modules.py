"""Freeze bounded actual GCN inputs for the separate CPU module consumer.

Templates are hints only: every selected metadata/profile/code is revalidated.
This never imports the output into runtime caches or constructs a graphics PSO.
"""
from pathlib import Path
import argparse,collections,json,time
from complete_pipeline_inputs import directory,file_sha
from game_module_inputs import freeze_module,need
from game_pso_owned_audit import load_canonical_layout,checked_bytes
from static_program_links import rows,lookup_table

def freeze_batch(*,project,templates,static_index,catalog_path,layout,output,limits):
 project=directory(Path(project));output=Path(output).absolute()
 need(output.is_relative_to(project/'evidence')and not output.exists(),'new private evidence directory required')
 need(limits and all(k in('compute','fragment','vertex','export')and 0<=v<=64 for k,v in limits.items())and sum(limits.values())<=128,'bounded stage limits')
 output.mkdir(mode=0o700);directory(output)
 catalog=json.loads(Path(catalog_path).read_text());table=lookup_table(list(rows(Path(static_index))))
 counts=collections.Counter();seen=set();packets=[];rejected=[]
 with Path(templates).open()as stream:
  for n,line in enumerate(stream):
   need(n<100000 and len(line)<=256*1024,'template budget');r=json.loads(line)
   stage=r.get('hardware_stage');identity=r.get('static',{}).get('identity');key=(r.get('program_hash'),stage)
   if not r.get('owned_gcn_consumer_template_complete')or counts[stage]>=limits.get(stage,0)or key in seen:continue
   matches=table.get(key,[]);need(len(matches)==1 and matches[0]['identity']==identity,'unique exact final static identity')
   refs=[e for v in matches[0]['variants']for e in v['evidence_refs']]
   need(refs,'independent static source reference required');seen.add(key)
   name=f'{stage}-{r["program_hash"]}'
   try:
    packet=freeze_module(stage=r['stage'],profile_reference=r['profile']['reference'],layout=layout,
     game_root=catalog['game_root'],catalog=catalog,static_identity=identity,evidence=refs[0],output=output/name)
    counts[stage]+=1;packets.append(dict(packet=str(output/name/'packet.json'),program_hash=packet['program_hash'],hardware_stage=stage,code_sha256=packet['code']['sha256'],native_compile_verified=False))
   except(ValueError,OSError)as error:rejected.append(dict(program_hash=r['program_hash'],hardware_stage=stage,error=str(error)))
 summary=dict(schema=1,status='frozen-owned-inputs',packets=packets,counts=dict(counts),rejected=rejected,
  identities=dict(templates_sha256=file_sha(templates),static_index_sha256=file_sha(static_index),source_catalog_sha256=file_sha(catalog_path)),
  native_compilation_verified=False,final_PSO_prepared=False,GPU_started=False,runtime_cache_import_allowed=False)
 (output/'manifest.json').write_text(json.dumps(summary,sort_keys=True,indent=2)+'\n');return summary

def main():
 p=argparse.ArgumentParser(description=__doc__)
 for name in('project','templates','static-index','catalog','layout','layout-source','output'):p.add_argument('--'+name,required=True,type=Path)
 p.add_argument('--compute',type=int,default=32);p.add_argument('--fragment',type=int,default=8);p.add_argument('--vertex',type=int,default=4);p.add_argument('--export',type=int,default=4)
 a=p.parse_args();checked_bytes(a.layout,limit=4*1024*1024);layout=load_canonical_layout(a.layout,a.layout_source,file_sha(a.layout))
 r=freeze_batch(project=a.project,templates=a.templates,static_index=a.static_index,catalog_path=a.catalog,layout=layout,output=a.output,limits={k:getattr(a,k)for k in('compute','fragment','vertex','export')})
 print(json.dumps(dict(counts=r['counts'],rejected=r['rejected'],manifest=str(a.output/'manifest.json')),sort_keys=True))
if __name__=='__main__':main()
