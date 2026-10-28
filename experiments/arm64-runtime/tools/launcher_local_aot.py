"""Prepare local Metal libraries at the original launcher's final exec boundary.

The original scripts still own mods, patches, language, networking and restarts.
This wrapper takes their final executable/arguments, prepares only shader caches,
then execs the same executable with the same arguments and exit-code semantics.
"""
from pathlib import Path
import json, os, signal, stat, sys, time
from contextlib import contextmanager

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
LOCAL_PREFIX = 'BB_MTL_LOCAL_AOT_'
OPTION_PREFIXES = ('BB_MTL_', 'BB_LAYER_', 'BB_PC_', 'BB_GUEST_')
VALIDATION_OPTIONS = ('MTL_SHADER_VALIDATION','MTL_SHADER_VALIDATION_ENABLE_PIPELINES',
                      'MTL_SHADER_VALIDATION_DISABLE_PIPELINES','MTL_DEBUG_LAYER')
_pending_signal = 0
PREPARATION_PHASES = frozenset(('archive-policy', 'local-libraries', 'fixed-helpers',
    'game-preparation', 'source-view', 'static-catalog', 'capture-receipt',
    'candidate-scan', 'module-inputs', 'geometry-inputs', 'complete-pso', 'local-finalize'))


def _phase_message(phase, event, started=None, outcome=None):
    # Only internal enums and a duration are emitted. Logging failures must not
    # replace the original preparation result or exception (including signals).
    try:
        message = dict(phase=phase, event=event)
        if started is not None:
            message['seconds'] = round(max(0.0, time.monotonic()-started), 6)
            message['outcome'] = outcome
        print('Metal preparation phase: '+json.dumps(message, sort_keys=True), flush=True)
    except Exception:
        pass


@contextmanager
def preparation_phase(phase):
    if phase not in PREPARATION_PHASES:
        raise ValueError('unknown internal preparation phase')
    started = time.monotonic()
    _phase_message(phase, 'start')
    outcome = 'raised'
    try:
        yield
        outcome = 'returned'
    finally:
        _phase_message(phase, 'end', started, outcome)


def regular(path, *, parent=None, code_profile=None):
    path = Path(path).absolute()
    if path != path.resolve(strict=True):
        raise ValueError('linked or noncanonical startup input')
    info = path.stat()
    owners=(0,os.getuid())if code_profile is not None else(os.getuid(),)
    if code_profile is not None:code_profile.code(path)
    if not stat.S_ISREG(info.st_mode) or info.st_uid not in owners:
        raise ValueError('startup input must be an owned regular file')
    if parent is not None and not path.is_relative_to(parent):
        raise ValueError('startup input outside isolated development root')
    return path


def launcher_inputs(arguments, environment, project, runtime_profile=None):
    """Read the existing native CLI; never select, create or copy a save."""
    if not arguments or arguments[0].startswith('--'):
        raise ValueError('expected original launcher boot argument')
    options = {}
    for key in ('--content-profile', '--patches', '--app0', '--user'):
        positions = [i for i, arg in enumerate(arguments) if arg == key]
        if len(positions) != 1 or positions[0]+1 >= len(arguments):
            raise ValueError('missing or repeated original launcher option: '+key)
        options[key] = arguments[positions[0]+1]
    shared = runtime_profile.data_root/'out'if runtime_profile is not None else project.parent
    inputs = {'boot': regular(arguments[0], parent=shared),
              'content_profile': regular(options['--content-profile'], parent=shared),
              'patches': regular(options['--patches'], parent=shared),
              'settings': regular(environment['BB_CONFIG'], parent=runtime_profile.data_root if runtime_profile is not None else shared)}
    # --app0 may be the original script's temporary merged mod view. Its contents
    # are already represented by the final boot/content/patch artifacts above.
    # Preserve the path exactly for exec; do not resolve/copy/rename the user path.
    if environment.get('BB_RENDERER') != 'metal' or environment.get('BB_ARM_LAUNCHER_COMPAT') != '1':
        raise ValueError('local startup preparation requires the isolated native Metal selection')
    if not environment.get('BB_GPU_PROCESS') or not environment.get('BB_GPU_USER_DIR'):
        raise ValueError('original native GPU selection is missing')
    return inputs


def prepare_launch(executable, arguments, environment, *, project=PROJECT,
                   execution_root=None, archive_prepare=None, local_prepare=None, observe=None, game_prepare=None, fixed_helper_prepare=None,runtime_profile=None):
    """One startup-only preparation; imports and toolchain work stay out of draws."""
    env = dict(environment)
    # A prior F3 child/cohort must not authorize capture for this new selection.
    # Only the current verified source/DSO preparation can supply these again.
    for key in ('BB_FETCH_CAPTURE_ROOT','BB_FETCH_CAPTURE_GAME_ROOT'):
        env.pop(key, None)
    explicit = env.get('BB_MTL_LOCAL_AOT_MODE')
    if explicit == 'off':
        if runtime_profile is None and execution_root is not None:
            from launcher_runtime_profile import derive
            runtime_profile=derive(execution_root,arguments,env,executable=executable)
        if runtime_profile is not None:
            runtime_profile.cpu(executable);runtime_profile.renderer(env['BB_GPU_PROCESS']);env=runtime_profile.environment(env)
        return env, dict(status='off', requested='off', builder_invoked=False)
    if explicit not in (None, '', 'auto', 'record', 'load'):
        raise ValueError('invalid local Metal preparation mode')
    if runtime_profile is None:
        from launcher_runtime_profile import derive
        runtime_profile=derive(execution_root,arguments,env,executable=executable)
    if runtime_profile is not None:
        runtime_profile.cpu(executable);runtime_profile.renderer(env['BB_GPU_PROCESS']);project=runtime_profile.work_root;env=runtime_profile.environment(env)
    project=Path(project).resolve(strict=True)
    inputs = launcher_inputs(arguments, env, project,runtime_profile)
    # The existing component stage intentionally uses a symlink to the selected
    # CPU executable. Validate its real owned target, retain the original exec path.
    code_root = Path(execution_root).resolve(strict=True) if execution_root is not None else None
    cpu = regular(Path(executable).resolve(strict=True),code_profile=runtime_profile)
    if runtime_profile is not None and str(cpu.relative_to(code_root))not in('bin/bb-probe-metal','bin/Bloodborne Game.app/Contents/MacOS/bb-probe-metal'):
        raise ValueError('formal CPU executable role mismatch')
    if not cpu.is_relative_to(project) and not (code_root and cpu.is_relative_to(code_root)):
        raise ValueError('native executable outside declared private code roots')
    selected_gpu = Path(env['BB_GPU_PROCESS']).resolve(strict=True)
    if runtime_profile is not None and selected_gpu!=code_root/'bin/Bloodborne Metal.app/Contents/MacOS/bb-gpu':
        raise ValueError('formal selected GPU executable role mismatch')
    if selected_gpu.is_relative_to(project/'backend'):
        backend_root = project/'backend'
    elif code_root and selected_gpu.is_relative_to(code_root):
        backend_root = code_root
    else:
        raise ValueError('selected GPU outside declared private code roots')
    gpu = Path(env['BB_GPU_USER_DIR']).absolute()
    if runtime_profile is not None:runtime_profile.gpu(gpu)
    elif gpu != gpu.resolve(strict=True) or gpu == project/'state' or not gpu.is_relative_to(project/'state'):
        raise ValueError('native preparation GPU state must stay isolated')
    # A stale previous child's build_id/cohort must never authorize this launch.
    for key in tuple(env):
        if key.startswith(LOCAL_PREFIX):
            del env[key]
    options = {k:v for k,v in env.items()
               if (k=='BB_GS_DEBUG' or k in VALIDATION_OPTIONS or k.startswith(OPTION_PREFIXES)) and
               k != 'BB_MTL_ARCHIVE_COMPATIBILITY_ID' and
               not any(tag in k for tag in ('AUDIT', 'LOG', 'CAPTURE', 'FILE', 'NONCE', 'RESTART'))}
    if archive_prepare is None:
        from warm_archive_manifest import prepare_startup_archive_policy,resolve_gpu_identity
        if runtime_profile is None:archive_prepare = prepare_startup_archive_policy
        else:
            def archive_prepare(**values):
                return prepare_startup_archive_policy(**values,resolver=lambda t,r:runtime_profile.native_identity(t),manifest_builder=runtime_profile.archive_manifest)
    # Share one bounded, authenticated builder session across this launch's
    # initial/fixed-helper/final library checks. The PSO controller still owns
    # its separate native record/load phases and full final admission.
    from contextlib import ExitStack
    with ExitStack() as cleanup:
        if runtime_profile is not None:
            from warm_archive_manifest import native_validation_scope
            cleanup.enter_context(native_validation_scope(runtime_profile))
        if local_prepare is None:
            import tempfile
            from local_aot_policy import prepare as policy_prepare
            from local_metal_builder import PreparationSession,NativeToolchain,FixedRunner,Safety
            from native_observer import NativeObserver
            from qa_memory_policy import MemoryPolicy
            from qa_build_guard import require_no_games
            from launcher_runtime_profile import work
            scratch=cleanup.enter_context(tempfile.TemporaryDirectory(prefix='library-session-',dir=work(project,runtime_profile)))
            session=PreparationSession(Path(scratch),runtime_profile=runtime_profile)
            cleanup.callback(session.close)
            safety_observer=NativeObserver();cleanup.callback(safety_observer.close)
            guard=Safety(safety_observer,require_no_games,MemoryPolicy)
            def local_prepare(**values):
                def builder(root,cohort,**build_options):
                    return session.build(root,cohort,guard=guard,
                        toolchain_factory=lambda:NativeToolchain(FixedRunner(Path(scratch))),**build_options)
                return policy_prepare(builder=builder,**values)
        if observe is None:
            from native_observer import NativeObserver
            observer=NativeObserver();cleanup.callback(observer.close)
            observe=observer.system
        return _prepared(executable, arguments, env, project, inputs, options,
                         explicit, archive_prepare, local_prepare, observe, backend_root, code_root, game_prepare, fixed_helper_prepare,runtime_profile)


def _prepared(executable, arguments, env, project, inputs, options, mode,
              archive_prepare, local_prepare, observe, backend_root, code_root, game_prepare, fixed_helper_prepare=None,runtime_profile=None):
    from launcher_runtime_profile import kw
    before = observe()
    if before['locked'] or before['pressure'] == 4:
        raise RuntimeError('locked or severe pressure before local Metal preparation')
    with preparation_phase('archive-policy'):
        updates, identity = archive_prepare(enabled=True, engine='arm64',
            selected_executable=Path(env['BB_GPU_PROCESS']), backend_root=backend_root,
            game_artifacts=inputs, options=options,
            profile={'engine':'arm64', 'namespace':'arm64-srt1',
                     'profile_abi':'SPV10-meta7-key5-msl3'})
    env.update(updates)
    cohort = identity['token']
    from quick_restart_preparation import binding as restart_binding
    restart = restart_binding(executable=executable, arguments=arguments, environment=env,
        cohort=cohort, stage=code_root, archive_identity=identity,**kw(runtime_profile))
    # Test the original same-supervisor receipt BEFORE ordinary library/helper
    # batches. A miss merely follows the existing normal preparation below.
    if restart is not None and env.get('BB_QUICKLOAD_PENDING')=='1' and mode in (None,'','auto','load'):
        from quick_restart_preparation import prepared_local
        previous=prepared_local(bound=restart,environment=env,gpu=env['BB_GPU_USER_DIR'],**kw(runtime_profile))
        if previous is not None:
            if game_prepare is None:
                from launcher_game_pso import prepare as game_prepare
            with preparation_phase('game-preparation'):
                reused=game_prepare(project=project,execution_root=code_root,arguments=arguments,
                    environment={**env,**previous},cohort=cohort,options=options,observe=observe,
                    restart_binding=restart,resume_only=True,**kw(runtime_profile))
            if reused is not None and reused.get('status')=='same-supervisor-f3-schedule-reused':
                capture=reused.get('capture_environment',{})
                if set(capture)-{'BB_FETCH_CAPTURE_ROOT','BB_FETCH_CAPTURE_GAME_ROOT'}:
                    raise ValueError('unexpected runtime capture environment')
                after=observe()
                if after['locked']or after['pressure']==4 or after['swap_mib']-before['swap_mib']>2048:
                    raise RuntimeError('protection after F3 preparation reuse')
                env.update(previous);env.update(capture)
                return env,dict(schema=1,status='prepared',requested=mode or 'auto',
                    local=dict(status='prepared',builder_invoked=False,same_supervisor_reused=True),
                    game_pso=reused,fixed_helpers=dict(status='same-supervisor-reused'),
                    cohort=cohort,before=before,after=after,original_arguments_preserved=True,
                    formal_data_modified=False)
    with preparation_phase('local-libraries'):
        updates, report = local_prepare(mode=mode or 'auto', gpu_root=env['BB_GPU_USER_DIR'],
                                       cohort=cohort, engine='arm64', graphics=True,**kw(runtime_profile))
    env.update(updates)
    if fixed_helper_prepare is None:
        from launcher_fixed_helpers import prepare as fixed_helper_prepare
    with preparation_phase('fixed-helpers'):
        helper_updates, helper_report = fixed_helper_prepare(project=project, execution_root=code_root,
            arguments=arguments, environment=env, cohort=cohort, options=options, observe=observe,
            mode=mode or 'auto', local_prepare=local_prepare, protection_baseline=before,**kw(runtime_profile))
    env.update(helper_updates)
    if 'local' in helper_report:
        report = helper_report['local']
    game_report = dict(status='deferred', reason='local toolchain is not ready')
    restart_context = None
    if report['status'] in ('prepared', 'record', 'source-fallback') and mode != 'record':
        if game_prepare is None:
            from launcher_game_pso import prepare as game_prepare
        with preparation_phase('game-preparation'):
            game_kwargs = dict(project=project, execution_root=code_root,
                arguments=arguments, environment=env, cohort=cohort, options=options, observe=observe)
            game_kwargs.update(kw(runtime_profile))
            if restart is not None:game_kwargs['restart_binding'] = restart
            game_report = game_prepare(**game_kwargs)
        restart_context = game_report.pop('_restart_context', None)
        capture = game_report.get('capture_environment', {})
        if set(capture)-{'BB_FETCH_CAPTURE_ROOT','BB_FETCH_CAPTURE_GAME_ROOT'}:
            raise ValueError('unexpected runtime capture environment')
        env.update(capture)
        # Candidate record requests may have extended this cohort. Reuse the
        # same policy to get the latest validated final exec build identity.
        if 'preparation' in game_report:
            with preparation_phase('local-finalize'):
                updates, report = local_prepare(mode=mode or 'auto', gpu_root=env['BB_GPU_USER_DIR'],
                                               cohort=cohort, engine='arm64', graphics=True,**kw(runtime_profile))
            env.update(updates)
    after = observe()
    # Preserve the entire preparation interval's cumulative budget. The builder
    # owns its continuous severe/burst checks; this boundary does not reset it.
    if after['locked'] or after['pressure'] == 4 or after['swap_mib']-before['swap_mib'] > 2048:
        raise RuntimeError('protection after local Metal preparation')
    if report['status'] in ('stopped', 'failed-protection'):
        raise RuntimeError('local Metal preparation stopped by its safety guard')
    # Toolchain absence is a deliberate, visible source fallback supplied by the
    # policy, not a fatal game/launcher failure or a fabricated completed build.
    if report['status'] not in ('record', 'prepared', 'source-fallback'):
        raise RuntimeError('local Metal preparation refused: '+report['status'])
    if restart is not None and restart_context is not None and report['status']=='prepared':
        from quick_restart_preparation import publish
        def restart_check():
            sample=observe()
            if sample['locked'] or sample['pressure']==4 or sample['swap_mib']-before['swap_mib']>2048:
                raise RuntimeError('protection during F3 preparation receipt')
        try:
            published=publish(bound=restart, context=restart_context, gpu=env['BB_GPU_USER_DIR'], check=restart_check,
                    local_environment=env,**kw(runtime_profile))
            game_report['restart_schedule_published']=published is True
        except (ValueError,OSError,KeyError,TypeError):
            pass # Optional scheduling hint cannot replace a completed original launch.
    manifest = dict(schema=1, status=report['status'], requested=mode or 'auto',
                    local=report, game_pso=game_report, fixed_helpers=helper_report, cohort=cohort, before=before, after=after,
                    original_arguments_preserved=True, formal_data_modified=False)
    return env, manifest


def main(argv=None, *, project=PROJECT, execution_root=None,runtime_profile=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv)<2:
        raise ValueError('expected original executable and native launcher arguments')
    executable, arguments = argv[0], argv[1:]
    env, report = prepare_launch(executable, arguments, os.environ, project=project, execution_root=execution_root,runtime_profile=runtime_profile)
    if _pending_signal:
        raise KeyboardInterrupt
    # Never emit the inherited environment: it may contain the original QA token.
    visible={k:report[k]for k in ('status','requested')if k in report}
    if report.get('local',{}).get('reason'):visible['reason']=report['local']['reason']
    if 'game_pso'in report:visible['game_pso']=report['game_pso'].get('status')
    if 'fixed_helpers'in report:visible['fixed_helpers']=report['fixed_helpers'].get('status')
    publication=report.get('game_pso',{}).get('restart_schedule_published')
    if type(publication)is bool:visible['f3_schedule_published']=publication
    health=report.get('game_pso',{}).get('startup_health',{})
    if isinstance(health,dict)and set(health)=={'observations','coalesced','forced'}and all(type(v)is int and v>=0 for v in health.values()):visible['startup_health']=health
    static=report.get('game_pso',{}).get('scan',{}).get('automatic_static_inputs',{})
    view=static.get('source_view',{})
    counts={}
    for name,value in (('source',view.get('source_hashes')),('snapshot',view.get('snapshot_hashes')),('catalogue',static.get('source_hashes'))):
        if isinstance(value,dict):
            counts[name]={k:value[k]for k in('hashed_files','hashed_bytes','reused_files')if type(value.get(k))is int and value[k]>=0}
            if type(value.get('journal_reused'))is bool:counts[name]['journal_reused']=value['journal_reused']
    if counts:visible['incremental_inputs']=counts
    local=report.get('local',{})
    visible_local={k:local[k]for k in('built','skipped','reused_products','repaired_products','requests')if type(local.get(k))is int and local[k]>=0}
    if type(local.get('persistent_audit_reused'))is bool:visible_local['persistent_audit_reused']=local['persistent_audit_reused']
    if local.get('same_supervisor_reused')is True:visible_local['same_supervisor_reused']=True
    if isinstance(local.get('toolchain_registration'),dict):visible_local['toolchain_registration']=local['toolchain_registration']
    if visible_local:visible['local_libraries']=visible_local
    modules=report.get('game_pso',{}).get('scan',{}).get('automatic_modules',{})
    typed=modules.get('typed_companions',{}).get('counts',{})
    module_counts={k:typed[k]for k in('inventory_records','native_attempts','native_completed','verified_target_products','verified_target_reuses','verified_existing','required_target_keys','verified_required_products','unresolved_static_stage_records','verified_generic_target_coverage')if type(typed.get(k))is int and typed[k]>=0}
    if modules.get('typed_companions',{}).get('historical_capture_inventory_skipped')is True:module_counts['historical_capture_inventory_skipped']=True
    if module_counts:visible['typed_modules']=module_counts
    if runtime_profile is not None:
        from launcher_runtime_profile import code_validation_work
        from gpu_candidate_identity import verification_work
        visible['startup_validation']=dict(package=runtime_profile.bundle.validation_work,
            code_digests=code_validation_work(),native_verifiers=verification_work(),**runtime_profile.preparation_work)
    print('Metal local preparation: '+json.dumps(visible,sort_keys=True),flush=True)
    if runtime_profile is not None:
        # Explicit temporary Shared-only metadata request, applied after all
        # preparation/identity work. Never changes compilation, saves or input.
        from texture_binding_request import apply as texture_diagnostic
        env, diagnostic = texture_diagnostic(env)
        if diagnostic is not None:
            print('Texture binding capture: '+json.dumps(diagnostic,sort_keys=True),flush=True)
    os.execve(executable, [executable, *arguments], env)


def cli(*, project=None, execution_root=None,runtime_profile=None):
    global _pending_signal
    def interrupted(number, frame):
        global _pending_signal
        if _pending_signal:
            return
        _pending_signal=number
        # FixedRunner's finally reaps its owned compiler group before returning.
        raise KeyboardInterrupt
    previous={s:signal.signal(s,interrupted)for s in (signal.SIGTERM,signal.SIGINT)}
    try:
        if project is None and execution_root is None:
            main()
        else:
            main(project=PROJECT if project is None else project, execution_root=execution_root,**({'runtime_profile':runtime_profile}if runtime_profile is not None else{}))
    except KeyboardInterrupt:
        if not _pending_signal:raise SystemExit(130)
    except Exception as error:
        print('Metal local preparation failed: '+str(error), file=sys.stderr, flush=True)
        # Ordinary failure; never emit reserved original launcher restart codes.
        raise SystemExit(74)
    finally:
        for number,handler in previous.items():signal.signal(number,handler)
        if _pending_signal:
            number=_pending_signal
            signal.signal(number,signal.SIG_DFL)
            os.kill(os.getpid(),number)


if __name__ == '__main__':cli()
