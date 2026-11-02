"""Production FEX code-role declaration; no game, cache or user-data access."""
from pathlib import Path
import hashlib,json,os,re

CPU=('bin/bb-probe-metal','bin/Bloodborne Game.app/Contents/MacOS/bb-probe-metal')
SCANNER='bin/fex-disk-tools/bbcpu-scan'
def need(value,reason):
    if not value:raise ValueError(reason)

def formal_roles(stage,native,reader):
    """reader(relative, expected_sha, maximum) retains the caller's code gate."""
    selected=native.get('fex_preparation')
    present=os.path.lexists(Path(stage)/'FEX-COMPONENT.json')
    if selected is None and not present:return None,{}
    need(selected=='auto'and present,'declared automatic FEX component missing')
    want=native.get('fex_component_sha256')
    need(isinstance(want,str)and re.fullmatch('[0-9a-f]{64}',want),'FEX descriptor digest required')
    doc=json.loads(reader('FEX-COMPONENT.json',want,1024*1024))
    need(doc.get('schema')==1 and doc.get('development_only')is False and doc.get('backend')=='fex'
         and doc.get('automatic_default')is True,'formal automatic FEX declaration required')
    need(native.get('production')is True and native.get('game_runtime')=='fex-arm64','formal FEX runtime required')
    aliases=doc.get('cpu_aliases')
    need(isinstance(aliases,list)and len(aliases)==1,'exact FEX executable aliases required')
    declared=[doc.get('cpu'),aliases[0],doc.get('scanner')];roles={}
    for row,name in zip(declared,(*CPU,SCANNER)):
        need(isinstance(row,dict)and set(row)=={'path','sha256'}and row['path']==name,'FEX actual role path mismatch')
        sha=row['sha256'];need(isinstance(sha,str)and re.fullmatch('[0-9a-f]{64}',sha),'FEX role digest required')
        roles[name]=sha
    deps=doc.get('runtime_dependencies')
    need(isinstance(deps,dict)and len(deps)<=64,'bounded FEX dependency closure required')
    for name,sha in deps.items():
        p=Path(name)
        need(not p.is_absolute()and '..'not in p.parts and len(p.parts)==3
             and p.parts[:2]==('deps-metal-arm64','lib')and p.suffix=='.dylib'
             and isinstance(sha,str)and re.fullmatch('[0-9a-f]{64}',sha),'invalid formal FEX dependency role')
        need(name not in roles,'duplicate FEX role');roles[name]=sha
    frozen=native.get('frozen_runtime',{})
    need(isinstance(frozen,dict),'frozen FEX roles must be an object')
    need(all(frozen.get(name)==sha for name,sha in roles.items()),'FEX descriptor differs from frozen actual roles')
    return doc,roles

def declaration(stage,dependencies):
    """Freeze final relocated/signed products, never generated game artifacts."""
    stage=Path(stage)
    def row(name):
        with(stage/name).open('rb')as stream:sha=hashlib.file_digest(stream,'sha256').hexdigest()
        return dict(path=name,sha256=sha)
    return dict(schema=1,development_only=False,backend='fex',automatic_default=True,
        cpu=row(CPU[0]),cpu_aliases=[row(CPU[1])],scanner=row(SCANNER),
        runtime_dependencies=dependencies,generated_game_assets_included=False,
        producer='same-original-game-host-after-original-runtime-hooks',
        runtime_loading='demand-copy-into-normal-invalidatable-FEX-JIT-buffer')
