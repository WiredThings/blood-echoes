"""Guarded scene sampling from the original Swift launch receipt, not preferences.

Only Continue is automated before a scene camera is observed. No exploration,
combat, screenshots, profiling or changes to original data are performed.
"""
from pathlib import Path
import argparse,collections,csv,hashlib,json,math,os,signal,stat,statistics,struct,subprocess,sys,time
from native_observer import NativeObserver
from qa_memory_policy import MemoryPolicy
from diagnostic_frame_tail import read_tail
from qa_build_guard import require_no_games
from run_game import usage
P=Path('/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype')
def sha(path):
 with Path(path).open('rb')as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def write(path,value):Path(path).write_text(json.dumps(value,indent=2)+'\n')
def stop_signal(number,frame):
 # Raising enters the existing finally block, which reaps only this run's tree.
 raise InterruptedError('benchmark supervisor received signal '+str(number))
def scene_seen(rows):
 for row in rows[-30:]:
  try:
   if int(row['draws'])>100 and all(row.get(k)and math.isfinite(float(row[k]))for k in('cam_x','cam_y','cam_z')):return True
  except(ValueError,TypeError,KeyError):pass
 return False
def deadline(elapsed,ready,startup,scene):
 if ready is None:return 'startup time budget'if elapsed>=startup else None
 return 'scene sample completed'if elapsed-ready>=scene else None

def tool_memory_summary(path,ready):
 """Observed whole owned-tree peaks, not an allocator HWM or physical sum.

 Read the complete bounded-line trace after teardown, not the scene deque,
 which can have discarded the beginning of a long preparation phase.
 """
 peak=before=0;roles={};observations=0
 with Path(path).open()as stream:
  for line in stream:
   if len(line)>4*1024*1024:raise ValueError('tool memory sample line bound')
   value=json.loads(line);rows=value.get('tool_usage')
   if rows is None:continue
   if not isinstance(rows,list)or len(rows)>4096:raise ValueError('tool memory process bound')
   total=0;per_role=collections.Counter();seen=set()
   for row in rows:
    pid=row['pid'];role=row['role'];size=row['footprint_bytes']
    if type(pid)is not int or pid<=0 or pid in seen or not isinstance(role,str)or not 0<len(role)<=256 or type(size)is not int or size<0:raise ValueError('tool memory sample shape')
    seen.add(pid);total+=size;per_role[role]+=size
   observations+=1;peak=max(peak,total)
   if ready is None or value['seconds']<=ready:before=max(before,total)
   for role,size in per_role.items():roles[role]=max(roles.get(role,0),size)
 return dict(observations=observations,owned_tree_peak_mib=peak/2**20,
  before_scene_peak_mib=before/2**20,role_peaks_mib={k:v/2**20 for k,v in sorted(roles.items())},
  sampled_not_allocator_hwm=True,shared_pages_may_be_counted_more_than_once=True)

# Native videoout logs complete CSV rows and flushes at least every 120 flips.
# Poll once/second; allow a bounded 10s durable-row lag for batching and polling.
# This is telemetry admission, not a looser memory/lock/run-stop policy.
FRAME_LOG_POLL_SECONDS=1.0
FRAME_LOG_FRESHNESS_SECONDS=10.0
FRAME_LOG_MIN_WALL_COVERAGE=.90
FRAME_LOG_MAX_SCENE_FRAMES=20000

class FrameProgress:
 """Bounded live provenance for the existing CSV; never invent frame counters."""
 def __init__(self):
  self.identity=None;self.size=0;self.latest=None;self.last_advance=None
  self.last_poll=None;self.observations=0;self.scene_start=None;self.scene_wall_start=None
  self.max_gap=0.0
 def observe(self,path,seconds,rows):
  if not math.isfinite(seconds)or seconds<0 or(self.last_poll is not None and seconds<self.last_poll):raise ValueError('frame observation clock moved backwards')
  self.last_poll=seconds
  if not path.exists():
   if self.identity is not None:raise ValueError('frame log disappeared during sample')
   return
  info=path.lstat()
  if not stat.S_ISREG(info.st_mode):raise ValueError('frame log must remain a plain file')
  identity=(info.st_dev,info.st_ino)
  if self.identity is not None and(identity!=self.identity or info.st_size<self.size):raise ValueError('frame log was replaced or truncated during sample')
  self.identity=identity;self.size=info.st_size
  if not rows:return
  try:value=float(rows[-1]['t_s'])
  except(ValueError,TypeError,KeyError)as error:raise ValueError('invalid latest frame clock')from error
  if not math.isfinite(value)or value<0:raise ValueError('invalid latest frame clock')
  if self.latest is not None and value<self.latest:raise ValueError('frame clock moved backwards during sample')
  if self.scene_start is not None:self.max_gap=max(self.max_gap,seconds-self.last_advance)
  if self.latest is None or value>self.latest:self.latest=value;self.last_advance=seconds
  self.observations+=1
 def start_scene(self,seconds):
  if self.latest is None or self.last_advance is None:raise ValueError('scene has no observed frame clock')
  self.scene_start=self.latest;self.scene_wall_start=seconds;self.max_gap=0.0
 def completion_reason(self,seconds,scene_seconds):
  if self.scene_wall_start is None:return None
  target=self.scene_wall_start+scene_seconds
  if seconds<target:return None
  # A recently frozen tail is insufficient: require a real published advance
  # AFTER the nominal wall deadline and the whole requested frame-clock span.
  if self.last_advance is not None and self.last_advance>=target and self.latest-self.scene_start>=scene_seconds-1e-6:return 'scene sample completed'
  if seconds>=target+FRAME_LOG_FRESHNESS_SECONDS:return 'scene frame progress time budget'
  return None
 def summary(self,ended):
  if self.scene_start is None or self.latest is None or self.last_advance is None:raise ValueError('scene has no bounded frame progress evidence')
  wall=ended-self.scene_wall_start;clock=self.latest-self.scene_start;age=ended-self.last_advance
  if not all(math.isfinite(v)for v in(ended,wall,clock,age))or wall<=0 or clock<=0 or age<0:raise ValueError('invalid scene frame progress span')
  if age>FRAME_LOG_FRESHNESS_SECONDS:raise ValueError('stale frame log at scene completion')
  if abs(clock-wall)>FRAME_LOG_FRESHNESS_SECONDS or clock<wall*FRAME_LOG_MIN_WALL_COVERAGE:raise ValueError('frame-clock span does not cover observed scene wall time')
  return dict(clock_start=self.scene_start,clock_end=self.latest,clock_seconds=clock,
   wall_start=self.scene_wall_start,wall_end=ended,wall_seconds=wall,
   boundary_difference_seconds=clock-wall,last_advance_seconds=self.last_advance,
   completion_age_seconds=age,max_observed_no_progress_seconds=max(self.max_gap,age),
   observations=self.observations,freshness_limit_seconds=FRAME_LOG_FRESHNESS_SECONDS,
   min_wall_coverage=FRAME_LOG_MIN_WALL_COVERAGE,file_device=self.identity[0],file_inode=self.identity[1],
   observed_file_bytes=self.size)

def scene_summary(frames,samples,ready,scene_seconds,*,frame_progress=None):
 """Full observed scene interval, including stalls; never reuse a frozen tail."""
 if not isinstance(frame_progress,dict):raise ValueError('bounded live frame progress evidence required')
 progress=frame_progress
 start,end=progress['clock_start'],progress['clock_end'];wall=progress['wall_seconds'];clock=end-start
 if (progress['wall_start']!=ready or wall<scene_seconds or clock<scene_seconds-1e-6 or
     progress['last_advance_seconds']<ready+scene_seconds or
     progress['completion_age_seconds']>FRAME_LOG_FRESHNESS_SECONDS or
     abs(clock-wall)>FRAME_LOG_FRESHNESS_SECONDS or clock<wall*FRAME_LOG_MIN_WALL_COVERAGE):raise ValueError('stale or unmatched scene wall/frame progress')
 info=frames.lstat()
 if not stat.S_ISREG(info.st_mode)or(info.st_dev,info.st_ino)!=(progress['file_device'],progress['file_inode'])or info.st_size<progress['observed_file_bytes']:raise ValueError('frame log changed after progress observation')
 selected=[];times=[];previous=None
 with frames.open()as stream:
  reader=csv.DictReader(stream)
  required={'t_s','frame_ms','draws','gpu_ms','cam_x','cam_y','cam_z'}
  if not required<=set(reader.fieldnames or()):raise ValueError('incomplete frame schema')
  for row in reader:
   try:t=float(row['t_s']);ms=float(row['frame_ms']);gpu=float(row['gpu_ms']);draws=int(row['draws'])
   except(ValueError,TypeError,KeyError):continue
   if not all(math.isfinite(v)for v in(t,ms,gpu))or ms<=0 or gpu<0 or draws<0:continue
   if previous is not None and t<previous:raise ValueError('frame clock moved backwards')
   interval=(t-previous)*1000 if previous is not None else ms;previous=t
   if not start<t<=end:continue
   if len(selected)>=FRAME_LOG_MAX_SCENE_FRAMES:raise ValueError('complete scene frame budget exceeded')
   selected.append(row);times.append(max(ms,interval))
 if len(selected)<100:raise ValueError('insufficient complete scene frames')
 if float(selected[-1]['t_s'])!=end:raise ValueError('observed ending frame is absent from final CSV')
 if sum(scene_seen([r])for r in selected)<len(selected)*.9:raise ValueError('frame interval is not a stable scene camera')
 stable=[s for s in samples if ready<=s['seconds']<=progress['wall_end']]
 if not stable:raise ValueError('missing wall-matched scene memory samples')
 ordered=sorted(times)
 return dict(frames=len(selected),fps=len(selected)/clock,
  p95_ms=ordered[int((len(ordered)-1)*.95)],p99_ms=ordered[int((len(ordered)-1)*.99)],
  max_frame_ms=max(times),gpu_ms=statistics.mean(float(r['gpu_ms'])for r in selected),
  draws_median=statistics.median(int(r['draws'])for r in selected),
  camera={k:selected[-1].get(k)for k in('cam_x','cam_y','cam_z')},
  combined_footprint_mib=statistics.median(sum(u['footprint_bytes']for u in s['usage'])/2**20 for s in stable),
  frame_progress=progress,window='complete observed scene clock interval; bounded CSV flush lag against supervisor wall')

def admit(conditions):
 from gpu_candidate_identity import verify
 from warm_archive_manifest import resolve_gpu_identity
 from qa_gpu_cache_seed import validate
 stage=conditions['stage']
 for rel,path in conditions['runtime'].items():
  if sha(path)!=conditions['runtime_sha256'][rel]:raise ValueError('packaged runtime changed')
 runtime=conditions['runtime'];hashes=conditions['runtime_sha256']
 gpu=verify(dict(target=str(runtime['bin/Bloodborne Metal.app/Contents/MacOS/bb-gpu']),library=str(runtime['bin/gpu-metal-arm64/libbbgpu.dylib']),library_sha256=hashes['bin/gpu-metal-arm64/libbbgpu.dylib']),stage)
 selected_cpu=conditions['environment'].get('BB_CPU_BACKEND','fex')
 if selected_cpu not in('fex','bbcpu'):raise ValueError('unknown selected CPU backend')
 cpu_role='bin/bb-probe-metal'
 host_role='bin/gpu-metal/libbbgpu.dylib'
 if selected_cpu=='bbcpu':
  from bbcpu_launch import component
  cpu_component,cpu_paths=component(stage)
  cpu_role=cpu_component['cpu']['path']
  if cpu_role not in runtime or runtime[cpu_role]!=cpu_paths['cpu']:raise ValueError('selected bbcpu host is not in admitted runtime roles')
  # This host is separately relocated and signed by the same package resolver;
  # verify its exact declared edge, not the FEX host's different install path.
  hosts=[role for role in cpu_component['runtime_dependencies']if Path(role).name=='libbbgpu.dylib']
  if len(hosts)!=1:raise ValueError('selected bbcpu needs one exact frozen host library')
  host_role=hosts[0]
 cpu=resolve_gpu_identity(runtime[cpu_role],stage)
 if cpu['library']!=str(runtime[host_role])or cpu['library_sha256']!=hashes[host_role]:raise ValueError('CPU actual host differs')
 manifest=stage.parent/'ARM64-QA-MANIFEST.json';m=json.loads(manifest.read_text())
 if m.get('kind')=='source-built-independent-cpu-qa':
  from qa_gpu_cache_seed import validate_source_built
  cache=validate_source_built(manifest,project=P,expected_packaged_gpu_sha256=hashes['bin/gpu-metal-arm64/libbbgpu.dylib'])
 else:cache=validate(manifest,project=P,expected_upstream_gpu_sha256=m['upstream_inputs']['gpu_library'])
 return dict(cpu_backend=selected_cpu,cpu=cpu,gpu=gpu),cache

CPU_ROLES=('bin/bb-probe-metal','bin/Bloodborne Game.app/Contents/MacOS/bb-probe-metal')
FORMAL_ROLES=frozenset((*CPU_ROLES,'bin/gpu-metal/libbbgpu.dylib',
 'bin/Bloodborne Metal.app/Contents/MacOS/bb-gpu','bin/gpu-metal-arm64/libbbgpu.dylib',
 'bin/gpu-net/libbbnet.dylib','bin/game-pso-preparer','bin/game-module-preparer'))

# Exactly one reviewed source transition, not a category of permitted UI edits.
# The source is 00385145 and the unchanged frozen Swift QA stage; the target is
# the 2026-10-30 optional-UI validation fix reviewed before game regression.
REVIEWED_OPTIONAL_UI_SCRIPT='scripts/ultrawide_ui.py'
REVIEWED_OPTIONAL_UI_SOURCE='5c6619d969e5bd1a8b50afc30baa6d7f3540bd39b628bb133dcdb4146415fefa'
REVIEWED_OPTIONAL_UI_TARGET='d55933de39172d83b194c61368e955d43df7b39b027b34db0809f6bc04277aff'

def cpu_paths(conditions):
 # Exact independently admitted roles. The shell chooses nested when present;
 # do not pretend the top executable was the observed nested process.
 return {str(conditions['runtime'][role])for role in CPU_ROLES if role in conditions['runtime']}

def app_seal(app,*,runner=None):
 """Whole resource envelope, not just executable signatures. No launch."""
 command=['/usr/bin/codesign','--verify','--deep','--strict',str(app)]
 try:
  result=(runner or subprocess.run)(command,capture_output=True,text=True,timeout=30)
  output=(result.stdout or'')+(result.stderr or'')
  if type(result.returncode)is not int or result.returncode or len(output)>65536:
   return dict(status='failed',command=command,exit=result.returncode,error=output[:2048]or'whole app resource seal rejected')
  return dict(status='verified',command=command,exit=0)
 except (OSError,subprocess.SubprocessError)as error:
  return dict(status='failed',command=command,error=str(error)[:2048])

def finish_app_seal(report,formal,*,runner=None):
 """Call only AFTER owned child teardown. Preserve the independent raw result."""
 result=dict(report,raw_run_status=report['status'])
 post=app_seal(formal['app'],runner=runner)
 result['whole_app_seal']=dict(pre=formal['seal'],post=post,post_after_owned_process_cleanup=True)
 if formal['seal'].get('status')!='verified'or post['status']!='verified':
  result.update(status='incomplete',seal_error='whole staged execution application resource seal failed')
 return result

def admit_formal(app,source,*,project=P,bundle_loader=None,gpu_verifier=None,identity_resolver=None,seal_runner=None):
 """Extra code-only admission. Original QA selection/save reader is unchanged.

 Default loaders perform real bundle byte, Mach-O ARM64, signature and declared
 load-edge checks. Test injections exercise contracts without native processes.
 """
 from qa_launch_conditions import canonical,SCRIPTS
 from launcher_runtime_profile import bundle,code_bytes
 from gpu_candidate_identity import verify
 from warm_archive_manifest import resolve_gpu_identity
 project=canonical(project,directory=True)
 if project!=P:raise ValueError('formal benchmark uses only the fixed Shared development project')
 app=canonical(app,directory=True,within=project/'evidence')
 if app.suffix!='.app':raise ValueError('formal-stage must be a new staged .app under development evidence')
 seal=app_seal(app,runner=seal_runner)
 if seal['status']!='verified':raise ValueError('whole application seal failed before run: '+seal.get('error','rejected'))
 stage=canonical(app/'Contents/Resources/bbport',directory=True,within=app)
 admitted=(bundle_loader or bundle)(stage)
 if admitted is None or admitted.stage!=stage or admitted.app!=app:raise ValueError('actual outer formal bundle admission missing')
 hashes=dict(admitted.native['frozen_runtime'])
 if set(hashes)!=FORMAL_ROLES:raise ValueError('formal stage requires exactly eight frozen actual runtime roles')
 runtime={role:canonical(stage/role,within=stage)for role in hashes}
 parity_record=json.loads(code_bytes(stage/'SOURCE-PARITY.json')[0]);parity=parity_record['effective_sources']
 expected=dict(source['effective_scripts']);image_reuse_projection=False;reviewed_script_projections=[]
 name=REVIEWED_OPTIONAL_UI_SCRIPT
 if set(parity)==set(expected)==set(SCRIPTS)and expected[name]==REVIEWED_OPTIONAL_UI_SOURCE and parity[name]==REVIEWED_OPTIONAL_UI_TARGET:
  # Validate both complete files, not only the manifests. The frozen receipt and
  # its source environment stay unchanged; all other scripts still compare below.
  original=code_bytes(Path(source['stage'])/name,limit=4*1024*1024,expected=REVIEWED_OPTIONAL_UI_SOURCE)[0]
  revised=code_bytes(stage/name,limit=4*1024*1024,expected=REVIEWED_OPTIONAL_UI_TARGET)[0]
  expected[name]=hashlib.sha256(revised).hexdigest()
  reviewed_script_projections.append(dict(script=name,source_revision='00385145',review='optional-ui-validation-20261030',
   source_sha256=hashlib.sha256(original).hexdigest(),target_sha256=expected[name],complete_file_bytes_checked=True))
 if parity!=expected and set(parity)==set(expected)==set(SCRIPTS)and{k for k in expected if parity[k]!=expected[k]}=={'run.sh'}and parity_record.get('image_preparation_reuse')is True:
  # One exact source-reviewed addition. Do not edit the original QA receipt,
  # accept arbitrary new script hashes, or skip any of the other 15 bodies.
  from prepare_local_aot_stage import image_preparation_revision,IMAGE_HOOK
  original=code_bytes(Path(source['stage'])/'run.sh',limit=4*1024*1024,expected=expected['run.sh'])[0]
  revised=image_preparation_revision(original.decode()).encode()
  actual=code_bytes(stage/'run.sh',limit=4*1024*1024,expected=parity['run.sh'])[0]
  if revised!=original and actual==revised and actual.count(IMAGE_HOOK.encode())==1:
   expected['run.sh']=hashlib.sha256(revised).hexdigest();image_reuse_projection=True
 if set(parity)!=set(SCRIPTS)or expected!=parity:raise ValueError('formal stage differs from original QA receipt sixteen scripts')
 owners={'code_owners':(0,os.getuid())};cpus={}
 for role in CPU_ROLES:
  value=(identity_resolver or resolve_gpu_identity)(runtime[role],stage,**owners)
  if value.get('target')!=str(runtime[role])or value.get('target_sha256')!=hashes[role]or value.get('library')!=str(runtime['bin/gpu-metal/libbbgpu.dylib'])or value.get('library_sha256')!=hashes['bin/gpu-metal/libbbgpu.dylib']or value.get('load_edge_verified')is not True or value.get('strict_signatures')is not True or value.get('architecture')!='arm64':
   raise ValueError('formal actual CPU/host role identity differs: '+role)
  cpus[role]=value
 gpu_role='bin/Bloodborne Metal.app/Contents/MacOS/bb-gpu'
 gpu=(gpu_verifier or verify)(dict(target=str(runtime[gpu_role]),library=str(runtime['bin/gpu-metal-arm64/libbbgpu.dylib']),library_sha256=hashes['bin/gpu-metal-arm64/libbbgpu.dylib']),stage,**owners)
 if gpu.get('target')!=str(runtime[gpu_role])or gpu.get('target_sha256')!=hashes[gpu_role]or gpu.get('library')!=str(runtime['bin/gpu-metal-arm64/libbbgpu.dylib'])or gpu.get('library_sha256')!=hashes['bin/gpu-metal-arm64/libbbgpu.dylib']or gpu.get('load_edge_verified')is not True or gpu.get('strict_signatures')is not True or gpu.get('architecture')!='arm64':
  raise ValueError('formal actual GPU/DSO role identity differs')
 return dict(app=app,stage=stage,runtime=runtime,runtime_sha256=hashes,seal=seal,
  identity=dict(profile='staged-formal-bundle-isolated-CLI',app=str(app),stage=str(stage),
   native_manifest_sha256=sha(stage/'NATIVE-METAL.json'),runtime_sha256=hashes,
   effective_scripts=parity,image_preparation_exact_projection=image_reuse_projection,
   reviewed_script_projections=reviewed_script_projections,cpu=cpus,gpu=gpu,whole_app_pre_seal=seal,formal_GUI_started=False,formal_data_read=False))

def reference_image(path):
 """Read actual x86/ARM Mach-O metadata with the existing package parser."""
 from launcher_runtime_profile import code_bytes
 root=Path(__file__).resolve().parents[3];sys.path.insert(0,str(root/'tools/macos'))
 from package_optimized import loads
 path=Path(path);before=code_bytes(path,limit=512*1024*1024,content=False)[1]
 with path.open('rb')as stream:header=stream.read(16)
 if len(header)!=16:raise ValueError('reference Mach-O header missing')
 magic,cpu,_,kind=struct.unpack('<4I',header)
 if magic!=0xfeedfacf or cpu not in(0x01000007,0x0100000c)or kind not in(2,6):raise ValueError('reference must be a thin x86_64/ARM64 Mach-O executable or dylib')
 arch='x86_64'if cpu==0x01000007 else'arm64'
 if subprocess.check_output(['/usr/bin/lipo','-archs',str(path)],text=True,timeout=10).strip()!=arch:raise ValueError('reference actual Mach-O architecture differs')
 dependencies=loads(path)
 signed=subprocess.run(['/usr/bin/codesign','--verify','--strict',str(path)],capture_output=True,text=True,timeout=30)
 if signed.returncode:raise ValueError('reference image signature rejected: '+str(path))
 if code_bytes(path,limit=512*1024*1024,content=False)[1]!=before:raise ValueError('reference code changed during native inspection')
 return dict(path=str(path),architecture=arch,filetype=kind,sha256=before['sha256'],dependencies=dependencies,strict_signature=True)

def reference_closure(stage,*,image_inspector=None):
 """Two real x86 CPUs/host and ARM backend, with bounded relative closure."""
 from gpu_candidate_identity import resolve_gpu_edge
 inspect=image_inspector or reference_image;cache={};pending=[]
 def image(path,architecture,kind):
  path=Path(path);key=str(path)
  if key not in cache:
   value=inspect(path)
   if value.get('path')!=key or value.get('strict_signature')is not True:raise ValueError('reference inspector did not verify actual image')
   cache[key]=value;pending.append((path,architecture))
  value=cache[key]
  if value.get('architecture')!=architecture or value.get('filetype')!=kind:raise ValueError('reference actual architecture/filetype mismatch')
  return value
 host=stage/'bin/gpu-metal/libbbgpu.dylib';gpu=stage/'bin/Bloodborne Metal.app/Contents/MacOS/bb-gpu';gpu_library=stage/'bin/gpu-metal-arm64/libbbgpu.dylib'
 cpus={}
 for role in CPU_ROLES:
  target=stage/role;value=image(target,'x86_64',2)
  edges=[e for e in value['dependencies']if Path(e).name.startswith('libbbgpu')]
  if len(edges)!=1 or not edges[0].startswith(('@loader_path/','@executable_path/'))or resolve_gpu_edge(target,edges[0])!=host:raise ValueError('reference CPU needs one exact relative host edge')
  cpus[role]=dict(target=str(target),target_sha256=value['sha256'],architecture='x86_64',library=str(host),load_edge_verified=True,strict_signatures=True)
 image(host,'x86_64',6);image(gpu,'arm64',2);image(gpu_library,'arm64',6)
 while pending:
  path,arch=pending.pop()
  if len(cache)>256:raise ValueError('reference native dependency closure bound')
  for edge in cache[str(path)]['dependencies']:
   if edge.startswith(('/usr/lib/','/System/')):continue
   if not edge.startswith(('@loader_path/','@executable_path/')):raise ValueError('reference dependency is not relative to its packaged image')
   target=resolve_gpu_edge(path,edge)
   if not target.is_relative_to(stage):raise ValueError('reference relative native dependency escapes code package')
   image(target,arch,6)
 for value in cpus.values():value['library_sha256']=cache[str(host)]['sha256']
 return cpus,dict(sorted(cache.items()))

def admit_reference(app,source,*,project=P,seal_runner=None,image_inspector=None,gpu_verifier=None):
 """Original 0.2.51 code-only reference; never its GUI or formal data."""
 from qa_launch_conditions import canonical,SCRIPTS
 from launcher_runtime_profile import code_bytes
 from gpu_candidate_identity import verify
 import plistlib
 project=canonical(project,directory=True)
 if project!=P:raise ValueError('reference benchmark uses only the fixed Shared development project')
 app=canonical(app,directory=True,within=project/'evidence')
 if app.suffix!='.app':raise ValueError('reference-stage must be a staged original application')
 seal=app_seal(app,runner=seal_runner)
 if seal['status']!='verified':raise ValueError('whole reference application seal failed before run')
 stage=canonical(app/'Contents/Resources/bbport',directory=True,within=app)
 info=plistlib.loads(code_bytes(app/'Contents/Info.plist')[0])
 if info.get('CFBundleIdentifier')!='io.github.bbport.optimized'or info.get('CFBundleShortVersionString')!='0.2.51':raise ValueError('reference requires original formal 0.2.51 identity')
 native=json.loads(code_bytes(stage/'NATIVE-METAL.json')[0])
 if native.get('game_runtime')=='fex-arm64'or native.get('default_enabled')is not True or native.get('minimum_os_major')!=27:raise ValueError('reference must retain its original enabled ARM64 Metal backend')
 if source['environment'].get('BB_METAL_ARM64')not in(None,'1'):raise ValueError('original source must already select the ARM64 Metal backend')
 scripts={name:code_bytes(stage/name,limit=4*1024*1024,content=False)[1]['sha256']for name in SCRIPTS}
 if set(source['original_scripts'])!=set(SCRIPTS)or dict(source['original_scripts'])!=scripts:raise ValueError('reference differs from receipt original sixteen scripts')
 runtime={role:canonical(stage/role,within=stage)for role in (*CPU_ROLES,'bin/gpu-metal/libbbgpu.dylib','bin/Bloodborne Metal.app/Contents/MacOS/bb-gpu','bin/gpu-metal-arm64/libbbgpu.dylib')}
 cpus,closure=reference_closure(stage,image_inspector=image_inspector)
 gpu_role='bin/Bloodborne Metal.app/Contents/MacOS/bb-gpu'
 gpu=(gpu_verifier or verify)(dict(target=str(runtime[gpu_role]),library=str(runtime['bin/gpu-metal-arm64/libbbgpu.dylib']),library_sha256=closure[str(runtime['bin/gpu-metal-arm64/libbbgpu.dylib'])]['sha256']),stage,code_owners=(0,os.getuid()))
 if gpu.get('target')!=str(runtime[gpu_role])or gpu.get('target_sha256')!=closure[str(runtime[gpu_role])]['sha256']or gpu.get('library')!=str(runtime['bin/gpu-metal-arm64/libbbgpu.dylib'])or gpu.get('library_sha256')!=closure[str(runtime['bin/gpu-metal-arm64/libbbgpu.dylib'])]['sha256']or gpu.get('architecture')!='arm64'or gpu.get('strict_signatures')is not True or gpu.get('load_edge_verified')is not True:raise ValueError('reference actual GPU/DSO differs')
 hashes={role:closure[str(path)]['sha256']for role,path in runtime.items()}
 return dict(app=app,stage=stage,runtime=runtime,runtime_sha256=hashes,seal=seal,
  identity=dict(profile='original-0.2.51-Rosetta-isolated-CLI',app=str(app),stage=str(stage),
   cpu_architecture='x86_64',GPU_architecture='arm64',cpu=cpus,gpu=gpu,native_closure=closure,
   original_scripts=scripts,whole_app_pre_seal=seal,reference_GUI_started=False,formal_data_read=False))


def execution_request(formal_stage,reference_stage,*,functional_f3=False):
 if formal_stage is not None and reference_stage is not None:raise ValueError('formal-stage and reference-stage are mutually exclusive')
 if reference_stage is not None and functional_f3:raise ValueError('reference-stage supports performance sampling only; F3 is not part of this comparison')
 return reference_stage if reference_stage is not None else formal_stage

def execution_conditions(source,formal):
 if formal is None:return source
 return dict(source,stage=formal['stage'],app=formal['app'],runtime=formal['runtime'],runtime_sha256=formal['runtime_sha256'],execution_code_admission=formal)

def execution_environment(source,case,formal=None,*,functional_f3=False):
 """The already cloned original conditions, with code path changes only."""
 stage=formal['stage']if formal is not None else source['stage']
 env=dict(PATH='/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin',HOME=str(P/'state'),TMPDIR=str(case['data_root'])+'/',PYTHON=str(stage/'python/bin/python3'),PYTHONHOME=str(stage/'python'),PYTHONNOUSERSITE='1',PYTHONUNBUFFERED='1',**case['environment'])
 if formal is not None:env['BB_GPU_PROCESS']=str(formal['runtime']['bin/Bloodborne Metal.app/Contents/MacOS/bb-gpu'])
 if env.get('BB_FRAME_STATS','0')=='1'or env.get('BB_SAVE_LOG','0')=='1':raise ValueError('unprofiled benchmark requires original heavy statistics off')
 if env.get('BB_AUDIO')!='none':raise ValueError('night QA requires original GUI audio=none')
 env.update(BB_PAD_FILE=str(case['pad']),BB_PAD_FILE_ONLY='1',BB_QUICKSAVE='0')
 if functional_f3:
  if source['environment'].get('BB_QUICKSAVE')!='1':raise ValueError('original GUI quicksave must already be enabled')
  env.update(BB_QUICKSAVE='1',BB_FRAME_STATS='1')
 return env

def benchmark_changes(source,case,env,*,formal=None,functional_f3=False):
 changes={k:dict(before=source['environment'].get(k),after=env.get(k))for k in sorted(set(source['environment'])|{k for k in env if k.startswith(('BB_','MTL_'))})if source['environment'].get(k)!=env.get(k)}
 allowed=set(case['allowed_changes'])|{'BB_QUICKSAVE','BB_PAD_FILE_ONLY'}|({'BB_FRAME_STATS'}if functional_f3 else set())
 if formal is not None:allowed.add('BB_GPU_PROCESS')
 if set(changes)-allowed:raise ValueError('unapproved game setting override')
 return changes,sorted(allowed)

def selected_cache_mode(requested,formal_stage):
 if formal_stage is not None and requested not in(None,'empty'):raise ValueError('staged code comparison requires empty cache mode; QA shader cache import is forbidden')
 return 'empty'if formal_stage is not None else requested or'seeded'

def sample(c,case,env,command,report,root,*,startup_seconds,scene_seconds,observer,policy,protection_start,functional_f3=False,track_tool_memory=False):
 from qa_launch_clone import inventory_savedata
 from qa_f3_contract import F3Check,NewLogLines
 f3=F3Check()if functional_f3 else None
 progress=None if functional_f3 else FrameProgress()
 fresh_logs=NewLogLines()if f3 is not None else None
 before=observer.system()
 if before['locked']or before['pressure']==4:raise RuntimeError('locked or severe pressure before run')
 write(root/'result.json',report);print('Run:',root,flush=True)
 started=time.monotonic();tracked={};samples=collections.deque(maxlen=1024);peak_pressure=before['pressure'];ready=None;last_press=-100;pressed=False;reason=None;next_frames=0;last_frame_rows=[];proc=None
 pad=case['pad'];frames=case['data_root']/'out/frames.csv'
 def input_(value):
  temporary=pad.with_name(pad.name+'.next');assert not temporary.exists();temporary.write_text(value);os.replace(temporary,pad)
 def descendants():
  pending=[proc.pid];seen=set();rows={}
  while pending:
   parent=pending.pop()
   if parent in seen:continue
   seen.add(parent);assert len(seen)<4096
   for pid,ppid,path in observer.processes(parent):
    rows[pid]=(pid,ppid,path);tracked[pid]=path
    if pid!=parent and pid not in seen:pending.append(pid)
  return list(rows.values())
 def stop():
  if proc is None:return
  if proc.poll()is None:proc.terminate()
  try:proc.wait(timeout=3)
  except subprocess.TimeoutExpired:pass
  for sig in(signal.SIGTERM,signal.SIGKILL):
   for pid,path in reversed(list(tracked.items())):
    if observer.path(pid)==path:
     try:os.kill(pid,sig)
     except ProcessLookupError:pass
   if sig==signal.SIGTERM:time.sleep(.3)
  if proc.poll()is None:proc.kill();proc.wait()
 try:
  if frames.exists():raise ValueError('old frame log was not archived before new run')
  with(root/'console.log').open('w')as log,(root/'samples.jsonl').open('w')as sample_log:
   proc=subprocess.Popen(command,cwd=c['stage'],env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
   while proc.poll()is None:
    now=time.monotonic();elapsed=now-started;rows=descendants();s=observer.system()
    measured=usage([f'{i} {j} 0 {path}'for i,j,path in rows],**({'include_tools':True}if track_tool_memory else{}))
    s.update(seconds=elapsed,processes=rows,usage=[row for row in measured if row['role']in('bb-probe-metal','bb-gpu','bb-game-arm64')])
    if track_tool_memory:s['tool_usage']=measured
    peak_pressure=max(peak_pressure,s['pressure'])
    reason=policy.observe(now-protection_start,s)
    if pressed and now-last_press>=.18:input_('');pressed=False
    if now>=next_frames:
     next_frames=now+FRAME_LOG_POLL_SECONDS
     if frames.exists():
      try:last_frame_rows=read_tail(frames)
      except ValueError:last_frame_rows=[]
     else:last_frame_rows=[]
     if progress is not None:
      progress.observe(frames,elapsed,last_frame_rows)
      if progress.latest is not None:s['frame_progress']=dict(t_s=progress.latest,last_advance_seconds=progress.last_advance)
     in_scene=scene_seen(last_frame_rows)
     if in_scene and ready is None:
      ready=elapsed
      if progress is not None:progress.start_scene(ready)
      print('Scene camera observed:',round(ready,2),flush=True)
     if f3 is not None:
      log_text,drained=fresh_logs.read(root/'console.log')
      token=None
      # Consume all pre-save logs before requesting a new checkpoint. An old
      # startup confirmation must never authorize restoring that old checkpoint.
      if drained or f3.phase not in('entering','settling'):
       token=f3.step(elapsed,scene=in_scene,cpus={pid for pid,ppid,path in rows if path in cpu_paths(c)},gpus={pid for pid,ppid,path in rows if path==str(c['runtime']['bin/Bloodborne Metal.app/Contents/MacOS/bb-gpu'])},log=log_text)
      if token:
       input_(token+'\n');pressed=True;last_press=now
       with(root/'actions.jsonl').open('a')as f:f.write(json.dumps(dict(action=token,seconds=elapsed,phase=f3.phase,utc_ns=time.time_ns()))+'\n')
     if ready is None and(f3 is None or f3.phase=='entering')and last_frame_rows and elapsed>=12 and now-last_press>=4:
      input_('cross\n');pressed=True;last_press=now
      with(root/'actions.jsonl').open('a')as f:f.write(json.dumps(dict(action='cross',seconds=elapsed,reason='Continue before scene camera'))+'\n')
    reason=reason or(('functional F3 completed'if f3.phase=='completed'else 'functional F3 time budget'if elapsed>=startup_seconds else None)if f3 is not None else(deadline(elapsed,ready,startup_seconds,scene_seconds)if ready is None else progress.completion_reason(elapsed,scene_seconds)))
    samples.append(s);sample_log.write(json.dumps(s,separators=(',',':'))+'\n');sample_log.flush()
    if reason:break
    time.sleep(.25)
   if reason is None:reason='unexpected process exit'
 except BaseException as error:
  report['error']=str(error)[:512];reason=reason or 'driver exception';raise
 finally:
  measurement_end=time.monotonic()-started
  input_('');stop();after=observer.system()
  source_same=inventory_savedata(c['user_root']/'savedata')==case['source_save_manifest']
  report.update(status=('functional-checked'if f3 is not None else'sampled')if reason in('scene sample completed','functional F3 completed')and source_same else'incomplete',stop=reason,process_exit=proc.returncode if proc else None,seconds=time.monotonic()-started,scene_ready=ready,before=before,after=after,peak_pressure=peak_pressure,swap_growth_mib=after['swap_mib']-before['swap_mib'],source_save_unchanged=source_same,memory_policy=MemoryPolicy.MANIFEST)
  if f3 is not None:report['functional_f3']={**vars(f3),'scripted_api_not_physical_key_test':True}
  if report['status']=='sampled':
   try:
    observed=progress.summary(measurement_end)
    report['scene_sample']=scene_summary(frames,samples,ready,scene_seconds,frame_progress=observed)
   except ValueError as error:report.update(status='incomplete',error=str(error))
  if track_tool_memory:
   try:report['tool_memory']=tool_memory_summary(root/'samples.jsonl',ready)
   except(ValueError,KeyError,TypeError)as error:report.update(status='incomplete',tool_memory_error=str(error))
  report['qualification']=('Original normal Play and original offline F3 supervisor; isolated scripted quicksave/quickload API, not a physical keyboard test. No FPS/performance or full visual/network conclusion.'if f3 is not None else'Original Swift frozen conditions; Continue only. Scene camera is not full gameplay/visual/network/F3 acceptance. Telemetry has overhead; combined footprints may double-count shared memory. No matched old/new performance or sustained-memory conclusion.')
  if c.get('execution_code_admission')is not None:
   sealed=finish_app_seal(report,c['execution_code_admission']);report.clear();report.update(sealed)
  if frames.exists():os.replace(frames,root/'frames.csv')
  write(root/'result.json',report);print(json.dumps({k:v for k,v in report.items()if k not in('native_identity','qualification')},indent=2),flush=True)
 return report

def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--receipt',type=Path,required=True);ap.add_argument('--stage',type=Path,required=True);ap.add_argument('--startup-seconds',type=int,default=1800);ap.add_argument('--scene-seconds',type=int,default=90);ap.add_argument('--cache-mode',choices=('seeded','empty'));code=ap.add_mutually_exclusive_group();code.add_argument('--formal-stage',type=Path);code.add_argument('--reference-stage',type=Path);ap.add_argument('--runs',type=int,choices=(1,2,3),default=1);ap.add_argument('--functional-f3',action='store_true');a=ap.parse_args()
 assert 60<=a.startup_seconds<=1800 and 45<=a.scene_seconds<=180
 assert not a.functional_f3 or a.runs==1,'functional F3 is one supervised session'
 from qa_launch_conditions import load
 from qa_launch_clone import create
 c=load(a.receipt,stage=a.stage,project=P);require_no_games()
 selected_code=execution_request(a.formal_stage,a.reference_stage,functional_f3=a.functional_f3)
 a.cache_mode=selected_cache_mode(a.cache_mode,selected_code)
 status=subprocess.run(['/Users/k.d./.local/bin/cua-driver','status'],capture_output=True,text=True,timeout=10)
 assert 'daemon is not running'in(status.stdout+status.stderr).lower(),'Cua must be stopped'
 native,cache_admission=admit(c)
 formal=admit_formal(a.formal_stage,c)if a.formal_stage is not None else None
 reference=admit_reference(a.reference_stage,c)if a.reference_stage is not None else None
 code_admission=formal or reference
 execution=execution_conditions(c,code_admission)
 observer=NativeObserver();before=observer.system();assert not before['locked']and before['pressure']!=4
 tag=str(time.time_ns());root=P/'evidence'/('original-launch-benchmark-'+tag);root.mkdir(mode=0o700)
 case=create(c,cache_admission,project=P,tag=tag,cache_mode=a.cache_mode)
 env=execution_environment(c,case,code_admission,functional_f3=a.functional_f3)
 actual_changes,allowed_changes=benchmark_changes(c,case,env,formal=code_admission,functional_f3=a.functional_f3)
 command=[c['executable'],str(execution['stage']/c['arguments'][0])]+([]if a.functional_f3 else['--benchmark'])
 report=dict(schema=1,status='running',receipt_sha256=sha(a.receipt),launch_id=c['launch_id'],native_identity=native,original_shell_entry_preserved=True,original_benchmark_flag_added=True,benchmark_control_overrides=dict(BB_QUICKSAVE='0',BB_PAD_FILE_ONLY='1'),cache_mode=a.cache_mode,silent=True,screen_capture=False,Cua=False,profiler=False,scripted_actions=['Continue until real scene camera'],formal_data_modified=False,source_save_unchanged=False,settings_sha256=case['config_sha256'],startup_budget_seconds=a.startup_seconds,scene_seconds=a.scene_seconds,quality_or_effects_changed=False)
 if formal is not None:report.update(formal_native_identity=formal['identity'],execution_profile='staged-formal-bundle-isolated-CLI',source_QA_only_for_clone=True,cache_namespace_policy='empty-base-to-new-namespace-by-original-bootstrap')
 if reference is not None:report.update(reference_native_identity=reference['identity'],execution_profile='original-0.2.51-Rosetta-isolated-CLI',source_QA_only_for_clone=True,cache_namespace_policy='empty-isolated-root-original-reference-policy')
 if a.functional_f3:report.update(original_benchmark_flag_added=False,benchmark_control_overrides={'BB_FRAME_STATS':'1','BB_ARM_QA_PAD_FILE':'isolated existing native QA API'},performance_sample=False,functional_f3_requested=True,incremental_log_events=True,scripted_actions=['Continue before first scene','quicksave API','quickload API','automatic continue observed; no scripted Continue after F3'])
 write(root/'conditions.json',dict(command=command,cwd=str(execution['stage']),environment=env,source_receipt=str(a.receipt),data=str(case['data_root']),user=str(case['user_root']),cache=str(case['cache_root']),pad=str(case['pad']),allowed_changes=allowed_changes,actual_changes=actual_changes,cache_clone=case['cache_clone'],cpu_object_seed=case.get('cpu_object_seed')))
 write(root/'result.json',report);print('Evidence:',root,flush=True)
 protection_start=time.monotonic();policy=MemoryPolicy(before)
 reports=[]
 previous_handlers={sig:signal.signal(sig,stop_signal)for sig in(signal.SIGTERM,signal.SIGINT)}
 try:
  for index in range(a.runs):
   require_no_games()
   run=root/('run-'+str(index+1));run.mkdir(mode=0o700)
   row=dict(report,run=index+1,phase='initial'if index==0 else'warm',
       same_private_cache_and_user=index>0)
   result=sample(execution,case,env,command,row,run,startup_seconds=a.startup_seconds,
       scene_seconds=a.scene_seconds,observer=observer,policy=policy,protection_start=protection_start,functional_f3=a.functional_f3)
   reports.append(result)
   write(root/'result.json',dict(schema=2,status=('functional-checked'if a.functional_f3 else'sampled')if all(r['status']in('sampled','functional-checked')for r in reports)and len(reports)==a.runs else'incomplete',runs=reports,formal_data_modified=False,memory_policy_continuous=True))
   if result['status']not in('sampled','functional-checked'):raise SystemExit(1)
 finally:
  observer.close()
  for sig,handler in previous_handlers.items():signal.signal(sig,handler)
if __name__=='__main__':main()
