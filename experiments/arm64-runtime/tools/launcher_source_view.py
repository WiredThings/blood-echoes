"""Admit the ORIGINAL mod view without weakening canonical shader readers.

Replay the admitted original merge algorithm into our own temporary link tree,
compare it to the actual final app0, then clonefile each exact canonical source
into an immutable local CoW snapshot. Never hardlink, copy fallback, edit a game
file, replace a mod, or change the actual exec arguments.
"""
from pathlib import Path
import contextlib, ctypes, errno, hashlib, importlib.util, io, json, os, shutil, stat, sys, tempfile, time
from launcher_game_catalog import directory, file_sha, digest, write_json
from incremental_file_hashes import Hashes,replaceable,stat_stamp
from bisect import bisect_left

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

# Only this real kernel-backed function enables the sealed-source optimization.
# Custom/injected clone callbacks retain full destination byte verification.
KERNEL_CLONE=clone


def previous_snapshot(views,check):
    """A retained fully verified readonly view, never a guessed file digest."""
    from game_pso_owned_audit import checked_bytes
    try:
        saved=json.loads(checked_bytes(views/'last-verified-view.json',limit=4096)[0])
        need(type(saved['schema'])is int and saved['schema']==1,'retained view schema')
        name=saved['generation'];identity=saved['identity']
        need(isinstance(identity,str)and len(identity)==64 and all(c in '0123456789abcdef'for c in identity),'retained view identity')
        need(isinstance(name,str)and Path(name).name==name and(name==identity or name.startswith(identity+'-retry-')),'retained view generation')
        generation=directory(views/name);view=directory(generation/'view')
        raw,_=checked_bytes(generation/'READY.json',limit=32*1024*1024,expected=saved['ready_sha256']);ready=json.loads(raw)
        need(type(ready['schema'])is int and ready['schema']==1 and ready['identity']==identity and digest(ready['proof'])==identity,'retained complete source proof')
        records=ready['proof']['files'];need(isinstance(records,list)and 0<len(records)<=MAX_FILES,'retained source count')
        for row in records:
            relative=Path(row['path']);need(not relative.is_absolute()and '..'not in relative.parts and str(relative)==row['path'],'retained source relative path')
        hashes=Hashes(views/('snapshot-metadata-'+name+'.json'),
            digest(dict(schema=1,role='readonly-mod-view',identity=identity,view=str(view))),
            hasher=file_sha,check=check,watch_roots=[view])
        if not hashes.journal_valid:return None
        verify_snapshot(view,records,check,hashes)
        hashes.verify()
        if not hashes.journal_valid:return None
        rows={}
        for row in records:
            source=view/row['path'];entry=hashes.seen[str(source)]
            rows[(row['source'],row['sha256'],row['bytes'])]=(source,entry)
        return dict(view=view,rows=rows)
    except(OSError,ValueError,KeyError,TypeError):return None


def sealed_clone(source,target,entry):
    """Copy the exact already verified inode with fclonefileat, no byte reread.

    Darwin guarantees atomic CoW data equality for a successful clone (clonefile
    system manual). Its source is a pinned 0400 owned snapshot, not a mutable
    game file or untrusted copy program. Final target membership, readonly mode,
    metadata and live selection checks still run. Never labels copied bytes as
    newly hashed, or grants any shader/function/pipeline admission.
    """
    source=Path(source);target=Path(target)
    need(source==source.resolve(strict=True)and target.parent==target.parent.resolve(strict=True),'canonical sealed clone paths')
    fd=os.open(source,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC);parent=None
    try:
        parent=os.open(target.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
        before=os.fstat(fd)
        if stat_stamp(before)!=entry['stamp']or stat.S_IMODE(before.st_mode)!=0o400 or before.st_nlink!=1:return None
        need(stat.S_ISREG(before.st_mode)and before.st_uid==os.getuid(),'owned sealed clone source')
        api=ctypes.CDLL('/usr/lib/libSystem.B.dylib',use_errno=True).fclonefileat
        api.argtypes=(ctypes.c_int,ctypes.c_int,ctypes.c_char_p,ctypes.c_int);api.restype=ctypes.c_int
        if api(fd,parent,os.fsencode(target.name),0):
            code=ctypes.get_errno();raise SnapshotUnavailable(code,'sealed APFS clone failed: '+os.strerror(code))
        need(stat_stamp(os.fstat(fd))==entry['stamp'],'sealed source changed during kernel clone')
        dest=clone_metadata(target,entry['bytes'])
        need(dest.st_nlink==1 and(dest.st_dev,dest.st_ino)!=(before.st_dev,before.st_ino)
             and stat.S_IMODE(dest.st_mode)==0o400 and dest.st_mtime_ns==before.st_mtime_ns,'sealed clone inode/attributes differ')
        return dict(stamp=stat_stamp(dest),sha256=entry['sha256'],bytes=entry['bytes'])
    finally:
        if parent is not None:os.close(parent)
        os.close(fd)


def remember_snapshot(views,view,identity,check):
    path=views/'last-verified-view.json'
    if replaceable(path):write_json(path,dict(schema=1,generation=view.parent.name,identity=identity,
        ready_sha256=file_sha(view.parent/'READY.json',check)[0]))


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


class RegisteredSources:
    """Keep the base inventory independent of the selected Mod combination.

    An unchanged base journal admits its previously complete file set, including
    files hidden by Mods. A lost journal returns to the original complete base
    check. Each Mod has its own hash history; original mods.py still decides
    membership, precedence and case. These hints never admit a Metal product.
    """
    def __init__(self,views,base,origins,algorithm,check):
        self.base=base;self.check=check;self.hashes={}
        for root in dict.fromkeys([base,*origins]):
            identity=digest(dict(schema=1,role='independent-source-origin',root=str(root),algorithm=algorithm))
            self.hashes[root]=Hashes(views/('origin-metadata-'+identity+'.json'),identity,
                hasher=file_sha,check=check,watch_roots=[root])
        hashes=self.hashes[base];self.base_registered=hashes.journal_valid
        if self.base_registered:
            files={}
            for name in hashes.previous:
                p=Path(name);need(p.is_relative_to(base)and p!=base,'registered base inventory escape')
                relative=p.relative_to(base)
                need('..'not in relative.parts and '.'not in relative.parts,'registered base inventory path')
                files[str(relative)]=p
            need(0<len(files)<=MAX_FILES,'registered base inventory size')
        else:
            plain_tree(base,check);files=mapping(base,[base],check)
        self.files=files;self.paths=sorted(files)
        # Preserve ALL base entries, even when currently covered by a Mod.
        for p in files.values():hashes.get(p,check)
        for root,hint in self.hashes.items():
            if root!=base and hint.journal_valid:
                for name in hint.previous:
                    need(Path(name).is_relative_to(root),'registered Mod hash escape')
                    hint.get(name,check)

    def selected(self,root,shape,origins):
        if root==self.base:return dict(self.files)
        result={}
        def add(relative,source):
            need(relative not in result,'duplicate virtual source path')
            result[relative]=source;need(len(result)<=MAX_FILES,'source-view file-count budget')
        for row in shape:
            self.check()
            if row[1]!='link':continue
            relative,target=row[0],Path(row[2])
            if target.is_relative_to(self.base):
                key=str(target.relative_to(self.base))
                if key in self.files:add(relative,target);continue
                # Only a base directory may be expanded from the registered
                # complete base inventory. Unknown targets use the original
                # strict mapper; no guessed descriptor or fabricated member.
                prefix=key+'/' if key!='.'else ''
                start=bisect_left(self.paths,prefix);found=False
                for name in self.paths[start:]:
                    if not name.startswith(prefix):break
                    found=True;add(str(Path(relative)/name[len(prefix):]),self.files[name])
                if found:continue
                # An empty directory contributes no leaves. Verify its kind.
                if target.is_dir():continue
                raise ValueError('registered base target missing')
            info=target.lstat()
            if not stat.S_ISREG(info.st_mode):return mapping(root,origins,self.check)
            need(info.st_uid==os.getuid(),'owned regular Mod source required')
            add(relative,target)
        return result

    def get(self,path,check):
        # Most specific origin prevents nested Mod roots from borrowing a
        # broader root's incomplete journal/hash registration.
        roots=[p for p in self.hashes if path.is_relative_to(p)]
        need(bool(roots),'selected source has no registered origin')
        return self.hashes[max(roots,key=lambda p:len(p.parts))].get(path,check)

    def verify(self):
        for hint in self.hashes.values():hint.verify()
        if not self.hashes[self.base].journal_valid:
            # Stamp equality on known leaves cannot detect an added/renamed
            # file. When history cannot prove membership, retain the original
            # complete plain-tree and mapping checks as well.
            plain_tree(self.base,self.check)
            need(mapping(self.base,[self.base],self.check)==self.files,
                 'selected source contents changed: base file set changed')

    def publish(self):
        self.verify()
        for hint in self.hashes.values():hint.publish()

    def report(self):
        reports=[h.report()for h in self.hashes.values()]
        return dict(receipt='independent-origins',base_receipt=self.hashes[self.base].receipt_status,base_inventory_reused=self.base_registered,
            hashed_files=sum(r['hashed_files']for r in reports),hashed_bytes=sum(r['hashed_bytes']for r in reports),
            reused_files=sum(r['reused_files']for r in reports),metadata_only=all(r['metadata_only']for r in reports),
            journal_reused=all(r['journal_reused']for r in reports))


def overlay_shape(root,base,origins,check):
    """Compare the original sparse link tree, never descend into base links.

    Full source registration remains authoritative. This only proves that the
    new ephemeral app0 selects the SAME registered sources as the original
    merger; continuous history separately proves those sources did not change.
    """
    root=directory(root)
    if root==base:return [('base',str(base))]
    rows=[]
    for parent,dirs,names in os.walk(root,followlinks=False):
        check();folder=directory(parent)
        for name in sorted(dirs+names):
            p=folder/name;info=p.lstat();relative=str(p.relative_to(root))
            if stat.S_ISLNK(info.st_mode):
                target=p.resolve(strict=True)
                need(any(target==origin or target.is_relative_to(origin)for origin in origins),'source-view shape escape')
                rows.append((relative,'link',str(target)))
            else:
                need(stat.S_ISDIR(info.st_mode)and info.st_uid==os.getuid(),'original overlay has a non-link leaf')
                rows.append((relative,'directory'))
            need(len(rows)<=MAX_FILES,'source-view shape budget')
    return sorted(rows)


def selection_binding(selection,algorithm,layers,shape):
    # Deliberately no temporary overlay path or entire launcher/UI identity.
    return dict(schema=1,original_algorithm=algorithm,
        selection=json.loads(json.dumps(selection,default=str,sort_keys=True)),
        layers=layers,overlay_shape_sha256=digest(shape))


def reuse_selection(views,bound,origins,check,verify_current=lambda:None):
    """Unchanged registered selection; uncertainty returns to full admission."""
    from game_pso_owned_audit import checked_bytes
    from startup_change_journal import unchanged
    path=views/('selection-'+digest(bound)+'.json')
    try:
        info=path.lstat()
        need(info.st_nlink==1 and stat.S_IMODE(info.st_mode)==0o600,'selection receipt mode/link identity')
        check();raw,_=checked_bytes(path,limit=16384);saved=json.loads(raw)
        need(set(saved)=={'schema','binding','generation','ready_sha256','identity','files','logical_bytes','journal'},'selection receipt fields')
        need(type(saved['schema'])is int and saved['schema']==1 and saved['binding']==bound,'selection receipt binding')
        name=saved['generation'];identity=saved['identity']
        need(isinstance(name,str)and Path(name).name==name and isinstance(identity,str)and len(identity)==64
            and all(c in '0123456789abcdef'for c in identity)
            and(name==identity or name.startswith(identity+'-retry-')),'registered selection generation')
        need(type(saved['files'])is int and 0<saved['files']<=MAX_FILES and type(saved['logical_bytes'])is int
            and 0<=saved['logical_bytes']<=MAX_BYTES,'registered selection bounds')
        generation=directory(views/name);view=directory(generation/'view')
        need(stat.S_IMODE(view.stat().st_mode)==0o500,'registered readonly view')
        roots=list(dict.fromkeys([*origins,view]))
        if not unchanged(saved['journal'],roots):return None
        need(file_sha(generation/'READY.json',check)[0]==saved['ready_sha256'],'registered selection receipt changed')
        verify_current();check()
        if not unchanged(saved['journal'],roots):return None
        counts=dict(hashed_files=0,hashed_bytes=0,reused_files=saved['files'],metadata_only=True,journal_reused=True)
        return dict(game_root=view,report=dict(status='reused',reuse_mode='registered-selection',source_identity=identity,
            files=saved['files'],logical_bytes=saved['logical_bytes'],CoW=True,original_app0_preserved=True,
            source_hashes=counts,snapshot_hashes=dict(counts),full_tree_enumerated=False))
    except(OSError,ValueError,KeyError,TypeError):return None


def publish_selection(views,bound,origins,view,identity,records,total,check,verify):
    """Publish only AFTER the existing complete source/view validation.

    Anchor history before a final original validation; never put a cursor after
    an unobserved mutation. A missing/dropped journal never grants reuse.
    """
    from startup_change_journal import query,unchanged
    path=views/('selection-'+digest(bound)+'.json')
    if not replaceable(path):return False
    roots=list(dict.fromkeys([*origins,view]));token=query(roots)
    if token is None:return False
    verify();check()
    ready=file_sha(view.parent/'READY.json',check)[0]
    if not unchanged(token,roots):return False
    saved=dict(schema=1,binding=bound,generation=view.parent.name,ready_sha256=ready,
        identity=identity,files=len(records),logical_bytes=total,journal=token)
    if len(json.dumps(saved,separators=(',',':')).encode())>16384:return False
    write_json(path,saved);return True


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
    view=directory(view)
    if hashes is not None and hashes.journal_valid:
        need(not(view.stat().st_mode&0o222),'source snapshot directory must remain readonly')
        need(set(hashes.previous)=={str(view/row['path'])for row in records},'source snapshot file set changed')
        for row in records:
            need(hashes.previous[str(view/row['path'])]['stamp'][5]==1,'source snapshot file has an alias')
            need(hashes.get(view/row['path'],check)==(row['sha256'],row['bytes']),'source snapshot contents changed')
        hashes.verify()
        return
    plain_tree(view,check)
    actual=mapping(view,[view],check)
    need(set(actual)=={row['path']for row in records},'source snapshot file set changed')
    for parent,dirs,files in os.walk(view):
        need(not(Path(parent).stat().st_mode&0o222),'source snapshot directory must remain readonly')
        for name in files:
            info=(Path(parent)/name).stat()
            need(not(info.st_mode&0o222),'source snapshot file must remain readonly')
            need(info.st_nlink==1,'source snapshot file has an alias')
    for row in records:
        need((hashes.get(actual[row['path']],check)if hashes is not None else file_sha(actual[row['path']],check))==(row['sha256'],row['bytes']),
             'source snapshot contents changed')
    if hashes is not None:
        # One complete real membership/byte-or-kernel-clone validation is
        # sufficient. Keep its BEFORE cursor; do not repeat the same full tree
        # in later publication helpers when history remained continuous.
        hashes.verify()
        from startup_change_journal import unchanged
        if hashes.journal_token is not None and unchanged(hashes.journal_token,[view]):
            hashes.previous=dict(hashes.seen);hashes.journal_valid=True


def verify_live_selection(actual,origins,observed,records,stage,environment,selection,check,runtime_profile=None,hashes=None,mapping_fn=None):
    current=lambda:mapping(actual,origins,check)if mapping_fn is None else mapping_fn()
    need(current()==observed,'final app0 mapping changed while snapshotting')
    need(recipe(stage,environment,runtime_profile)==selection,'original mod selection changed while snapshotting')
    # Comparing mappings alone does not catch an earlier file changing after its
    # clone has completed. Revalidate the whole source read-set before adoption.
    if hashes is not None:hashes.verify()
    else:
        for row in records:
            check()
            need(file_sha(Path(row['source']),check)==(row['sha256'],row['bytes']),
                 'selected source contents changed while snapshotting')
    need(current()==observed,'final app0 mapping changed during source validation')
    need(recipe(stage,environment,runtime_profile)==selection,'original mod selection changed during source validation')


def prepare(*,project,gpu_root,stage,arguments,environment,check=lambda:None,clone_file=clone,runtime_profile=None):
    """Startup-only callable. The caller keeps original app0 for final exec."""
    project=directory(project);gpu=directory(gpu_root);stage=runtime_profile.code(stage)if runtime_profile is not None else directory(stage)
    if runtime_profile is not None:runtime_profile.work(project);runtime_profile.gpu(gpu)
    else:need(gpu.is_relative_to(project/'state')and gpu!=project/'state','isolated source snapshot GPU root')
    actual=directory(Path(arguments[arguments.index('--app0')+1]).absolute())
    module,algorithm=load_original(stage,runtime_profile);selection=recipe(stage,environment,runtime_profile)
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
        expected=directory(Path(lines[0]))
        expected_shape=overlay_shape(expected,selection['base'],origins,check)
        actual_shape=overlay_shape(actual,selection['base'],origins,check)
        need(expected_shape==actual_shape,'final app0 differs from original selected mod view')
        bound=selection_binding(selection,algorithm,layers,expected_shape)
        def verify_current():
            need(recipe(stage,environment,runtime_profile)==selection,'original mod selection changed during registered reuse')
            need(overlay_shape(actual,selection['base'],origins,check)==actual_shape,'final app0 shape changed during registered reuse')
        reused=reuse_selection(views,bound,origins,check,verify_current)
        if reused is not None:
            remember_snapshot(views,reused['game_root'],reused['report']['source_identity'],check)
            return reused
        live_hashes=RegisteredSources(views,selection['base'],origins,algorithm,check)
        def selected_mapping():
            shape=overlay_shape(actual,selection['base'],origins,check)
            return live_hashes.selected(actual,shape,origins)
        wanted=live_hashes.selected(expected,expected_shape,origins)
        observed=selected_mapping()
        need(wanted==observed,'final app0 differs from original selected mod view')
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
                    digest(dict(schema=1,role='readonly-mod-view',identity=identity,view=str(view))),hasher=file_sha,check=check,watch_roots=[view])
                verify_snapshot(view,records,check,snapshot_hashes)
            except(OSError,ValueError,KeyError,TypeError):cache_problem='source-snapshot-invalid'
            else:
                verify_live_selection(actual,origins,observed,records,stage,environment,selection,check,runtime_profile,live_hashes,selected_mapping)
                snapshot_hashes.verify();live_hashes.publish();snapshot_hashes.publish()
                def verify_registered():
                    verify_snapshot(view,records,check,snapshot_hashes)
                    verify_live_selection(actual,origins,observed,records,stage,environment,selection,check,runtime_profile,live_hashes,selected_mapping)
                publish_selection(views,bound,origins,view,identity,records,total,check,verify_registered)
                remember_snapshot(views,view,identity,check)
                return dict(game_root=view,report=dict(status='reused',source_identity=identity,files=len(records),logical_bytes=total,CoW=True,original_app0_preserved=True,
                    source_hashes=live_hashes.report(),snapshot_hashes=snapshot_hashes.report()))
        if generation.exists()or generation.is_symlink():generation=views/(identity+'-retry-'+str(time.time_ns()))
        retained=previous_snapshot(views,check)if clone_file is KERNEL_CLONE else None
        generation.mkdir(mode=0o700);view=generation/'view';view.mkdir(mode=0o700)
        cloned_proofs={}
        for row in records:
            check();source=Path(row['source']);destination=view/row['path'];destination.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            previous=retained['rows'].get((row['source'],row['sha256'],row['bytes']))if retained is not None else None
            if previous is not None:
                copied=sealed_clone(previous[0],destination,previous[1])
                if copied is not None:
                    cloned_proofs[str(destination)]=copied
                    continue
            before=clone_metadata(source,row['bytes'])
            clone_file(source,destination)
            target=clone_metadata(destination,row['bytes']);after=clone_metadata(source,row['bytes'])
            need(target.st_nlink==1 and (target.st_dev,target.st_ino)not in
                 ((before.st_dev,before.st_ino),(after.st_dev,after.st_ino)),
                 'CoW snapshot must not alias its source')
            destination.chmod(0o400)
        for root,dirs,files in os.walk(view,topdown=False):Path(root).chmod(0o500)
        snapshot_hashes=Hashes(views/('snapshot-metadata-'+generation.name+'.json'),
            digest(dict(schema=1,role='readonly-mod-view',identity=identity,view=str(view))),hasher=file_sha,check=check,watch_roots=[view])
        # A successful fclonefileat proves data equality with its exact pinned,
        # previously hashed readonly inode. Carry that real byte proof, not a
        # newly claimed hash. get()/verify() still reject later target mutation;
        # custom clone callbacks above cannot produce these certificates.
        snapshot_hashes.seen.update(cloned_proofs);snapshot_hashes.reused_files+=len(cloned_proofs)
        verify_snapshot(view,records,check,snapshot_hashes)
        verify_live_selection(actual,origins,observed,records,stage,environment,selection,check,runtime_profile,live_hashes,selected_mapping)
        live_hashes.publish();snapshot_hashes.publish()
        write_json(generation/'READY.json',dict(schema=1,identity=identity,proof=proof,
            construction=dict(sealed_kernel_clones=len(cloned_proofs),kernel='fclonefileat'if cloned_proofs else None)))
        # Existing invalid links/foreign nodes are retained; never follow or
        # delete them to publish a speed hint. A future launch rechecks safely.
        if replaceable(pointer):write_json(pointer,dict(generation=generation.name))
        def verify_registered():
            verify_snapshot(view,records,check,snapshot_hashes)
            verify_live_selection(actual,origins,observed,records,stage,environment,selection,check,runtime_profile,live_hashes,selected_mapping)
        publish_selection(views,bound,origins,view,identity,records,total,check,verify_registered)
        remember_snapshot(views,view,identity,check)
        return dict(game_root=view,report=dict(status='snapshotted',source_identity=identity,files=len(records),logical_bytes=total,CoW=True,original_app0_preserved=True,
            source_hashes=live_hashes.report(),snapshot_hashes=snapshot_hashes.report(),cache_recovery=cache_problem,
            sealed_kernel_clones=len(cloned_proofs)))
