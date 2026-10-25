from pathlib import Path
import argparse,json,hashlib,subprocess,time,sys
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'))
from qa_build_guard import require_no_games
parser=argparse.ArgumentParser();parser.add_argument('--baseline-header',type=Path,required=True);args=parser.parse_args();baseline=args.baseline_header.resolve(strict=True)
require_no_games();e=P/'evidence'/('runtime-key-authored-'+str(time.time_ns()));e.mkdir();m=json.loads((P/'evidence/texture-expansion-build.json').read_text());header=P/'vendor/metal_api/shadps4/shader_recompiler/runtime_info.h';current_header=header.read_bytes();old=baseline.read_text();records=[]
source=e/'probe.mm';source.write_text(r'''
#include <array>
#include <cstdio>
#include <ranges>
#include "shader_recompiler/info.h"
#include "shader_recompiler/specialization.h"
unsigned failures=0,checks=0;
void check(bool ok,const char* name){++checks;if(!ok){++failures;std::printf("FAIL %s\n",name);}}
Shader::StageSpecialization make(Shader::Info& info,Shader::HwStage hw,Shader::SwStage sw){Shader::StageSpecialization out{};out.info=&info;out.runtime_info.Initialize(hw,sw);return out;}
int main(){
 Shader::Info info{};auto base=make(info,Shader::HwStage::Geometry,Shader::SwStage::Geometry);
 auto& g=base.runtime_info.hw.gs;g.num_invocations=1;g.output_vertices=8;g.in_vertex_data_size=28;g.out_vertex_data_size=28;g.vs_copy_hash=42;
 std::array<u32,3> one{11,22,33},two=one;g.vs_copy=one;
 auto distinct=[&](const auto& other,const char* name){check(!(base.runtime_info.hw.gs==other.runtime_info.hw.gs),name);check(!(base.runtime_info==other.runtime_info),name);check(!(base==other),name);std::array<Shader::StageSpecialization,1> store{base};check(std::ranges::find(store,other)==store.end(),name);};
 check(base==base,"gs-reflexive");auto other=base;other.runtime_info.hw.gs.num_invocations=2;distinct(other,"gs-invocations");
 other=base;other.runtime_info.hw.gs.in_vertex_data_size+=4;distinct(other,"gs-input-size");other=base;other.runtime_info.hw.gs.out_vertex_data_size+=4;distinct(other,"gs-output-size");
 other=base;other.runtime_info.hw.gs.vs_copy_hash++;distinct(other,"gs-copy-hash");other=base;other.runtime_info.hw.gs.vs_copy=two;check(base==other,"gs-address-relocation-same-content-hash");
 other=base;other.runtime_info.hw.gs.mode=AmdGpu::GsScenario::ScenarioC;check(base==other,"gs-unused-mode-no-new-variant");
 other=base;other.runtime_info.hw.gs.num_invocations=0;check(other==other,"gs-zero-reflexive-not-valid-emission");check(!(base==other),"gs-zero-different");
 auto cs=make(info,Shader::HwStage::Compute,Shader::SwStage::Compute);cs.runtime_info.hw.cs.workgroup_size={32,1,1};cs.runtime_info.hw.cs.shared_memory_size=1024;auto changed=cs;changed.runtime_info.hw.cs.shared_memory_size=2048;
 check(!(cs.runtime_info.hw.cs==changed.runtime_info.hw.cs),"cs-shared-size");check(!(cs.runtime_info==changed.runtime_info),"cs-shared-size-runtime");check(!(cs==changed),"cs-shared-size-specialization");std::array<Shader::StageSpecialization,1> cache{cs};check(std::ranges::find(cache,changed)==cache.end(),"cs-shared-size-find");
 changed=cs;check(cs==changed,"cs-same-hit");changed.runtime_info.hw.cs.workgroup_size[0]=64;check(!(cs==changed),"cs-workgroup-different");changed=cs;changed.runtime_info.hw.cs.tgid_enable[0]=true;check(!(cs==changed),"cs-tgid-different");
 std::printf("RESULT checks=%u failures=%u\n",checks,failures);return failures?1:0;
}
''')
oldexpr='return num_outputs == other.num_outputs && outputs == other.outputs && num_invocations &&\n               other.num_invocations && output_vertices == other.output_vertices &&'
newexpr='return num_outputs == other.num_outputs && outputs == other.outputs &&\n               num_invocations == other.num_invocations && output_vertices == other.output_vertices &&\n               in_vertex_data_size == other.in_vertex_data_size &&\n               out_vertex_data_size == other.out_vertex_data_size &&'
assert oldexpr in old
geometry=old.replace(oldexpr,newexpr);needle='return workgroup_size == other.workgroup_size && tgid_enable == other.tgid_enable;';assert needle in geometry
complete=geometry.replace(needle,'return shared_memory_size == other.shared_memory_size &&\n               workgroup_size == other.workgroup_size && tgid_enable == other.tgid_enable;')
for name,text,expected in [('baseline',old,1),('geometry-only',geometry,1),('geometry-and-compute',complete,0)]:
 require_no_games();shadow=e/name/'shader_recompiler';shadow.mkdir(parents=True);(shadow/'runtime_info.h').write_text(text)
 obj=e/(name+'.o');cmd=list(m['compile']);oldobj=cmd[cmd.index('-o')+1];cmd[cmd.index('-o')+1]=str(obj);cmd[cmd.index('-MF')+1]=str(obj.with_suffix('.d'));cmd[-1]=str(source);cmd.insert(1,'-I'+str(shadow.parent));cmd.insert(2,'-I'+str(P/'tests/metal'));cmd.insert(cmd.index('-c'),'-UNDEBUG')
 with(e/(name+'-build.log')).open('w')as log:
  result=subprocess.run(cmd,cwd='/private/tmp/blood-echoes-arm64-metal-20261023/build',stdout=log,stderr=subprocess.STDOUT);assert result.returncode==0,str(e/(name+'-build.log'))
  binary=e/(name+'-probe');link=['/usr/bin/clang++','-arch','arm64','-flto=thin',str(obj),'-o',str(binary)]
  result=subprocess.run(link,cwd='/private/tmp/blood-echoes-arm64-metal-20261023/build',stdout=log,stderr=subprocess.STDOUT);assert result.returncode==0,str(e/(name+'-build.log'))
 result=subprocess.run([str(binary)],capture_output=True,text=True);(e/(name+'-result.txt')).write_text(result.stdout+result.stderr);assert result.returncode==expected,(name,result.returncode,result.stdout,result.stderr)
 records.append(dict(name=name,exit=result.returncode,expected=expected,output=result.stdout.strip(),header_sha256=hashlib.sha256(text.encode()).hexdigest(),compile=cmd,link=link))
assert header.read_bytes()==current_header
(e/'result.json').write_text(json.dumps(dict(status='passed',actual_source_unchanged=True,baseline_header=str(baseline),game_or_gpu_work_launched=False,controls=records,qualification='authored actual inline equality/RuntimeInfo/StageSpecialization/ranges lookup; no shader output or live white-model claim'),indent=2)+'\n');print(e);print(json.dumps([{k:v for k,v in x.items()if k in['name','exit','expected','output']}for x in records],indent=2))
