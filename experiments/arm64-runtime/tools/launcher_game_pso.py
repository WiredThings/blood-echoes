"""Original final-exec integration; selected QA cache, no second frontend."""
from pathlib import Path
import ast,hashlib,json,os,re,struct,time
from game_pso_owned_audit import checked_bytes
from game_pso_component import load
from game_pso_candidates import build,digest

def title_id(arguments,stage,project):
 """Use the original admitted SFO reader on the ACTUAL original --app0 path."""
 i=arguments.index('--app0');app=Path(arguments[i+1]).resolve(strict=True)
 if not app.is_relative_to(project.parent):raise ValueError('app0 outside isolated Shared root')
 sfo=(app/'sce_sys/param.sfo').resolve(strict=True)
 if not sfo.is_relative_to(project.parent):raise ValueError('SFO outside isolated Shared root')
 raw,_=checked_bytes(sfo,limit=64*1024)
 parity=json.loads(checked_bytes(stage/'SOURCE-PARITY.json')[0]);relative='scripts/prepare.py'
 source,_=checked_bytes(stage/relative,limit=4*1024*1024,expected=parity['effective_sources'][relative])
 tree=ast.parse(source);wanted={'span','unpack','sfo'}
 funcs=[n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name in wanted]
 if {n.name for n in funcs}!=wanted:raise ValueError('original SFO reader unavailable')
 # Exactly the admitted original function bodies, not a second SFO parser.
 ns={'struct':struct};exec(compile(ast.Module(body=funcs,type_ignores=[]),str(stage/relative),'exec'),ns)
 title=ns['sfo'](raw).get('TITLE_ID')
 if not isinstance(title,str)or not re.fullmatch(r'CUSA[0-9]{5}',title):raise ValueError('invalid actual TITLE_ID')
 return title

def prepare(*,project,execution_root,arguments,environment,cohort,options,observe,
            runner=None,component_loader=load,candidate_builder=build,toolchain_probe=None):
 project=Path(project).resolve(strict=True)
 if execution_root is None:return dict(status='unavailable',reason='packaged PSO component unavailable')
 stage=Path(execution_root).resolve(strict=True);component=component_loader(stage)
 if component is None:return dict(status='unavailable',reason='packaged PSO component unavailable')
 from local_aot_policy import available_toolchain
 if not (toolchain_probe or available_toolchain)():return dict(status='source-fallback',reason='toolchain-unavailable')
 # Verify selected original renderer loads the SAME final packaged DSO; a mere
 # independently valid worker/library pair is insufficient for this launch.
 from warm_archive_manifest import resolve_gpu_identity
 selected=resolve_gpu_identity(Path(environment['BB_GPU_PROCESS']),stage)
 if selected['library_sha256']!=component['library_sha256']:raise ValueError('selected renderer/preparation DSO mismatch')
 title=title_id(arguments,stage,project);gpu=Path(environment['BB_GPU_USER_DIR']).resolve(strict=True)
 if not gpu.is_relative_to(project/'state')or gpu==project/'state':raise ValueError('isolated selected GPU root required')
 from qa_memory_policy import MemoryPolicy
 start=observe();policy=MemoryPolicy(start);started=time.monotonic()
 def check():
  now=observe();reason=policy.observe(time.monotonic()-started,now)
  if reason:raise RuntimeError('game PSO preparation safety stop: '+reason)
 check();run=project/'state'/('game-pso-launch-'+str(time.time_ns()));run.mkdir(mode=0o700)
 manifest=run/'candidates.jsonl'
 summary=candidate_builder(gpu_root=gpu,title_id=title,layout=component['native_layout'],output=manifest,check=check)
 if not summary['complete']:return dict(status='deferred',reason='incomplete automatic candidate scan',scan=summary)
 if summary['eligible']==0:return dict(status='empty',scan=summary)
 # This exact directory is ShaderCompiler::DefaultCacheDir / PsoCache cache_dir,
 # NOT a developer-created test artifact directory.
 artifact=gpu/'cache'/title/'metal';artifact.mkdir(parents=True,exist_ok=True)
 if artifact!=artifact.resolve(strict=True):raise ValueError('linked original renderer cache_dir')
 work=project/'state'/('game-pso-worker-'+str(time.time_ns()));work.mkdir(mode=0o700)
 if runner is None:
  from prepare_game_psos import prepare as runner
 config=dict(project=project,worker=component['worker'],worker_sha256=component['worker_sha256'],library_sha256=component['library_sha256'],
  manifest=manifest,layout=component['layout'],layout_sha256=component['layout_sha256'],source_root=component['source_root'],
  cohort=cohort,gpu_root=gpu,run_root=work,artifact_cache_dir=artifact,phase='all',allow_compile=True,
  page_size=16,max_recipes=None,seconds=600,progress_root=gpu/'game-pso-progress',environment={k:v for k,v in environment.items()if k=='BB_GS_DEBUG' or k in ('MTL_SHADER_VALIDATION','MTL_SHADER_VALIDATION_ENABLE_PIPELINES','MTL_SHADER_VALIDATION_DISABLE_PIPELINES','MTL_DEBUG_LAYER')or k.startswith(('BB_MTL_','BB_LAYER_','BB_PC_','BB_GUEST_'))},options_identity=digest(options))
 report=runner(config);check()
 if report.get('status')in('stopped','failed-protection'):raise RuntimeError('game PSO preparation stopped by safety guard')
 return dict(status=report.get('status','failed'),scan=summary,preparation=report,artifact_cache_dir=str(artifact),title_id=title,
             runtime_cache_import_allowed=False,original_arguments_preserved=True)
