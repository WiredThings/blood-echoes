"""CPU-only hook after the original launcher's image/Metal preparation.

Development authority is explicit. This never creates/selects a save, starts
FEX, changes restart codes, or implements a second supervisor.
"""
from pathlib import Path
import hashlib,json,os,plistlib,shutil,stat,subprocess,sys,tempfile,time,uuid

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
def prepare(executable,arguments,environment,*,stage,project,runner=subprocess.run):
    started=time.monotonic();env=dict(environment)
    need(env.get('BB_CPU_BACKEND','fex')=='bbcpu','bbcpu hook requires the selected CPU')
    doc,paths=component(stage);stage=Path(stage)
    # Original script arguments already select the effective linked image and
    # patches. Reuse its eboot.elf output; never parse/copy the game again.
    boot=owned(arguments[0]);elf=owned(boot.parent/'eboot.elf');patch=option(arguments,'--patches');startup=option(arguments,'--startup',False)
    data=private(env['BB_DATA_DIR']);project=private(project)
    need(data.is_relative_to(project/'state')and boot.is_relative_to(data/'out'),'CPU preparation escaped original isolated launch data')
    root=data/'bbcpu-preparation';root.mkdir(mode=0o700,exist_ok=True);private(root)
    effective=root/'effective';effective.mkdir(mode=0o700,exist_ok=True);private(effective)
    pins=[state(p)for p in(boot,elf,patch,*([startup]if startup else[]),paths['preparer'])]
    schedule=hashlib.sha256(json.dumps(pins,separators=(',',':')).encode()).hexdigest();index=effective/(schedule+'.json')
    selected=None
    if index.is_file():
        try:old=read(index)
        except(ValueError,OSError):old={}
        candidate=Path(old.get('directory',''))
        if candidate.parent==effective and old.get('inputs')==pins:
            try:
                if [state(candidate/name)for name in('effective.elf','pointers.txt','effective-input.json')]==old.get('products'):selected=candidate
            except(OSError,ValueError):pass
    safe={k:v for k,v in env.items()if not k.startswith(('BB_','BBCPU_','DYLD_'))};safe.update(PYTHONDONTWRITEBYTECODE='1',PYTHONNOUSERSITE='1')
    def run(argv,tag):
        # A serial full-game first build can exceed an hour. This is a bounded
        # preparation deadline, independent from the short live-game QA budget.
        result=runner(list(map(str,argv)),env=safe,capture_output=True,text=True,timeout=7200 if tag=='static-library'else 3600)
        log=root/(tag+'-'+uuid.uuid4().hex+'.log');log.write_text(result.stdout+result.stderr)
        need(result.returncode==0,'bbcpu '+tag+' failed; isolated diagnostic: '+str(log))
        return result.stdout
    exported=selected is None
    if selected is None:
        selected=effective/uuid.uuid4().hex
        argv=[paths['preparer'],'--boot',boot,'--elf',elf,'--patches',patch,'--out',selected]
        if startup:argv+=['--startup',startup]
        run(argv,'effective-code')
        need([state(Path(row[0]))for row in pins]==pins,'effective CPU sources changed during preparation')
        publish(index,dict(schema=1,inputs=pins,directory=str(selected),products=[state(selected/name)for name in('effective.elf','pointers.txt','effective-input.json')]))
    toolchain=doc['toolchain']
    products=root/'products';products.mkdir(mode=0o700,exist_ok=True);private(products)
    argv=[sys.executable,paths['producer'],'--effective',selected,'--work',products,'--tools',paths['scanner'].parent,'--abi',paths['abi'],'--jobs','1']
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
