"""CPU-only hook after the original launcher's image/Metal preparation.

Development authority is explicit. This never creates/selects a save, starts
FEX, changes restart codes, or implements a second supervisor.
"""
from pathlib import Path
from bbcpu_compile_schedule import maximum_jobs
import hashlib,json,os,plistlib,selectors,shutil,signal,stat,subprocess,sys,tempfile,time,uuid

PRIVATE=(Path('/private/tmp'),Path('/Users/Shared/BloodEchoesDevelopment'))
def need(value,reason):
    if not value:raise ValueError(reason)
def owned(path,directory=False):
    p=Path(path).absolute();s=p.lstat()
    need(p==p.resolve(strict=True) and s.st_uid in(0,os.getuid()) and not s.st_mode&0o022 and
         (stat.S_ISDIR(s.st_mode) if directory else stat.S_ISREG(s.st_mode)),'untrusted CPU component input: '+str(p))
    return p
def sha(path):
    with path.open('rb')as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def state(path):
    p=owned(path);s=p.stat()
    return [str(p),s.st_dev,s.st_ino,s.st_uid,s.st_mode,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns]
def read(path):
    p=owned(path);need(p.stat().st_size<=4*1024*1024,'CPU descriptor budget exceeded')
    return json.loads(p.read_bytes())
def publish(path,value):
    if path.exists():shutil.copyfile(owned(path),path.with_name(path.stem+'-previous-'+uuid.uuid4().hex+'.json'))
    fd,name=tempfile.mkstemp(prefix=path.name+'-',dir=path.parent)
    with os.fdopen(fd,'w')as stream:json.dump(value,stream,sort_keys=True);stream.write('\n')
    os.replace(name,path)
def stream_cpu_command(argv,*,environment,log,timeout):
    """Stream bounded progress to the existing launcher's output collector."""
    process=subprocess.Popen(list(map(str,argv)),env=environment,stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,start_new_session=True)
    selector=selectors.DefaultSelector();selector.register(process.stdout,selectors.EVENT_READ)
    started=time.monotonic();output=bytearray();pending=bytearray()
    try:
        with log.open('wb')as trace:
            while selector.get_map():
                if time.monotonic()-started>timeout:raise TimeoutError('CPU preparation deadline; completed objects retained')
                for key,_ in selector.select(.25):
                    raw=os.read(key.fd,65536)
                    if not raw:selector.unregister(key.fileobj);continue
                    trace.write(raw);trace.flush();output.extend(raw);pending.extend(raw)
                    need(len(output)<=16*1024*1024 and len(pending)<=1024*1024,'CPU preparation output bound')
                    while b'\n'in pending:
                        line,_,rest=pending.partition(b'\n');pending=bytearray(rest)
                        if line.startswith(b'{'):
                            event=json.loads(line)
                            if event.get('event')=='compile-progress':
                                print('CPU preparation progress: '+json.dumps(event),flush=True)
            code=process.wait(timeout=5)
        return subprocess.CompletedProcess(argv,code,output.decode(errors='replace'),'')
    finally:
        selector.close();process.stdout.close()
        # Only this new preparation process group, never a game/supervisor.
        try:os.killpg(process.pid,signal.SIGTERM)
        except ProcessLookupError:pass
        try:process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            process.wait()
def private(path):
    p=owned(path,True);need(any(p.is_relative_to(root)and p!=root for root in PRIVATE),'bbcpu development authority must stay isolated')
    return p
def role(root,row):
    rel=Path(row['path']);need(not rel.is_absolute()and rel.parts and not set(rel.parts)&{'..','.'},'invalid CPU component role')
    p=owned(root/rel);need(p.is_relative_to(root)and sha(p)==row['sha256'],'CPU component bytes changed: '+str(rel));return p
def component(stage):
    stage=private(stage)
    info=stage.parents[2]/'Contents/Info.plist'
    if info.is_file():need(plistlib.loads(info.read_bytes()).get('CFBundleIdentifier')!='io.github.bbport.optimized','bbcpu component is development-only')
    doc=read(stage/'BBCPU-COMPONENT.json')
    need(doc.get('schema')==1 and doc.get('development_only')is True and doc.get('backend')=='bbcpu','unsupported CPU component')
    native=read(stage/'NATIVE-METAL.json')
    need(native.get('game_runtime')=='arm64'and native.get('production')is False and native.get('cpu_backends')==['fex','bbcpu'],'explicit independent CPU capabilities required')
    paths={name:role(stage,doc[name])for name in('cpu','preparer','scanner','generator','abi')}
    dependencies=doc.get('runtime_dependencies',{})
    need(isinstance(dependencies,dict)and len(dependencies)<=64,'bounded CPU dependency closure required')
    for name,digest in dependencies.items():role(stage,dict(path=name,sha256=digest))
    frozen=native.get('frozen_runtime',{})
    need(isinstance(frozen,dict)and all(frozen.get(doc[name]['path'])==doc[name]['sha256']for name in('cpu','preparer','scanner','generator','abi'))and
         all(frozen.get(name)==digest for name,digest in dependencies.items()),'CPU component differs from selected native package roles')
    headers=doc.get('dependency_headers',{})
    need(isinstance(headers,dict)and len(headers)<=128,'bounded CPU header closure required')
    for name,digest in headers.items():role(stage,dict(path=name,sha256=digest))
    need(paths['scanner'].name=='bbcpu-scan'and paths['generator'].name=='bbrecomp'and paths['scanner'].parent==paths['generator'].parent,'selected CPU tools must match producer roles')
    kit=private(doc['producer_root']);files=doc['producer_files']
    need(isinstance(files,dict)and 0<len(files)<=256,'bounded producer source closure required')
    for name,digest in files.items():role(kit,dict(path=name,sha256=digest))
    entry=doc['producer_entry'];need(entry in files,'producer entry is not frozen')
    paths['producer']=kit/entry
    return doc,paths
def option(arguments,name,required=True):
    positions=[i for i,value in enumerate(arguments)if value==name]
    need(len(positions)<=1 and(not positions or positions[0]+1<len(arguments)),'ambiguous original CPU option: '+name)
    need(bool(positions)or not required,'missing original CPU option: '+name)
    return owned(arguments[positions[0]+1])if positions else None
def registered_inputs(root,paths):
    """Register these few selected sources, not the entire resource tree.

    File identity/ctime changes trigger a real digest. Rewriting a small patch
    file with identical bytes must not launch another native exporter.
    Runtime admission still verifies the actual final mapped code.
    """
    register=root/'input-register.json'
    try:old=read(register)
    except(OSError,ValueError):old={}
    rows={};pins=[]
    for path in paths:
        before=state(path);saved=old.get(str(path),{})
        value=saved.get('sha256')if saved.get('state')==before else None
        if not isinstance(value,str)or len(value)!=64:value=sha(path)
        need(state(path)==before,'CPU source changed during registration')
        rows[str(path)]=dict(state=before,sha256=value);pins.append(before)
    if rows!=old:publish(register,rows)
    return pins,[[str(path),rows[str(path)]['sha256']]for path in paths]
def export_context(arguments,environment,mode):
    keys=('BB_ARM_RESOURCE_HOOKS','BB_ARM_EXTRA_HEAPS','BB_ARM_EXTRA_HEAPS_FRESH',
          'BB_GAME_MENU','BB_ARM_PAYLOAD_GATE','BB_ARM_RESOURCE_FAST','BB_ARM_RESOURCE_LEAF',
          'BB_STUTTER_AUDIT','BB_FREE_CHECK','BB_LABEL_TRAP','BB_GUEST_HOOK_TRAPS',
          'BB_DEV_WORLD_WARPER_DIR','BB_ARM_ALLOCATION_AUDIT','BB_NET_BACKEND')
    return dict(mode=mode,arguments=list(map(str,arguments)),flags={key:environment[key]for key in keys if key in environment})
def prepare(executable,arguments,environment,*,stage,project,runner=subprocess.run):
    started=time.monotonic();env=dict(environment)
    jobs=env.pop('BB_CPU_COMPILE_JOBS','auto')
    maximum_jobs(jobs)
    need(env.get('BB_CPU_BACKEND','fex')=='bbcpu','bbcpu hook requires the selected CPU')
    doc,paths=component(stage);stage=Path(stage)
    # Original script arguments already select the effective linked image and
    # patches. Reuse its eboot.elf output; never parse/copy the game again.
    boot=owned(arguments[0]);elf=owned(boot.parent/'eboot.elf');patch=option(arguments,'--patches');startup=option(arguments,'--startup',False)
    data=private(env['BB_DATA_DIR']);project=private(project)
    need(data.is_relative_to(project/'state')and boot.is_relative_to(data/'out'),'CPU preparation escaped original isolated launch data')
    root=data/'bbcpu-preparation';root.mkdir(mode=0o700,exist_ok=True);private(root)
    effective=root/'effective';effective.mkdir(mode=0o700,exist_ok=True);private(effective)
    pins,semantic_inputs=registered_inputs(effective,(boot,elf,patch,*([startup]if startup else[]),paths['preparer']))
    context=export_context(arguments,env,doc.get('runtime_image_export',0))
    product_names=('effective.elf','pointers.txt','effective-input.json',*(('analysis/effective.elf',)if doc.get('runtime_image_export')==1 else()))
    schedule=hashlib.sha256(json.dumps(dict(inputs=semantic_inputs,context=context),sort_keys=True,separators=(',',':')).encode()).hexdigest();index=effective/(schedule+'.json')
    selected=None
    if index.is_file():
        try:old=read(index)
        except(ValueError,OSError):old={}
        candidate=Path(old.get('directory',''))
        if candidate.parent==effective and old.get('semantic_inputs')==semantic_inputs and old.get('context')==context:
            try:
                if [state(candidate/name)for name in product_names]==old.get('products'):selected=candidate
            except(OSError,ValueError):pass
    safe={k:v for k,v in env.items()if not k.startswith(('BB_','BBCPU_','DYLD_'))};safe.update(PYTHONDONTWRITEBYTECODE='1',PYTHONNOUSERSITE='1')
    def run(argv,tag,environment=None):
        # A serial full-game first build can exceed an hour. This is a bounded
        # preparation deadline, independent from the short live-game QA budget.
        log=root/(tag+'-'+uuid.uuid4().hex+'.log')
        if tag=='static-library'and runner is subprocess.run:
            result=stream_cpu_command(argv,environment=safe,log=log,timeout=7200)
        else:
            result=runner(list(map(str,argv)),env=environment or safe,capture_output=True,text=True,timeout=7200 if tag=='static-library'else 3600)
            log.write_text(result.stdout+result.stderr)
        need(result.returncode==0,'bbcpu '+tag+' failed; isolated diagnostic: '+str(log))
        return result.stdout
    exported=selected is None
    if selected is None:
        selected=effective/uuid.uuid4().hex
        if doc.get('runtime_image_export')==1:
            # The same host installs its actual native resource/heap/menu hooks
            # and exits before guest entry. Keep original arguments/semantics;
            # no second frontend, guest run, or authenticated network request.
            export_env={k:v for k,v in env.items()if k!='BB_RECOMP_LIB'and not any(word in k.upper()for word in('TOKEN','CREDENTIAL','SECRET','KEYCHAIN'))and k not in('BB_AUTH_FILE','BB_NET_AUTH_FILE')}
            export_env.update(BB_CPU_EXPORT_ROOT=str(selected),BB_CPU_EXPORT_ELF=str(elf),BB_CPU_STATIC_PATCH_AUDIT='0',BB_CPU_STATIC_PATCH_AUDIT_ONLY='0')
            run([paths['preparer'],*arguments],'effective-code',export_env)
        else:
            argv=[paths['preparer'],'--boot',boot,'--elf',elf,'--patches',patch,'--out',selected]
            if startup:argv+=['--startup',startup]
            run(argv,'effective-code')
        need([state(Path(row[0]))for row in pins]==pins,'effective CPU sources changed during preparation')
        publish(index,dict(schema=1,inputs=pins,semantic_inputs=semantic_inputs,context=context,directory=str(selected),products=[state(selected/name)for name in product_names]))
    toolchain=doc['toolchain']
    products=root/'products';products.mkdir(mode=0o700,exist_ok=True);private(products)
    argv=[sys.executable,paths['producer'],'--effective',selected,'--work',products,'--tools',paths['scanner'].parent,'--abi',paths['abi'],'--jobs',jobs]
    for name in('compiler','linker','sdk'):argv+=['--'+name,toolchain[name]]
    roots={str(Path(path).resolve(strict=True))for path in toolchain['dependency_roots']}
    # Admit the final relocated tool closure as well as the explicit original
    # compiler roots. These directories come only from verified component roles.
    roots.update(str((stage/name).parent)for name in doc.get('runtime_dependencies',{}))
    for path in sorted(roots):argv+=['--dependency-root',path]
    for path in toolchain.get('includes',[]):argv+=['--include',path]
    events=[json.loads(line)for line in run(argv,'static-library').splitlines()if line.startswith('{')]
    done=[row for row in events if row.get('event')=='done'];need(len(done)==1,'missing CPU producer completion')
    report=done[0];artifact=private(report['artifact']);need(artifact.is_relative_to(root/'products'),'CPU artifact escaped isolated work')
    env['BB_RECOMP_LIB']=str(owned(artifact/'librecomp.dylib'))
    # CPU C++ admission still checks actual effective image, ABI, bytes and a
    # pinned fd. This scheduling result is not permission to bypass it.
    return str(paths['cpu']),env,dict(backend='bbcpu',default_execution='static',effective_exported=exported,seconds=time.monotonic()-started,producer=report)
