"""Default local preparation; exact source fallback preserves normal startup."""
from pathlib import Path
import os,subprocess
P=Path(__file__).resolve().parents[1]

def available_toolchain():
 """No download/install/UI; query both fixed developer tools with bounded output."""
 for name in ('metal','metallib'):
  try:
   result=subprocess.run(['/usr/bin/xcrun','--find',name],stdin=subprocess.DEVNULL,
     stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=10,
     env={'PATH':'/usr/bin:/bin:/usr/sbin:/sbin'})
  except (OSError,subprocess.TimeoutExpired):return False
  if result.returncode or len(result.stdout)>4096:return False
  try:path=Path(result.stdout.decode('utf-8').strip())
  except (UnicodeError,ValueError):return False
  if not path.is_absolute()or not path.is_file():return False
 return True

def prepare(*,mode='auto',gpu_root,cohort,engine,graphics,builder=None,toolchain_probe=None,runtime_profile=None):
 if mode=='off':return {},dict(status='off',requested='off')
 if mode not in ('auto','record','load')or engine!='arm64'or not graphics:raise ValueError('local AOT requires ARM64 graphics mode')
 from local_metal_builder import HEX
 if not HEX.fullmatch(cohort):raise ValueError('local AOT requires verified compatibility cohort')
 gpu=Path(gpu_root).absolute()
 if runtime_profile is not None:runtime_profile.gpu(gpu)
 elif gpu!=gpu.resolve(strict=True)or not gpu.is_relative_to(P/'state')or gpu==P/'state':raise ValueError('local AOT requires canonical isolated GPU state')
 root=gpu/'local-metal';root.mkdir(mode=0o700,exist_ok=True)
 if root!=root.resolve(strict=True)or root.is_symlink():raise ValueError('linked local AOT root')
 env=dict(BB_MTL_LOCAL_AOT_MODE='record',BB_MTL_LOCAL_AOT_ROOT=str(root),BB_MTL_LOCAL_AOT_COHORT=cohort)
 report=dict(status='record',requested=mode,effective='record',root=str(root),cohort=cohort,builder_invoked=False)
 requests=root/cohort/'requests'
 if mode=='record'or not requests.is_dir():
  report['reason']='collecting-first-use-inputs';return env,report
 if mode=='auto'and not (toolchain_probe or available_toolchain)():
  report.update(reason='toolchain-unavailable',message='Metal Toolchain unavailable; normal runtime compilation retained, local preparation resumes after installation.')
  return env,report
 if builder is None:
  from local_metal_builder import build
  builder=build
 old=os.environ.get('BB_GPU_USER_DIR');os.environ['BB_GPU_USER_DIR']=str(gpu)
 try:summary=builder(root,cohort,enabled=True,**({'runtime_profile':runtime_profile}if runtime_profile is not None else{}))
 finally:
  if old is None:os.environ.pop('BB_GPU_USER_DIR',None)
  else:os.environ['BB_GPU_USER_DIR']=old
 report.update(status=summary['status'],preparation_status=summary['status'],builder_invoked=True,
  build_id=summary.get('build_id'),built=summary.get('built',0),skipped=summary.get('skipped',0),
  requests=summary.get('requests',0),summary=str(root/cohort/'artifacts/builder-summary.json'))
 if type(summary.get('persistent_audit_reused'))is bool:report['persistent_audit_reused']=summary['persistent_audit_reused']
 # Stop/lock/memory safeguards are not source-fallback conditions.
 if summary['status']=='stopped':
  report['error']=summary.get('error','local preparation stopped');return {},report
 partial=summary['status']=='partial'and mode=='auto'
 if summary['status']!='passed'and not partial:
  report['error']=summary.get('error','local preparation failed')
  if mode=='auto':
   report.update(status='record',effective='record',reason='preparation-failed-source-fallback')
   return env,report
  return {},report
 if summary.get('requests',0)>0:
  if not HEX.fullmatch(summary.get('build_id','')):raise ValueError('invalid successful local build identity')
  env.update(BB_MTL_LOCAL_AOT_MODE='load',BB_MTL_LOCAL_AOT_BUILD_ID=summary['build_id'])
  if HEX.fullmatch(summary.get('preparation_environment_identity','')):
   env['BB_MTL_LOCAL_AOT_ENVIRONMENT_ID']=summary['preparation_environment_identity']
  report.update(status='prepared',effective='load',reason='prepared-partial'if partial else'prepared')
 else:report.update(status='record',effective='record',reason='no-complete-requests')
 return env,report

def gameplay_guard(before_preparation,after_preparation):
 from qa_memory_policy import MemoryPolicy
 policy=MemoryPolicy(after_preparation)
 policy.baseline=min(before_preparation['swap_mib'],after_preparation['swap_mib'])
 return policy
