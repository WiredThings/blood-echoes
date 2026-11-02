"""Original-launcher FEX preparation: actual installed image -> existing scanner
-> original FEX compiler -> bounded disk shards -> normal FEX JIT buffer.

This admitted component never selects saves, changes restart semantics,
loads bbcpu, invokes Clang, or installs a second execution engine.
"""
from pathlib import Path
import hashlib,json,os,plistlib,re,struct,subprocess,time,uuid
from bbcpu_launch import need,owned,private,role,read,sha,state,publish,registered_inputs,option,export_context,stream_cpu_command
from bbcpu_resume_catalog import compilation_catalog
from cpu_code_map import parse as parse_code_map

def component(stage,runtime_profile=None):
    if runtime_profile is None:
        stage=private(stage);info=stage.parents[2]/'Contents/Info.plist'
        if info.exists():need(plistlib.loads(info.read_bytes()).get('CFBundleIdentifier')!='io.github.bbport.optimized','formal FEX component requires actual runtime profile')
        doc=read(stage/'FEX-COMPONENT.json');native=read(stage/'NATIVE-METAL.json')
        need(doc.get('schema')==1 and doc.get('development_only')is True and doc.get('backend')=='fex','unsupported FEX component')
        need(native.get('game_runtime')=='arm64'and native.get('production')is False,'FEX disk preparation requires admitted private original launcher')
        verify=lambda row:role(stage,row)
    else:
        from launcher_runtime_profile import code_bytes
        from fex_component import formal_roles
        stage=Path(stage);need(stage==runtime_profile.bundle.stage,'formal FEX component stage mismatch')
        native=runtime_profile.bundle.native
        doc,_=formal_roles(stage,native,lambda name,want,limit:code_bytes(runtime_profile.code(stage/name),limit=limit,expected=want)[0])
        need(doc is not None,'formal automatic FEX declaration missing')
        def verify(row):
            p=runtime_profile.code(stage/row['path']);code_bytes(p,limit=512*1024*1024,expected=row['sha256'],content=False);return p
    paths={name:verify(doc[name])for name in('cpu','scanner')}
    dependencies=doc.get('runtime_dependencies',{})
    need(isinstance(dependencies,dict)and len(dependencies)<=64,'FEX dependency closure budget')
    for name,digest in dependencies.items():verify(dict(path=name,sha256=digest))
    frozen=native.get('frozen_runtime',{})
    need(all(frozen.get(doc[name]['path'])==doc[name]['sha256']for name in paths)and all(frozen.get(name)==digest for name,digest in dependencies.items()),'FEX component differs from original selected package roles')
    need(paths['scanner'].name=='bbcpu-scan','use the existing standalone x86 scanner, never a second CPU runtime')
    aliases=doc.get('cpu_aliases',[]);need(isinstance(aliases,list)and len(aliases)<=4,'FEX executable alias budget')
    paths['aliases']=[paths['cpu']]
    for row in aliases:
        alias=verify(row);need(frozen.get(row['path'])==row['sha256'],'FEX alias differs from actual package role');paths['aliases'].append(alias)
    return doc,paths

def fingerprint(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def config_inputs(environment):
    # Match this embedded FEX's nonportable configuration locations. Checking
    # these few selected files does not enumerate home/config/resource trees.
    home=Path(environment.get('HOME',str(Path.home())));legacy=home/'.fex-emu'
    override=environment.get('FEX_APP_CONFIG_LOCATION')
    local=Path(override).absolute()if override else legacy if legacy.exists()else Path(environment.get('XDG_CONFIG_HOME',str(home/'.config')))/'fex-emu'
    paths=[Path('/usr/local/share/fex-emu/Config.json'),local/'Config.json']
    return [owned(path)for path in paths if path.exists()],[[str(path),path.exists()]for path in paths]

def catalog(scan,descriptor,manifest,captures):
    """Reuse the authored static/resume catalogue and strict real code-map reader.

    FEX still admits each requested entry against actual registered source code.
    Static analysis never supplies invented instruction bytes or descriptors.
    """
    _,offsets,_=compilation_catalog(scan,descriptor.get('resume_entries',[]),descriptor.get('excluded_code_slots',[]))
    spans=manifest['sections'];entries={int(value,16)for value in offsets}
    functions={int(line.split('\t')[0],16)for line in(scan/'compilation-functions.tsv').read_text().splitlines()[1:]if line}
    blocks=owned(scan/'entries.tsv');need(blocks.stat().st_size<=256*1024*1024,'FEX static control-flow inventory budget')
    with blocks.open()as rows:
        need(next(rows)=='address\tfunction\tkind\n','FEX scanner block inventory format')
        for line in rows:
            address,source,kind=line.rstrip('\n').split('\t');need(kind in('continuation','direct-target'),'unknown scanner entry kind')
            if int(source,16)in functions:entries.add(int(address,16))
    for path in captures:
        try:
            captured=read(path.with_suffix('.json'))if path.with_suffix('.json').exists()else manifest
            need(all(captured.get(key)==manifest.get(key)for key in('identity_sha256','file_id','image_bytes','sections','eligible_bytes')),'capture compiler input identity differs')
            entries.update(parse_code_map(owned(path).read_bytes(),captured)['blocks'])
        except(ValueError,OSError)as error:print('FEX capture not admitted: '+json.dumps(dict(file=str(path),reason=str(error))),flush=True)
    return sorted(value for value in entries if any(lo<=value<hi for lo,hi in spans))

def product(path):
    """A real content digest pinned to this bounded file identity, not a bool."""
    before=state(path);digest=sha(path);need(state(path)==before,'FEX product changed during registration')
    return dict(state=before,sha256=digest)

def current_product(path,saved):
    try:
        if state(path)==saved.get('state'):return True
        return product(path)['sha256']==saved.get('sha256')
    except(OSError,ValueError,AttributeError):return False

def prepare(executable,arguments,environment,*,stage,project,runner=subprocess.run,runtime_profile=None,reuse_session=False):
    started=time.monotonic();env=dict(environment)
    need(env.get('BB_CPU_BACKEND','fex')=='fex','FEX preparation requires selected FEX backend')
    doc,paths=component(stage,runtime_profile);actual=owned(executable);need(actual in paths['aliases'],'FEX component must be the actual original execution role');paths['cpu']=actual
    boot=owned(arguments[0]);elf=owned(boot.parent/'eboot.elf');patch=option(arguments,'--patches');startup=option(arguments,'--startup',False)
    if runtime_profile is None:
        data=private(env['BB_DATA_DIR']);project=private(project)
        need(data.is_relative_to(project/'state')and boot.is_relative_to(data/'out'),'FEX preparation escaped original isolated launch data')
        root=data/'fex-preparation';root.mkdir(mode=0o700,exist_ok=True);private(root)
        directory=private
    else:
        from launcher_runtime_profile import owned_dir
        data=runtime_profile.data_root
        need(Path(env['BB_DATA_DIR'])==data and Path(project)==runtime_profile.work_root,'formal FEX selected data/work mismatch')
        need(boot==data/'out/boot-linked.bin'and elf==data/'out/eboot.elf'and patch==data/'out/patches.bin'
             and(startup is None or startup.parent==data/'out'),'formal FEX original input roles mismatch')
        runtime_profile.cpu(actual);root=runtime_profile.cpu_preparation_root();need(not(root.stat().st_mode&0o022),'FEX preparation root writable by others')
        def directory(path):
            p=owned_dir(path);need(p.is_relative_to(root)and not(p.stat().st_mode&0o022),'formal FEX output escaped preparation root or writable by others');return p
    effective=root/'effective';effective.mkdir(mode=0o700,exist_ok=True);directory(effective)
    configs,configuration_selection=config_inputs(env)
    dependencies=[owned(Path(stage)/name)for name in doc.get('runtime_dependencies',{})]
    if runtime_profile is not None:dependencies.append(runtime_profile.code(Path(stage)/'bin/gpu-metal/libbbgpu.dylib'))
    context=export_context(arguments,env,1)
    # One source registration for the actual selected closure. Do not overwrite
    # its memo with a second network-only registration (which rehashes the CPU
    # and linked image on every online launch).
    context['network_enabled']=env.get('BB_ONLINE')=='1' and env.get('BB_ARM_ALLOW_NETWORK')=='1'
    if context['network_enabled']:
        module=owned(env['BB_NET_MODULE'])
        if runtime_profile is not None:need(module==stage/'bin/gpu-net/libbbnet.dylib','formal FEX selected network role mismatch')
        dependencies.append(module)
    _,inputs=registered_inputs(effective,(boot,elf,patch,*([startup]if startup else[]),paths['cpu'],paths['scanner'],*dependencies,*configs))
    context['translator_environment']={key:value for key,value in env.items()if key.startswith('FEX_')or key in('BB_ARM_HW_TSO',)}
    context['host']=[os.uname().sysname,os.uname().release,os.uname().version,os.uname().machine]
    context['configuration_selection']=configuration_selection
    schedule=fingerprint(dict(inputs=inputs,context=context));registration=effective/(schedule+'.json')
    selected=None
    try:
        old=read(registration);candidate=Path(old['directory'])
        if candidate.parent==effective and re.fullmatch('[0-9a-f]{32}',candidate.name) and directory(candidate)==candidate and old.get('inputs')==inputs and old.get('context')==context and set(old.get('products',{}))=={'effective.elf','pointers.txt','effective-input.json','cpu-cache-environment.json','cpu-code-manifest.json'} and all(current_product(candidate/name,row)for name,row in old['products'].items()):selected=candidate
    except(OSError,ValueError,KeyError,TypeError):pass
    preparation_env={key:value for key,value in env.items()if not any(word in key.upper()for word in('TOKEN','CREDENTIAL','SECRET','KEYCHAIN'))and key not in('BB_AUTH_FILE','BB_NET_AUTH_FILE','BB_FEX_CACHE_DIRECTORY','BB_FEX_PREPARE_PLAN','BB_FEX_PREPARE_KEY','BB_ARM_CPU_CACHE','BB_ARM_CPU_CODEMAP','BB_RECOMP_LIB')}
    preparation_env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONNOUSERSITE='1')
    process_counts=dict(export=0,scanner=0,compiler=0)
    def run(argv,tag,override=None):
        log=root/(tag+'-'+uuid.uuid4().hex+'.log');actual={**preparation_env,**(override or{})}
        if tag=='compile'and runner is subprocess.run:
            result=stream_cpu_command(argv,environment=actual,log=log,timeout=7200)
        else:
            result=runner(list(map(str,argv)),env=actual,capture_output=True,text=True,timeout=7200 if tag=='compile'else 1800);log.write_text(result.stdout+result.stderr)
        need(result.returncode==0,'FEX '+tag+' failed; completed disk shards retained; diagnostic: '+str(log));return result.stdout
    exported=selected is None
    if exported:
        selected=effective/uuid.uuid4().hex
        run([paths['cpu'],*arguments],'export',dict(BB_FEX_EXPORT_ROOT=str(selected),BB_CPU_EXPORT_ELF=str(elf)))
        process_counts['export']+=1
        names=('effective.elf','pointers.txt','effective-input.json','cpu-cache-environment.json','cpu-code-manifest.json')
        products={name:product(selected/name)for name in names}
        publish(registration,dict(schema=1,directory=str(selected),inputs=inputs,context=context,products=products))
    directory(selected)
    descriptor=read(selected/'effective-input.json');manifest=read(selected/'cpu-code-manifest.json');identity=read(selected/'cpu-cache-environment.json')
    key=identity.get('key_sha256');need(isinstance(key,str)and re.fullmatch('[0-9a-f]{64}',key),'native FEX compatibility identity unavailable')
    generations=root/'products';generations.mkdir(mode=0o700,exist_ok=True);directory(generations);generation=generations/key;generation.mkdir(mode=0o700,exist_ok=True);directory(generation)
    scan=generation/'scan'
    scan_valid=False
    try:
        checked=read(scan/'completed.json');scan_valid=checked.get('effective')==descriptor['effective_identity']and checked.get('scanner_sha256')==doc['scanner']['sha256']and all(current_product(scan/name,checked.get(name,{}))for name in('functions.tsv','entries.tsv'))
    except(OSError,ValueError,KeyError,TypeError):pass
    if not scan_valid:
        attempt=generation/('scan-attempt-'+uuid.uuid4().hex);attempt.mkdir(mode=0o700)
        run([paths['scanner'],selected/'effective.elf',attempt,selected/'pointers.txt'],'scanner');process_counts['scanner']+=1
        # Publish a content registration only after the scanner completed.
        if scan.exists():scan.rename(generation/('scan-previous-'+uuid.uuid4().hex))
        attempt.rename(scan)
        publish(scan/'completed.json',{'schema':1,'effective':descriptor['effective_identity'],'scanner_sha256':doc['scanner']['sha256'],**{name:product(scan/name)for name in('functions.tsv','entries.tsv')}})
    ready=generation/'ready.json';index=generation/'directory.bin';repair=generation/'repair-requests.bin';cached=None;record={};deferred=False
    try:record=read(ready)
    except(OSError,ValueError):pass
    captures=[];capture_states=[]
    if reuse_session and record.get('key')==key and current_product(index,record.get('index',{}))and not repair.exists():
        # The original GPU/supervisor proof already bound this immediate F3
        # selection. New capture provenance is consumed on the next ordinary
        # launch, never falsely marked as compiled by this shortcut.
        cached=record;deferred=True
    else:
        captures=sorted(generation.glob('cpu-code-*.map'));need(len(captures)<=4096,'FEX capture inventory bound')
        capture_states=[state(path)for path in captures]
        if record.get('key')==key and record.get('capture_states')==capture_states and current_product(index,record.get('index',{}))and not repair.exists():cached=record
    if cached is None:
        entries=catalog(scan,descriptor,manifest,captures);need(0<len(entries)<=4000000,'no/bounded statically eligible FEX entries')
        raw=b'BFXPLAN1'+struct.pack('<Q',len(entries))+struct.pack('<'+'Q'*len(entries),*entries)
        plan_hash=hashlib.sha256(raw).hexdigest();plan=generation/('plan-'+plan_hash+'.bin')
        if not plan.exists():plan.write_bytes(raw);plan.chmod(0o600)
        else:need(owned(plan).read_bytes()==raw,'FEX entry plan content differs')
        if record.get('key')==key and record.get('plan_sha256')==plan_hash and current_product(index,record.get('index',{}))and not repair.exists():
            # A new capture file containing only known blocks is provenance,
            # not permission to rerun the native compiler/whole-shard validator.
            cached={**record,'capture_states':capture_states}
        else:
            output=run([paths['cpu'],*arguments],'compile',dict(BB_FEX_PREPARE_PLAN=str(plan),BB_FEX_PREPARE_KEY=key,BB_FEX_CACHE_DIRECTORY=str(generation)))
            process_counts['compiler']+=1
            events=[json.loads(line)for line in output.splitlines()if line.startswith('{')]
            done=[event for event in events if event.get('event')=='done'and event.get('backend')=='fex']
            need(len(done)==1 and done[0].get('guest_executed')is False,'missing real FEX preparation completion')
            if repair.exists():repair.rename(generation/('repair-processed-'+uuid.uuid4().hex+'.bin'))
            cached=dict(schema=1,key=key,capture_states=capture_states,plan_sha256=plan_hash,index=product(index),producer=done[0])
        publish(ready,cached)
    # Runtime repeats ACTUAL image/ABI/host admission, validates one shard and
    # its source pages on demand, then uses the normal invalidatable JIT buffer.
    env.update(BB_FEX_CACHE_DIRECTORY=str(generation),BB_FEX_CAPTURE_ROOT=str(generation),BB_ARM_CPU_CODEMAP='1')
    for name in('BB_FEX_EXPORT_ROOT','BB_CPU_EXPORT_ELF','BB_FEX_PREPARE_PLAN','BB_FEX_PREPARE_KEY','BB_RECOMP_LIB','BB_ARM_CPU_CACHE'):env.pop(name,None)
    return str(paths['cpu']),env,dict(backend='fex',default_execution='precompiled-disk-with-own-jit-fallback',effective_exported=exported,seconds=time.monotonic()-started,processes=process_counts,generation=str(generation),producer=cached['producer'],same_supervisor_reused=bool(reuse_session and not exported and not any(process_counts.values())),capture_deferred=deferred)
