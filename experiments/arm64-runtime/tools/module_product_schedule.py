"""Scheduling reuse of source-validated modules; never native import authority.

The existing controller retains target discovery, new captures, native misses
and final receipt checks. Only redundant original-container extraction is
skipped with unchanged module bytes AND continuous readonly-source history.
"""
from pathlib import Path
import json,re,stat
from launcher_game_catalog import directory,digest,file_sha,write_json
from game_pso_owned_audit import checked_bytes
from incremental_file_hashes import replaceable
from pso_startup_schedule import content_proof
from startup_change_journal import query,unchanged

MAX_RECEIPT=16*1024*1024
CASE=re.compile(r'module-[0-9a-f]{64}-[0-9a-f]{16}')

class ModuleProducts:
    def __init__(self,*,root,source,compiler,catalog,check):
        self.root=directory(root);self.source=directory(source);self.check=check
        self.path=self.root.parent/('module-products-'+compiler+'.json')
        self.readonly_source=stat.S_IMODE(self.source.stat().st_mode)==0o500
        tools=Path(__file__).parent
        self.binding=dict(schema=1,compiler=compiler,source=str(self.source),
            catalog=file_sha(catalog,check)[0],validators={name:file_sha(tools/name,check)[0]for name in
                ('module_product_schedule.py','run_game_module_contracts.py','run_owned_geometry_contracts.py',
                 'complete_geometry_psos.py','game_module_inputs.py','game_shader_catalog.py',
                 'gnm_shader_metadata.py','decode_shader_cache.py','pso_startup_schedule.py')})
        self.before=content_proof([self.root],check,module_products=True)
        self.source_token=query([self.source]);self.prior={};self.verified={};self.hits=0
        try:
            info=self.path.lstat()
            if info.st_nlink!=1 or stat.S_IMODE(info.st_mode)!=0o600:return
            saved=json.loads(checked_bytes(self.path,limit=MAX_RECEIPT)[0]);body=saved['body']
            if set(saved)!={'body','sha256'}or digest(body)!=saved['sha256']or body['binding']!=self.binding:return
            if not self.readonly_source or self.before is None or body['proof']!=self.before or not unchanged(body['source_journal'],[self.source]):return
            if not isinstance(body['verified'],dict)or len(body['verified'])>100000:return
            self.prior=body['verified'];self.source_token=body['source_journal']
        except(OSError,ValueError,KeyError,TypeError):pass

    def identity(self,entry):
        # No reference can hide in the skipped administrative/inventory area.
        packet=Path(entry['packet']);case=packet.parent.parent
        if case.parent!=self.root or not CASE.fullmatch(case.name):return None
        if packet!=case/'input/packet.json'or Path(entry['receipt'])!=case/'output/result.json'or Path(entry['spirv'])!=case/'output/module.spv':return None
        return digest(entry)

    def packet(self,uid,entry):
        self.check();key=self.identity(entry)
        if key is None or self.prior.get(uid)!=key:return None
        path=Path(entry['packet'])
        packet=json.loads(checked_bytes(path,limit=256*1024,expected=entry['packet_sha256'])[0])
        # Keep all actual frozen input byte/length/path checks. The only omitted
        # work is re-extracting the SAME code from its unchanged source archive.
        total=0
        for name in ('code','profile','spec','flat','expected_spirv','walker'):
            ref=packet[name]
            if ref is None:
                if name!='walker'or packet['walker_bytes']!=0:raise ValueError('module null input')
                continue
            p=Path(ref['path'])
            if p.parent!=path.parent or p.name!=ref['name']:raise ValueError('module frozen input root')
            raw,_=checked_bytes(p,expected=ref['sha256']);total+=len(raw)
            if len(raw)!=ref['bytes']or total>2*1024*1024:raise ValueError('module frozen input extent')
        self.hits+=1;return packet

    def record(self,uid,entry):
        key=self.identity(entry)
        if key is not None:self.verified[uid]=key

    def publish(self):
        self.check()
        stable_source=unchanged(self.source_token,[self.source])
        if self.hits and not stable_source:raise ValueError('registered source changed during module reuse')
        if not self.readonly_source or self.before is None or not self.verified or not stable_source:return False
        after=content_proof([self.root],self.check,module_products=True)
        # A newly expanded set must complete an ordinary stable pass before it
        # can grant this scheduling shortcut; never copy ready flags to it.
        if after!=self.before or not replaceable(self.path):return False
        body=dict(binding=self.binding,proof=after,source_journal=self.source_token,verified=self.verified)
        saved=dict(body=body,sha256=digest(body))
        if len(json.dumps(saved,separators=(',',':')).encode())>MAX_RECEIPT:return False
        write_json(self.path,saved);return True
