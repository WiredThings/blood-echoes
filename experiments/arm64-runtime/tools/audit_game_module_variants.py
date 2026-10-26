"""Read-only comparison of recorded profile/spec/SPIR-V variants for one module.

Historical metadata's serialization versions are not compiler/code provenance.
This reports conflicts without choosing a winner or importing any old artifact.
"""
from pathlib import Path
import argparse,collections,json,re
from game_module_inputs import need
from game_pso_owned_audit import checked_bytes
from complete_pipeline_inputs import file_sha

def summarize(records):
 groups={};identities=set();stages=set();code_shas=set()
 for r in records:
  identity=(r['program_hash'],r['hardware_stage']);identities.add(identity);stages.add(r['hardware_stage']);code_shas.add(r['static']['identity']['code_sha256'])
  key=(r['profile']['semantic_sha256'],r['stage']['serialized_spec_sha256']);g=groups.setdefault(key,dict(profile_semantic_sha256=key[0],serialized_spec_sha256=key[1],raw_profile_shas=set(),spv=collections.Counter(),records=0))
  g['raw_profile_shas'].add(r['profile']['reference']['sha256']);g['spv'][(r['stage']['spirv']['sha256'],r['stage']['spirv']['bytes'])]+=1;g['records']+=1
 need(len(identities)==1,'one exact hash/hardware-stage domain required')
 output=[]
 for _,g in sorted(groups.items()):
  output.append(dict(profile_semantic_sha256=g['profile_semantic_sha256'],serialized_spec_sha256=g['serialized_spec_sha256'],raw_profile_variants=len(g['raw_profile_shas']),records=g['records'],spv_variants=[dict(sha256=s,bytes=n,records=c)for(s,n),c in sorted(g['spv'].items())],same_recorded_profile_spec_multiple_spv=len(g['spv'])>1))
 return dict(program_hash=next(iter(identities))[0],hardware_stage=next(iter(stages)),records=sum(g['records']for g in output),static_code_sha256=sorted(code_shas),groups=output,conflicting_recorded_input_groups=sum(g['same_recorded_profile_spec_multiple_spv']for g in output),observed_gcn_sha_recorded=False,producer_compiler_identity_recorded=False,producer_compatibility_proven=False,patch_identity_recorded=False,root_cause_proven=False,runtime_cache_import_allowed=False)

def audit(path,program,stage):
 selected=[]
 with Path(path).open()as source:
  for i,line in enumerate(source):
   need(i<100000 and len(line)<=256*1024,'bounded module records');r=json.loads(line)
   if(r.get('program_hash'),r.get('hardware_stage'))==(program,stage):
    for ref in(r['profile']['reference'],r['stage']['metadata'],r['stage']['spirv']):
     raw,_=checked_bytes(Path(ref['path']),expected=ref['sha256']);need(len(raw)==ref['bytes'],'unchanged observed input length')
    selected.append(r)
 need(selected,'observed module required');result=summarize(selected);result['manifest_sha256']=file_sha(path);return result,selected

def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--modules',type=Path,required=True);ap.add_argument('--program',required=True);ap.add_argument('--stage',default='compute');ap.add_argument('--output',type=Path,required=True);a=ap.parse_args();need(re.fullmatch('[0-9a-f]{16}',a.program),'exact program hash')
 need(not a.output.exists(),'fresh evidence output');a.output.mkdir(mode=0o700);need(a.output.absolute()==a.output.resolve(),'canonical output')
 result,selected=audit(a.modules,a.program,a.stage);(a.output/'summary.json').write_text(json.dumps(result,sort_keys=True,indent=2)+'\n');(a.output/'private-source-links.json').write_text(json.dumps(selected,sort_keys=True,indent=2)+'\n');print(json.dumps(result,sort_keys=True))
if __name__=='__main__':main()
