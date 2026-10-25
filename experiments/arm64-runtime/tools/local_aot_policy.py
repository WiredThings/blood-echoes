"""Private startup preparation policy; offline libraries are generated on this host."""
from pathlib import Path
import os
P=Path(__file__).resolve().parents[1]
def prepare(*,mode,gpu_root,cohort,engine,graphics,builder=None):
 if mode=='off':return {},dict(status='off',requested='off')
 if mode not in('record','load')or engine!='arm64'or not graphics:raise ValueError('local AOT requires explicit ARM64 graphics mode')
 from local_metal_builder import HEX
 if not HEX.fullmatch(cohort):raise ValueError('local AOT requires verified compatibility cohort')
 gpu=Path(gpu_root).absolute()
 if gpu!=gpu.resolve(strict=True)or not gpu.is_relative_to(P/'state')or gpu==P/'state':raise ValueError('local AOT requires canonical isolated GPU state')
 root=gpu/'local-metal';root.mkdir(mode=0o700,exist_ok=True)
 if root!=root.resolve(strict=True)or root.is_symlink():raise ValueError('linked local AOT root')
 env=dict(BB_MTL_LOCAL_AOT_MODE='record',BB_MTL_LOCAL_AOT_ROOT=str(root),BB_MTL_LOCAL_AOT_COHORT=cohort)
 report=dict(status='record',requested=mode,effective='record',root=str(root),cohort=cohort,builder_invoked=False)
 requests=root/cohort/'requests'
 # First use may have no complete effective source yet. Explicitly collect it;
 # this does not pretend that unknown runtime combinations were precompiled.
 if mode=='record'or not requests.is_dir():return env,report
 if builder is None:
  from local_metal_builder import build
  builder=build
 old=os.environ.get('BB_GPU_USER_DIR');os.environ['BB_GPU_USER_DIR']=str(gpu)
 try:summary=builder(root,cohort,enabled=True)
 finally:
  if old is None:os.environ.pop('BB_GPU_USER_DIR',None)
  else:os.environ['BB_GPU_USER_DIR']=old
 report.update(status=summary['status'],builder_invoked=True,build_id=summary.get('build_id'),built=summary.get('built',0),skipped=summary.get('skipped',0),requests=summary.get('requests',0),summary=str(root/cohort/'artifacts/builder-summary.json'))
 if summary['status']!='passed':
  report['error']=summary.get('error','local preparation failed');return {},report
 if summary.get('requests',0)>0:
  if not HEX.fullmatch(summary['build_id']):raise ValueError('invalid successful local build identity')
  env.update(BB_MTL_LOCAL_AOT_MODE='load',BB_MTL_LOCAL_AOT_BUILD_ID=summary['build_id']);report.update(status='prepared',effective='load')
 else:report.update(status='record',effective='record')
 return env,report

def gameplay_guard(before_preparation,after_preparation):
 from qa_memory_policy import MemoryPolicy
 policy=MemoryPolicy(after_preparation)
 # Reset only the short burst history. Preserve the stricter cumulative budget
 # across both preparation and gameplay, even when preparation reduced swap.
 policy.baseline=min(before_preparation['swap_mib'],after_preparation['swap_mib'])
 return policy
