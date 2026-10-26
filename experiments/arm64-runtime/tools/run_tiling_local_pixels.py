#!/usr/bin/env python3
"""Isolated Source/Local pixel comparisons with real loader-root admission.

Consumes existing authored pixel sources and an admitted native-library fixture.
Never changes the renderer, loader checks, original fixture, or original cache.
"""
from pathlib import Path
import argparse, ast, hashlib, json, os, re, shutil, signal, subprocess, sys, time

ap=argparse.ArgumentParser()
ap.add_argument('--project',type=Path,required=True)
ap.add_argument('--native-dir',type=Path,required=True)
ap.add_argument('--output',type=Path,required=True)
a=ap.parse_args()
P=a.project.resolve(strict=True);N=a.native_dir.resolve(strict=True)
assert P.is_absolute() and (P/'state').is_dir() and not a.output.exists()
a.output.mkdir(parents=True);E=a.output.resolve(strict=True)
sys.path[:0]=[str(Path(__file__).resolve().parent),str(P/'tools')]
from native_observer import NativeObserver
from qa_memory_policy import MemoryPolicy
from qa_build_guard import require_no_games
from prepare_game_psos import dyld_gpu_image,verify_loaded_gpu

def sha(p):
 with Path(p).open('rb')as f:return hashlib.file_digest(f,'sha256').hexdigest()

def inventory(root):
 assert root==root.resolve(strict=True)
 result={}
 for f in root.rglob('*'):
  assert not f.is_symlink(),('linked fixture input',f)
  if f.is_file():result[str(f.relative_to(root))]=sha(f)
 return result

build=json.loads((N/'build.json').read_text())
pixels=json.loads((N/'pixel-build.json').read_text())
fixture=json.loads((N/'fixed-helpers-validation.json').read_text())
library=Path(build['library']);assert sha(library)==build['library_sha256']==pixels['library_sha256']
cache=Path(fixture['state'])/'gpu/local-metal'/fixture['cohort']
original_cache=inventory(cache)
tiling=fixture['calls'][2]['result']['tiling_libraries']
assert fixture['all_passed'] and tiling['local_ready']==14
target_keys={(x['bits'],x['texture']):x['request_key']for x in tiling['items']if x['macro']and x['bits']in(32,64)}
scene_path=Path(build['source_root'])/'vendor/metal_api/shadps4/video_core/renderer_metal/mtl_scene_targets.mm'
scene_text=scene_path.read_text()
scene_literal=re.search(r'NSString\* const CopySource = (.*?);\n',scene_text,re.S)
assert scene_literal is not None
scene_source=''.join(ast.literal_eval(v.removeprefix('@'))for v in re.findall(r'@?"(?:\\.|[^"\\])*"',scene_literal[1]))
scene_source_sha=hashlib.sha256(scene_source.encode()).hexdigest()
require_no_games()
X=P/'evidence'/('tiling-local-pixels-'+str(time.time_ns()));X.mkdir(mode=0o700)
state=P/'state'/X.name;state.mkdir(mode=0o700)
observer=NativeObserver();before=observer.system()
assert before['pressure']!=4 and not before['locked']
policy=MemoryPolicy(before);started=time.monotonic();steps=[];cases=[];samples=[]
report=dict(schema=1,status='incomplete',cases=cases,steps=steps,
 library=str(library),library_sha256=sha(library),execution_root=str(X),state=str(state),
 before=before,formal_data_modified=False,renderer_modified=False,loader_checks_modified=False,
 original_cache_modified=False,game_started=False,author_runtime_stubs=True,
 performance_evidence=False,memory_policy=MemoryPolicy.MANIFEST,dependency_warmups=[])

def check():
 elapsed=time.monotonic()-started;s=observer.system();samples.append(dict(seconds=elapsed,**s))
 reason=policy.observe(elapsed,s)
 if reason or elapsed>240:raise RuntimeError(reason or 'bounded pixel comparison interval')

def execute(label,command,cwd,env=None):
 require_no_games();check();log=cwd/(label+'.log');t=time.monotonic()
 with log.open('w')as out:
  p=subprocess.Popen(command,cwd=cwd,env=env,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
  try:
   while p.poll()is None:
    check()
    if log.stat().st_size>16*1024*1024:raise RuntimeError('bounded pixel log')
    time.sleep(.1)
  finally:
   if p.poll()is None:
    os.killpg(p.pid,signal.SIGTERM)
    try:p.wait(timeout=3)
    except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
 row=dict(label=label,pid=p.pid,exit=p.returncode,seconds=time.monotonic()-t,log=str(log))
 steps.append(row)
 assert p.returncode==0,row
 return row,log.read_text()

def analyze(row,text,name,bounded,mode,selected):
 row.update(test=name,bounded=bounded,mode=mode,configuration=dict(selected))
 loaded={v for line in text.splitlines()if(v:=dyld_gpu_image(line,row['pid']))}
 row['loaded_gpu']=verify_loaded_gpu(loaded,build)
 row['hit_keys']=sorted(set(re.findall(r'Metal helper local AOT: hit key=([0-9a-f]{64})',text)))
 api=re.findall(r'^PIXEL_API (\d+) (\d+) (\d+) (\d+)$',text,re.M);assert len(api)==1
 row['source_calls'],row['source_failed'],row['local_calls'],row['local_failed']=map(int,api[0])
 row['digests']=re.findall(r'^PIXEL_DIGEST (\S+ \d+ \d+ \d+ \d+ \d+) ([0-9a-f]{64})$',text,re.M)
 row['fail_lines']=[line for line in text.splitlines()if 'FAIL'in line]
 return row

def prepare_scene_dependency(case,gpu,local):
 # The proxy test also resolves scene images through the original CopySource
 # library. This is a test dependency, not a new tiling-library seed or renderer change.
 current=local/fixture['cohort'];extra=[]
 for p in(current/'requests').glob('*/*.json'):
  if str(p.relative_to(current))not in original_cache:extra.append(json.loads(p.read_text()))
 assert len(extra)==1 and extra[0]['source_sha256']==scene_source_sha,('unexpected extra shader dependency',extra)
 import local_metal_builder as builder
 builder.PROJECT=P # Select the existing isolated project, never weaken runtime root checks.
 tmp=case/'builder-tmp';tmp.mkdir(mode=0o700)
 other,guard,toolchain=builder.real_services(tmp)
 class CombinedGuard:
  def check(self,*,preflight=False):
   check();return guard.check(preflight=preflight)
 old=os.environ.get('BB_GPU_USER_DIR');os.environ['BB_GPU_USER_DIR']=str(gpu)
 try:summary=builder.build(local,fixture['cohort'],enabled=True,guard=CombinedGuard(),toolchain=toolchain)
 finally:
  if old is None:os.environ.pop('BB_GPU_USER_DIR',None)
  else:os.environ['BB_GPU_USER_DIR']=old
  other.close()
 assert summary['status']=='passed'and summary['built']==1 and summary['requests']==23,summary
 return dict(request_key=extra[0]['key'],source_sha256=scene_source_sha,
  source_provider_sha256=sha(scene_path),builder=summary)

prefix='''#include <CommonCrypto/CommonDigest.h>
#include <cstdio>
#include <cstdint>
#include <cstdlib>
static void PixelDigest(const char* kind,unsigned bits,unsigned w,unsigned h,unsigned swizzle,const void* bytes,size_t size){
 if(size>UINT32_MAX)std::abort();unsigned char hash[CC_SHA256_DIGEST_LENGTH];
 CC_SHA256(bytes,(CC_LONG)size,hash);std::printf("PIXEL_DIGEST %s %u %u %u %u %zu ",kind,bits,w,h,swizzle,size);
 for(unsigned char v:hash)std::printf("%02x",v);std::puts("");
}
'''
suffix='''
#include "video_core/renderer_metal/mtl_phase_counters.h"
#include <dlfcn.h>
int main(){int result=original_pixel_main();
 // Read the loaded renderer's storage, not an executable-local inline variable.
 void* handle=dlopen(PIXEL_GPU_LIBRARY,RTLD_LAZY|RTLD_NOLOAD);if(!handle)return 93;
 auto* values=reinterpret_cast<decltype(&BbMetalPhase::values)>(dlsym(handle,"_ZN12BbMetalPhase6valuesE"));
 if(!values)return 94;BbMetalPhase::Snapshot a{},b{};
 for(unsigned phase=0;phase<2;++phase)for(unsigned k=0;k<BbMetalPhase::Count;++k){
  auto& v=(*values)[phase][k];(phase?b:a)[k]={v.calls.load(),v.ok.load(),v.failed.load(),v.api_ns.load()};
 }
 const auto i=unsigned(BbMetalPhase::Kind::SourceLibrary),j=unsigned(BbMetalPhase::Kind::LocalLibraryData);
 std::printf("PIXEL_API %llu %llu %llu %llu\\n",(unsigned long long)(a[i].calls+b[i].calls),(unsigned long long)(a[i].failed+b[i].failed),(unsigned long long)(a[j].calls+b[j].calls),(unsigned long long)(a[j].failed+b[j].failed));
 dlclose(handle);
 return result;
}
'''

try:
 binaries={};sources={}
 compile0=next(x['command']for x in build['units']if x['source'].endswith('/game_artifact_native.mm'))
 object0=compile0[compile0.index('-o')+1];link0=build['binaries']['author']['link']
 hostobj=next(x for x in link0 if x.endswith('/host.o'))
 for original in pixels['binaries']:
  name=Path(original['binary']).name;source=Path(original['source']);text=source.read_text();sources[str(source)]=sha(source)
  if name=='test_mtl_runtime':
   begin=text.index('static void TestTextureTiling(');end=text.index('static void TestAdaptiveStaging(',begin)
   body=text[begin:end];anchor='CHECK(std::memcmp(reference->mapped_data.data(),actual->mapped_data.data(),actual->SizeBytes())==0);'
   # Two real comparisons: direct path and TileManager integration. Score the final result once.
   assert body.count(anchor)==2;where=body.rindex(anchor)+len(anchor)
   body=body[:where]+'\n PixelDigest("native",bytes*8,w,h,swizzle,actual->mapped_data.data(),actual->SizeBytes());'+body[where:]
   text=text[:begin]+body+text[end:]
  else:
   begin=text.index('static void TestProxyTextureTiling(');end=text.index('static void TestDirectCopyToNative(',begin)
   body=text[begin:end];anchor='CHECK(std::memcmp(actual.mapped_data.data(),reference.mapped_data.data(),info.guest_size)==0);'
   assert body.count(anchor)==1;body=body.replace(anchor,anchor+'\n PixelDigest("proxy",bytes*8,1920,1080,0,actual.mapped_data.data(),info.guest_size);')
   text=text[:begin]+body+text[end:]
  assert text.count('int main()')==1
  text=prefix+text.replace('int main()','int original_pixel_main()')+suffix
  target_source=X/(name+'.mm');target_source.write_text(text);obj=X/(name+'.o');binary=X/name
  command=compile0[:];command[-1]=str(target_source);command[command.index('-o')+1]=str(obj)
  command[1:1]=['-DPIXEL_GPU_LIBRARY='+json.dumps(str(library))]
  execute(name+'-compile',command,X)
  command=[str(obj)if v==object0 else v for v in link0 if v!=hostobj];command[command.index('-o')+1]=str(binary)
  execute(name+'-link',command,X);execute(name+'-sign',['codesign','--force','--sign','-',str(binary)],X)
  symbols=subprocess.check_output(['nm','-m',str(binary)],text=True)
  assert not any('BbMetalPhase6valuesE'in line for line in symbols.splitlines()),'test executable owns a second counter storage'
  binaries[name]=binary
 report['original_source_sha256']=sources
 comparisons={}
 for name,only in(('test_mtl_runtime','BB_TEST_TEXTURE_TILING_ONLY'),('test_mtl_scene_targets','BB_TEST_PROXY_TILING_ONLY')):
  for bounded in('0','1'):
   modes=('off','load','wrong-root')if name=='test_mtl_runtime'and bounded=='0'else('off','load')
   for mode in modes:
    case=state/(name+'-'+bounded+'-'+mode);case.mkdir();(case/'tmp').mkdir();gpu=case/'gpu';gpu.mkdir()
    local=gpu/'local-metal';local.mkdir();shutil.copytree(cache,local/fixture['cohort'])
    assert inventory(local/fixture['cohort'])==original_cache
    assert local.is_relative_to(gpu) and local==local.resolve(strict=True)
    env={k:v for k,v in os.environ.items()if not k.startswith(('BB_','DYLD_'))}
    selected=dict(BB_GPU_USER_DIR=str(gpu),BB_MTL_LOCAL_AOT_ROOT=str(cache.parent if mode=='wrong-root'else local),BB_MTL_LOCAL_AOT_MODE='off'if mode=='off'else'load',
      BB_MTL_LOCAL_AOT_COHORT=fixture['cohort'],BB_MTL_LOCAL_AOT_BUILD_ID=fixture['local_builder']['build_id'],
      BB_MTL_ARCHIVE_COMPATIBILITY_ID=fixture['cohort'],BB_MTL_PERSIST_WARM_ARCHIVE='0',BB_MTL_RELEASE_WARM_ARCHIVE='1',
      BB_GUEST_IN_PLACE='0',BB_LAYER_MEMORY='0',BB_MTL_TEXTURE_TILING='1',BB_MTL_TEXTURE_TILING_HALF='1',
      BB_ARM_TILER_BOUNDS=bounded,BB_MTL_FLOAT_RULES='strict',BB_MTL_STRICT_FLOAT='1',MTL_DEBUG_LAYER='1')
    env.update(selected,HOME=str(case),TMPDIR=str(case/'tmp'),DYLD_PRINT_LIBRARIES='1');env[only]='1'
    row,text=execute('pixels',[str(binaries[name])],case,env)
    row=analyze(row,text,name,bounded,mode,selected)
    if mode=='load'and row['source_calls']:
     assert name=='test_mtl_scene_targets'and not row['fail_lines'],'unexpected non-proxy fallback'
     expected={v for (bits,texture),v in target_keys.items()if texture}
     assert expected.issubset(row['hit_keys'])and row['source_calls']==2
     report['dependency_warmups'].append(row)
     dependency=prepare_scene_dependency(case,gpu,local)
     selected['BB_MTL_LOCAL_AOT_BUILD_ID']=dependency['builder']['build_id'];env.update(selected)
     row,text=execute('pixels-prepared',[str(binaries[name])],case,env)
     row=analyze(row,text,name,bounded,mode,selected);row['scene_dependency']=dependency
    cases.append(row);assert not row['fail_lines'] and len(row['digests'])==(18 if name=='test_mtl_runtime'else 2)
    assert row['source_failed']==0 and row['local_failed']==0
    if mode in('off','wrong-root'):
     assert row['source_calls']>0 and row['local_calls']==0 and not row['hit_keys']
     if mode=='wrong-root':assert row['digests']==comparisons[(name,bounded)]['digests']
    else:
     expected={v for (bits,texture),v in target_keys.items()if texture or name=='test_mtl_runtime'}
     assert expected.issubset(row['hit_keys']),('tiling library admission incomplete',row)
     assert row['local_calls']>0 and row['source_calls']==0,('unexpected source fallback',row)
     source_case=comparisons[(name,bounded)];assert row['digests']==source_case['digests'],'Source/Local output differs'
    if mode=='off':comparisons[(name,bounded)]=row
    print(name,bounded,mode,'source',row['source_calls'],'local',row['local_calls'],flush=True)
 report['status']='passed'
finally:
 report.update(after=observer.system(),seconds=time.monotonic()-started,samples=samples)
 report['original_cache_modified']=inventory(cache)!=original_cache
 (E/'result.json').write_text(json.dumps(report,indent=2)+'\n');observer.close()
 assert not report['original_cache_modified'],'original fixture cache changed'
print(json.dumps(dict(status=report['status'],cases=len(cases),output=str(E)),indent=2))
