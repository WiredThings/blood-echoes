"""Same-supervisor F3 scheduling hint, never an artifact or source authority."""
from pathlib import Path
import hashlib,json,os,re,shlex,stat,subprocess
from launcher_game_catalog import directory,file_sha,digest,read_ready,write_json
from game_pso_owned_audit import checked_bytes

HEX32=re.compile(r'[0-9a-f]{32}')
HEX64=re.compile(r'[0-9a-f]{64}')

def process(pid):
    result=subprocess.run(['/bin/ps','-p',str(pid),'-o','ppid=','-o','uid=','-o','lstart=','-o','command='],
        stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=2,
        env={'PATH':'/usr/bin:/bin:/usr/sbin:/sbin'})
    if result.returncode or len(result.stdout)>4096:raise ValueError('restart parent unavailable')
    parts=result.stdout.decode('utf-8').strip().split(None,7)
    if len(parts)!=8 or int(parts[1])!=os.getuid():raise ValueError('restart parent identity')
    command=shlex.split(parts[7])
    if len(command)<2 or command[0]not in('bash','/bin/bash'):raise ValueError('restart parent shell')
    return dict(pid=pid,parent=int(parts[0]),start=' '.join(parts[2:7]),role=Path(command[1]).name,script=command[1])

def process_cwd(pid,*,code_owners=None):
    result=subprocess.run(['/usr/sbin/lsof','-a','-p',str(pid),'-d','cwd','-Fpn'],
        stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=2,
        env={'PATH':'/usr/bin:/bin:/usr/sbin:/sbin'})
    if result.returncode or len(result.stdout)>4096:raise ValueError('restart cwd unavailable')
    lines=result.stdout.decode('utf-8').splitlines()
    # Darwin emits the mandatory file-descriptor field even with -Fpn.
    # Accept exactly one observed cwd for this PID, never another fd/name.
    if len(lines)!=3 or lines[0]!='p'+str(pid) or lines[1]!='fcwd' or not lines[2].startswith('n'):raise ValueError('restart cwd envelope')
    if code_owners is None:return directory(lines[2][1:])
    path=Path(lines[2][1:]).absolute();info=path.lstat()
    if path!=path.resolve(strict=True)or not stat.S_ISDIR(info.st_mode)or info.st_uid not in code_owners:raise ValueError('restart code cwd identity')
    return path

def supervisor(stage,probe=process,cwd_probe=process_cwd,*,expected_parity_sha=None,runtime_profile=None):
    stage=runtime_profile.code(stage)if runtime_profile is not None else directory(stage)
    code_args={'code_owners':(0,os.getuid())}if runtime_profile is not None else{}
    raw,_=checked_bytes(stage/'SOURCE-PARITY.json',limit=1024*1024,**code_args)
    if expected_parity_sha is not None and hashlib.sha256(raw).hexdigest()!=expected_parity_sha:raise ValueError('restart original script manifest changed')
    parity=json.loads(raw)['effective_sources']
    def original(pid,relative):
        parent=probe(pid)
        if parent['pid']!=pid or parent['script']not in(relative,str(stage/relative)):
            raise ValueError('restart parent script is not from selected stage')
        cwd=process_cwd(pid,**code_args)if runtime_profile is not None and cwd_probe is process_cwd else cwd_probe(pid)
        if (runtime_profile.code(cwd)if runtime_profile is not None else directory(cwd))!=stage:raise ValueError('restart parent cwd differs from selected stage')
        if runtime_profile is not None:
            from launcher_runtime_profile import code_bytes
            script_sha=code_bytes(runtime_profile.code(stage/relative),limit=4*1024*1024,content=False)[1]['sha256']
        else:script_sha=file_sha(stage/relative)[0]
        if script_sha!=parity[relative]:raise ValueError('original restart script changed')
        if probe(pid)!=parent:raise ValueError('restart parent changed while observing origin')
        return parent
    parent=probe(os.getppid())
    if parent['role']=='run.sh':
        parent=original(os.getppid(),'run.sh')
        parent=original(parent['parent'],'tools/macos/supervise_restart.sh')
    else:parent=original(os.getppid(),'tools/macos/supervise_restart.sh')
    if parent['role']!='supervise_restart.sh':raise ValueError('original restart supervisor unavailable')
    return dict(pid=parent['pid'],start=parent['start'])

def binding(*,executable,arguments,environment,cohort,stage,archive_identity,probe=process,cwd_probe=process_cwd,runtime_profile=None):
    """Full current launch identity, computed again; no receipt supplies identity."""
    if stage is None or environment.get('BB_RESTART_SUPERVISED')!='1' or environment.get('BB_ARM_LAUNCHER_COMPAT')!='1' or environment.get('BB_ONLINE')!='0':return None
    nonces=[environment.get(k,'')for k in('BB_ARM_SESSION_NONCE','BB_ARM_RESTART_NONCE')]
    if not all(HEX32.fullmatch(n)for n in nonces):return None
    try:
        from launcher_source_view import recipe
        root=runtime_profile.code(stage)if runtime_profile is not None else directory(stage);files={}
        code_args={'code_owners':(0,os.getuid())}if runtime_profile is not None else{}
        for name in('GAME-PSO-COMPONENT.json','NATIVE-METAL.json','ARM64-LOCAL-PREPARATION.json','SOURCE-PARITY.json'):
            files[name]=hashlib.sha256(checked_bytes(root/name,limit=1024*1024,**code_args)[0]).hexdigest()
        current=dict(archive_identity['manifest']['game'])
        if set(current)!={'boot','content_profile','patches','settings'}:return None
        args=list(arguments);args[args.index('--app0')+1]='<original-final-overlay>'
        selection=json.loads(json.dumps(recipe(root,environment,runtime_profile),default=str,sort_keys=True))
        if runtime_profile is not None:
            from launcher_runtime_profile import code_bytes
            cpu_sha=code_bytes(runtime_profile.code(Path(executable).resolve(strict=True)),limit=256*1024*1024,content=False)[1]['sha256']
        else:cpu_sha=file_sha(Path(executable).resolve(strict=True))[0]
        value=dict(schema=1,supervisor=supervisor(root,probe,cwd_probe,expected_parity_sha=files['SOURCE-PARITY.json'],runtime_profile=runtime_profile),nonce_identity=digest(nonces),cohort=cohort,
            stage=str(root),cpu=dict(path=str(Path(executable).resolve(strict=True)),sha256=cpu_sha),
            component_files=files,final_inputs=current,arguments=args,selection=selection,
            gpu_root=str(Path(environment['BB_GPU_USER_DIR']).resolve(strict=True)))
        return dict(identity=digest(value),value=value)
    except(ValueError,OSError,KeyError,TypeError,subprocess.SubprocessError):return None

def _path(gpu,bound):
    if not HEX64.fullmatch(bound['identity']) or digest(bound['value'])!=bound['identity']:raise ValueError('restart binding digest')
    root=directory(Path(gpu)/'automatic-game-inputs')
    return root/('f3-prepared-'+bound['identity']+'.json')

def _context(context,gpu,check):
    cache=directory(Path(gpu)/'automatic-game-inputs')
    generation=directory(context['generation']);game=directory(context['game_root'])
    if generation.parent!=cache or game.parent.parent!=cache/'source-views' or game.name!='view':
        raise ValueError('restart static context outside selected preparation roots')
    if stat.S_IMODE(game.stat().st_mode)!=0o500:raise ValueError('restart snapshot is not readonly')
    if file_sha(game.parent/'READY.json',check)[0]!=context['snapshot_ready_sha256']:raise ValueError('restart snapshot provenance changed')
    check();ready=read_ready(generation,context['identity'])
    if ready is None:raise ValueError('restart static preparation is incomplete')
    if file_sha(generation/'READY.json',check)[0]!=context['ready_sha256']:raise ValueError('restart static receipt changed')
    return dict(game_root=game,catalog=generation/'catalog.json',index=generation/'static-program-index.jsonl')

def pending(context):
    return dict(generation=str(context['generation']),identity=context['identity'],game_root=str(context['game_root']),
        ready_sha256=file_sha(Path(context['generation'])/'READY.json')[0],
        snapshot_ready_sha256=file_sha(Path(context['game_root']).parent/'READY.json')[0])

def publish(*,bound,context,gpu,check=lambda:None,probe=process,cwd_probe=process_cwd,runtime_profile=None):
    try:
        if bound is None or supervisor(bound['value']['stage'],probe,cwd_probe,expected_parity_sha=bound['value']['component_files']['SOURCE-PARITY.json'],runtime_profile=runtime_profile)!=bound['value']['supervisor']:return False
        _context(context,gpu,check)
        path=_path(gpu,bound)
        payload=dict(schema=1,kind='same-supervisor-f3-preparation-schedule',binding=bound,context=context,
            artifact_authority=False,source_contents_revalidated=False)
        if len(json.dumps(payload,sort_keys=True,separators=(',',':')).encode())>16384:return False
        write_json(path,payload)
        return True
    except(ValueError,OSError,KeyError,TypeError,subprocess.SubprocessError):return False

def resume(*,bound,environment,gpu,component,backend,cohort,check,configure_capture,probe=process,cwd_probe=process_cwd,runtime_profile=None):
    if bound is None or environment.get('BB_QUICKLOAD_PENDING')!='1' or component.get('automatic_capture_schema')!=1:return None
    try:
        if environment.get('BB_RESTART_SUPERVISED')!='1' or environment.get('BB_ARM_LAUNCHER_COMPAT')!='1' or environment.get('BB_ONLINE')!='0':return None
        nonces=[environment.get(k,'')for k in('BB_ARM_SESSION_NONCE','BB_ARM_RESTART_NONCE')]
        if not all(HEX32.fullmatch(n)for n in nonces) or digest(nonces)!=bound['value']['nonce_identity']:return None
        if bound['value']['cohort']!=cohort or bound['value']['gpu_root']!=str(directory(gpu)):return None
        if supervisor(bound['value']['stage'],probe,cwd_probe,expected_parity_sha=bound['value']['component_files']['SOURCE-PARITY.json'],runtime_profile=runtime_profile)!=bound['value']['supervisor']:return None
        raw,_=checked_bytes(_path(gpu,bound),limit=16384);saved=json.loads(raw)
        if saved.get('schema')!=1 or saved.get('kind')!='same-supervisor-f3-preparation-schedule' or saved.get('binding')!=bound or saved.get('artifact_authority')is not False or saved.get('source_contents_revalidated')is not False:return None
        context=_context(saved['context'],gpu,check)
        root=directory(Path(gpu)/'automatic-game-inputs'/('runtime-'+cohort))
        report=configure_capture(root=root,**context,backend=Path(backend),runtime_source_root=component['source_root'],**({'code_owners':(0,os.getuid())}if runtime_profile is not None else{}))
        if supervisor(bound['value']['stage'],probe,cwd_probe,expected_parity_sha=bound['value']['component_files']['SOURCE-PARITY.json'],runtime_profile=runtime_profile)!=bound['value']['supervisor']:return None
        capture=report['capture_environment']
        if set(capture)!={'BB_FETCH_CAPTURE_ROOT','BB_FETCH_CAPTURE_GAME_ROOT'} or capture['BB_FETCH_CAPTURE_ROOT']!=str(root) or capture['BB_FETCH_CAPTURE_GAME_ROOT']!=str(context['game_root']):return None
        return dict(status='same-supervisor-f3-schedule-reused',capture_environment=capture,
            anticipatory_scan_and_warmup_skipped=True,artifact_authority=False,runtime_full_key_checks_retained=True,
            runtime_cache_import_allowed=False,actual_final_read_admission=False,full_final_pso_ready=False,
            static_registry_rescanned=False,dynamic_capture_gap='changed-or-new-code-outside-verified-static-registry-is-not-captured',
            original_arguments_preserved=True)
    except(ValueError,OSError,KeyError,TypeError,subprocess.SubprocessError):return None
