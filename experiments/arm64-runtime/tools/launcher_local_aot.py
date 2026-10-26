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
                return prepare_startup_archive_policy(**values,resolver=lambda t,r:resolve_gpu_identity(t,r,code_owners=(0,os.getuid())))
    if local_prepare is None:
        from local_aot_policy import prepare
        local_prepare = prepare
    if observe is None:
        from native_observer import NativeObserver
        observer = NativeObserver()
        try:
            return _prepared(executable, arguments, env, project, inputs, options,
                             explicit, archive_prepare, local_prepare, observer.system, backend_root, code_root, game_prepare, fixed_helper_prepare,runtime_profile)
        finally:
            observer.close()
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
            publish(bound=restart, context=restart_context, gpu=env['BB_GPU_USER_DIR'], check=restart_check,**kw(runtime_profile))
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
    print('Metal local preparation: '+json.dumps(visible,sort_keys=True),flush=True)
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
