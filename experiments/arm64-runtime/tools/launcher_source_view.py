"""Admit the ORIGINAL mod view without weakening canonical shader readers.

Replay the admitted original merge algorithm into our own temporary link tree,
compare it to the actual final app0, then clonefile each exact canonical source
into an immutable local CoW snapshot. Never hardlink, copy fallback, edit a game
file, replace a mod, or change the actual exec arguments.
"""
from pathlib import Path
import contextlib, ctypes, errno, hashlib, importlib.util, io, json, os, shutil, stat, sys, tempfile, time
from launcher_game_catalog import directory, file_sha, digest, write_json
from incremental_file_hashes import Hashes,replaceable

MAX_FILES=100000
MAX_BYTES=128*1024*1024*1024


class SnapshotUnavailable(OSError):
    pass


def need(value,message):
    if not value:raise ValueError(message)


def clone_metadata(path,size):
    """Structural checks only; final whole-read-set hashes remain authoritative."""
    path=Path(path).absolute()
    need(path==path.resolve(strict=True),'linked CoW source or destination refused')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        info=os.fstat(fd)
        need(stat.S_ISREG(info.st_mode)and info.st_uid==os.getuid()and info.st_size==size,
             'owned regular CoW file with recorded size required')
        return info
    finally:os.close(fd)


def clone(source,target):
    """Real APFS CoW only. A failure never falls through to a large copy."""
    api=ctypes.CDLL('/usr/lib/libSystem.B.dylib',use_errno=True).clonefile
    api.argtypes=(ctypes.c_char_p,ctypes.c_char_p,ctypes.c_int);api.restype=ctypes.c_int
    if api(os.fsencode(source),os.fsencode(target),0):
        code=ctypes.get_errno()
        raise SnapshotUnavailable(code,'APFS source snapshot unavailable: '+os.strerror(code))


def absolute(value,stage):
    path=Path(value)
    # The original shell runs from stage and mods.build_overlay resolves the
    # selected input path. Normalize at this admission boundary, then retain
    # the strict canonical/ownership checks in all actual source readers.
    return(path if path.is_absolute()else stage/path).resolve(strict=False)


def load_original(stage,runtime_profile=None):
    stage=runtime_profile.code(stage)if runtime_profile is not None else directory(stage)
    if runtime_profile is not None:
        from launcher_runtime_profile import code_bytes
        parity=json.loads(code_bytes(stage/'SOURCE-PARITY.json')[0])
        path=stage/'scripts/mods.py';value=code_bytes(path,limit=4*1024*1024,content=False)[1]['sha256']
    else:
        parity=json.loads((stage/'SOURCE-PARITY.json').read_text())
        path=stage/'scripts/mods.py';value,_=file_sha(path)
    need(value==parity['effective_sources']['scripts/mods.py'],'original mod algorithm changed')
    spec=importlib.util.spec_from_file_location('_blood_echoes_admitted_mods_'+value,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module,value


def plain_tree(root,check):
    """The original source tree may not hide links behind an admitted view."""
    root=directory(root);count=0
    for parent,dirs,files in os.walk(root,followlinks=False):
        check()
        for name in dirs:directory(Path(parent)/name)
        for name in files:
            path=Path(parent)/name
            need(path==path.resolve(strict=True),'linked original game source refused')
            s=path.lstat();need(stat.S_ISREG(s.st_mode)and s.st_uid==os.getuid(),'owned original game source')
            count+=1;need(count<=MAX_FILES,'original source file-count budget')


def mapping(root,origins,check):
    root=directory(root);seen=set();files={}
    for parent,dirs,names in os.walk(root,followlinks=True):
        check();actual=Path(parent).resolve(strict=True)
        need(actual==root or actual.is_relative_to(root)or any(actual==origin or actual.is_relative_to(origin)for origin in origins),
             'source-view directory outside original selected sources')
        s=actual.stat();need(stat.S_ISDIR(s.st_mode)and s.st_uid==os.getuid(),'owned source-view directory')
        stamp=(s.st_dev,s.st_ino);need(stamp not in seen,'source-view alias or cycle');seen.add(stamp)
        for name in dirs:
            p=(Path(parent)/name).resolve(strict=True)
            need(p==root or p.is_relative_to(root)or any(p==origin or p.is_relative_to(origin)for origin in origins),'source-view directory escape')
        for name in sorted(names):
            p=Path(parent)/name;source=p.resolve(strict=True)
            need(any(source.is_relative_to(origin)for origin in origins),'source-view file escape')
            info=source.lstat();need(stat.S_ISREG(info.st_mode)and info.st_uid==os.getuid(),'owned regular source-view file')
            relative=str(p.relative_to(root));need(relative not in files,'duplicate virtual source path')
            files[relative]=source;need(len(files)<=MAX_FILES,'source-view file-count budget')
    return files


def recipe(stage,environment,runtime_profile=None):
    stage=runtime_profile.code(stage)if runtime_profile is not None else directory(stage);env=environment
    base=directory(absolute(env.get('BB_GAME_DIR','../CUSA03173'),stage))
    data=absolute(env.get('BB_DATA_DIR','.'),stage)
    mods=absolute(env.get('BB_MODS_DIR',str(data/'mods')),stage)
    config=absolute(env.get('BB_MODS_CONFIG',str(data/'mods.json')),stage)
    config_proof=dict(path=str(config),exists=config.exists())
    if config.exists():config_proof.update(sha256=file_sha(config)[0])
    source=env.get('BB_CHINESE_SOURCE','official')
    need(source in('official','community'),'unknown original language source')
    last=None
    if source=='community':
        need(env.get('BB_CHINESE_LANGUAGE')in('10','11')and env.get('BB_CHINESE_TRANSLATION_DIR'),
             'original community language admission is missing')
        last=directory(absolute(env['BB_CHINESE_TRANSLATION_DIR'],stage))
    return dict(base=base,data=data,mods=mods,config=config,config_proof=config_proof,
                enabled=env.get('BB_MODS_ENABLED','1'),last=last)


def verify_snapshot(view,records,check,hashes=None):
    """A receipt authorizes exactly this file set, not merely its named subset."""
    view=directory(view);plain_tree(view,check)
    actual=mapping(view,[view],check)
    need(set(actual)=={row['path']for row in records},'source snapshot file set changed')
    for parent,dirs,files in os.walk(view):
        need(not(Path(parent).stat().st_mode&0o222),'source snapshot directory must remain readonly')
        for name in files:need(not((Path(parent)/name).stat().st_mode&0o222),'source snapshot file must remain readonly')
    for row in records:
        need((hashes.get(actual[row['path']],check)if hashes is not None else file_sha(actual[row['path']],check))==(row['sha256'],row['bytes']),
             'source snapshot contents changed')


def verify_live_selection(actual,origins,observed,records,stage,environment,selection,check,runtime_profile=None,hashes=None):
    need(mapping(actual,origins,check)==observed,'final app0 mapping changed while snapshotting')
    need(recipe(stage,environment,runtime_profile)==selection,'original mod selection changed while snapshotting')
    # Comparing mappings alone does not catch an earlier file changing after its
    # clone has completed. Revalidate the whole source read-set before adoption.
    if hashes is not None:hashes.verify()
    else:
        for row in records:
            check()
            need(file_sha(Path(row['source']),check)==(row['sha256'],row['bytes']),
                 'selected source contents changed while snapshotting')
    need(mapping(actual,origins,check)==observed,'final app0 mapping changed during source validation')
    need(recipe(stage,environment,runtime_profile)==selection,'original mod selection changed during source validation')


def prepare(*,project,gpu_root,stage,arguments,environment,check=lambda:None,clone_file=clone,runtime_profile=None):
    """Startup-only callable. The caller keeps original app0 for final exec."""
    project=directory(project);gpu=directory(gpu_root);stage=runtime_profile.code(stage)if runtime_profile is not None else directory(stage)
    if runtime_profile is not None:runtime_profile.work(project);runtime_profile.gpu(gpu)
    else:need(gpu.is_relative_to(project/'state')and gpu!=project/'state','isolated source snapshot GPU root')
    actual=directory(Path(arguments[arguments.index('--app0')+1]).absolute())
    module,algorithm=load_original(stage,runtime_profile);selection=recipe(stage,environment,runtime_profile)
    plain_tree(selection['base'],check)
    cache=gpu/'automatic-game-inputs';cache.mkdir(mode=0o700,exist_ok=True);directory(cache)
    views=cache/'source-views';views.mkdir(mode=0o700,exist_ok=True);directory(views)
    origins=[];layers=[];build=module.build_overlay
    # The original main owns layer discovery, disable/order policy, wrapper
    # formats, case handling and Chinese-last precedence. No second mod merger.
    def original_build(game,out,mods):
        origins.append(directory(game))
        for name,path in mods:
            parsed=module.content_root(path);need(parsed is not None,'admitted mod layout')
            origins.append(directory(Path(parsed[0]).resolve(strict=True)))
            layers.append(dict(name=name,root=str(origins[-1])))
        return build(game,out,mods)
    module.build_overlay=original_build
    with tempfile.TemporaryDirectory(prefix='source-view-check-',dir=views)as temporary:
        out=Path(temporary);args=[str(selection['base']),'--out',str(out),'--mods-dir',str(selection['mods']),
                                '--config',str(selection['config']),'--enabled',selection['enabled']]
        if selection['last']is not None:args+=['--last-mod',str(selection['last'])]
        original_argv=sys.argv;stdout=io.StringIO()
        try:
            sys.argv=[str(stage/'scripts/mods.py'),*args]
            with contextlib.redirect_stdout(stdout):module.main()
        finally:sys.argv=original_argv
        lines=stdout.getvalue().splitlines();need(len(lines)==1,'original mod view result envelope')
        expected=directory(Path(lines[0]));wanted=mapping(expected,origins,check)
        observed=mapping(actual,origins,check)
        need(wanted==observed,'final app0 differs from original selected mod view')
        selection_identity=digest(dict(schema=1,role='selected-file-content',algorithm=algorithm,base=str(selection['base'])))
        live_hashes=Hashes(views/('source-metadata-'+selection_identity+'.json'),selection_identity,hasher=file_sha,check=check)
        total=0;records=[]
        for relative,path in sorted(wanted.items()):
            check();value,size=live_hashes.get(path,check);total+=size
            need(total<=MAX_BYTES,'logical source snapshot size budget')
            records.append(dict(path=relative,source=str(path),sha256=value,bytes=size))
        proof=dict(schema=1,original_algorithm=algorithm,base=str(selection['base']),layers=layers,
                   config=selection['config_proof'],enabled=selection['enabled'],files=records)
        identity=digest(proof);generation=views/identity;pointer=views/(identity+'.json')
        cache_problem=None
        if pointer.exists() or pointer.is_symlink():
            try:
                file_sha(pointer);need(pointer.stat().st_size<=4096,'source view pointer budget')
                selected=json.loads(pointer.read_text());name=selected.get('generation')
                need(isinstance(name,str)and Path(name).name==name and(name==identity or name.startswith(identity+'-retry-')),
                     'source view pointer path/identity')
                generation=views/name
            except(OSError,ValueError,KeyError,TypeError):cache_problem='source-view-pointer-invalid'
        ready=generation/'READY.json'
        if cache_problem is None and ready.exists():
            try:
                file_sha(ready);need(ready.stat().st_size<=32*1024*1024,'source snapshot receipt budget')
                previous=json.loads(ready.read_text());need(previous['identity']==identity and previous['proof']==proof,'source snapshot identity changed')
                view=directory(generation/'view')
                snapshot_hashes=Hashes(views/('snapshot-metadata-'+generation.name+'.json'),
                    digest(dict(schema=1,role='readonly-mod-view',identity=identity,view=str(view))),hasher=file_sha,check=check)
                verify_snapshot(view,records,check,snapshot_hashes)
            except(OSError,ValueError,KeyError,TypeError):cache_problem='source-snapshot-invalid'
            else:
                verify_live_selection(actual,origins,observed,records,stage,environment,selection,check,runtime_profile,live_hashes)
                snapshot_hashes.verify();live_hashes.publish();snapshot_hashes.publish()
                return dict(game_root=view,report=dict(status='reused',source_identity=identity,files=len(records),logical_bytes=total,CoW=True,original_app0_preserved=True,
                    source_hashes=live_hashes.report(),snapshot_hashes=snapshot_hashes.report()))
        if generation.exists()or generation.is_symlink():generation=views/(identity+'-retry-'+str(time.time_ns()))
        generation.mkdir(mode=0o700);view=generation/'view';view.mkdir(mode=0o700)
        for row in records:
            check();source=Path(row['source']);destination=view/row['path'];destination.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            before=clone_metadata(source,row['bytes'])
            clone_file(source,destination)
            target=clone_metadata(destination,row['bytes']);after=clone_metadata(source,row['bytes'])
            need(target.st_nlink==1 and (target.st_dev,target.st_ino)not in
                 ((before.st_dev,before.st_ino),(after.st_dev,after.st_ino)),
                 'CoW snapshot must not alias its source')
            destination.chmod(0o400)
        for root,dirs,files in os.walk(view,topdown=False):Path(root).chmod(0o500)
        snapshot_hashes=Hashes(views/('snapshot-metadata-'+generation.name+'.json'),
            digest(dict(schema=1,role='readonly-mod-view',identity=identity,view=str(view))),hasher=file_sha,check=check)
        verify_snapshot(view,records,check,snapshot_hashes)
        verify_live_selection(actual,origins,observed,records,stage,environment,selection,check,runtime_profile,live_hashes)
        live_hashes.publish();snapshot_hashes.publish()
        write_json(generation/'READY.json',dict(schema=1,identity=identity,proof=proof))
        # Existing invalid links/foreign nodes are retained; never follow or
        # delete them to publish a speed hint. A future launch rechecks safely.
        if replaceable(pointer):write_json(pointer,dict(generation=generation.name))
        return dict(game_root=view,report=dict(status='snapshotted',source_identity=identity,files=len(records),logical_bytes=total,CoW=True,original_app0_preserved=True,
            source_hashes=live_hashes.report(),snapshot_hashes=snapshot_hashes.report(),cache_recovery=cache_problem))
