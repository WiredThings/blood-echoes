"""Same-supervisor F3 scheduling hint, never an artifact or source authority."""
from pathlib import Path
import hashlib,json,os,re,shlex,stat,subprocess,time
from launcher_game_catalog import directory,file_sha,digest,read_ready,write_json
from game_pso_owned_audit import checked_bytes

HEX32=re.compile(r'[0-9a-f]{32}')
HEX64=re.compile(r'[0-9a-f]{64}')
WARM_MAX_BYTES=8*1024*1024

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
    # Online may REGISTER completed GPU preparation. Only the original
    # supervisor's offline F3 child may RESUME it (checked again below). Online
    # credentials/mode are never restored; full actual boot/patch/settings and
    # compile identities still have to match, otherwise use normal preparation.
    if stage is None or environment.get('BB_RESTART_SUPERVISED')!='1' or environment.get('BB_ARM_LAUNCHER_COMPAT')!='1' or environment.get('BB_ONLINE')not in('0','1'):return None
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

LOCAL_ENV=frozenset(('BB_MTL_LOCAL_AOT_MODE','BB_MTL_LOCAL_AOT_ROOT',
    'BB_MTL_LOCAL_AOT_COHORT','BB_MTL_LOCAL_AOT_BUILD_ID','BB_MTL_LOCAL_AOT_ENVIRONMENT_ID'))

def local_selection(environment,gpu,cohort):
    """Only preparation-owned fields; never restore account or game state."""
    if not isinstance(environment,dict):return None
    selected={k:v for k,v in environment.items()if k.startswith('BB_MTL_LOCAL_AOT_')}
    if set(selected)!=LOCAL_ENV or selected['BB_MTL_LOCAL_AOT_MODE']!='load':return None
    if selected['BB_MTL_LOCAL_AOT_COHORT']!=cohort:return None
    if not all(isinstance(selected[k],str)and HEX64.fullmatch(selected[k])for k in
        ('BB_MTL_LOCAL_AOT_COHORT','BB_MTL_LOCAL_AOT_BUILD_ID','BB_MTL_LOCAL_AOT_ENVIRONMENT_ID')):return None
    root=directory(selected['BB_MTL_LOCAL_AOT_ROOT'])
    if root!=directory(gpu)/'local-metal':return None
    roots=[directory(root/cohort/'requests'),directory(root/cohort/'artifacts'/selected['BB_MTL_LOCAL_AOT_BUILD_ID'])]
    return selected,roots

def warm_binding(bound):
    """Preparation identity across Play sessions, not F3/session authority.

    Start with the CURRENT admitted original launch, never a receipt-provided
    identity. Saves are represented only by the original --user argument, not
    their changing contents. Accounts and online choice are never serialized.
    """
    if not isinstance(bound,dict)or digest(bound.get('value'))!=bound.get('identity'):return None
    value=bound['value']
    if set(value)!={'schema','supervisor','nonce_identity','cohort','stage','cpu','component_files','final_inputs','arguments','selection','gpu_root'}:return None
    stable={k:v for k,v in value.items()if k not in('supervisor','nonce_identity')}
    return dict(identity=digest(stable),value=stable)

def _warm_path(gpu,bound):
    if bound is None or not HEX64.fullmatch(bound['identity'])or digest(bound['value'])!=bound['identity']:raise ValueError('warm binding digest')
    return directory(Path(gpu)/'automatic-game-inputs')/('warm-prepared-'+bound['identity']+'.json')

def source_scope(bound):
    """Watch the actual original sources, never the save/log/data parent.

    A missing optional mod root is pinned as absent and checked on reuse, so
    adding it cannot evade history without watching the noisy data directory.
    """
    selection=bound['value']['selection'];roots=[directory(selection['base'])];optional={}
    for name in ('mods','last'):
        value=selection[name]
        if value is None or(name=='mods'and selection['enabled']!='1'):continue
        path=Path(value).absolute();exists=os.path.lexists(path);optional[str(path)]=exists
        if exists:roots.append(directory(path))
    return dict(roots=sorted({str(p)for p in roots}),optional=optional)

def warm_anchor(bound):
    """Anchor sources BEFORE the existing full preparation, not after it."""
    try:
        stable=warm_binding(bound)
        if stable is None:return None
        scope=source_scope(stable)
        from startup_change_journal import query
        token=query(scope['roots'])
        return dict(binding=stable,scope=scope,journal=token)if token is not None else None
    except(ValueError,OSError,KeyError,TypeError):return None

def _warm_products(gpu,bound,context,local):
    selected=local_selection(local,gpu,bound['value']['cohort'])
    if selected is None:raise ValueError('warm local selection')
    roots=list(selected[1])+[directory(context['generation']),directory(context['game_root'])]
    # The complete required queue is derived from source/index plus actual
    # renderer cache keys/meta/SPIR-V, all watched below. Supplementary capture
    # packets are observations (including session-specific equivalent inputs),
    # not additional required PSOs. They stay on disk for the next incremental
    # pass when a real key/source changes; watching their append-only store here
    # would invalidate every Play although every required target is prepared.
    optional={}
    for path in (Path(gpu)/'cache',):
        exists=os.path.lexists(path);optional[str(path)]=exists
        if exists:roots.append(directory(path))
    return dict(roots=sorted({str(p)for p in roots}),optional=optional)

def _optional_current(scope):
    return all(os.path.lexists(p)is exists for p,exists in scope['optional'].items())

def registered_local(local,check):
    """Load the existing real tools/SDK registration; a miss never probes all
    tools here. Ordinary incremental preparation still owns that recovery.
    """
    import tempfile
    from local_metal_builder import NativeToolchain,FixedRunner,canonical,digest as byte_digest
    from tool_environment_registration import Registration
    class Guard:
        def check(self,**_):check()
    root=directory(local['BB_MTL_LOCAL_AOT_ROOT'])
    with tempfile.TemporaryDirectory(prefix='warm-environment-',dir=root)as scratch:
        chain=NativeToolchain(FixedRunner(Path(scratch)));registration=Registration(root)
        result=registration.load(chain,Guard())
        return (result is not None and result[0]==local['BB_MTL_LOCAL_AOT_BUILD_ID']
            and byte_digest(canonical(registration.active['toolchain']['environment']))==local['BB_MTL_LOCAL_AOT_ENVIRONMENT_ID'])

def _inventory(scope,context,check):
    """Once after complete preparation, retain exact mutable product bytes.

    Runtime re-publishes identical captured packets/keys atomically. Only those
    files named by continuous journal events are re-read next time; never stat
    or hash every product on an ordinary hit. Source snapshot events stay strict.
    """
    immutable={context['generation'],context['game_root']};rows={};total=0;next_check=0
    for name in scope['roots']:
        if name in immutable:continue
        root=directory(name)
        for parent,dirs,files in os.walk(root,followlinks=False):
            for p in [Path(parent),*(Path(parent)/n for n in files)]:
                if time.monotonic()>=next_check:check();next_check=time.monotonic()+.1
                s=p.lstat()
                if p!=p.resolve(strict=True)or s.st_uid!=os.getuid()or s.st_mode&0o022:raise ValueError('unsafe warm product')
                if stat.S_ISDIR(s.st_mode):row=['directory',s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid]
                elif stat.S_ISREG(s.st_mode)and s.st_nlink==1 and 0<=s.st_size<=256*1024*1024:
                    total+=s.st_size
                    if total>1024*1024*1024:raise ValueError('warm product byte budget')
                    if p.name=='startup-entry.json':
                        from pso_startup_schedule import startup_preference
                        preference=startup_preference(p,check)
                        if preference is None:raise ValueError('invalid prepared artifact preference')
                        row=['preference',preference,s.st_mode,s.st_uid,s.st_gid]
                    else:row=['file',*file_sha(p),s.st_mode,s.st_uid,s.st_gid]
                else:raise ValueError('nonplain warm product')
                rows[str(p)]=row
                if len(rows)>20000:raise ValueError('warm product count budget')
            # Do not let os.walk silently skip linked directories.
            for n in dirs:directory(Path(parent)/n)
    check();return rows

def _products_current(products,context,check,diagnostic=None):
    def at(stage,path=None):
        if diagnostic is not None:
            diagnostic['product_check']=stage
            if path is not None:diagnostic['product_event_id']=digest(str(path))
    from startup_change_journal import query,unchanged
    at('journal-query')
    scope=products['scope'];old=products['journal'];current=query(scope['roots'],old)
    if current is None:return False
    if current.get('unchanged')is True:at('accepted');return True
    known={json.dumps(r,separators=(',',':'))for r in old['events']};paths=set()
    mounts={}
    for pin in old['pins'].values():
        if pin[0]in mounts and mounts[pin[0]]!=pin[-1]:return False
        mounts[pin[0]]=pin[-1]
    for event in current['events']:
        if json.dumps(event,separators=(',',':'))in known:continue
        relative=Path(event[3].lstrip('/'))
        if '..'in relative.parts or event[0]not in mounts:return False
        paths.add(Path(mounts[event[0]])/relative)
    immutable=[Path(context['generation']),Path(context['game_root'])];next_check=0
    for path in paths:
        at('event-scope',path)
        if any(path==p or path.is_relative_to(p)for p in immutable):return False
        if time.monotonic()>=next_check:check();next_check=time.monotonic()+.1
        row=products['files'].get(str(path))
        if row is None:
            # These private native writers' uncommitted temporary names are
            # never input. Ignore only an exact known shape AND absent node.
            temp=re.fullmatch(r'\.pending-[0-9]+-[0-9]+|[A-Za-z0-9_.-]+\.tmp(?:-[0-9]+-[0-9]+)?',path.name)
            if temp and not os.path.lexists(path)and any(path.is_relative_to(Path(r))for r in scope['roots']):continue
            at('unregistered-event',path)
            return False
        s=path.lstat()
        if path!=path.resolve(strict=True):return False
        if row[0]=='directory':
            if row!=['directory',s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid]:return False
        elif row[0]=='file':
            at('file-identity-and-bytes',path)
            if not stat.S_ISREG(s.st_mode)or s.st_nlink!=1 or s.st_size!=row[2]or row[3:]!=[s.st_mode,s.st_uid,s.st_gid]:return False
            if list(file_sha(path))!=row[1:3]:return False
        elif row[0]=='preference':
            at('startup-preference',path)
            from pso_startup_schedule import startup_preference
            if not stat.S_ISREG(s.st_mode)or s.st_nlink!=1 or row[2:]!=[s.st_mode,s.st_uid,s.st_gid]:return False
            if startup_preference(path,check)!=row[1]:return False
        else:return False
    check();at('final-journal');return unchanged(current,scope['roots'])

def publish_warm(*,anchor,context,gpu,local_environment,check=lambda:None):
    """Persist completed scheduling, without granting artifact import rights."""
    try:
        if anchor is None:return False
        bound=anchor['binding'];scope=source_scope(bound)
        from startup_change_journal import query,unchanged
        if scope!=anchor['scope']or not unchanged(anchor['journal'],scope['roots']):return False
        _context(context,gpu,check)
        selected=local_selection(local_environment,gpu,bound['value']['cohort'])
        if selected is None:return False
        products=_warm_products(gpu,bound,context,selected[0]);token=query(products['roots']);check()
        if token is None:return False
        files=_inventory(products,context,check)
        if not unchanged(token,products['roots']):return False
        payload=dict(schema=1,kind='unchanged-preparation-schedule',binding=bound,context=context,
            source=anchor,products=dict(scope=products,journal=token,files=files),local=selected[0],artifact_authority=False)
        if len(json.dumps(payload,sort_keys=True,separators=(',',':')).encode())>WARM_MAX_BYTES:return False
        if source_scope(bound)!=scope or not unchanged(anchor['journal'],scope['roots']):return False
        write_json(_warm_path(gpu,bound),dict(body=payload,sha256=digest(payload)));return True
    except(ValueError,OSError,KeyError,TypeError):return False

def prepared_warm(*,bound,environment,gpu,check=lambda:None,tool_check=registered_local,diagnostic=None):
    """Cross-session scheduling reuse; never restore quickload, mode or tokens."""
    try:
        def at(stage):
            if diagnostic is not None:diagnostic['last_check']=stage
        at('current-binding')
        if environment.get('BB_QUICKLOAD_PENDING')=='1':return None
        stable=warm_binding(bound)
        if stable is None or stable['value']['gpu_root']!=str(directory(gpu)):return None
        at('receipt-file');path=_warm_path(gpu,stable);info=path.lstat()
        if info.st_nlink!=1 or stat.S_IMODE(info.st_mode)!=0o600:return None
        at('receipt-structure');document=json.loads(checked_bytes(path,limit=WARM_MAX_BYTES)[0])
        if set(document)!={'body','sha256'}or digest(document['body'])!=document['sha256']:return None
        saved=document['body']
        if saved.get('schema')!=1 or saved.get('kind')!='unchanged-preparation-schedule'or saved.get('binding')!=stable or saved.get('artifact_authority')is not False:return None
        at('source-scope');scope=source_scope(stable);source=saved['source']
        if source['binding']!=stable or source['scope']!=scope or not _optional_current(scope):return None
        at('local-selection');selected=local_selection(saved['local'],gpu,stable['value']['cohort'])
        if selected is None or saved['local']!=selected[0]:return None
        at('product-scope');products=_warm_products(gpu,stable,saved['context'],selected[0])
        if products!=saved['products']['scope']or not _optional_current(products):return None
        from startup_change_journal import unchanged
        at('source-history')
        if not unchanged(source['journal'],scope['roots']):return None
        at('product-history-and-bytes')
        if not _products_current(saved['products'],saved['context'],check,diagnostic):return None
        at('registered-tool-environment')
        if not tool_check(selected[0],check):return None
        check();at('accepted');return saved
    except(ValueError,OSError,KeyError,TypeError,subprocess.SubprocessError):return None

def resume_warm(*,schedule,gpu,component,backend,cohort,check,configure_capture,runtime_profile=None):
    try:
        if schedule is None or component.get('automatic_capture_schema')!=1 or schedule['binding']['value']['cohort']!=cohort:return None
        context=_context(schedule['context'],gpu,check)
        root=directory(Path(gpu)/'automatic-game-inputs'/('runtime-'+cohort))
        report=configure_capture(root=root,**context,backend=Path(backend),runtime_source_root=component['source_root'],**({'code_owners':(0,os.getuid())}if runtime_profile is not None else{}))
        capture=report['capture_environment']
        if capture!={'BB_FETCH_CAPTURE_ROOT':str(root),'BB_FETCH_CAPTURE_GAME_ROOT':str(context['game_root'])}:return None
        from startup_change_journal import unchanged
        source=schedule['source'];products=schedule['products']
        if source_scope(schedule['binding'])!=source['scope']or not _optional_current(products['scope']):return None
        if not unchanged(source['journal'],source['scope']['roots'])or not _products_current(products,schedule['context'],check):return None
        return dict(status='unchanged-preparation-schedule-reused',capture_environment=capture,
            anticipatory_scan_and_warmup_skipped=True,artifact_authority=False,runtime_full_key_checks_retained=True,
            runtime_cache_import_allowed=False,actual_final_read_admission=False,full_final_pso_ready=False,
            static_registry_rescanned=False,original_arguments_preserved=True)
    except(ValueError,OSError,KeyError,TypeError):return None

def prepared_local(*,bound,environment,gpu,probe=process,cwd_probe=process_cwd,runtime_profile=None):
    """Front-loaded F3 hint; changed/unobservable products use normal policy.

    The original completed builder publishes this, not a child-supplied env.
    Runtime still admits actual libraries/PSOs by their full native keys.
    """
    if bound is None or environment.get('BB_QUICKLOAD_PENDING')!='1':return None
    try:
        if environment.get('BB_ONLINE')!='0' or environment.get('BB_RESTART_SUPERVISED')!='1' or environment.get('BB_ARM_LAUNCHER_COMPAT')!='1':return None
        nonces=[environment.get(k,'')for k in('BB_ARM_SESSION_NONCE','BB_ARM_RESTART_NONCE')]
        if not all(HEX32.fullmatch(n)for n in nonces)or digest(nonces)!=bound['value']['nonce_identity']:return None
        if bound['value']['gpu_root']!=str(directory(gpu)):return None
        if supervisor(bound['value']['stage'],probe,cwd_probe,expected_parity_sha=bound['value']['component_files']['SOURCE-PARITY.json'],runtime_profile=runtime_profile)!=bound['value']['supervisor']:return None
        path=_path(gpu,bound);info=path.lstat()
        if info.st_nlink!=1 or stat.S_IMODE(info.st_mode)!=0o600:return None
        saved=json.loads(checked_bytes(path,limit=16384)[0])
        if saved.get('schema')!=1 or saved.get('kind')!='same-supervisor-f3-preparation-schedule' or saved.get('binding')!=bound or saved.get('artifact_authority')is not False or saved.get('source_contents_revalidated')is not False:return None
        local=saved.get('local_preparation')
        if not isinstance(local,dict)or set(local)!={'environment','journal'}:return None
        if not isinstance(local['environment'],dict)or set(local['environment'])!=LOCAL_ENV:return None
        current=local_selection(local['environment'],gpu,bound['value']['cohort'])
        if current is None:return None
        from startup_change_journal import unchanged
        if not unchanged(local['journal'],current[1]):return None
        return dict(current[0])
    except(ValueError,OSError,KeyError,TypeError,subprocess.SubprocessError):return None

def publish(*,bound,context,gpu,check=lambda:None,probe=process,cwd_probe=process_cwd,runtime_profile=None,local_environment=None):
    try:
        if bound is None or supervisor(bound['value']['stage'],probe,cwd_probe,expected_parity_sha=bound['value']['component_files']['SOURCE-PARITY.json'],runtime_profile=runtime_profile)!=bound['value']['supervisor']:return False
        _context(context,gpu,check)
        path=_path(gpu,bound)
        payload=dict(schema=1,kind='same-supervisor-f3-preparation-schedule',binding=bound,context=context,
            artifact_authority=False,source_contents_revalidated=False)
        if local_environment is not None:
            current=local_selection(local_environment,gpu,bound['value']['cohort'])
            if current is not None:
                from startup_change_journal import query
                journal=query(current[1]);check()
                if journal is not None:payload['local_preparation']=dict(environment=current[0],journal=journal)
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
