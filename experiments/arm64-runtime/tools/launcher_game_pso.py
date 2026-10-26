"""Original final-exec integration; selected QA cache, no second frontend."""
from pathlib import Path
import ast,hashlib,json,os,re,struct,time
from game_pso_owned_audit import checked_bytes
from game_pso_component import load
from game_pso_candidates import build,digest
from launcher_local_aot import preparation_phase

def title_id(arguments,stage,project,runtime_profile=None):
 """Use the original admitted SFO reader on the ACTUAL original --app0 path."""
 i=arguments.index('--app0');app=Path(arguments[i+1]).resolve(strict=True)
 if runtime_profile is not None:runtime_profile.source(app)
 elif not app.is_relative_to(project.parent):raise ValueError('app0 outside isolated Shared root')
 sfo=(app/'sce_sys/param.sfo').resolve(strict=True)
 if runtime_profile is None and not sfo.is_relative_to(project.parent):raise ValueError('SFO outside isolated Shared root')
 raw,_=checked_bytes(sfo,limit=64*1024)
 code_args={'code_owners':(0,os.getuid())}if runtime_profile is not None else{}
 parity=json.loads(checked_bytes(stage/'SOURCE-PARITY.json',**code_args)[0]);relative='scripts/prepare.py'
 source,_=checked_bytes(stage/relative,limit=4*1024*1024,expected=parity['effective_sources'][relative],**code_args)
 tree=ast.parse(source);wanted={'span','unpack','sfo'}
 funcs=[n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name in wanted]
 if {n.name for n in funcs}!=wanted:raise ValueError('original SFO reader unavailable')
 # Exactly the admitted original function bodies, not a second SFO parser.
 ns={'struct':struct};exec(compile(ast.Module(body=funcs,type_ignores=[]),str(stage/relative),'exec'),ns)
 title=ns['sfo'](raw).get('TITLE_ID')
 if not isinstance(title,str)or not re.fullmatch(r'CUSA[0-9]{5}',title):raise ValueError('invalid actual TITLE_ID')
 return title

def prepare(*,project,execution_root,arguments,environment,cohort,options,observe,
            runner=None,component_loader=load,candidate_builder=build,toolchain_probe=None,prepared_inputs=None,
            catalogue_prepare=None,module_prepare=None,capture_configure=None,source_view_prepare=None,restart_binding=None,runtime_profile=None):
 from launcher_runtime_profile import kw,work
 project=Path(project).resolve(strict=True)
 if execution_root is None:return dict(status='unavailable',reason='packaged PSO component unavailable')
 stage=Path(execution_root).resolve(strict=True);component=component_loader(stage,**kw(runtime_profile))
 if component is None:return dict(status='unavailable',reason='packaged PSO component unavailable')
 from local_aot_policy import available_toolchain
 if not (toolchain_probe or available_toolchain)():return dict(status='source-fallback',reason='toolchain-unavailable')
 # Verify selected original renderer loads the SAME final packaged DSO; a mere
 # independently valid worker/library pair is insufficient for this launch.
 from warm_archive_manifest import resolve_gpu_identity
 selected=resolve_gpu_identity(Path(environment['BB_GPU_PROCESS']),stage,**({'code_owners':(0,os.getuid())}if runtime_profile is not None else{}))
 if selected['library_sha256']!=component['library_sha256']:raise ValueError('selected renderer/preparation DSO mismatch')
 gpu=Path(environment['BB_GPU_USER_DIR']).resolve(strict=True)
 if runtime_profile is not None:runtime_profile.work(project);runtime_profile.gpu(gpu)
 elif not gpu.is_relative_to(project/'state')or gpu==project/'state':raise ValueError('isolated selected GPU root required')
 from qa_memory_policy import MemoryPolicy
 start=observe();policy=MemoryPolicy(start);started=time.monotonic()
 def check():
  now=observe();reason=policy.observe(time.monotonic()-started,now)
  if reason:raise RuntimeError('game PSO preparation safety stop: '+reason)
 check();run=work(project,runtime_profile)/('game-pso-launch-'+str(time.time_ns()));run.mkdir(mode=0o700)
 if restart_binding is not None:
  if capture_configure is None:
   from prepare_fetch_capture_receipt import configure_capture as capture_configure
  from quick_restart_preparation import resume
  reused=resume(bound=restart_binding,environment=environment,gpu=gpu,component=component,
      backend=selected['library'],cohort=cohort,check=check,configure_capture=capture_configure,**kw(runtime_profile))
  if reused is not None:
   check();return reused
 # Default source preparation precedes cache enumeration, including a fresh
 # install with no shader-cache records. Keep original app0/arguments unchanged.
 if prepared_inputs is None:
  if source_view_prepare is None:
   from launcher_source_view import prepare as source_view_prepare
  from launcher_source_view import SnapshotUnavailable
  try:
   with preparation_phase('source-view'):
    source_view=source_view_prepare(project=project,gpu_root=gpu,stage=stage,
        arguments=arguments,environment=environment,check=check,**kw(runtime_profile))
  except SnapshotUnavailable as error:
   return dict(status='source-fallback',reason='APFS source snapshot unavailable',
               source_view=dict(status='unavailable',errno=error.errno),original_arguments_preserved=True)
  if catalogue_prepare is None:
   from launcher_game_catalog import prepare as catalogue_prepare
  with preparation_phase('static-catalog'):
   prepared_inputs=catalogue_prepare(project=project,gpu_root=gpu,game_root=source_view['game_root'],
       source_root=component['source_root'],cohort=cohort,check=check,**kw(runtime_profile))
  prepared_inputs['report']['source_view']=source_view['report']
 # Use the admitted original SFO reader on the byte-equal canonical snapshot.
 # The actual final exec still receives the untouched original mod view.
 title_arguments=list(arguments)
 if prepared_inputs.get('game_root')is not None:
  title_arguments[title_arguments.index('--app0')+1]=str(prepared_inputs['game_root'])
 title=title_id(title_arguments,stage,project,**kw(runtime_profile))
 catalogue_report=prepared_inputs.get('report',dict(status='external-internal-test-context'))
 capture_environment={};capture_report=dict(status='unavailable',reason='packaged capture capability unavailable')
 if component.get('automatic_capture_schema')==1:
  if capture_configure is None:
   from prepare_fetch_capture_receipt import configure_capture as capture_configure
  capture_root=gpu/'automatic-game-inputs'/('runtime-'+cohort)
  capture_root.mkdir(mode=0o700,parents=True,exist_ok=True)
  with preparation_phase('capture-receipt'):
   capture_report=capture_configure(root=capture_root,game_root=prepared_inputs['game_root'],
       catalog=prepared_inputs['catalog'],index=prepared_inputs['index'],
       backend=Path(selected['library']),runtime_source_root=component['source_root'],**({'code_owners':(0,os.getuid())}if runtime_profile is not None else{}))
  capture_environment=capture_report['capture_environment']
  prepared_inputs['capture_root']=capture_root
 manifest=run/'candidates.jsonl'
 with preparation_phase('candidate-scan'):
  summary=candidate_builder(gpu_root=gpu,title_id=title,layout=component['native_layout'],output=manifest,check=check)
 summary['automatic_static_inputs']=catalogue_report
 summary['automatic_runtime_capture']={k:v for k,v in capture_report.items()if k!='capture_environment'}
 if not summary['complete']:return dict(status='deferred',reason='incomplete automatic candidate scan',scan=summary,
                                       capture_environment=capture_environment)
 if module_prepare is None:
  from launcher_game_modules import prepare as module_prepare
 with preparation_phase('module-inputs'):
  module_inputs=module_prepare(prepared_inputs,project=project,
      caches=[Path(r['cache'])for r in summary.get('cache_inventory_audits',[])],
      component=component,cohort=cohort,check=check,environment=environment,
      selected_renderer=Path(environment['BB_GPU_PROCESS']),backend_root=stage,
      candidate_manifest={'path':str(manifest),'sha256':summary['manifest_sha256']},**kw(runtime_profile))
 summary['automatic_modules']={k:v for k,v in module_inputs.items()if k not in('geometry_resolver','cache_root')}
 # A stable owned recipe is admitted only after this same-build controller's
 # native capability, load edge and complete input/result checks have succeeded.
 if module_inputs.get('geometry_resolver')is not None:
  prepared_inputs={**prepared_inputs,'geometry_resolver':module_inputs['geometry_resolver'],
                   'cache_root':module_inputs['cache_root']}
 # The source/index controller owns module preparation and producer admission.
 # Keep its resolved GS records in the original queue and native worker flow.
 resolver=prepared_inputs.get('geometry_resolver')if prepared_inputs else None
 if resolver is not None:
  from game_pso_geometry_adapter import augment_manifest
  combined=run/'candidates-owned.jsonl'
  with preparation_phase('geometry-inputs'):
   geometry=augment_manifest(manifest,combined,layout=component['native_layout'],resolver=resolver,cache_root=prepared_inputs['cache_root'],check=check)
  manifest=combined;summary['geometry_source_adapter']=geometry;summary['eligible']+=geometry['admitted'];summary['excluded']-=geometry['admitted']
  with manifest.open('rb')as stream:summary['manifest_sha256']=hashlib.file_digest(stream,'sha256').hexdigest()
  family='geometry-owned-source-existing-native-chain';summary.setdefault('eligible_by_family',{})[family]=geometry['admitted']
 else:summary['geometry_source_adapter']=dict(status='not-provided',admitted=0)
 if summary['eligible']==0:
  result=dict(status='empty',scan=summary,capture_environment=capture_environment)
  # An exhaustively scanned empty anticipatory queue has no PSO to prewarm.
  # This hint carries no native-ready claim; the original publisher still
  # checks Local/final guards and the strict context before atomic publication.
  if restart_binding is not None and summary['complete']is True and capture_report.get('status')=='configured'and capture_environment=={
      'BB_FETCH_CAPTURE_ROOT':str(capture_root),'BB_FETCH_CAPTURE_GAME_ROOT':str(prepared_inputs['game_root'])}:
   from quick_restart_preparation import pending
   try:result['_restart_context']=pending(prepared_inputs)
   except(ValueError,OSError,KeyError,TypeError):pass
  return result
 # This exact directory is ShaderCompiler::DefaultCacheDir / PsoCache cache_dir,
 # NOT a developer-created test artifact directory.
 artifact=gpu/'cache'/title/'metal';artifact.mkdir(parents=True,exist_ok=True)
 if artifact!=artifact.resolve(strict=True):raise ValueError('linked original renderer cache_dir')
 job=work(project,runtime_profile)/('game-pso-worker-'+str(time.time_ns()));job.mkdir(mode=0o700)
 if runner is None:
  from prepare_game_psos import prepare as runner
 config=dict(project=project,worker=component['worker'],worker_sha256=component['worker_sha256'],library_sha256=component['library_sha256'],
  manifest=manifest,layout=component['layout'],layout_sha256=component['layout_sha256'],source_root=component['source_root'],
  cohort=cohort,gpu_root=gpu,run_root=job,artifact_cache_dir=artifact,phase='all',allow_compile=True,
  page_size=16,max_recipes=None,seconds=600,progress_root=gpu/'game-pso-progress',environment={k:v for k,v in environment.items()if k=='BB_GS_DEBUG' or k in ('MTL_SHADER_VALIDATION','MTL_SHADER_VALIDATION_ENABLE_PIPELINES','MTL_SHADER_VALIDATION_DISABLE_PIPELINES','MTL_DEBUG_LAYER')or k.startswith(('BB_MTL_','BB_LAYER_','BB_PC_','BB_GUEST_'))},options_identity=digest(options))
 config.update(kw(runtime_profile))
 with preparation_phase('complete-pso'):
  report=runner(config)
 check()
 if report.get('status')in('stopped','failed-protection'):raise RuntimeError('game PSO preparation stopped by safety guard')
 result=dict(status=report.get('status','failed'),scan=summary,preparation=report,artifact_cache_dir=str(artifact),title_id=title,
             runtime_cache_import_allowed=False,original_arguments_preserved=True,capture_environment=capture_environment)
 if restart_binding is not None and report.get('status')=='completed' and report.get('full_manifest_preparation_verified')is True and capture_environment:
  from quick_restart_preparation import pending
  try:result['_restart_context']=pending(prepared_inputs)
  except(ValueError,OSError,KeyError,TypeError):pass
 return result
