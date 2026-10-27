"""Automatic private static-input preparation at the original launch boundary.

Reuse the existing inventory, GNM and full-resource readers. This module owns
only scheduling, integrity and incremental publication, never shader decoding or
compilation. A static program identity is explicitly NOT a complete PSO recipe.
The child process releases its temporary decompression/index memory before play.
"""
from pathlib import Path
import argparse, ast, hashlib, json, os, signal, stat, subprocess, sys, tempfile, time

SCHEMA = 1
MAX_FILES = 100000
MAX_METADATA = 512 * 1024 * 1024
MAX_LINE = 32 * 1024 * 1024
TOOLS = Path(__file__).resolve().parent
from incremental_file_hashes import Hashes,replaceable
DEPENDENCIES = ('launcher_game_catalog.py', 'game_shader_catalog.py',
                'gnm_shader_metadata.py', 'full_game_catalog.py',
                'static_program_links.py', 'decode_shader_cache.py', 'resource_parse_products.py')


def need(value, message):
    if not value:
        raise ValueError(message)


def directory(path):
    path = Path(path).absolute()
    info = path.lstat()
    need(path == path.resolve(strict=True) and stat.S_ISDIR(info.st_mode)
         and info.st_uid == os.getuid(), 'owned canonical preparation directory')
    return path


def file_sha(path, check=lambda: None):
    path = Path(path).absolute()
    need(path == path.resolve(strict=True), 'linked source or metadata refused')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        need(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid(), 'owned regular source required')
        value = hashlib.sha256()
        for piece in iter(lambda: stream.read(1024 * 1024), b''):
            check()
            value.update(piece)
        after = os.fstat(stream.fileno())
    stamp = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    need(stamp(before) == stamp(after), 'source changed during preparation hash')
    return value.hexdigest(), before.st_size


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def write_json(path, value):
    """Publish only a fully written successful metadata record; retain old files."""
    path = Path(path)
    need(not path.is_symlink(), 'linked metadata publication target')
    fd, temporary = tempfile.mkstemp(prefix='.catalog-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, sort_keys=True, separators=(',', ':'))
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def source_inventory(game_root, check=lambda: None):
    """Content hashes, including additions/removals; stat-only trust is not used."""
    root = directory(game_root)
    sources = []
    for base, dirs, names in os.walk(root, followlinks=False):
        check()
        for name in dirs:
            directory(Path(base) / name)
        for name in sorted(names):
            path = Path(base) / name
            value, size = file_sha(path, check)
            sources.append(dict(path=str(path.relative_to(root)), sha256=value, bytes=size))
            need(len(sources) <= MAX_FILES, 'static input file-count budget')
    need(sources, 'empty game source inventory')
    return sorted(sources, key=lambda row: row['path'])


def scanner_identity(source_root,runtime_profile=None):
    """Bind scanner/parser code, original SELF loader and native stage enum."""
    def code_hash(path):
        from launcher_runtime_profile import code_bytes
        if runtime_profile is not None:runtime_profile.code(path)
        return code_bytes(path,limit=4*1024*1024,content=False)[1]['sha256']
    files = {name: code_hash(TOOLS / name) for name in DEPENDENCIES}
    controller=(TOOLS/'launcher_game_catalog.py').read_bytes()
    need(hashlib.sha256(controller).hexdigest()==files['launcher_game_catalog.py'],'scanner controller changed')
    files['launcher_game_catalog.py']=scanner_body_digest(controller.decode())
    original_loader = TOOLS.parent / 'vendor/blood_echoes/loader/prepare.py'
    files['original-self-loader'] = code_hash(original_loader)
    files['native-runtime-info'] = code_hash(Path(source_root) / 'shader_recompiler/runtime_info.h')
    return digest(files)


# The previous scheduler hashed this whole module into scanner identity. Its
# parsing/publication function bodies and bounds are unchanged: permit only that one
# known identity to reuse validated old indices. Other parsers/enum bytes must
# still match, and a later parsing change disables this migration automatically.
LEGACY_CONTROLLER_SHA='0299bbac7743ff75a5702a8e953ebc612ba55a6c3c90a92657a8d3a18ad69021'
LEGACY_SCANNER_BODY='a21fb83f31fd5d369b98fee99fa66d7736c164ce5377e213d0830bfb0f8795bc'
SCANNER_FUNCTIONS={'need','directory','file_sha','digest','write_json','source_inventory','output_receipt','read_ready','build_static','_build_static'}

SCANNER_CONSTANTS={'SCHEMA','MAX_FILES','MAX_METADATA','MAX_LINE'}

def scanner_body_digest(text):
    # ast.dump's empty-field rendering differs between Python 3.11 and 3.14.
    # Ignore only the added *empty* type_params field, never actual syntax.
    def canonical(value):
        if isinstance(value,ast.AST):
            return [type(value).__name__,[(k,canonical(v))for k,v in ast.iter_fields(value)if not(k=='type_params'and not v)]]
        if isinstance(value,list):return [canonical(v)for v in value]
        if isinstance(value,bytes):return dict(bytes_hex=value.hex())
        return value
    tree=ast.parse(text)
    def selected(n):
        return (isinstance(n,ast.FunctionDef)and n.name in SCANNER_FUNCTIONS)or(isinstance(n,ast.Assign)and any(isinstance(t,ast.Name)and t.id in SCANNER_CONSTANTS for t in n.targets))
    return digest([canonical(n)for n in tree.body if selected(n)])

def legacy_scanners(source_root,runtime_profile=None):
    if scanner_body_digest(Path(__file__).read_text())!=LEGACY_SCANNER_BODY:return []
    def code_hash(path):
        from launcher_runtime_profile import code_bytes
        if runtime_profile is not None:runtime_profile.code(path)
        return code_bytes(path,limit=4*1024*1024,content=False)[1]['sha256']
    files={name:LEGACY_CONTROLLER_SHA if name=='launcher_game_catalog.py'else code_hash(TOOLS/name)for name in DEPENDENCIES}
    files['original-self-loader']=code_hash(TOOLS.parent/'vendor/blood_echoes/loader/prepare.py')
    files['native-runtime-info']=code_hash(Path(source_root)/'shader_recompiler/runtime_info.h')
    return [digest(files)]


def plain_files(root,check=lambda:None):
    root=directory(root);files={}
    for base,dirs,names in os.walk(root,followlinks=False):
        check()
        for name in dirs:directory(Path(base)/name)
        for name in sorted(names):
            p=Path(base)/name
            need(p==p.resolve(strict=True),'linked static source refused')
            files[str(p.relative_to(root))]=p
            need(len(files)<=MAX_FILES,'static input file-count budget')
    need(files,'empty game source inventory')
    return files


def registered_snapshot_hashes(game,cache,check):
    """Reuse the original snapshot's complete byte proof across catalogues.

    A changed/lost history or any receipt mismatch returns to the existing
    source inventory. This does not grant compiler/product admission.
    """
    try:
        if game.name!='view' or game.parent.parent!=cache/'source-views':return None
        raw,_=file_sha(game.parent/'READY.json',check)
        need((game.parent/'READY.json').stat().st_size<=32*1024*1024,'snapshot receipt bound')
        saved=json.loads((game.parent/'READY.json').read_text())
        need(file_sha(game.parent/'READY.json',check)[0]==raw,'snapshot receipt changed')
        identity=saved['identity'];proof=saved['proof']
        need(type(saved['schema'])is int and saved['schema']==1 and identity==digest(proof),'snapshot proof identity')
        need(game.parent.name==identity or game.parent.name.startswith(identity+'-retry-'),'snapshot generation identity')
        rows=proof['files'];need(isinstance(rows,list)and 0<len(rows)<=MAX_FILES,'snapshot member count')
        names=set()
        for row in rows:
            p=Path(row['path']);need(not p.is_absolute()and '..'not in p.parts and str(p)==row['path'] and str(p)not in names,'snapshot member path')
            names.add(str(p))
        hint=Hashes(cache/'source-views'/('snapshot-metadata-'+game.parent.name+'.json'),
            digest(dict(schema=1,role='readonly-mod-view',identity=identity,view=str(game))),
            hasher=file_sha,check=check,watch_roots=[game])
        if not hint.journal_valid:return None
        from launcher_source_view import verify_snapshot
        verify_snapshot(game,rows,check,hint)
        check();hint.verify()
        if not hint.journal_valid:return None
        return hint
    except(OSError,ValueError,KeyError,TypeError):return None


def output_receipt(root):
    files = []
    total = 0
    for path in sorted(Path(root).rglob('*')):
        if path.is_dir():
            directory(path); continue
        if path.name == 'READY.json':
            continue
        value, size = file_sha(path)
        total += size
        need(total <= MAX_METADATA, 'static metadata disk budget')
        files.append(dict(path=str(path.relative_to(root)), sha256=value, bytes=size))
    return files


def read_ready(root, identity):
    path = Path(root) / 'READY.json'
    if not path.exists():
        return None
    file_sha(path)
    need(path.stat().st_size <= 8 * 1024 * 1024, 'catalogue receipt bound')
    row = json.loads(path.read_text())
    need(row['schema'] == SCHEMA and row['identity'] == identity, 'catalogue identity mismatch')
    need(row['files'] == output_receipt(root), 'static metadata changed after publication')
    return row


def build_static(*, game_root, source_root, output, identity, source_files, source_journal=None):
    """Verify the actual read set once, then reuse exact per-resource parsing."""
    from startup_change_journal import query,unchanged
    from resource_parse_products import Products
    import sqlite3
    root=directory(game_root);output=directory(output)
    continuous=(not(root.stat().st_mode&0o222)and unchanged(source_journal,[root]))
    if not continuous:
        source_journal=query([root])
        need(source_inventory(root)==source_files,'game inputs changed before static preparation')
    products=None
    try:
        try:products=Products(output.parent/'resource-products',scanner_identity(source_root))
        except(OSError,ValueError,sqlite3.DatabaseError):pass # Preserve bad DB; original readers remain available.
        return _build_static(game_root=root,source_root=source_root,output=output,identity=identity,
            source_files=source_files,source_journal=source_journal,products=products)
    finally:
        if products is not None:products.close()


def _build_static(*, game_root, source_root, output, identity, source_files,source_journal,products):
    """One worker, existing decoders. Unknown fields remain explicit gaps."""
    import game_shader_catalog as catalog_reader
    import gnm_shader_metadata as gnm
    import static_program_links as links
    from full_game_catalog import Scanner
    root = directory(game_root); output = directory(output)
    need(not any(output.iterdir()), 'fresh catalogue generation required')
    catalog = catalog_reader.inventory(root,products=products,source_files=source_files)
    # The original inventory's SELF offsets and reconstructed ELF offsets are
    # different domains. Reuse the original bounded loader for embedded code.
    for package in catalog['packages']:
        if package['kind'] != 'embedded':
            continue
        proof=dict(kind='embedded-reconstruction-v1',package_sha256=digest(package))
        cached=products.get(proof)if products is not None else None
        if cached is not None:
            package.update(cached);continue
        path = root / package['path']; raw = path.read_bytes()
        need(hashlib.sha256(raw).hexdigest() == package['sha256'], 'embedded source changed')
        elf, _header, _ph, _segments, missing = gnm.checked_elf(raw, package['path'])
        scanned = catalog_reader.orb_metadata(elf)
        decoded=dict(binary_info=scanned['records'], rejected_signature_candidates=scanned['rejected'],
                       elf_sha256=hashlib.sha256(elf).hexdigest(), offset_domain='reconstructed-elf-bytes',
                       compressed_segments_not_decoded=False, missing_optional_program_headers=missing)
        package.update(decoded)
        if products is not None:products.put(proof,decoded)
        del raw, elf
    catalog['programs'] = [dict(package=p['path'], entry=e.get('name'), debug=p['debug'], **r)
                          for p in catalog['packages'] for e in
                          (p.get('entries', []) if p['kind'] == 'shader' else [p] if p['kind'] == 'embedded' else [])
                          for r in e.get('binary_info', [])]
    catalog['summary'] = dict(packages=len(catalog['packages']), program_records=len(catalog['programs']),
                            embedded_offset_domain='reconstructed-elf-bytes', complete_psos=0)
    catalog_path = output / 'catalog.json'; write_json(catalog_path, catalog)
    gnm.census(root, catalog_path, output / 'gnm',products=products,source_files=source_files)
    gap_path = output / 'gaps.jsonl'
    accepted = output / 'accepted-headers.jsonl'
    with accepted.open('x') as good, gap_path.open('x') as gaps:
        for row in links.rows(output / 'gnm/headers.jsonl'):
            if row.get('rejected') or row.get('unassociated_programs'):
                gnm.write_row(gaps, dict(kind='unresolved-static-header', source=row))
            else:
                gnm.write_row(good, row)
        good.flush(); os.fsync(good.fileno())
        records, _entries, summary = links.collect(accepted, catalog, Path(source_root) / 'shader_recompiler/runtime_info.h')
        with (output / 'static-program-index.jsonl').open('x') as stream:
            for row in records:
                gnm.write_row(stream, row)
                gnm.write_row(gaps, dict(kind='dynamic-compile-input-required', identity=row['identity'],
                    missing=['draw-adjusted-runtime-info', 'resource-specializations', 'complete-pipeline-state'],
                    capture='normal-runtime-owned-input', inferred=False))
    full = output / 'resources'; full.mkdir(mode=0o700)
    scanner = Scanner(root, catalog_path, full, TOOLS.parent,products=products,source_files=source_files)
    try:
        resource_summary = scanner.run()
    finally:
        for stream in scanner.streams.values():
            if not stream.closed:
                stream.close()
    # Do not commit a generation if any input was changed while decoding.
    from startup_change_journal import unchanged
    if not unchanged(source_journal,[root]):
        need(source_inventory(root) == source_files, 'game inputs changed during static preparation')
    receipt = dict(schema=SCHEMA, identity=identity, status='indexed',
                   static=summary, resources=resource_summary, files=output_receipt(output),
                   resource_products=products.report()if products is not None else dict(status='unavailable',artifact_authority=False),
                   complete_pso_claim=False, game_started=False, formal_data_modified=False)
    write_json(output / 'READY.json', receipt)
    return receipt


def prepare(*, project, gpu_root, game_root, source_root, cohort, check=lambda: None, runner=None,runtime_profile=None):
    """Default startup entry. Run expensive decoding in one reclaimable child."""
    started = time.monotonic(); project = directory(project); gpu = directory(gpu_root)
    from launcher_runtime_profile import work
    state=work(project,runtime_profile)
    if runtime_profile is not None:runtime_profile.gpu(gpu)
    else:need(gpu.is_relative_to(project / 'state') and gpu != project / 'state', 'isolated selected GPU root required')
    game = directory(game_root)
    if runtime_profile is not None:runtime_profile.source(game)
    else:need(game.is_relative_to(project.parent) and not gpu.is_relative_to(game), 'isolated actual app0 required')
    cache = gpu / 'automatic-game-inputs'; cache.mkdir(mode=0o700, exist_ok=True); directory(cache)
    hashes=Hashes(cache/('catalogue-source-metadata-'+digest(str(game))+'.json'),
        digest(dict(schema=1,role='static-source-inventory',root=str(game))),hasher=file_sha,check=check,watch_roots=[game])
    if not hashes.journal_valid:
        registered=registered_snapshot_hashes(game,cache,check)
        if registered is not None:hashes=registered
    if hashes.journal_valid:
        selected_files={str(Path(name).relative_to(game)):Path(name)for name in hashes.previous}
        need(selected_files,'empty game source inventory')
    else:selected_files=plain_files(game,check)
    sources=[]
    for name,path in sorted(selected_files.items()):
        value,size=hashes.get(path,check);sources.append(dict(path=name,sha256=value,bytes=size))
    scan_identity = scanner_identity(source_root,runtime_profile)
    scheduler_identity=scan_identity
    identity = digest(dict(schema=SCHEMA, source_files=sources, scanner=scan_identity))
    generation = cache / identity
    current = cache / 'current.json'
    cache_problem=None;compiler_game=game
    try:
        if current.exists()or current.is_symlink():
            file_sha(current)
            need(current.stat().st_size <= 4096, 'static generation pointer budget')
            selected = json.loads(current.read_text())
            name = selected.get('generation')
            need(isinstance(name, str) and Path(name).name == name, 'static generation pointer path')
            selected_identity=selected.get('identity')
            if selected_identity!=identity:
                for legacy in legacy_scanners(source_root,runtime_profile):
                    legacy_identity=digest(dict(schema=SCHEMA,source_files=sources,scanner=legacy))
                    if legacy_identity==selected_identity:
                        identity=legacy_identity;scan_identity=legacy;generation=cache/identity;break
            if selected_identity == identity:
                need(name == identity or name.startswith(identity + '-retry-'), 'static generation pointer identity')
                generation = cache / name
        ready = read_ready(generation, identity) if generation.exists() else None
        if ready is not None:
            # Catalogue products deliberately exclude the source-view location.
            # Preserve their original physical provenance, but only reuse that
            # view if its COMPLETE readonly byte set equals today's selection.
            # Never relabel a catalog's game_root or fabricate a source alias.
            catalog_root=directory(json.loads((generation/'catalog.json').read_text())['game_root'])
            if catalog_root!=game:
                retained=registered_snapshot_hashes(catalog_root,cache,check)
                need(retained is not None,'retained catalogue source proof unavailable')
                previous=[dict(path=str(Path(name).relative_to(catalog_root)),sha256=row['sha256'],bytes=row['bytes'])for name,row in sorted(retained.seen.items())]
                need(previous==sources,'retained catalogue source bytes differ from selected input')
                retained.verify();compiler_game=catalog_root
    except(OSError,ValueError,KeyError,TypeError):
        ready=None;cache_problem='static-metadata-invalid'
    reused = ready is not None
    if ready is None:
        # An interrupted generation is retained for inspection, never trusted or
        # overwritten as a completed generation. No previous cache is deleted.
        if generation.exists():
            generation = cache / (identity + '-retry-' + str(time.time_ns()))
        generation.mkdir(mode=0o700)
        request = state / ('catalogue-request-' + str(time.time_ns()) + '.json')
        write_json(request, dict(game_root=str(game), source_root=str(source_root), output=str(generation),
                                 identity=identity, source_files=sources,source_journal=hashes.journal_token))
        if runner is not None:
            runner(request, check)
        else:
            log_path = state / ('catalogue-worker-' + str(time.time_ns()) + '.log')
            with log_path.open('x') as log:
                proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--request', str(request)],
                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                    env={k: v for k, v in os.environ.items() if not k.startswith(('DYLD_', 'BB_'))})
                try:
                    while proc.poll() is None:
                        check()
                        need(time.monotonic() - started <= 1800, 'static preparation time budget')
                        need(log_path.stat().st_size <= 8 * 1024 * 1024, 'static preparation log budget')
                        try: proc.wait(timeout=.1)
                        except subprocess.TimeoutExpired: pass
                    need(proc.returncode == 0, 'static preparation worker failed; private log: ' + str(log_path))
                finally:
                    if proc.poll() is None:
                        os.killpg(proc.pid, signal.SIGTERM)
                        try: proc.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            os.killpg(proc.pid, signal.SIGKILL); proc.wait()
        ready = read_ready(generation, identity)
        need(ready is not None, 'worker did not publish complete static metadata')
        if replaceable(current):write_json(cache / 'current.json', dict(identity=identity, generation=generation.name))
    check();hashes.verify()
    if not hashes.journal_valid:need(plain_files(game,check)==selected_files,'static source file set changed during preparation')
    hashes.publish()
    return dict(catalog=generation / 'catalog.json', index=generation / 'static-program-index.jsonl',
                game_root=compiler_game, gpu_root=gpu, source_root=Path(source_root), cache_root=cache,
                identity=identity, cohort=cohort, generation=generation,
                report=dict(status='reused' if reused else 'indexed', identity=identity,
                            source_files=len(sources), scanner_identity=scan_identity,scheduler_identity=scheduler_identity,
                            source_hashes=hashes.report(),cache_recovery=cache_problem,
                            selected_game_root=str(game),compiler_game_root=str(compiler_game),
                            source_view_reused_by_complete_byte_identity=compiler_game!=game,
                            seconds=time.monotonic() - started, ready=ready,
                            full_pso_claim=False, default_enabled=True, formal_data_modified=False))


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--request', required=True, type=Path)
    args = parser.parse_args(); file_sha(args.request)
    need(args.request.stat().st_size <= 32 * 1024 * 1024, 'static worker request budget')
    request = json.loads(args.request.read_text())
    build_static(**request)


if __name__ == '__main__':
    main()
